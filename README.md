# 学习模型 LearningModel

在日常学习中，大家是否感到资料不够，无所适从？或是没时间作详细的规划，只能看着时间悄悄溜走？

我们郑重宣布：一款致力于解决日常学习规划的应用来啦！

本应用使用 Python 与 PySide6 构建，包含学习计划、学习记录、知识图谱、学习助理、可恢复的学科管理等多项有趣且实用的功能。

本项目自身代码采用 **GNU AGPL v3.0（AGPL-3.0-only）**，详见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。第三方组件保留各自的许可。

## 主要功能

这款应用包含了很多很有意思的功能，包括但不限于：

一、学习助理

这是本应用的核心功能！通过接入您自己的API，就可以使用LLM辅助学习啦！学习助理可以帮助您规划学科的具体学习内容，编排每日的学习计划，检查您的作业并更新掌握度，之后还会有更多更好玩的功能！

二、学习计划

通过分析您在本学科内不同知识模块的掌握情况和每个模块的复习次数和频率，学习计划将会为您提供个性化的定制学习计划，帮助您最大限度的复习巩固已学内容。

三、学习记录

您可以将学习资料，作业情况，教材PDF上传给学习助理，助理将会自动帮您整理归类进用户数据库，并更新您的掌握度情况，从而更直观地反应您的学习情况！

四、知识图谱

应用内有专门的学习图谱界面，您不仅可以直观地看见每个章节的先后学习关系，还可以通过点击图框，看见每个章节的掌握度情况！

## 下载与体验

学习模型的第一个公开测试版 **v0.1.0** 已发布，欢迎下载体验！

👉 [下载 Windows x64 安装包](https://github.com/78979660-bit/LearningModel/releases/download/v0.1.0/LearningModel-Setup-0.1.0-x64.exe)

安装后，可在应用内引入学科，开始安排学习计划。

这是一个仍在成长中的项目。目前已在 Windows 11 虚拟机中验证主要使用流程，安装包尚未进行代码签名。

[查看更新内容、验收范围与全部下载文件](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)

## 从源码运行

如果你想参与开发，或看看学习模型是如何工作的，可以从源码启动。

当前验证环境为 Windows x64、CPython 3.14.5。在 PowerShell 中执行：

```powershell
git clone https://github.com/78979660-bit/LearningModel.git
cd LearningModel
py -3.14 -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-build.lock.txt
& .\.venv-build\Scripts\python.exe run_study_app.pyw
```

首次启动使用空记录和干净默认模型。通过应用内学科管理引入学科。用户数据保存在 `%LOCALAPPDATA%\LearningModel`。

OCR 需要另外配置 Tesseract；AI 服务密钥由用户自行设置。源码不包含密钥或个人学习记录。

## 测试与构建

```powershell
& .\.venv-build\Scripts\python.exe -m pytest -q
pwsh -NoProfile -File tools/build_release.ps1
```

安装器构建还需要 Inno Setup 7.1.0。详细环境、参数和现有限制见 [构建说明](docs/BUILDING.md)。测试通过临时目录隔离用户资料。

## 目录

| 目录或文件 | 内容 |
| --- | --- |
| `study_app/` | 界面、算法服务、数据库和干净默认资源 |
| `learning_*.py` | 学习模型算法与兼容入口 |
| `tests/` | 自动化测试 |
| `packaging/` | PyInstaller 和 Inno Setup 配置 |
| `tools/` | 构建、验证和迁移工具 |
| `THIRD_PARTY_LICENSES/` | 构建环境收集的第三方许可材料 |
| `docs/OPEN_SOURCE.md` | 许可选择、发布范围与二进制发布待办 |

问题反馈请使用仓库 Issues。提交日志前请移除个人路径、学习内容和服务凭据。
