# 学习模型 LearningModel

在日常学习中，大家是否感到资料不够，无所适从？或是没时间作详细的规划，只能看着时间悄悄溜走？

我们郑重宣布：一款致力于解决日常学习规划的应用来啦！

本应用使用 Python 与 PySide6 构建，包含学习计划、学习记录、知识图谱、学习助理、可恢复的学科管理等多项有趣且实用的功能。

本项目自身代码采用 **GNU AGPL v3.0（AGPL-3.0-only）**，详见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。第三方组件保留各自的许可。

## 主要功能

### 学习助理

配置自己的 AI 服务后，可以使用学习助理辅助规划学科内容、生成学习计划建议、识别作业中的做题信息。涉及写入学习记录的操作需要先核对并确认；外部 AI 服务的完整流程仍在测试中。

后续还会有更多更好玩的功能！

### 学习计划

应用结合已有学习记录、知识点掌握度和遗忘风险安排复习任务，并可按当天可用时间预览和保存计划。

### 学习记录

在“新增记录”中填写学习内容、做题情况和错因，也可以选择本机材料作为附件。系统会尝试识别题目与作答结果；确认并保存记录后，相关证据会用于更新掌握度。

### 知识图谱

图谱展示章节及已确认的先修关系。点击章节可查看学习状态、覆盖情况和掌握度；缺少评估证据时会显示“待评估”，非常直观！

## 0.1.2 未签名正式版

采用 QML 界面，修复单学科计划混入其他学科任务、空分数预览显示 undefined，以及扫描 PDF 的 OCR 参数和无文本页识别问题。保留现有用户数据目录、升级 AppId 和 schema v5。2026-10-13 完成 11 项发布前核验；具体环境、证据范围及限制见 [正式版验收](docs/UNSIGNED_RELEASE_ACCEPTANCE_0.1.2.md)。

## 0.1.1 更新与打包验收

这一版主要整理源码结构、修复配置和数据处理问题，让后续维护与功能迭代更容易。

### 已完成的源码整改

- **学习计划页拆分**：预算安排、详细计划和复习模式弹窗分别管理自己的控件与状态，减少大函数和共享闭包；保留页面回调和后台任务约定。
- **数据层职责拆分**：分离数据库运行时、学习计划、OJ、知识预警、设置与审计存储。仓储使用公开事务接口，明确连接关闭、回滚和只读快照的边界。
- **AI 设置真正生效**：错因归类、知识点映射和难度校准开关同时控制提示词与结果处理；调整请求参数前复制配置，避免影响后续请求。
- **异常更容易追查**：修正审计失败被上层忽略的问题，保留失败状态、日志和降级原因。
- **OCR 检测统一**：设置页与实际处理流程使用一致的运行时检测，减少可用性判断不一致。
- **代码清理**：移除无调用的旧实现，合并重复判断，整理界面创建入口和部分中文文本表达，补充相关行为回归测试。
- **文档更新**：补齐公开下载入口、版本对应关系、构建与哈希校验步骤，以及第三方发布材料说明。

源码整改提交为 `18b7d61`。该轮全量回归通过 **1121 项测试、279 项子测试**，保留 5 条既有弃用警告；详细范围见 [源码质量整改验证](docs/CODE_QUALITY_VALIDATION.md)。这份结果针对该轮源码，不代表后续所有改动或安装包均已验收。

### 本轮纳入构建的后续修正

以下修改已纳入 0.1.1 构建，并通过本轮完整回归；对应源码随 `v0.1.1` 标签和发布附件提供：

- 学习覆盖统计补充“已掌握”“已学待复习”状态，并避免一条仅指向章节的记录将该章节下所有知识点都算作已覆盖。
- 学科目录尚未采用第一版结构时，保留已有模型中的章节数据。
- 综合计划中的“上述知识点”等描述使用实际优先知识点计算参考难度；已有做题证据时，识别需要刷新的旧计划提示。

