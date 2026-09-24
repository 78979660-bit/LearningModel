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

应用结合已有学习记录、知识点掌握度和遗忘风险安排复习任务，并可按当天可用时间预览和保存计划。，提供个性化的规划服务！

### 学习记录

在“新增记录”中填写学习内容、做题情况和错因，也可以选择本机材料作为附件。系统会尝试识别题目与作答结果；确认并保存记录后，相关证据会用于更新掌握度。，反映您的学习成果！

### 知识图谱

图谱展示章节及已确认的先修关系。点击章节可查看学习状态、覆盖情况和掌握度；缺少评估证据时会显示“待评估”，非常直观！

## 下载与体验

学习模型的第一个公开测试版 **v0.1.0** 已发布，欢迎下载体验！

👉 [下载 Windows x64 安装包](https://github.com/78979660-bit/LearningModel/releases/download/v0.1.0/LearningModel-Setup-0.1.0-x64.exe)

安装后，可在应用内引入学科并记录实际学习情况。新学科尚无学习覆盖时，知识点不会自动进入计划候选；补充能关联知识点的学习记录后，再尝试生成计划建议。

这是一个仍在成长中的项目。目前已在 Windows 11 虚拟机中验证主要使用流程，安装包尚未进行代码签名。

[查看更新内容、验收范围与全部下载文件](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)

## 从源码运行

如果你想参与开发，或看看学习模型是如何工作的，可以从源码启动。下方命令使用[公开安装包对应的源码提交](https://github.com/78979660-bit/LearningModel/tree/8aeaf92bc786adc517b8d2569b291cdad79243c1)，版本关系见 [构建说明](docs/BUILDING.md#与公开-v010-安装包的关系)。

当前验证环境为 Windows x64、CPython 3.14.5。在 PowerShell 中执行：

```powershell
git clone https://github.com/78979660-bit/LearningModel.git
cd LearningModel
git checkout 8aeaf92bc786adc517b8d2569b291cdad79243c1
py -3.14 -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-build.lock.txt
& .\.venv-build\Scripts\python.exe run_study_app.pyw
```

首次启动使用空记录和干净默认模型。通过应用内学科管理引入学科。用户数据保存在 `%LOCALAPPDATA%\LearningModel`。

OCR 需要另外配置 Tesseract；ChatGPT 桌面桥接需要对应桌面应用；AI 服务密钥由用户自行设置。不使用这些功能时，无需提前配置。

## 开发与构建

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
