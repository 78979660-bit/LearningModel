# 学习模型 LearningModel

在日常学习中，大家是否感到资料不够，无所适从？或是没时间作详细的规划，只能看着时间悄悄溜走？

我们郑重宣布：一款致力于解决日常学习规划的应用来啦！

学习模型使用 Python 与 PySide6 构建，把学习计划、学习记录、知识图谱、学习助理和学科管理放到一起。希望它能陪你理清每天要做的事情，也把一点点进步留下来。

## 都能做些什么？

### 学习助理

面对一门新学科，不知道从哪里开始？配置自己的 AI 服务后，就可以和学习助理聊聊，梳理学科内容、讨论学习计划，也可以让它辅助识别作业中的做题信息。

识别结果和涉及学习记录的写入需要先由你核对、确认，再保存。AI 也会看错，所以自己的学习情况，还是由自己把关！不同服务商和网络环境的表现可能不同，外部 AI 的完整流程仍有未测范围。

### 学习计划

今天能学多久，哪些内容该复习了？告诉应用你当天可用的时间，它会结合已有学习记录、知识点掌握度和遗忘风险，帮你安排任务。先看看计划合不合适，再保存下来，按自己的节奏慢慢推进。

想专心学一门课，也可以只选择这个学科，不用在其他学科的任务里来回寻找。

### 学习记录

学了什么、做了多少题、哪里容易出错，都可以记在这里。图片和 PDF 也能作为附件加入记录，方便回头查看。配置 Tesseract 后，还可以尝试识别图片和扫描 PDF 中的文字。

暂时没有分数也没关系，留空就好；应用会把“未填写”和 0 分分开显示。核对并保存后的记录，会参与后续的掌握度评估和复习安排。

### 知识图谱

章节之间有什么联系？哪些内容已经学过，哪些还需要多花一点时间？打开图谱，就能查看章节和已确认的先修关系，也能点击章节看看学习状态、覆盖情况和掌握度，非常直观！

还没有足够评估证据的内容会显示“待评估”，可以在积累学习记录后再来看。

### 学科管理

从应用内引入学科，就可以开始记录自己的学习。暂时不学的学科可以移除，之后需要时再恢复；已有的学习记录也能随它一起保留下来。

后续还会有更多更好玩的功能！

## 1.0.0，欢迎体验！

这一版正式采用 QML 界面，整理了学科列表刷新、页面切换和菜单动画，也修复了单学科计划混入其他任务、空分数预览显示 undefined，以及扫描 PDF 的 OCR 问题。

1.0.0 在已发布的 0.1.2 基础上统一版本号，并调整这份说明。已有的用户数据目录、升级标识和数据库结构继续沿用，升级前仍建议先备份自己的学习资料。

具体的构建、补测范围和历史验收关系见 [1.0.0 发布说明](https://github.com/78979660-bit/LearningModel/blob/v1.0.0/docs/RELEASE_ACCEPTANCE_1.0.0.md)；以往版本的更新见 [更新记录](https://github.com/78979660-bit/LearningModel/blob/v1.0.0/release/更新记录.md)。

## 下载与安装

👉 [下载 Windows x64 安装包](https://github.com/78979660-bit/LearningModel/releases/download/v1.0.0/LearningModel-Setup-1.0.0-x64.exe)

[查看全部发布文件](https://github.com/78979660-bit/LearningModel/releases/tag/v1.0.0) · [下载同版校验清单](https://github.com/78979660-bit/LearningModel/releases/download/v1.0.0/SHA256SUMS.txt)

适用于 Windows 10 1809 及以上的 x64 系统。直接运行安装器即可，不需要另行安装 Python、Qt 或开发工具。第一次打开时会从干净的数据开始，通过学科管理引入想学的内容，就能逐步建立自己的学习记录啦！

**这一版仍未进行代码签名。** Windows 或浏览器可能提示未知发布者或 SmartScreen。请从本项目 Release 下载，并用同版 `SHA256SUMS.txt` 核对文件；校验和只能验证文件一致性，不能代替发布者身份验证。

升级安装会沿用现有用户数据，卸载默认保留学习资料。备份、升级及卸载的具体步骤见 [安装与升级说明](https://github.com/78979660-bit/LearningModel/blob/v1.0.0/release/安装与升级说明.md)。

## 开始之前的小提示

- 核心学习功能可以离线使用。AI 功能需要自行配置服务和密钥，安装包不包含预设密钥。
- OCR 需要另外配置 Tesseract；没有 OCR 引擎时，仍可保存附件并使用其他本地功能。
- 新学科还没有学习证据时，知识点不会自动进入计划候选。补充能关联知识点的学习记录后，再试着生成计划。
- 用户数据默认保存在 `%LOCALAPPDATA%\LearningModel`。升级或卸载前，记得备份重要资料。
- 本地练习卷和 PDF 导出继续保留；本版已移除 ChatGPT 桌面 PDF 桥接。

## 想看看它是怎么做的？

欢迎从源码运行，也欢迎一起参与开发！下方命令使用与 1.0.0 发布包对应的源码标签，验证环境为 Windows x64、CPython 3.14.5。

在 PowerShell 中执行：

```powershell
git clone https://github.com/78979660-bit/LearningModel.git
cd LearningModel
git checkout v1.0.0
py -3.14 -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-build.lock.txt
& .\.venv-build\Scripts\python.exe run_study_app.pyw
```

运行测试：

```powershell
& .\.venv-build\Scripts\python.exe -m pytest -q
```

构建 Windows 安装包需要 PowerShell 7 和 Inno Setup 7.1.0。构建参数、资源检查和校验步骤见 [构建说明](https://github.com/78979660-bit/LearningModel/blob/v1.0.0/docs/BUILDING.md)。界面迁移的过程和阶段记录也保留在 [迁移方案](https://github.com/78979660-bit/LearningModel/blob/v1.0.0/docs/QML_MIGRATION_PLAN.md) 中，感兴趣的话可以继续看看。

## 项目里有什么？

| 目录或文件 | 内容 |
| --- | --- |
| `study_app/` | 界面、算法服务、数据库和干净默认资源 |
| `learning_*.py` | 学习模型算法与兼容入口 |
| `tests/` | 自动化测试 |
| `packaging/` | 程序与安装器打包配置 |
| `tools/` | 构建、验证和迁移工具 |
| `THIRD_PARTY_LICENSES/` | 第三方许可材料 |
| `docs/` | 构建说明、设计记录和验收范围 |

本项目自身代码采用 **GNU AGPL v3.0（AGPL-3.0-only）**，详见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。第三方组件保留各自的许可；对应源码和第三方材料随同版 Release 提供。

欢迎通过 [Issues](https://github.com/78979660-bit/LearningModel/issues) 反馈问题、提出建议，或者聊聊你希望加入的功能。遇到问题时，请附上应用版本、Windows 版本和复现步骤；分享日志前，记得移除个人路径、学习内容和服务凭据。

欢迎下载体验，也祝大家学习顺利！
