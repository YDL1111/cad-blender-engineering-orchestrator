# CAD–Blender 工程智能体编排 Skill

让 Codex 帮你把一个工程建模需求，从“我想做什么”推进到“可以打开、查看和检查的 CAD 图纸与 Blender 模型”。

**版本：0.1.0 · 作者：YDL1111 · 许可证：[MIT](LICENSE)**

本项目定位为**工程任务的编排与验证层**，不是自研 CAD/Blender 软件控制引擎。软件操作可以复用开源 MCP 或软件原生脚本/API；本 Skill 把这些能力组织成有需求确认、任务交接、独立审核和版本化交付的完整流程。

你可以先用自然语言描述想做的设备或空间。Skill 会整理需求、提出必要的问题，等关键条件确认后，再安排 CAD 建模、Blender 展示、独立审核和交付。你主要与总控 Agent 对话，不需要自己逐个指挥 CAD、Blender 和审核 Agent。

**第一次使用建议：先安装 → 检查软件路径 → 做一个小模型 → 再尝试完整项目。**

## 先看一个实际成果

下面两张图片展示第二次双泵撬装供水机组验证项目的同一套已接受几何，使用一致的斜侧面观察方向：第一张是 FreeCAD 模型预览，第二张是 Blender 总览渲染。

**CAD：工程模型的斜侧面预览**

