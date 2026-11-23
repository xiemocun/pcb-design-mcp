# 软件安装与验收（0.16.10）

2026-11-07通用接口增量已交付，先见 [GENERALIZATION.md](GENERALIZATION.md) 和本轮报告。当前包在 `work/generalization/dist/`，SHA256 `c025c300f26162a60a4c4a9c95224a02d96d32549580bbc8d9515c101c6f6662`；同版本号历史包在 `work/software-release/dist/`，不能混用。基础目录仍随包附带，新器件和模型通过不可变资料包导入；以下既有安装、恢复流程继续适用。

本发行版交付 MCP 服务、数据计划客户端、真实元件/封装库、来源证据和固定版本布线器。自然语言分析、方案制定和选型仍由调用 MCP 的 AI 完成；客户端执行经过真实器件查询形成的 JSON 计划，不内置模型账户或任意电路综合引擎。

## 安装

当前验证平台为 macOS、Python 3.12+、KiCad 10（含符号/封装库和自带 pcbnew Python）、Java 25+。KiCad、Java 不打入 Python 包。wheel 内置 38 条当前元件记录、证据缓存、定制符号/封装和固定 Freerouting 2.4.1 JAR；不包含用户工程、审批私钥、旧测试数据或虚拟环境。

在新虚拟环境安装交付的 wheel：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install /absolute/path/pcb_design_mcp-0.16.10-py3-none-any.whl
.venv/bin/pcb-mcp doctor
```

若 Java 不在 PATH，创建自己的 `runtime.toml`，用本机实际路径替换示例：

```toml
[runtime]
java = "/absolute/path/to/jdk/bin/java"
data_dir = "./projects"
```

```sh
.venv/bin/pcb-mcp --config runtime.toml doctor
.venv/bin/pcb-mcp --config runtime.toml host-config
.venv/bin/pcb-mcp --config runtime.toml serve
```

`host-config` 输出可用于支持 stdio MCP 的客户端的 JSON 配置，不自动修改应用设置。`doctor` 失败时返回非零退出码及失败项/修复路径。默认安装数据位置为 macOS 用户目录 `Library/Application Support/PCB Design MCP/data`；源码运行仍沿用仓库 `data`。`--data-dir` 优先于配置文件，配置文件优先于同名环境变量。相对配置路径相对于 TOML 文件。

可选 runtime 字段：`data_dir`、`java`、`kicad_cli`、`device_library`、`footprint_root`、`pcb_python`、`router`、`asset_root`、`reviewer`、`approval_authority`。它们对应 `PCB_MCP_` 环境变量；修改资源来源必须通过现有来源哈希检查。

软件 CAD 验证不需要编译审阅签字程序。后续独立人工审阅可用 `pcb-mcp build-reviewer` 编译随包 Swift 源码，所需 Xcode Command Line Tools 单独安装；该命令不创建签名身份、不签字、不生成生产文件。

## 执行与恢复

```sh
pcb-mcp --config runtime.toml run circuit-plan.json --requirement requirement.json --output ./run-01 --preflight-only
pcb-mcp --config runtime.toml run circuit-plan.json --requirement requirement.json --output ./run-02
pcb-mcp --config runtime.toml run circuit-plan.json --requirement requirement.json --output ./run-02 --resume
```

预检和完整执行使用不同的输出目录。预检核对来源身份、真实引脚覆盖、引用和工具 schema。完整执行创建空工程，完成原理图、独立网表/ERC、布局、布线、DRC及审阅文件导出；发现 error 即停止。不会代签或调用生产导出。

需求声明 `functional_check` 验收项时，完整执行须传入 `--functional contract.json`，必要时另传 `--model-bindings bindings.json`。缺少功能契约时在建项前返回错误。运行结束仍缺任何已声明验收的证据时，保留报告并返回非零退出码。

`journal.json` 在调用前记录请求，返回后保存结果和工程文件快照。恢复绑定原始需求、计划、客户端/服务端代码、数据目录和工具 schema；复用已完成结果，并用原幂等键恢复已提交但丢失响应的修改。另一个进程正在运行、工程被外部修改、来源变更、已有文件变化时拒绝恢复。

首版创建和改版分叉中断时，执行器核对原请求、原幂等键和初始工程。尚未执行的请求可用原键继续。改版分支已发布但回执尚未写入时，服务先核对该分支的初始状态和父版，再补记回执并接续同一工程。自动布线完成后回执丢失时，执行器核对原请求、工程状态及布线产物哈希，接续原 PCB 构建的验收。证据无法核对或工程发生变化时，保留现场并报告 `UNCERTAIN_NON_IDEMPOTENT_CALL`。已返回的电路错误会保持失败。修改需求或计划后使用新的输出目录。 实际中断验证

功能检查已经写入报告但回执丢失时，执行器核对待确认请求、原工程快照、功能索引、产物哈希及运行环境。证据完整时在原任务接续，不重复求解。证据变化或缺失时保留失败现场。

PCB 审阅 ZIP 已写完但导出回执丢失时，执行器用原请求、导出前后的构建快照和 ZIP 哈希找回原包，并继续原任务的剩余验收。若已写入的包或相关证据发生变化，保留中断状态，报告 `RESUME_PCB_REVIEW_EVIDENCE_CHANGED`。 审阅导出断线复验

输出包括 `run.json`、`journal.json`、`transcript.json`、`checks.json`、`acceptance.json` 以及解析后的目录/工程状态。实际 CAD 文件保存在所配置的数据目录下，审阅导出位置见 `checks.json`。

### 基于已交付设计改版

调用 MCP 的 AI 将用户的改版原话保存为含 `natural_language` 的 JSON，并把确认的布局改动转为数据计划。先用 `python -m pcb_mcp_client.revision <首版运行目录>` 读取父版本、`base_sha256` 及原生器件坐标。计划使用 `mode: mechanical_revision`，绑定该哈希和改版请求文件的 SHA256，`placements` 只列要移动的器件及其位号、目标坐标、旋转和依据。其它位置取自父版审阅包中已核对的实际 PCB。

用户限定可移动的器件或指定坐标时，在改版请求的 `placement_requirements` 中固定这些条件。每项提供 `ref`，可指定 `x_mm`、`y_mm` 和 `rotation_deg`。提供任何轴值时，其余轴继承父版。只写位号时，候选可自行选择该器件的坐标和旋转。候选必须满足已指定的目标，且只能移动列出的器件。此规则适用于机械改版及包含板框变更的改版。

机械改版利用父版中与新位置兼容的走线和过孔作为自动布线的起点。布线输入按网络名固定这些铜线记录的顺序，保留每条记录的原始几何和规则。新版交付仍需通过实际 KiCad 连接与 DRC 检查。

```sh
pcb-mcp --config runtime.toml run revision-plan.json --requirement revision-request.json \
  --base-run ./original-run --output ./revised-run
