# 第三方依赖的获取方式

本仓库不含下列文件。它们可以自由下载，但再分发条件与代码不同，所以留在仓库之外，只记录来源和校验值。
**安装后优先用内置命令恢复**：`pcb-evidence`（器件证据）与 `pcb-router`（布线器）；
源码 checkout 里等价入口是 `python install_evidence.py` / `python install_router.py`。

## 0. 文件放在哪里（程序实际读取的路径）

程序按 `pcb_mcp.config.asset_root()` 读取运行资源，优先级：

1. 环境变量 `PCB_MCP_ASSET_ROOT` 指定的目录；
2. 已安装包的 `pcb_mcp/_assets/`（wheel 安装即 site-packages 内）；
3. 源码 checkout 根目录。

因此：

| 资源 | 源码 checkout | wheel 安装 |
|---|---|---|
| 器件证据快照 | `evidence-cache/` | `pcb_mcp/_assets/evidence-cache/` |
| Freerouting JAR | `vendor/freerouting-2.4.1.jar` | `pcb_mcp/_assets/vendor/freerouting-2.4.1.jar` |

`pcb-evidence` / `pcb-router` 自动写入对应位置，不需要手工搬文件。
厂商 PDF 与 JAR **永远不会被打进 wheel 或 sdist**（有回归测试 `tests/test_packaging_assets.py` 看守）。

## 1. Freerouting 自动布线器（必需，用于布线）

| 项 | 值 |
|---|---|
| 版本 | 2.4.1 |
| 下载地址 | https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar |
| SHA-256 | `251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9` |
| 存放位置 | 见第 0 节（`vendor/freerouting-2.4.1.jar`） |

```bash
pcb-router                 # 下载 + SHA-256 校验 + ZIP CRC 全量校验 + 写安装记录
pcb-router --check         # 只检查是否已就位、哈希是否一致
pcb-router --url <镜像URL>  # 官方下载慢时可走镜像；校验不放松
```

手工等价操作：

```bash
mkdir -p vendor
curl -L -o vendor/freerouting-2.4.1.jar \
  https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar
echo "251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9  vendor/freerouting-2.4.1.jar" | sha256sum -c -
```

许可证与再分发条件以上游仓库为准（GPL-3.0，源码 https://github.com/freerouting/freerouting/tree/v2.4.1 ）。
本仓库不重打包、不修改该 JAR。同样的信息记录在 `runtime/router.json`，程序读取那里。

**实测（2026-11-24，Alibaba Cloud Linux 3，Python 3.14.9 干净 wheel 环境）**：
`pcb-router --url https://ghfast.top/https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar`
恢复成功，SHA-256 与 pin 一致，ZIP CRC 全量通过；`--check` 报 `present`。
本机无 Java，**实际布线运行待有 Java 25 的环境验证**。

## 2. Java 运行时（必需，用于运行布线器）

`runtime/router.json` 记录的是开发机上使用的 **Temurin 25.0.5+8**。你可以用任意兼容的 Java 25 运行时；若要与开发环境完全一致：

| 项 | 值 |
|---|---|
| 来源 | https://api.adoptium.net/v3/binary/latest/25/ga/…（按平台选择） |
| 开发机所用归档 SHA-256 | `72ebd76b502c849e88dea39afae51baf5f82f2b5b1b6389c28ea707b20451bce` |

该哈希对应 macOS/arm64 归档，**换平台时请以你下载的归档为准**，不要照抄。

## 3. 器件证据（30 份被钉住的文件）

`catalog/parts.json` 与相关参考目录共钉住 **30 份证据文件**，分三类：

1. **8 份一方事实摘录（JSON）**：从厂商网页/数据手册人工摘录、可公开再分发，**随发行包内置**
   （`catalog/evidence/`，含 AVX C0G、TDK×3、Würth、Recom R-78K、Espressif 复位、TI 启动）。
   `pcb-evidence --bundled-only` 离线恢复到证据目录，无需联网。
2. **19 份厂商数据手册 PDF**：不随包分发，`pcb-evidence` 按 catalog 里的 URL 下载并逐一核对 SHA-256。
   允许域名包括 TI、Analog Devices、Vishay、Kingbright、Aosong、GCT、Würth、Panasonic 工业站。
3. **3 份 USB-IF 规范文档**：从 usb.org 官方 release zip 中抽取对应成员并核对哈希
   （`usb_20_20250603.zip` → usb20-core / device-capacitance ECN；Type-C 2.5 release zip → usb-type-c-2.5）。

```bash
pcb-evidence --bundled-only   # 只恢复内置摘录（离线）
pcb-evidence --check          # 只报告现状，不下载
pcb-evidence                  # 全量恢复；漂移/不可达只报告，退出码非 0
```

**漂移策略**：下载件哈希与 pin 不符时，工具输出 `HASH-DRIFT`、不安装、**不修改 catalog**。
这是有意设计：厂商改版意味着事实可能变化，必须人工目录评审后才能更新 pin（`install_evidence.py` 时代的
「no silent repinning」规则保持不变）。

**实测（2026-11-24，同上环境）**：30 份中 22 份可公开恢复——8 份内置摘录 + 11 份厂商 PDF 哈希仍匹配
（adg4612-rev0、am2302、gct-usb4105、mrs、panasonic-fc-a、tlv9001-sbos833r、tnpw_e3、tpsm82821、
tusb320lai、txs0102、wp7113id）+ 3 份 USB-IF 文档全部从官方 zip 抽取且哈希匹配。
其余 **8 份厂商 PDF 上游已改版**，当日实测哈希如下（供人工评审比对，**不是新 pin**）：

| 文件 | 上游现哈希（2026-11-24） |
|---|---|
| ltc6992-revd-ti-mirror.pdf | `a3a751f584fe2fcb48cd8025589cb608e714f7209e90141b221ef40dc4b7d8c4` |
| sn74lvc1g07.pdf | `0471dcddef6983c851c474963ea86075f98c26a2995b7337218aecfedf1ee9c4` |
| sn74lvc1g14.pdf | `3d59568b16a2b7abb63d33ebe6f8ddd37e12dd3ca2616dd803872c509a583283` |
| sn74lvc1g17.pdf | `ade19853307838593bd60d27ae4f6a026fc8a52935de8d5f487457b8214e050f` |
| tlc555-2026.pdf | `18595d6adcc30d45c14fcf6eee24f23c971025e469560ed2cd21617c180a4eef` |
| tlv755p.pdf | `f9e676918d1af7144e55835616108def097b48fb5784bd0084ae77a418c64fbf` |
| tps2553.pdf | `b6a2d18ce02fb21eafcc7e1f1d8c4d69074cac38fad44d496682f3393f26445e` |
| vishay-dcrcwe3-2026.pdf | `7bb1860ab4bd41c7dc05d176602a33e384d64f413c17e49dce61ceb45d2cd0c1` |

在拿到这 8 份的原始版本（或完成人工评审更新 pin）之前，相关器件的证据状态为 `missing_or_changed`。
用完整原始资源补齐后，本机按 `parts.py` 同一哈希逻辑数据级核对 **38/38 器件证据可用**；
完整 `parts.catalog()`（含封装定义）仍需 KiCad 10 库，见 docs/TEST_STATUS.md 第 6 节。

开发机当时缓存的全部校验值见交接包 `SHA256SUMS`；仓库 `catalog/` 内文件已逐一与该清单核对一致。

## 4. ESP-IDF（可选，用于编译参考固件）

`firmware/am2302/` 用 **ESP-IDF 5.5.0** 构建。按上游文档安装同名版本即可；仓库内不含工具链。