![双泵撬装供水机组 FreeCAD 工程模型的斜侧面预览](https://raw.githubusercontent.com/YDL1111/cad-blender-engineering-orchestrator/main/docs/images/dual-pump-cad.png)

**Blender：使用接受后的 CAD 几何制作展示**

![同一双泵撬装供水机组的 Blender 总览渲染](https://raw.githubusercontent.com/YDL1111/cad-blender-engineering-orchestrator/main/docs/images/dual-pump-blender.png)

可以对照观察两台泵、电机、立式压力罐、控制柜、公共进出水管、阀门、桥架和支撑。两张图使用相同的相机方向与正交视域；CAD 用工程显示风格，Blender 增加材质、灯光和背景。CAD 图片由历史交付中的 FCStd 副本重新导出，只调整显示与相机，不保存模型；Blender 图片取自同一历史交付包。

该项目包含 **41 个受控对象**，采用冻结的 Canonical Mesh 与 `exact_mesh`；交付时的检查覆盖网格、对象关系、变换和关键尺寸，关键尺寸验收容差为 **0.1 mm**。图片用于展示，正式一致性结论来自结构化比较和独立审核，不能只靠两张图片目测判断。

> 这是概念与协调级案例。截图来自历史双泵项目；下文发布前的小规模实机测试是另一项验证，二者的数据不混用。

## 它可以帮我做什么？

适合需要同时使用 CAD 和 Blender、并且关心尺寸与形状一致性的项目。

| 场景 | 可以尝试的任务 |
|---|---|
| 机械设备与产品 | 双泵撬装机组、设备底座、简单装配、设备接口与检修空间展示 |
| 建筑与室内 | 房间布局、家具设备布置、带尺寸的平面图与空间模型 |
| 建筑机电协调 | 管道、风管、桥架和设备的空间布置与展示 |
| 厂房与设施布局 | 设备位置、通道、维护区域和整体空间协调 |

交付物可以包括可编辑 CAD 文件、STEP、PDF 图纸、Blender `.blend`、PNG 图片，以及需求、审核和一致性报告。**输出格式、视图数量、尺寸精度等会在需求阶段确认，并取决于本机软件实际支持的能力。**

目前已在 Windows 上通过 FreeCAD 和 Blender 的脚本接口完成实际验证。其他 CAD 软件需要满足相应接口契约并经过验证；仅安装 AutoCAD 或 Revit，并不代表本版本已经能够直接使用它们。

纯 CAD 修改、纯 Blender 艺术创作、工程仿真，以及需要正式制造批准或工程师签章的任务，不属于本 Skill 的主要范围。

## 工具基础与开源致谢

本项目的探索和使用过程中采用了以下两个开源 MCP 项目。感谢上游作者及贡献者提供的软件连接与操作能力：

| 开源项目 | 提供的能力 | 许可证与署名 |
|---|---|---|
| [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp)（现名 [mcp-for-blender](https://github.com/ahujasid/mcp-for-blender)） | 连接 AI 客户端与 Blender，提供场景查询、对象与材质操作、Blender Python 执行等能力 | [MIT](https://github.com/ahujasid/mcp-for-blender/blob/main/LICENSE)；Copyright © 2025 Siddharth Ahuja |
| [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) | 连接 AI 客户端与 FreeCAD，提供模型创建与编辑、文档查询、FreeCAD Python 脚本执行等能力 | [MIT](https://github.com/neka-nat/freecad-mcp/blob/main/LICENSE)；Copyright © 2025 Shirokuma (k tanaka) |

可以这样理解分工：

- **FreeCAD / Blender**：实际完成几何计算、建模、保存和渲染。
- **开源 MCP 或原生脚本/API**：让 Agent 能够调用软件里的操作。
- **本 Skill**：规定先确认什么需求、给哪个 Agent 什么任务、以哪个版本为准、怎么审核，以及什么条件下才能交付。

因此，本项目的主要贡献是需求基线、Project Manifest、Work Packet、状态机、独立回读与审核、跨软件一致性校验，以及 accepted/working 交付隔离。上述开源 MCP 的软件操作接口不作为本项目的自研成果。

**这里的开源致谢与测试范围需要区分。** 发布前的小规模实机 smoke test 使用 FreeCAD/Blender 原生脚本/API及独立软件进程完成，不代表已经对这两个 MCP 的所有版本或全部功能完成兼容性认证。换用 MCP 执行时，仍需检查实际连接、操作、保存、导出和回读能力。

如果希望通过 MCP 操作软件，请分别参考上游的 [Blender MCP 安装与使用说明](https://github.com/ahujasid/mcp-for-blender#quickstart)和 [FreeCAD MCP 安装与使用说明](https://github.com/neka-nat/freecad-mcp#readme)。Skill 安装、MCP 配置、软件插件启用是不同步骤；只复制本 Skill 不会自动完成后两步。

本仓库发布包不包含上述 MCP 的源码或安装包。它们以及 FreeCAD、Blender 均遵循各自许可证；本项目的 MIT 许可证不替代上游许可证。

## 它是怎么工作的？

### 一个入口，几个分工明确的 Agent

| 角色 | 通俗理解 | 负责什么 |
|---|---|---|
| 总控 Agent | 项目负责人 | 跟你沟通，整理需求，分配任务，记录版本，推进流程 |
| CAD Agent | 工程建模与制图人员 | 根据确认的需求创建工程几何、出图和导出文件 |
| Blender Agent | 展示制作人员 | 使用审核通过的 CAD 几何，添加材质、灯光、相机和渲染 |
| 独立审核 Agent | 检查人员 | 检查尺寸、形状、布局、文件和证据，提出需要修正的问题 |
| Python 校验脚本 | 自动检查工具 | 比对对象、尺寸、版本、文件哈希和交付完整性 |

这是**一个总控 Skill 内部的多 Agent 工作流程**。子 Agent 接收当前任务需要的上下文和写入权限，具体调度由总控安排。你通常不需要手动为每个子 Agent 新开任务。

### 从需求到交付的九个步骤

1. **理解你的需求**：确定要做什么、用于什么、有哪些资料、要交付哪些文件。
2. **补齐关键条件**：询问尺寸、接口、设备类型、空间限制等；没有资料时，可以先提出概念参数让你确认。
3. **冻结需求与计划**：记录确认的条件、接受的假设和未明确的信息，建立项目数据和工作包。
4. **CAD 建模与出图**：完成权威工程几何，保存文件并导出约定格式。
5. **独立检查 CAD**：重新打开保存的文件，核对对象、尺寸、接口和图纸；发现问题就返回修正。
6. **Blender 建模与展示**：使用已接受的 CAD 几何完成展示场景和图片。
7. **独立检查 Blender**：检查实际形状、对象关系、可见性和图片质量。
8. **跨软件核对**：确认两边没有丢对象、错单位、改位置或改变受控形状。
9. **发布交付版本**：通过门禁后保存正式快照、交付包和可编辑工作副本。

**关键需求确认后，可以让总控连续完成执行阶段。** CAD 和 Blender 内部仍有先后顺序：CAD 审核通过后，才进入依赖它的 Blender 工作。遇到关键资料缺失、软件不可用或未解决的审核问题，流程会暂停并说明原因。

### 为什么先做 CAD，再做 Blender？

CAD 保存受控尺寸和工程几何，是项目的权威来源。Blender 主要负责材质、灯光、相机和展示，必须保留约定的对象 ID、尺寸、坐标和装配关系。

对真实形状有要求时，可以在需求中明确选择 `exact_mesh`。它让 Blender 使用从 CAD 导出的冻结网格，并检查顶点、三角面和拓扑等证据。包围盒只能说明物体“占多大空间”，不能说明圆柱仍是圆柱，因此不能单独作为真实形状一致的证明。

## 使用前需要准备什么？

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11；当前验证主要面向 Windows |
| Codex | 支持本地 Skills、文件操作、命令执行与所需 Agent 调度能力的客户端 |
| Python | 3.11 或更新版本，用于运行附带的校验脚本 |
| FreeCAD | 已安装，并具有可调用的命令或脚本接口 |
| Blender | 已安装，并具有可调用的可执行文件和 Python 接口 |
| 项目目录 | 一个可写、独立于软件安装目录和 Skill 目录的位置 |

本地实机验证使用了 **Python 3.11.9、FreeCAD 1.1.3 Revision 20260725、Blender 5.2.1 LTS**。换机器或换软件版本后，建议先做小规模测试。

Skill 自带运行脚本使用 Python 标准库。开发者工具 `skill-creator` 中的 `quick_validate.py` 另外需要 PyYAML；普通使用者不必为了运行 Skill 专门执行这个开发者校验。

本仓库包含工作流程、模板和验证工具，FreeCAD 与 Blender 需要另外安装。执行 Agent 根据工作包，通过已验证可用的 MCP 工具或原生脚本/API 操作软件。本版本不要求必须安装 CAD/Blender MCP，也不会自动安装软件或建立 MCP 连接；发布前实机验证采用的是原生脚本/API 路径。

## 安装 Skill

### 方法一：下载 ZIP，手动复制

适合不熟悉 Git 的用户。

1. 打开 [v0.1.0 发布页](https://github.com/YDL1111/cad-blender-engineering-orchestrator/releases/tag/v0.1.0)。
2. 下载附件 **`cad-blender-engineering-orchestrator-0.1.0.zip`**。这是为手动安装整理的包；GitHub 自动生成的 `Source code` 压缩包目录结构与它不同。
3. 解压后，将整个 `cad-blender-engineering-orchestrator` 文件夹复制到用户目录的 `.codex/skills/` 下。
4. 在 Codex 中新建任务，尝试显式调用这个 Skill；如果没有发现它，再重新打开客户端检查。

Windows 上通常是：

```text
%USERPROFILE%\.codex\skills\cad-blender-engineering-orchestrator\SKILL.md
```

`SKILL.md` 应直接位于 Skill 文件夹内，不要多套一层同名文件夹。需要连同 `scripts/`、`references/`、`assets/` 和 `agents/` 一起复制。若你自定义了 Skill 安装根目录，请使用对应位置。

### 方法二：使用 Git 安装

已安装 Git 的用户可以在 PowerShell 执行：

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.codex\skills') | Out-Null
git clone --branch v0.1.0 https://github.com/YDL1111/cad-blender-engineering-orchestrator.git "$HOME\.codex\skills\cad-blender-engineering-orchestrator"
```

这会安装已发布的 `v0.1.0`，便于复现。仓库 `main` 分支包含更新后的中文 README 和演示图片；若希望同时获取这些更新，可去掉 `--branch v0.1.0`。正式发布的 ZIP 和标签保留原内容，不随文档修改而覆盖。

目标文件夹已存在时，请先按[安装与升级说明](docs/installation.md)处理，避免覆盖本地改动。

## 配置软件路径和项目目录

### 最容易上手：在对话里告诉 Codex

将路径替换成你的实际安装位置：

```text
请使用 $cad-blender-engineering-orchestrator。

我的软件路径是：
FreeCAD：C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe
Blender：C:\Program Files\Blender Foundation\Blender 5.2\blender.exe
项目根目录：E:\EngineeringProjects

先检查这些路径和脚本接口是否可用，并告诉我检查结果。
```

项目应放在独立目录中，不要放在 FreeCAD、Blender 的安装文件夹里，也不要放进 Skill 本体。

### 经常使用：配置环境变量

下面是示例，**请替换成自己的实际路径**。在 PowerShell 中执行：

```powershell
$env:CBE_PROJECTS_ROOT = 'E:\EngineeringProjects'
$env:CBE_FREECAD_CMD = 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe'
$env:CBE_BLENDER_EXE = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'

Set-Location "$HOME\.codex\skills\cad-blender-engineering-orchestrator"
python scripts\check_environment.py
```

`$env:...` 只影响当前 PowerShell 及其后续启动的子进程，已经运行的 Codex 不会自动获得这些值。你可以在对话中显式提供路径；希望长期生效时，可在 Windows 的“编辑账户的环境变量”中添加上述三个变量，再重新启动 Codex。

| 配置 | 优先顺序 |
|---|---|
| 项目根目录 | 用户或命令行指定 → `CBE_PROJECTS_ROOT` → 用户配置文件 → `~/CBEngineeringProjects` |
| 软件可执行文件 | 命令行指定 → 对应环境变量 → PATH → Windows 常见安装目录 |

项目目录也可通过 `~/.config/cad-blender-engineering-orchestrator/config.json` 配置。该文件目前只配置 `projects_root`，软件路径使用显式参数或环境变量。详见[配置说明](docs/configuration.md)。

### 怎么看检测结果？

下面的软件路径检测针对原生脚本执行，不能代替 MCP 连接测试。使用 MCP 时，还需确认服务及软件侧插件可用，并真正执行一次操作和结果查询；仅有配置项或工具名称不代表连接成功。

- `preflight_ready: true`：基础检查通过，可继续验证真实保存、导出和回读能力。
- `blockers` 非空：存在阻塞项，例如软件路径不存在或版本查询失败，先按报告修正。
- `engineering_export: not_verified_by_read_only_preflight`：正常提示。找到软件不等于验证过导出能力，需要通过实际小项目确认。

若指定了错误路径，检测器会直接报错，方便你发现配置问题，不会悄悄换用另一套软件。

## 第一次使用：做一个小项目

先从三个简单物体开始，容易观察圆柱是否变形、尺寸是否一致，也比完整设备或房屋项目省时。

将下面的提示词复制到 Codex，项目路径可自行修改：

```text
请使用 $cad-blender-engineering-orchestrator，完成一个小规模端到端测试。

项目目录：E:\EngineeringProjects\three-part-demo-001
需要建模：
- 一块 600×400×20 mm 底板；
- 一个直径 120 mm、高 200 mm 的圆柱；
- 一个 160×100×80 mm 的长方体。

圆柱和长方体放在底板上，位置由你提出一套合理布局。
目标是概念展示与跨软件一致性验证，使用毫米。
CAD 为权威几何，Blender 使用 exact_mesh，保留真实形状。
希望得到可编辑 CAD、STEP、Blender 文件和至少两张清晰图片。

先整理需求基线，集中询问必要问题。
我确认基线后，授权创建项目并连续执行 CAD、Blender、
独立审核、跨软件校验和版本化交付。
遇到关键未知项或审核阻塞时再向我说明。
```

首次测试应真正执行保存和导出，并在独立软件进程中重新打开文件核对。不要只以“能找到软件”或“脚本退出码为 0”判断成功。

已有同名项目时，换一个项目 ID，或明确提出正式修订需求；脚手架不会直接覆盖现有项目。

## 正式项目怎么提需求？

不需要一开始就写成几十条建模指令。先讲清用途、范围和已知条件，让 Skill 帮你补齐。

### 示例：双泵撬装供水机组

```text
请使用 $cad-blender-engineering-orchestrator，帮我做一个双泵撬装供水机组。

用途：方案讨论和空间协调展示。
项目目录：E:\EngineeringProjects\dual-pump-demo-001
希望包括两台泵、公共进出水管路、阀门仪表、压力罐、
控制柜、桥架、底座和必要支撑。

我暂时没有完整设备选型资料。
请先分析需求，识别缺少的关键参数，提出一套概念配置供我确认。
确认后连续完成 CAD 建模与图纸、Blender 展示、独立审核、
跨软件一致性检查和交付。

CAD 是权威几何；Blender 要保留真实设备和管路形状，
最终受控模型不要用包围盒代理替代。
```

### 提供这些信息会更顺利

| 信息 | 示例 |
|---|---|
| 用途与深度 | 概念方案、协调展示、需要设计师继续深化的初稿 |
| 空间边界 | 最大长宽高、房间尺寸、安装区域 |
| 已知工程参数 | 设备外形、接口方向、管径、层高、坐标和单位 |
| 功能与布局要求 | 一用一备、检修侧、通道、开门方向 |
| 参考资料 | 尺寸图、已有 CAD、产品手册、平面图和风格图片 |
| 交付要求 | PDF 幅面、视图数量、STEP、Blend、图片分辨率 |
| 验收要求 | 关键尺寸容差、形状保真等级、必须检查的细节 |

没有资料的地方可以让 Agent 提出假设，但关键参数需要开工前明确。仅凭模糊图片无法可靠判断承重结构、暗埋管线或设备真实接口。

## 执行期间我需要做什么？

你主要参与三个时点：

1. **需求确认**：确认目标、关键尺寸、假设和交付要求。
2. **处理阻塞**：软件不可用、资料冲突或关键判断缺少依据时，补充信息或调整范围。
3. **查看交付**：打开工作副本，检查是否满足实际用途。

总控负责内部任务调度。执行 Agent 根据工作包操作软件，审核 Agent 根据独立证据提出问题；允许范围内的修正可由总控安排，阻塞项未解决则不会直接交付。

项目越复杂，建模、出图、渲染和审核越耗时，也会消耗更多 token。第一次建议少量对象、少量视图，先确认环境和流程，再逐步扩大规模。

## 交付后去哪找文件？

一个项目通常包含如下目录，具体文件名和子目录会随项目调整：

```text
项目目录/
├── inputs/                 你提供的原始资料
├── requirements/           确认后的需求
├── manifests/              各版本项目数据
├── work-packets/           CAD、Blender 和审核任务
├── cad/                    CAD 阶段成果与证据
├── blender/                Blender 阶段成果与图片
├── reviews/                独立审核报告
├── reports/                检测、比较和执行记录
├── delivery/
│   ├── accepted/r1/        已验收的正式快照
│   ├── delivery-report-r1.json
│   ├── final-hash-ledger-r1.json
│   └── package-r1.zip      正式交付包
└── working/
    ├── cad/                用于日常打开和继续编辑的 CAD 副本
    └── blender/            用于日常打开和继续编辑的 Blender 副本
```

**平时查看模型、切换可见性、调整视角或继续编辑，请打开 `working/` 的文件。** `delivery/accepted/rN/` 用于保留当时验收的事实，重新保存其中的文件会改变哈希，使原始验证失效。

如果 working 的修改需要成为正式成果，应让总控创建新工作包，重新执行必要审核，发布为 `r2`，保留 `r1`。

当前发布器会保留已有的同名 working 文件，不自动覆盖。因此发布 `r2` 后，旧工作副本未必已经更新。要查看新版，请从对应 accepted 版本复制到新的工作目录，或让总控明确准备新版副本。

## 文件夹里那些 JSON、脚本有什么用？

普通使用者不需要逐个编辑，它们让长任务能够被检查和追踪。

| 名称 | 通俗解释 |
|---|---|
| Requirements Baseline | “我们已经确认要做什么” |
| Project Manifest | “这个项目以哪些对象、尺寸和版本为准” |
| Work Packet | “这次给某个 Agent 的任务和权限范围” |
| Stage Result | “这个阶段做出了哪些真实文件，有哪些回读证据” |
| Review Report | “检查了什么，发现什么问题，能否通过” |
| Canonical Mesh | “用于核对 CAD 与 Blender 的冻结网格数据” |
| Hash Ledger | “正式交付文件的指纹清单，用于发现后续修改” |

`SKILL.md` 负责流程；`references/` 存放角色和领域规则；`assets/` 提供模板；`scripts/` 执行确定性验证；`agents/openai.yaml` 提供名称、简介和默认调用提示等入口信息。

## 常见问题

### 已经能用 MCP 建模，为什么还需要这个 Skill？

MCP 解决的是“Agent 怎么操作软件”；本 Skill 解决的是“如何把一个需求可靠地做成可检查、可追溯的跨软件成果”。例如，MCP 能创建一个圆柱，但不会仅凭这一操作保证它符合已确认尺寸、进入了正确版本、在 Blender 中保持形状，或具备独立回读和交付证据。两者是不同层次的能力，配合使用。

### 安装 Skill 后，会自动连上两个 MCP 吗？

不会。需要按上游说明分别安装和配置 MCP，并确认软件侧插件及连接实际可用。也可以使用已验证的原生脚本/API 路径。无论使用哪种接口，都要满足相同的工作包、独立回读、审核和交付要求；接口可用不等于工程验收通过。

### Codex 没有发现 Skill

检查 `SKILL.md` 是否直接位于安装目录，完整资源是否都已复制。新建任务并显式输入 `$cad-blender-engineering-orchestrator`；仍未发现时，重新打开客户端，检查实际使用的 Skill 安装根目录。

### 已安装软件，为什么还说找不到？

自定义安装目录不一定会被自动搜索到。提供可执行文件的完整路径，或设置 `CBE_FREECAD_CMD`、`CBE_BLENDER_EXE`。不要只提供安装文件夹，带空格的路径应使用引号。

### 设置了环境变量，Codex 为什么还是看不到？

当前 PowerShell 的变量不会回传给已经打开的 Codex。可以在对话中显式提供路径，或设置 Windows 用户环境变量后重新启动 Codex。

### 为什么不停下来直接做完？

关键需求确认后可以连续执行。但尺寸、接口、检修方向或保真等级等条件决定了几何和验收结果，先确认能避免把错误假设带进后续建模。

### 两个软件都生成了文件，为什么还是不能通过？

文件存在不代表内容正确。流程还检查是否能重新打开、对象是否完整、尺寸和形状是否一致，以及审核证据是否对应当前版本。报告会指出具体问题。

### 只想做个好看的展示模型，也需要这些检查吗？

可以在需求阶段明确概念展示用途，约定适合的保真级别和交付规模。受控对象仍需按约定检查；纯艺术创作建议使用 Blender 专项工作流。

### 能直接用于施工或制造吗？

当前主要用于概念和协调。水力、结构、应力、选型、当地规范和正式审批需要相应专业人员负责。跨软件一致性通过，并不自动证明设计合理或获得审批。

更多问题见[故障排查](docs/troubleshooting.md)和[工程边界说明](docs/security-and-engineering-boundaries.md)。

## 验证过什么？

发布前的小规模实机测试包括：

- FreeCAD 保存 FCStd、导出 STEP，并使用独立进程重新打开回读。
- Blender 使用接受的 Canonical Mesh 保存 `.blend`，独立回读并生成两张 1920×1080 PNG。
- 三个受控对象，共 268 个顶点、524 个三角面，跨软件比较差异为 0。
- CAD、Blender 和跨软件审核通过，正式交付报告校验为零错误、零警告。
- 检查 working 与正式交付隔离，以及越权写入、证据串台、拓扑伪造、错误版本链和交付篡改等情况。

测试圆柱从 CAD 解析曲面转换成网格时，有约 0.0373 mm 的 Y 向近似差，属于 CAD 三角化结果；Blender 与冻结网格保持一致。`exact_mesh` 不等于声称网格和 CAD 解析曲面在数学表达上完全相同。

这些验证证明指定环境和案例下的流程可用。其他机器、软件版本和复杂领域规则仍需按实际项目检查。

## 升级、反馈与许可

升级前查看 [VERSION](VERSION) 和 [CHANGELOG](CHANGELOG.md)，按[安装与升级说明](docs/installation.md)更新 Skill。安装目录和工程项目相互独立，升级 Skill 不应覆盖工程项目或历史 accepted 快照。

遇到问题可以到 [GitHub Issues](https://github.com/YDL1111/cad-blender-engineering-orchestrator/issues) 反馈。请提供 Skill 版本、Python/FreeCAD/Blender 版本、复现步骤和脱敏报错；不要上传令牌、个人路径或未获授权的工程资料。

本项目采用 [MIT License](LICENSE)，版权归 YDL1111 所有。FreeCAD、Blender 等外部软件遵循各自许可证，不包含在发布包中。
