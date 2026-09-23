# 学习模型 LearningModel

面向个人学习的 Windows 桌面应用，使用 Python 与 PySide6 构建。包含学习计划、学习记录、知识图谱、学习助理和可恢复的学科管理。

本项目自身代码采用 **GNU AGPL v3.0（AGPL-3.0-only）**，详见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。第三方组件保留各自的许可。

## 当前发布状态

源码已按开源方向整理。0.1.0 安装包目前为 **Release 草稿**，尚无公开下载版本。已准备第三方源码、许可补充材料和 [干净 Windows 验收包](docs/CLEAN_WINDOWS_ACCEPTANCE.md)。已在 Windows 11 虚拟机完成主要流程验收，详情见 [验收结果与限制](docs/RELEASE_ACCEPTANCE.md)。候选包未签名，尚未完成 Windows 10、多档 DPI 和外部服务完整验收。

## 从源码启动

推荐 Windows x64。现有构建记录使用 CPython 3.14.5 x64；其他版本尚未验证。以下命令在 PowerShell 中运行：

```powershell
git clone https://github.com/78979660-bit/LearningModel.git
cd LearningModel
py -3.14 -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-build.lock.txt
& .\.venv-build\Scripts\python.exe run_study_app.pyw
```

首次启动使用空记录和干净默认模型。通过应用内学科管理引入学科。用户数据保存在 `%LOCALAPPDATA%\LearningModel`，不应提交到 Git。

OCR 需要另外配置 Tesseract；ChatGPT 桌面桥接需要对应桌面应用；AI 服务密钥由用户自行设置。源码不包含密钥或个人学习记录。

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