```

入口校验父工程版本、功能检查和原审阅包。新工程从该版本分支，沿用原功能契约、模型和验收条件，重新构建和复验 CAD 与装配交付。`revision.json` 记录父版、位置变化、运行结论和新版审阅包。越出板框等预检错误在创建分支前报告。 开发板验证

dev21 的离线安装验证覆盖从空工程交付四层板，并从一次已提交的布局修改恢复、完成改版。 安装与复验

`mode: component_revision` 使用同一入口，`replacement` 指定一个 `ref`、`mpn` 及 `part_definition_hash`。若原器件已绑定模型，先导入替代器件的模型，并在 `replacement.model_id` 指定它。执行器保持网络、实物引脚、其余器件和布局条件。对于带模型的替换，提交前用新模型复验候选，提交后在当前规格重新验证。静态预览阻塞时，其他替换也可在未提交候选上运行冻结契约。通过的候选证据随预览绑定，提交时再次核对。失败、预览与后续复验记录均保留。 四层替换验证

需要一起替换多个器件时，在同一模式中提供 `replacements`，列出 2 至 16 个不同位号及各自的器件定义和模型。完整候选先用继承的功能契约复验，通过后在一次项目修订中提交全部变更，随后从新状态执行功能、CAD 和交付检查。

用户明确要求调整功能指标时，AI 在改版请求的 `functional_limit_changes` 里填写功能检查 `id` 和 `previous`、`revised` 两个带单位的区间。旧限值须匹配交付基准。若工程的文字需求也记录了该限值，在对应条目内提供 `project_requirement` 的 `key`、`previous` 和 `revised`，以便核对并更新该项。

调整已交付 I²C 上拉的标称阻值时，在 `i2c_pullup_changes` 填写原检查的 `id`，并在 `previous` 和 `revised` 中各填写 `sda_ohm`、`scl_ohm`。用 `component_revision` 提交实际需要替换的一个电阻或一组电阻。执行器从基准功能证据确认位号，在提交换件前验证新值和原端子。若相应阻值也写入工程需求，可在该条目使用 `project_requirement` 同步更新。契约说明文字可通过 `contract_description_change` 的 `previous` 和 `revised` 更新。

`mode: functional_revision` 用于保留原电路并复验新限值。它保留父版已布线 PCB 和板规则的原始文件，重新运行功能检查、原理图 ERC、PCB 验证、DRC 及交付检查。若需要换用真实器件，可用 `component_revision` 执行同一份改版请求。两者都保留其余功能指标、测量端子、激励、观察窗、模型和原有 CAD 验收。执行器以冻结后的新版契约重新验证，`revision.json` 记录新旧契约哈希和变更。新版继续作为后续改版的基准。

`mode: circuit_extension` 用于新增来源绑定的器件和连接。`components` 和 `placements` 指定新增器件及位置。`attachments` 用既有网络名列出接入的新增引脚，`nets` 声明新网络，`no_connects` 记录有依据的新增悬空脚。原器件、原引脚连接、原坐标和其余板规则从已验证的父版继承。执行器核对新增实物引脚覆盖，再分支、构建并验证新交付。

新增器件需要仿真模型时，先通过 `verification_model_import` 导入来源绑定的模型，再在 `design_job_attempt` 的 `model_bindings` 中填写新增位号及其模型 ID。命令行运行可用 `--model-bindings` 传同样的映射。执行器在分支前核对模型与确切器件定义，并将新增模型与继承的模型一起用于复验。后续改版从交付记录继承这组模型。

`mode: connectivity_revision` 用于把已连接的引脚迁移到另一个既有网络。创建新的 MCP 改版任务时，把用户确认的完整改线范围写入 `requirement_json` 的 `connectivity_changes`。每条记录包含 `pin` 的 `ref` 和 `number`、`from_net` 及 `to_net`。候选的 `moves` 必须逐条匹配，顺序可不同。多改、漏改或改到错误网络时，在创建分支前报告具体引脚。执行器根据父版核对全部源连接，在改版分支原子提交整批变更，然后重新检查功能、CAD 和交付。原器件、坐标、其余引脚和供电声明从父版继承。命令行的改版请求提供相同字段时执行同样的范围核对。

移除可选子电路时，在改版 `requirement_json` 的 `circuit_removal` 中列出要移除的 `components` 位号和随之停用的 `retire_checks` 检查 ID，再提交 `mode: circuit_removal` 的计划。仅可停用引用被移除器件的检查。原有功能、仍被使用的仿真工况、其余器件位置和板级规则继续验收。执行器一次移除器件及因此清空的网络，剩下单个引脚的网络、还被约束引用的器件和破坏供电连接的移除请求会被拒绝。

调整层数、厚度或叠层时，在改版 `requirement_json` 中提供 `stackup_change`，其 `previous` 和 `revised` 都包含 `layers`、`thickness_mm` 和 `stackup`。四层必须提供完整叠层。执行 `mode: stackup_revision` 时先核对父版叠层，继承电路、板框和其余规则。器件位置默认继承父版，用户同时要求移件时，在同一请求的 `placement_requirements` 冻结授权位号和指定坐标，在候选的 `placements` 提供目标。按最终叠层和布局重新布线并复验原功能及实际 KiCad 文件。原布线约束若引用已停用的层，任务会被拒绝。

调整矩形板框尺寸时，在改版 `requirement_json` 的 `outline_change` 中用 `previous` 和 `revised` 分别声明原版和目标的 `width_mm`、`height_mm`。候选使用 `outline_revision`，默认保持父版实际器件位置。使用 `placement_requirements` 限定可移动的器件及目标，或在用户允许任意重新布局时设置 `allow_placement_changes`。候选的 `placements` 列出变动器件。执行器核对父版、继承原电路及规则，在新版上重新布线并复验功能、原生板框和交付。越界器件、孔或约束须通过实际修改解决后再尝试。

同时调整板框和叠层时，在同一改版请求中声明 `outline_change` 和 `stackup_change`，候选使用 `outline_revision`。两组旧值均与同一父版核对，目标板框、叠层和允许的位置变更一次应用，并在同一份新版中复验。

调整非镀铜安装孔时，在 `mounting_hole_change` 中提供父版和目标的完整有序列表 `previous` 与 `revised`。每项提供 `x_mm`、`y_mm`、`diameter_mm` 和 `keepout_mm`。板框和叠层沿用父版时，使用 `mounting_revision`。如果用户还要给新孔位让出空间，在同一请求的 `placement_requirements` 记录可移动的位号和指定坐标，并在候选的 `placements` 提供目标位置。执行器一次应用孔位和授权的位置改动，再按最终几何复验实际钻孔和各铜层净空。与板框或叠层同改时，使用对应的板框或叠层模式。同一序号孔位的精确验收可随明确改动更新，独立的上下限继续继承。

增删孔位时，逐孔验收随保留的孔移到新版对应位置。移除指定孔只撤销该孔专属的验收。若孔数量与保留孔位置同时变化且需要确定对应关系，`removed_indices` 指定原列表删除项，`added_indices` 指定新列表新增项。两组索引均从 0 起，剩余孔按各自列表顺序对应。保留孔的上下限继续生效。

改版通过后，`design_job_attempt` 回执的 `revision_summary.path` 指向 Markdown 改版说明，`revision_summary.sha256` 绑定其内容。命令行改版把同一说明写到运行目录的 `revision-summary.md`。说明对照两份实际交付的板框、安装孔、叠层、BOM、位置和原理图网络，并列出有变化的验收项目。成功交付可用作下一次改版的基准，接续前同时核对说明和审阅包的哈希。

若父版验收对实际板宽、板高或板厚设置了精确值，且该值匹配用户改版请求中的旧值，执行器按请求更新该项的期望值。实际板宽和板高使用 `pcb_verify` 的 `/details/fabrication/rectangle_bounds_mm/2` 和 `/details/fabrication/rectangle_bounds_mm/3`，板厚使用 `/details/stackup/thickness_mm`。上限、下限及其他验收继续继承。新的有效验收随交付保存，后续改版从该版本接续。

改版需求可用 `functional_additions` 加入新功能检查，例如验收新增监测口的电压和极性。若用户还要求增加供电、负载或温度工况，用 `functional_scenario_additions` 提供符合首版 `contract.scenarios` 格式的场景，并由新检查引用该场景 ID。每个新检查和新场景使用独立 ID，与原有检查及工况一并冻结和执行。只增加验收时，使用 `functional_revision` 保留电路和布局。

## 明确的验收条件

MCP 设计任务在未声明 `software_acceptance` 时自动应用默认验收。默认核对功能、原理图、ERC、PCB 连接、布局、DRC、零未连接和铜层几何。有效验收随运行记录保存。成功回执还要求可编辑 KiCad、来源 BOM、装配坐标和审阅归档完整，并核对归档内容。后续改版可以直接使用这份回执。

同一任务已经成功交付后，后续候选失败时，`design_job_read` 继续返回完成状态和原 `latest_delivery`，失败原因和证据保留在对应的 `attempts` 条目。后续改版从成功交付回执的尝试编号接续。

创建任务时可用 `attempt_timeout_s` 设置每次执行的时间上限，范围为 30–600 秒，默认 300 秒。设置较长执行时，宿主的工具超时须覆盖执行上限和退出清理。对测的整题预算另外按预登记规则执行。达到上限时保留执行日志、终态与清理诊断。

通过 MCP 的 `design_job_create` 创建首版时，在 `requirement_json` 保存需求原话和 `formal_requirements`。每项标量检查的 `id` 与功能契约一致，`allowed` 的上下限在换算后须与契约精确匹配。时延用时间单位，增益分贝用 `dB`，THD+N 可用 `%`，I²C 帧数和 I²S 字数分别用 `frame` 和 `word`。

I²C 上拉的连接要求可在 `formal_requirements` 中使用与 `contract.i2c_pullups` 相同的条目。逐项核对 `id`、SDA、SCL、供电端子和两只上拉的标称电阻值。执行时继续从实际网络检查连接和电阻值。仅在契约中声明上拉的既有请求仍可执行。

音频和噪声检查可在 `conditions.measurement_band` 绑定明确的频带。对带观察窗的检查，用 `conditions.observation_window` 绑定实际测量区间。每个条件以 `minimum`、`maximum`、`unit` 表达。I²C 和 I²S 契约同时保留预期帧或字序列、采样条件和时序限制。

需求 JSON 可选 `software_acceptance` 数组：

```json
{
  "software_acceptance": [
    {"id": "drc", "report": "drc_run", "pointer": "/verdict", "operator": "equals", "expected": "pass"},
    {"id": "no-open-nets", "report": "drc_run", "pointer": "/details/unconnected_count", "operator": "equals", "expected": 0}
  ]
}
```

只允许已收集检查报告、JSON Pointer 与 `equals`/`minimum`/`maximum`；不执行表达式或代码。缺失报告/字段为 `incomplete`，不匹配为 `fail`，有失败则执行退出码为 1。没有显式映射的自然语言需求不算通过；已有电气模型仍按原规则检查需求，不能通过这些比较绕过。

`cad_checks_passed` 只表示所列 CAD 检查通过。`acceptance.json` 的 pass 只覆盖声明的报告比较；`full_function_qualified` 和 `physical_verified` 保持 false。电气模型不完整、器件容量依据缺失或物理行为未知不会因为 CAD 通过而被改成合格。

## 软件回归入口

源码发行包包含可移植的故障测试和两份历史电路计划。它们是既有双路逻辑板与精密脉冲源的回归夹具，仅追加软件验收条件，不改变硬件要求，不代表新功能盲测。使用安装后的 Python，从源码目录外运行：

```sh
python -m unittest discover -s /absolute/path/to/source/tests -p 'test_software_release.py' -v
python /absolute/path/to/source/tests/software_release_e2e.py --fixtures /absolute/path/to/source/tests/fixtures/software-release --output /absolute/path/to/new-evidence-directory
```

端到端测试应设置 `PCB_MCP_DATA_DIR` 为新的隔离目录、`PCB_MCP_JAVA` 为 Java 25 路径，并清除 `PYTHONPATH`。测试故意丢失一次真实修改响应，随后通过 CLI 恢复；运行两份 CAD 计划，再提交不可能满足的频率要求确认在 CAD 构建前拒绝。负例和日志都会保留。

尚待板子验证：真实上电、连续采集、负载变化、温升及实物接口波形。软件侧的有效电容依据和完整电源模型资格也仍是独立缺口，不能把它们全部归为等待实物。原主板 CAD/固件交付作为历史来源绑定证据保留，本次运行资源改动不重新认证旧板。