**本轮结果（2026-10-03）：**版本元数据和 Windows 版本资源已同步为 `0.1.1`。全量回归通过 **1127 项测试、279 项子测试**，保留 5 条既有弃用警告；冻结程序自检、包内资源与版本检查通过，已生成本地 Windows x64 安装包。正常登录验收虚拟机后，0.1.0 升级、卸载保留数据、重装和独立空配置自检均通过；五个主要页面、菜单展开/收起及窗口最大化/还原实测通过。本地打包与安装生命周期通过，完整发布签核仍有未测项；当前数据保留实测不含非空学习记录。详见 [0.1.1 验收记录](docs/RELEASE_ACCEPTANCE_0.1.1.md)。

**发布状态：**[v0.1.1 预发布测试版](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.1)已开放下载，包含本节列出的源码更新和配套源码、许可及验收材料。

## 下载与体验

当前正式版为 **v0.1.2（未签名）**。

[下载 Windows x64 安装包](https://github.com/78979660-bit/LearningModel/releases/download/v0.1.2/LearningModel-Setup-0.1.2-x64.exe) · [全部发布文件](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.2) · [SHA256SUMS.txt](https://github.com/78979660-bit/LearningModel/releases/download/v0.1.2/SHA256SUMS.txt)

适用于 Windows 10 1809 及以上的 x64 系统；已在 Windows 10 和 Windows 11 测试环境验收。安装包未进行代码签名，Windows 或浏览器可能显示未知发布者或 SmartScreen 提示。请从本项目 Release 下载并核对校验和；校验和不代替发布者身份验证。

覆盖安装保留现有用户数据，升级前建议备份。安装与卸载方式见 [安装说明](release/安装与升级说明.md)。OCR 另需 Tesseract，AI 服务需自行配置；外部 AI 全部服务商和网络环境未穷举。

## 从源码运行

如果你想参与开发，或看看学习模型是如何工作的，可以从源码启动。下方命令使用[v0.1.2 对应的源码标签](https://github.com/78979660-bit/LearningModel/tree/v0.1.2)，版本关系见 [构建说明](docs/BUILDING.md#与公开-v011-安装包的关系)。

当前验证环境为 Windows x64、CPython 3.14.5。在 PowerShell 中执行：

```powershell
git clone https://github.com/78979660-bit/LearningModel.git
cd LearningModel
git checkout v0.1.2
py -3.14 -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-build.lock.txt
& .\.venv-build\Scripts\python.exe run_study_app.pyw
```

首次启动使用空记录和干净默认模型。通过应用内学科管理引入学科。用户数据保存在 `%LOCALAPPDATA%\LearningModel`。

OCR 需要另外配置 Tesseract；AI 服务密钥由用户自行设置。不使用这些功能时，无需提前配置。本迁移分支已移除 ChatGPT 桌面 PDF 生成，本地练习卷与 PDF 导出保留。

## 开发与构建

本分支正在分阶段迁移和纸学习室到 **PySide6＋Qt Quick/QML**。审查、顺序和安全边界见 [迁移方案](docs/QML_MIGRATION_PLAN.md)，验证见 [阶段一](docs/QML_PHASE1_ACCEPTANCE.md)、[阶段二](docs/QML_PHASE2_ACCEPTANCE.md)、[阶段三](docs/QML_PHASE3_ACCEPTANCE.md)、[阶段四](docs/QML_PHASE4_ACCEPTANCE.md)和 [阶段五](docs/QML_PHASE5_ACCEPTANCE.md)。完整 QML 窗口的五条主路由均接入真实服务，覆盖计划、记录、助理、图谱、设置，以及学科引入、可恢复移除和恢复。密钥由 Python 管理的 Windows 系统对话框收集，页面只显示凭据状态；凭据与服务商及接口身份绑定。所有 I/O 和提交通过后台 Python 适配层调用既有服务。首页已接入真实每日总结、显式云端增强、模式开关、显式收起到图标浮窗和原生托盘；浮窗双击恢复，主窗口关闭直接退出；唤醒在 GUI 线程执行，明确退出后先排空任务再释放单实例保护。ChatGPT 桌面 PDF 已移除，本地练习 PDF 和手动复制上下文保留。旧 Widgets 入口继续作对照；常规入口已切换至 QML；本版发布验收范围见上方正式版验收，外部 AI 服务仍有未测环境。

2026-10-06 已对照最终和纸概念包重构五页 QML 的主题、导航和纸面布局。预算、进度、图谱及所有确认流程仍接真实服务；和纸、樱花、竹叶为独立 SVG。启动参数为 `--qml`，最新截图、内部包和验证记录见 [视觉重构说明](docs/QML_VISUAL_REBUILD.md)。

2026-10-09 继续优化 QML 页面切换与菜单动画：计划子标签保留草稿和控件，隐藏页面减少重复排版，纸面改用场景图形绘制，菜单宽度与文字动画统一。首次进入等待初始数据及布局帧完成后再呈现，避免异步创建期间暴露窄卡片、零高度和位置回跳；首次失败会显示错误并允许重试，回访仍直接显示保留页面。验证范围、原始日志和限制见 [本轮性能记录](docs/PERFORMANCE_AUDIT_20261009.md)。

QML 应用现在在启动准备界面后预加载五个主页面、计划页的四个子标签，以及图谱和设置的子页，完成数据读取与布局后回到首页开放操作。切换复用已有控件；文件或学习数据变化时仍会刷新。启动等待与内存占用会增加，具体验证见同一性能记录。

```powershell
# 使用已经安装 requirements-build.lock.txt 的项目环境
& .\.venv-build\Scripts\python.exe -m study_app.quick
# 自检自动使用临时数据，报告写到指定路径
& .\.venv-build\Scripts\python.exe -m study_app.quick --self-test output\qml-self-test.json
# 原 Widgets 入口继续保留；冻结程序的 QML 入口使用 --qml
& .\.venv-build\Scripts\python.exe run_study_app.pyw
```

阶段五已在全新的 Python 3.14.5 x64 环境按 `requirements-build.lock.txt` 安装全部锁定依赖，复现独立 QA 冻结目录；构建、环境、源码输入哈希和验收日志分别保存。先前 `.venv-qml`（Python 3.12.14）验证记录仍保留。[发布前收尾](docs/QML_PRERELEASE_CLOSEOUT.md)进一步提供防输入漂移检查、完整冻结文件哈希与独立内部候选安装器。安装器尚未完成本次安装生命周期验收或发布，不能替代公开 v0.1.1 的同名附件。

```powershell
# 选择已经装好锁定依赖的正式解释器；每次使用新的 RunName
& tools/build_qml_qa.ps1 -PythonPath .venv-build/Scripts/python.exe -RunName qa-qml-check -RequireReleaseRuntime
# 只核对环境与输入，不构建或创建目录
& tools/build_qml_qa.ps1 -PythonPath .venv-build/Scripts/python.exe -RunName qa-qml-preflight -RequireReleaseRuntime -PreflightOnly
```

运行测试：

```powershell
& .\.venv-build\Scripts\python.exe -m pytest -q
```

生成 Windows 安装包需要 PowerShell 7 和 Inno Setup 7.1.0。完整命令、构建参数和校验步骤见 [构建说明](docs/BUILDING.md)。

## 目录

| 目录或文件 | 内容 |
| --- | --- |
| `study_app/` | 界面、算法服务、数据库和干净默认资源 |
| `learning_*.py` | 学习模型算法与兼容入口 |
| `tests/` | 自动化测试 |
| `packaging/` | PyInstaller 和 Inno Setup 配置 |
| `tools/` | 构建、验证和迁移工具 |
| `THIRD_PARTY_LICENSES/` | 构建环境收集的第三方许可材料 |
| `docs/OPEN_SOURCE.md` | 许可选择、公开预发布范围与对应源码说明 |

欢迎通过 [Issues](https://github.com/78979660-bit/LearningModel/issues) 反馈问题或提出建议。描述问题时，请附上应用版本、Windows 版本和复现步骤；提交日志前请移除个人路径、学习内容和服务凭据。
