# PCB Design MCP

把自然语言的设计需求，变成**可编辑的 KiCad 工程**——原理图、PCB、物料表（BOM）、贴装坐标（CPL）和验证记录，通过 MCP（Model Context Protocol）暴露给调用它的 AI。

> **状态：0.17.0.dev68，试用候选。** 这是一个开发版本，不是稳定版，也还没有物理打板验证。详见下面的「试用版本状态」。

## 它能做什么

- 描述一块板子要实现的功能，得到可打开、可编辑的 KiCad 工程，而不是图片或网表片段
- 按器件名称、型号或历史设计找回既有工程
- 对生成的工程做独立校验：网表/ERC、PCB 网络与布局、DRC、铜线几何
- 产出制造用的 BOM 与贴装坐标
- 固件绑定：从 CAD 证据生成 GPIO/身份头文件，并记录源码、ELF 与镜像哈希

设计由两部分完成：**调用 MCP 的 AI 负责理解需求、提出方案**，MCP 服务负责把它变成有约束、可验证的工程数据。

## 安装

需要 **Python 3.12+**。

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

安装后提供五个命令：

| 命令 | 用途 |
|---|---|
| `pcb-design-mcp` | MCP 服务（stdio） |
| `pcb-mcp` | 客户端命令行 |
| `pcb-plan` | 生成 / 查看计划 |
| `pcb-evidence` | 恢复器件证据（一方摘录 + 按固定哈希下载厂商文档） |
| `pcb-router` | 安装固定版本的 Freerouting 布线器 JAR |

也可以直接安装发布 wheel：`pip install pcb_design_mcp-0.17.0.dev68-py3-none-any.whl`。

### 恢复运行资源（新安装必做一次）

发行包**不含**厂商数据手册 PDF 和布线器 JAR（再分发条件限制），但内置了全部一方事实摘录与来源清单。
安装后运行：

```bash
pcb-evidence --bundled-only   # 离线：把 8 份一方摘录恢复到安装目录（无需联网）
pcb-evidence                  # 联网：按 catalog 固定哈希下载厂商 PDF 与 USB-IF 文档
pcb-router                    # 下载并校验 Freerouting 2.4.1（需要 Java 25 才能运行布线）
```

- 文件写入**程序实际读取的目录**：源码 checkout 是仓库根的 `evidence-cache/`、`vendor/`；
  wheel 安装是 site-packages 里的 `pcb_mcp/_assets/evidence-cache/`、`pcb_mcp/_assets/vendor/`。
  可用环境变量 `PCB_MCP_ASSET_ROOT` 指到别处。
- 上游文件若已改版（哈希与 pin 不符），工具**只报告、不安装、绝不静默改 pin**；
  当前已知 8 份厂商 PDF 漂移，清单与处理方式见 [DEPENDENCIES.md](DEPENDENCIES.md)。
- `pcb-router --url <镜像>` 可走镜像下载，SHA-256 与 ZIP CRC 校验不放松。

### 接到 MCP 客户端

服务走 stdio。在支持 MCP 的客户端里这样配置（把路径换成你的虚拟环境）：

```json
{
  "mcpServers": {
    "pcb-design": {
      "command": "/absolute/path/to/.venv/bin/pcb-design-mcp"
    }
  }
}
```

### 确认装好了

```bash
python -c "import pcb_mcp; print(pcb_mcp.__version__)"   # 0.17.0.dev68
pcb-mcp --help
```

## 测试现状（如实说明）

`tests/` 下共 **229 个 Python 文件**，其中 **53 个 `test_*.py` unittest 模块**——「229」是文件数，不是用例数。

- **5 个模块 / 78 项用例完全自包含**（`test_rpc`、`test_measurements`、`test_stackup`、`test_design_contract`、`test_software_release`），已接入 CI，对安装好的 wheel 运行：

```bash
python -m unittest -v tests.test_rpc tests.test_measurements tests.test_stackup \
  tests.test_design_contract tests.test_software_release
```

- 本机全量扫描：**47/53 个模块可通过**（其中 4 个需在 `tests/` 目录内运行）；其余 6 个被已核实的缺失依赖挡住（历史工作区数据、未入库的辅助模块、KiCad 库）。
- 测试引用的 **34 个 `acceptance-*` 历史验收目录已核实全部缺失**，将逐步改为可生成的夹具；完整清单、逐项缺失原因与可复制命令见 **[docs/TEST_STATUS.md](docs/TEST_STATUS.md)**。

CI 每次 push/PR 还会跑：

```bash
pip install build && python -m build      # 构建 sdist 与 wheel
pip install dist/*.whl                     # 装进干净环境
python -m unittest tests.test_packaging_assets   # 打包回归：植入模拟 PDF/JAR 后构建，必须被排除
python scripts/stdio_smoke.py              # 源码目录外，真实 stdio 连接：工具发现 + 建工程 + 读回
```

并检查五个入口命令存在、wheel 里**不含** JAR 与 PDF、`pcb-evidence --bundled-only` 能离线恢复一方摘录。

## 试用版本状态

| 项 | 状态 |
|---|---|
| 版本 | `0.17.0.dev68`（开发版本号，非稳定版） |
| 首个可用能力 | 从空工程可交付可编辑 KiCad 工程、BOM、贴装坐标与验证记录 |
| 物理验证 | **未完成**——目前没有实物板 |
| 独立用户首版 | 未验证，仍依赖开发介入 |
| 正式评测 | 未通过（早期 40 题已转为开发诊断，不再计分） |
| 目标 | 见 [V1_GOAL.md](V1_GOAL.md) |

**能力边界，需要直说：**

- 任意需求都能作为入口，**不等于任意电路都能保证成功**
- 生成结果可能需要工程介入，**不等于生产合格**
- 自动布线在给定设置下完成，**未做实物信号完整性验证**
- 早期版本的历史结论**不能作为当前版本的证据**

## 仓库结构

```
src/pcb_mcp/          MCP 服务：工具、校验、几何
src/pcb_mcp_client/   客户端：命令行、计划、固件绑定
firmware/am2302/      参考固件（ESP-IDF 5.5.0）
tests/                回归与端到端用例（现状见 docs/TEST_STATUS.md）
scripts/stdio_smoke.py  对安装好的 wheel 做真实 stdio 冒烟
install_evidence.py   源码 checkout 的证据恢复入口（等同 pcb-evidence）
install_router.py     源码 checkout 的布线器安装入口（等同 pcb-router）
catalog/              器件 / 符号 / 封装的来源绑定资料（含可公开的一方事实摘录）
runtime/router.json   布线器与 JRE 的来源、版本、校验值
docs/DEVLOG-0.16.md   0.16 阶段的开发流水（历史记录，其中的工作区链接已失效）
```

`GENERALIZATION.md` 与 `GENERALIZATION_PLAN.md` 是设计说明与通用化计划。

## 厂商资料与第三方组件

本仓库**不含厂商数据手册 PDF，也不含布线器 JAR**——两者各有再分发条件。获取方式与校验值见 [DEPENDENCIES.md](DEPENDENCIES.md)。

## 许可证

[MIT](LICENSE) © 2026 xiemocun

第三方组件遵循各自许可证：Freerouting（GPL-3.0）不随仓库分发，获取方式见 [DEPENDENCIES.md](DEPENDENCIES.md)。

## 致谢

布线依赖 [Freerouting](https://github.com/freerouting/freerouting)；硬件描述依赖 [SKiDL](https://github.com/devbisme/skidl) 与 [KiCad](https://www.kicad.org/)。