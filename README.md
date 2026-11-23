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

安装后提供三个命令：

| 命令 | 用途 |
|---|---|
| `pcb-design-mcp` | MCP 服务（stdio） |
| `pcb-mcp` | 客户端命令行 |
| `pcb-plan` | 生成 / 查看计划 |

自动布线依赖 Freerouting，**不随仓库分发**，需要自行下载，见 [DEPENDENCIES.md](DEPENDENCIES.md)。

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
tests/                回归与端到端用例
catalog/              器件 / 符号 / 封装的来源绑定资料
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