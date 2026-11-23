# 通用设计接口与使用

2026-11-09 开发版本 `0.17.0.dev11` 正按 [v1.0 目标](V1_GOAL.md)持续扩展。下方历史四题和 0.16.10 安装包不是当前实现的重新认证。新接口可用 `pcb-mcp --data-dir ./isolated-data tools --output tools.json` 查询，单个请求使用 `pcb-mcp --data-dir ./isolated-data call TOOL --request request.json --output response.json`；文件中填写工具 `request` 内部的数据，客户端验证当前 stdio schema 后发送。每次输出使用新路径，错误回执亦保留。

资料包可由 JSON 草案生成：`pcb-mcp pack-assets catalog spec.json --output package.json`，模型对应 `pack-assets model`。草案的每个 asset 可用包内相对 `file` 代替 base64；客户端计算文件哈希，拒绝路径越界、已声明哈希不匹配和伪装/解析损坏的 PDF，再按原有目录或模型 schema 校验。事实和页节依据仍须来自实际资料，打包不会自动证明事实正确。

目录草案可省略符号/封装定义哈希，准备器会从所附库或实际安装库读取并填入；已声明的哈希不会被覆盖。SPICE资产可用 `files` 顺序连接包内原始文件和纯引脚包装层，要求文件边界有换行、哈希和路径限制不变；不改原厂参数。`VerificationModel.spice_dialect="pspice"` 明确请求 ngspice 全网表兼容及固定标准代码模型，实际二进制哈希进入运行证据和缓存身份。未经资格验证的 XOR 方言转换仍拒绝。

四层 `board_constraints_set` 要求明确 `thickness_mm` 与 `stackup`：按 F.Cu/In1.Cu/In2.Cu/B.Cu 顺序声明铜厚，逐层介质厚度/材料/介电常数/损耗和构造依据；厚度总和、DSN 和原生 CAD 会独立核对。构造声明不等于受控阻抗或板厂工艺资格。

可选 `spice_dialect="ltspice"` 通过外置官方 Mac LTspice 运行保留的文本模型，或通过 `external_ltspice` 引用官方库中哈希绑定的原始二进制/加密模型及其匹配 ASY。模型与运行引擎不随软件包重新分发，不解密、不翻译参数；不接受调用方提供的可执行文件或二进制模型。配置 `runtime.ltspice_root`（或 `PCB_MCP_LTSPICE_ROOT`）为官方 Mac 包的隔离 SharedSupport/ltspice 目录，其本地 Wine 用户 Documents 目录须已存在。运行记录绑定引擎、完整模型/符号库、网表、日志及全部请求向量；进程零退出码而无完整结果仍拒绝。当前只验证这一 Mac 运行布局，不能推称其他平台可用。

功能场景的 `boundary_loads` 可明确外接电阻、电容或串联R/L夹具，只能接不同的真实外部端口节点。电容 `initial_v` 只用于瞬态且负端须为参考地，通过 `.ic` 求解一致的初始工作点，避免跳过工作点产生理想电容冲击。这些夹具不加入板载BOM，不代表真实电池化学模型或电机参数资格。带状态控制器须验证启动后的行为，不能把单纯 `.op` 的锁存状态当作正常工作证明。

等效有源负载可在直流工作点或瞬态场景中声明 `current_sink`，字段为 `current_a` 和 `compliance_v`。正向端口电压达到 `compliance_v` 时吸收 `current_a`，正电压低于该值时线性减载，零电压及反向电压时电流为零。端口电流和功率按实际求解的端口电压及夹具负载曲线复算，保留两端方向。该夹具用于验证板端带载能力，真实负载的启动行为和全温电流须有器件来源或实际测量。

dev5将厂商求解暂存到短ASCII私有目录，结束或超时后保留完整原始输出和日志再清理暂存区。实际安装验收发现Windows式输入路径超过约260字符时厂商引擎会零退出码且无结果；dev4该失败保留，不能改名路径后将其重标成功。390字符中文/空格结果路径的独立原厂引擎参考已通过，完整安装流程仍单独验收。

