# 第三方依赖的获取方式

本仓库不含下列文件。它们可以自由下载，但再分发条件与代码不同，所以留在仓库之外，只记录来源和校验值。

## 1. Freerouting 自动布线器（必需，用于布线）

| 项 | 值 |
|---|---|
| 版本 | 2.4.1 |
| 下载地址 | https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar |
| SHA-256 | `251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9` |
| 存放位置 | 仓库根目录的 `vendor/freerouting-2.4.1.jar` |

```bash
mkdir -p vendor
curl -L -o vendor/freerouting-2.4.1.jar \
  https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar
echo "251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9  vendor/freerouting-2.4.1.jar" | sha256sum -c -
```

许可证与再分发条件以上游仓库为准（GPL-3.0）。本仓库不重打包、不修改该 JAR。

同样的信息也记录在 `runtime/router.json` 里，程序读取那里。

## 2. Java 运行时（必需，用于运行布线器）

`runtime/router.json` 记录的是开发机上使用的 **Temurin 25.0.5+8**。你可以用任意兼容的 Java 25 运行时；若要与开发环境完全一致：

| 项 | 值 |
|---|---|
| 来源 | https://api.adoptium.net/v3/binary/latest/25/ga/…（按平台选择） |
| 开发机所用归档 SHA-256 | `72ebd76b502c849e88dea39afae51baf5f82f2b5b1b6389c28ea707b20451bce` |

该哈希对应 macOS/arm64 归档，**换平台时请以你下载的归档为准**，不要照抄。

## 3. 厂商数据手册 PDF（可选，用于器件来源绑定）

开发过程中曾缓存 22 份厂商数据手册（TI、Analog Devices、GCT 等）。它们**不在本仓库内**，原因：

- 多数厂商的条款只允许原样分发，不允许再打包进第三方仓库
- 这些文件会随厂商修订而变化，仓库里的副本很快就会过期

需要时请从厂商官网按型号下载，放进 `evidence-cache/`（该目录已被 `.gitignore` 忽略）。

开发机当时缓存的校验值（仅供你比对下载到的文件是否同一版本）：

```
da7e5b537396e1b53eb5e8b1cd45fcb3076b3991978a98ecf381bdaee038ba9d  adg4612-rev0.pdf
a8f321e5f91420d78c6282e17c8913a85c89f265991a20c5d8e6111b10f600fd  am2302.pdf
fb331fbabee8392ed2937ed757c1610cb0f174b84625147c0b580a18eea8c0e5  gct-usb4105.pdf
3467508cd090fd975cf4007a33d34c33173168046e99da95cc48f706c76990ac  ltc6992-revd-ti-mirror.pdf
```

其余清单可由交接包内的 `SHA256SUMS` 恢复。

## 4. ESP-IDF（可选，用于编译参考固件）

`firmware/am2302/` 用 **ESP-IDF 5.5.0** 构建。按上游文档安装同名版本即可；仓库内不含工具链。