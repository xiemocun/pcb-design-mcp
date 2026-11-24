# 测试与验证现状（2026-11-24 实测）

本文件把「能独立跑通的测试」「依赖历史工作区的验收」「需要 CAD/Java 环境才能验证的项」分开列清楚。
所有数字来自当天在服务器上的实际运行，不是估计。

## 1. tests/ 目录的真实构成

- `tests/` 下共 **229 个 Python 文件**，其中 **53 个是 `test_*.py` unittest 模块**，其余是辅助脚本、夹具与端到端工具。「229」是文件数，不是用例数。
- 53 个模块中，**5 个模块 / 78 项用例完全自包含**，不需要仓库外的任何产物，已接入 CI：

| 模块 | 用例数 |
|---|---|
| `tests.test_rpc` | 2 |
| `tests.test_measurements` | 10 |
| `tests.test_stackup` | 12 |
| `tests.test_design_contract` | 20 |
| `tests.test_software_release` | 34 |
| 合计 | **78** |

可复制命令（源码 checkout，仓库根目录）：

```bash
python -m unittest -v tests.test_rpc tests.test_measurements tests.test_stackup \
  tests.test_design_contract tests.test_software_release
```

对**安装好的 wheel** 跑同一组（在 checkout 根目录执行，导入的是已安装的包）：

```bash
/path/to/venv/bin/python -m unittest -v tests.test_rpc tests.test_measurements \
  tests.test_stackup tests.test_design_contract tests.test_software_release
```

另有 4 个模块（`test_ac_reference`、`test_measurement_ratio`、`test_spice_bundle`、`test_spice_units`）
依赖同目录辅助模块的导入路径，需要**在 `tests/` 目录内**运行：

```bash
cd tests
python -m unittest test_ac_reference test_measurement_ratio test_spice_bundle test_spice_units
```

实测：10 + 10 + 4 + 2 = 26 项，全部通过（含 9 项环境性跳过）。

## 2. 本机全量扫描结果

平台：Alibaba Cloud Linux 3（x86_64），Python 3.13.18（源码 editable 安装）。
逐模块运行 53 个 `test_*.py`（`PCB_MCP_DATA_DIR` 指向临时目录）：

- **47 个模块通过**：43 个从仓库根目录以 `tests.test_x` 运行通过（372 项），加上第 1 节里 4 个需在 `tests/` 内运行的模块（26 项）。
- **6 个模块被缺失依赖挡住**，逐项核实如下：

| 模块 | 已核实的缺失项 |
|---|---|
| `test_catalog_import` | KiCad 符号库不在本机（`SYMBOL_LIBRARY_NOT_AVAILABLE: Transistor_FET`） |
| `test_feedforward_nominal` | 历史工作区数据 `work/riding-intercom/data/e4569a2267ec4b0c8dbfe22e4bb60820/…` |
| `test_ltspice` | 历史 macOS 临时目录 `/private/tmp/pcb-ltspice-5ctbx0zi` |
| `test_transient` | 历史工作区文件 `work/pulse-load-startup/frozen-requirement.json` |
| `test_mainboard_diagnostics` | 辅助模块 `tests/adapt_models.py` 不在仓库内 |
| `test_transient_mainboard` | 辅助模块 `tests/run_diagnostics.py` 不在仓库内 |

## 3. 历史验收目录：已核实全部缺失

测试代码引用了 **34 个 `acceptance-*` 验收目录**，本机逐一核对，**全部不存在**于仓库或服务器：

```
acceptance-approval            acceptance-audit-concurrency   acceptance-bom
acceptance-bom-evidence        acceptance-complete-bom        acceptance-complete-evidence
acceptance-connectivity-retry  acceptance-copper              acceptance-copper-evidence
acceptance-copper-native       acceptance-design-gates        acceptance-editing
acceptance-esp32-reference     acceptance-evidence            acceptance-final
acceptance-generic             acceptance-layout-faults       acceptance-live
acceptance-native-bom          acceptance-parts               acceptance-pcb
acceptance-pcb-mixed           acceptance-power-budget        acceptance-power-evidence
acceptance-power-module        acceptance-reset               acceptance-reset-evidence
acceptance-router-nonconvergence  acceptance-sensor-module    acceptance-startup
acceptance-startup-evidence    acceptance-usb-evidence        acceptance-usb-interlock
acceptance-usb-native
```