同一板约束可声明 `path_constraints`：实际 `source`/`target` Pin、`max_length_mm`、`max_vias`及依据。两端须为同网且不同的已连接真实引脚。布线前明确deferred，布线后按原生直接铜接触图寻找同时满足过孔预算的最短整段走线，不用地铜捷径；不满足会使 `pcb_layout_check` 失败。它检查长度上界和实际过孔，不证明阻抗、时延或模拟稳定性。PCB预览按每个铜层分别输出，主预览为正面，避免底层大地铜遮住正面线路。

功能契约增加 `current`（流入所选实际器件端子）、`power`（全部仪表化端子总功率）、`frequency` 与 `duty_cycle`。电流/功耗正负端必须属于同一实际器件；周期测量要求 transient、明确电压阈值和至少三个完整周期。`average`/`rms` 按实际时间轴及插值边界积分，`peak_to_peak` 取窗口峰峰值；均为声明模型的数值参考。

SPICE边界驱动可选 `kind="current"`，使用 `dc_a`、`ac_a`，正电流从 positive 端流向 negative 端；`pulse` 的前两项此时为安培，其余仍为秒。默认电压源沿用 `dc_v`/`ac_v`，两种单位不能混用。电流源不会作为稳压源证明复用。交流电压要求必须明确 `ac_measurement="magnitude"|"real"|"imaginary"`；交流电流默认幅值，也可明确投影。结果记录所用分量，不能把有符号复数直接与电压上下限比较。

Plan 可固定 `routing_attempt_limit`（默认2，上限4）与 `routing_max_passes`（默认20）；只有实际 DRC 单纯未连接/悬空过孔才会保留父失败并经已有 API 增量重布，其他错误停止。每轮原始检查保存在 `routing-attempts.json`，最终 CAD 成功不抹去原失败。完整来源 BOM 的 `pcb_review_export` 现在包含原生坐标、装配表以及本地使用库的可编辑 KiCad 工程，移植副本会重新导出网表并执行原生 DRC。

当前软件把新需求交给调用 MCP 的 AI 研究和设计，再通过同一套资料、组合、功能验证、CAD及修复接口执行。新型号和新拓扑可以作为数据进入，不必在核心中增加型号分支。软件没有内置一个能够保证任何电路成功的综合器；遇到模型、几何或资料缺口会给出具体失败及留存结果。

执行计划见 [GENERALIZATION_PLAN.md](GENERALIZATION_PLAN.md)，实际验收和交付见 work/generalization/交付报告.md。原主板与固件是独立历史工程，其未验证事项不因本次通用能力验收而消失。

## 启动当前代码

本机已有 Python、KiCad、Java 和依赖，可用当前工程的绝对入口启动 stdio 服务：

```sh
"<工作区>/pcb-design-mcp-v16" \
  "<工作区>/pcb-design-mcp-v16"
```

在 MCP 客户端中分别将上面两项作为 `command` 和 `args[0]`；设置 `PCB_MCP_DATA_DIR` 可使用隔离工程目录。不要把 stdout 当成日志终端；它传送 MCP 协议。新进程已实际验证，现有桌面连接是否已重新载入未作推断。

可安装包位于 `work/generalization/dist/`。它仍使用项目的0.16.10版本号，本次交付以报告中的完整SHA256区分旧包；`work/software-release/dist/`里的历史同名包不包含本次通用增量。安装和平台条件见 [SOFTWARE.md](SOFTWARE.md)。不包含本轮下载的受限厂家模型，模型须从原厂取得并经导入接口验证。

## AI 调用顺序

所有有参数的 MCP 工具接收 `{"request": {...}}`；精确 schema 以当前进程 `tools/list` 为准。

