# 数据计划客户端

把AI依据真实查询写出的电路计划，交给同一个执行器调用现有PCB MCP。这里没有电路模板、自然语言解析器、模型API或新增MCP工具。不同功能的器件、引脚、网络和条件放在JSON计划里；坐标与布线仍由现有确定性后端完成。本轮实际执行案例是两路5V→3.3V逻辑输入板；尚未证明任意功能的生成成功率。

## 运行

在工程根目录（含run_server.py）中，使用本工程Python环境：

```sh
PYTHONPATH=src .venv/bin/python -m pcb_mcp_client.run_plan /absolute/path/circuit-plan.json \
  --requirement /absolute/path/frozen-requirement.json \
  --output /absolute/path/new-run-directory
```

`--preflight-only`只查询真实型号、符号/封装、检查引脚覆盖和参数schema，不创建项目。输出目录必须不存在，避免覆盖失败记录。完整执行每次从空项目开始；失败工程保留，当前不自动续传/回滚。MCP服务器仍有它自己的模型和支持范围，客户端不能扩展器件功能保证。

## 计划内容

- `requirement_sha256`：已冻结需求文件的字节哈希，防止执行时替换需求。
- `components`：引用编号、查询后的精确MPN与`part_definition_hash`；被动器件另填`value`。执行时重新search/get，要求当前证据可用且hash一致，不猜引脚或从模糊搜索自动挑第一条。
- `nets`：网络名、种类和每个真实引脚的`ref`/`number`；每个引脚恰好连到一个网络，或在`no_connects`中给出原因。符号的堆叠引脚用查询返回的完整number，不凭物理脚数量拆开。
- `settings`：现有的需求、外部电源/范围、被动额定值、电源预算、板约束、铜线和连接约束工具。工具名封闭枚举；`args`以服务器实际工具schema校验。
- 引用对象只有`{"$pin":["J1","1"]}`、`{"$component":"U1"}`、`{"$net":"V3V3"}`三种；普通字符串不作为代码执行。不得填项目ID、版本号和幂等键，它们由客户端维护。

预检不证明电路功能，也不会发现所有来自服务器语义规则的错误。身份/引脚/schema预检通过后，才创建工程、绑定器件、连网、设置条件，调用规格/BOM/电气/预算检查，再生成并独立核验原理图/ERC、PCB/布局/布线/DRC和审阅包。

`cad_checks_passed`仅表示列出的CAD检查通过。`checks.json`仍保留BOM、电气、铜线等incomplete结果，`full_function_qualified`和`physical_verified`仍为false。已知error会停止执行；没有签字、制造文件导出或下单入口。

## 审阅与失败

每个运行目录保存原需求、计划、查询解析结果、实际工具schema、逐次调用/响应、当前工程身份、检查结果和结束原因。模型的选型/推理仍需结合需求与厂家资料审查；仅比较需求哈希不等于已验证计划满足需求。具体任务的独立功能验收应在执行器外进行，不能把某套BOM硬编码进通用执行器。

本轮保留了两次服务端语义失败：独立逻辑器件被旧电源白名单拒绝，以及被错误套入ESP32传感器接口。修正适用范围后，同一份计划第三次完成CAD链路。没有修改原需求、放宽板尺寸、跳过错误或为这块板添加执行器分支。

## 当前主板固件绑定

对已经生成并校验的ESP32/AM2302工程，可执行：

```sh
PYTHONPATH=src .venv/bin/python -m pcb_mcp_client.firmware \
  --data-root data --project-id 541005c8bf52487b8b7453953fbd40bd \
  --pcb-build-id cffb0e18da80424aacd186827f1842f8
```

命令只输出绑定JSON，不修改工程、不烧录。它复用当前版本与真实库检查，核对原理图、PCB及检查文件哈希、独立网表、实际接收/发送GPIO，并拒绝失败或歧义接口、缺失/陈旧证据。完整铜线评估的incomplete状态保留；只允许实际铜线几何通过且没有已知error的候选用于固件绑定。

当前`firmware/am2302/hardware.json`已使用该结果。构建通过`hardware_config.py`同时生成板身份和GPIO，成功后写入构建证据，交付校验拒绝旧镜像/源码混用。当前验证与交付位于`work/mainboard-firmware-sync/`。这不是通用固件生成器，也没有改变既有电路计划执行方式。

## Transient evidence helpers

`transient.py` compares a KiCad XML netlist with MCP state, reads vendor ASY
port order, parses strict real/forward LTspice RAW data and measures every pulse
cycle in a declared observation window. It does not replace circuit models,
run approval tools, or change manufacturing qualification.

The first actual vendor-model integration and reproducible fixture are retained
in `work/pulse-load-startup` at the workspace root. This remains an explicit
LTC6992 fixture, not automatic model selection for arbitrary plans. LTspice and
its encrypted model are external local dependencies and are not distributed as
part of this Python package. Existing `run_plan.py` behavior is unchanged.