这些是历史验收产物（报告、冻结需求、工程快照），当时生成在开发工作区里，没有入库。
**后续计划**：逐步把它们改成可由测试自行生成的夹具；在此之前，依赖它们的用例不作为公开 CI 的门槛。

## 4. CI 实际跑什么

`.github/workflows/build.yml`，每次 push/PR 在 ubuntu-latest、Python 3.12 与 3.13 上：

1. `python -m build` 构建 sdist 与 wheel，装进干净 venv；
2. 版本与 5 个入口命令（`pcb-design-mcp`、`pcb-mcp`、`pcb-plan`、`pcb-evidence`、`pcb-router`）检查；
3. `pcb-evidence --bundled-only` 离线恢复 8 份一方事实摘录到安装目录；
4. wheel 内不得出现任何 `.jar` / `.pdf`；
5. **78 项自包含 unittest**（对安装好的 wheel 运行）；
6. **打包回归** `tests.test_packaging_assets`：在源码树副本里植入模拟 `evidence-cache/*.pdf` 与
   `vendor/freerouting-2.4.1.jar` 后直接构建，断言 wheel 与 sdist 都不含它们、一方运行资源仍在
   （该回归已做变异验证：换回旧打包逻辑时它会失败）；
7. **真实 stdio 冒烟**：在源码目录之外，用安装好的 wheel 启动 MCP，握手、发现 83 个工具、
   创建临时工程、读回并列出（`scripts/stdio_smoke.py`）。

## 5. 运行资源恢复：本机实测

平台同上；wheel 安装环境为 Python 3.14.9 干净 venv。命令与结果：

```bash
pcb-evidence --bundled-only   # 8/8 一方摘录离线恢复，退出码 0
pcb-evidence                  # 联网按 catalog 固定哈希恢复厂商 PDF 与 USB-IF 文档
pcb-router                    # 按 runtime/router.json 的 URL 与 SHA-256 安装 Freerouting 2.4.1
pcb-router --check            # present，哈希一致
python scripts/stdio_smoke.py # STDIO SMOKE PASS（83 工具，创建/读回/列出通过）
```

- 30 份被钉住的证据文件中：**22 份可从公开渠道恢复**（8 份一方摘录随发行包内置 + 11 份厂商 PDF 哈希仍匹配 + 3 份 USB-IF 文档从官方 zip 抽取且哈希匹配）。
- **8 份厂商 PDF 上游已改版**（哈希漂移）：`ltc6992-revd-ti-mirror.pdf`、`sn74lvc1g07.pdf`、`sn74lvc1g14.pdf`、`sn74lvc1g17.pdf`、`tlc555-2026.pdf`、`tlv755p.pdf`、`tps2553.pdf`、`vishay-dcrcwe3-2026.pdf`。
  恢复工具**只报告、不安装、不改 pin**，需要人工目录评审后决定如何更新来源。详见 DEPENDENCIES.md。
- 用完整原始资源（私有交接缓存）补齐后，本机按 `parts.py` 同一哈希逻辑做数据级核对：**38/38 器件证据可用**。
  注意这是数据级核对；完整 `parts.catalog()` 还需 KiCad 封装库（见第 6 节）。

## 6. 仍需环境验证的项（本机无法完成）

| 项 | 需要的环境 | 现状 |
|---|---|---|
| 完整 `parts.catalog()`（38 器件含封装定义哈希） | KiCad 10 符号/封装库 | 本机无 KiCad 库，`Resistor_THT`/`Transistor_FET` 等库不可用；**待验证** |
| Freerouting 实际布线运行 | Java 25（Temurin 25.0.5+8 或兼容） | 本机无 Java；JAR 已按 pin 恢复并通过 SHA-256/ZIP CRC 校验；**运行待验证** |
| macOS 原生评审器（Reviewer.swift） | macOS | 本机 Linux；**待验证** |
| 38/38 器件证据在完整 CAD 环境可用 | macOS + KiCad 10 + Java 25 | 仓库拥有者报告已达成（完整原始资源）；本会话未在 CAD 环境复跑 |