1. 查找资料和真实器件。`library_inventory`、`library_search/get`、`footprint_search/get`、`parts_search/part_get` 返回实际库与哈希。`catalog_import` 接收不可变资料包，逐项绑定厂家、MPN、引脚、封装和来源文件。未知器件不能只改显示名称冒充绑定。
2. 形成需求与候选。`design_compose` 接收自然语言来源、带单位的功能要求、模块及端口和连接，检查电压、电流、方向、参考地及真实引脚覆盖，输出标准Plan。端口声明本身不算功能证据。也可以直接提交经过真实查询形成的Plan。
3. 接入功能模型。`verification_model_import` 把原始SPICE器件/子电路或来源明确的数字真值模型绑定到精确器件哈希。所有引脚必须映射或以厂家NC依据明确排除；声明温度、供电与参考应力条件。
4. 冻结验收。`design_job_create` 保存原始需求JSON、`FunctionalContract`及尝试预算。功能检查支持线性DC参数角点、SPICE工作点/暂态/AC、静态数字真值。每个要求必须关联场景与数值上下界。
5. 提交候选。`design_job_attempt` 依次创建空工程、计算功能、检查规格/BOM/电气/电源预算、生成与独立验证原理图、生成/布线/检查PCB、导出审阅文件。发现error即停止，原始记录保留。
6. 阅读并修复。`design_job_read` 返回诊断和尝试记录；AI根据真实问题提交新候选，保持原验收要求和幂等键语义。已有PCB单纯未连接失败可用 `pcb_route_retry_prepare` 保留父板后增量重布，必须重新验证、DRC和导出；修复不会自动把原任务的失败历史改为通过。
7. 从交付继续改版。首版通过原功能、声明的软件验收和审阅包核对后，`design_job_attempt` 返回 `delivery`，`design_job_read` 返回 `latest_delivery`。在同一 MCP 连接上用 `design_job_create` 提交改版原文和 `base_job: {job_id, attempt}`。该请求继承功能契约，返回 `requirement_sha256` 与基准哈希、坐标和交付信息。再向 `design_job_attempt` 提交 `mechanical_revision` 或 `component_revision` 计划。新版使用独立项目，保留原检查和失败记录。需要改变功能测量契约的请求仍须单独规划。 MCP 实跑
8. 接续同一次尝试。连接中断后，以相同的任务编号、候选参数和幂等键重新调用 `design_job_attempt`。原执行器仍持锁时返回 `DESIGN_JOB_ATTEMPT_RUNNING`，可以等待原执行器结束再试。已完成的执行从实际交付补记回执，日志可接续的执行沿同一尝试继续。遇到无法确认是否已提交的新建工程、分叉或布线调用，沿用底层日志的停止规则，保留检查记录。

可复用的真实输入在 `work/generalization/heldout-01/`：`plan.json`、`contract.json`、`requirement.json`、`part-package.json`、`model-package.json`，组合示例另有 `proposal.json`。包内原厂资料仅供当前本地验证，遵守其再分发条件。`tests/heldout_protocol.py`是验收输入生成器，不是生产核心中的电路选择逻辑。

## 结果怎么解释

- `functional_check.verdict=pass`：当前网表、器件与模型哈希下，所列功能场景满足所列要求。线性电阻角点可以覆盖其声明参数区间；标称SPICE不自动覆盖公差、寄生、全工况或热行为；数字真值不证明时序。
- `cad_checks_passed`：实际独立网表/ERC、焊盘网络、布局、布线和DRC通过。BOM额定值完备性、电气覆盖、铜线载流或物理电源资格仍可能为incomplete，原始报告完整保留。
- `completed_reference_design`：任务中的功能参考和CAD链路完成，不等于生产认证。没有声明式 `software_acceptance` 时其字段仍为incomplete，不能据此推断未映射的文字要求通过。
- `failed` / `incomplete`：分别表示发现矛盾/超限，或缺少足够证据。错误不会靠改成warning、缩窄冻结工况或改目标修复。

新能力可以通过资料包扩展；遇到不支持的模型方言、复杂多单元符号、超过当前几何资源限制、RF/高速/热/EMC或完整制造资格，仍需对应通用适配器或额外工程验证。支持任意需求作为入口，不等于保证所有需求已经有完整验证后端。

## 本轮复现

从当前目录运行，输出目录必须不存在：

```sh
PYTHONPATH=src .venv/bin/python tests/heldout_protocol.py \
  --case passive_bandpass --output work/generalization/my-bandpass-run
```

其他case为 `power_shunt_reference`、`two_input_interlock`、`bipolar_low_side_switch`，需要对应原厂资料完整存在。脚本先校验冻结核心，再通过真实stdio MCP执行。若以后正常改动核心，应建立新的冻结清单与独立验收集，不能修改旧清单来伪装旧结果仍有效。
