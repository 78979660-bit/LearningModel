# 构建说明

## 环境

- Windows x64，PowerShell 7。
- CPython 3.14.5 x64（现有候选包的构建版本）。
- `requirements-build.lock.txt` 中固定的依赖。
- Inno Setup 7.1.0；安装器使用其 ChineseSimplified 语言文件。

在项目根目录创建 `.venv-build` 并安装锁定依赖，命令见 README。不要复制其他机器的虚拟环境。

## 验证源码

```powershell
& .\.venv-build\Scripts\python.exe -m pytest -q
```

`tests/conftest.py` 使用临时用户目录并禁止自动探测已有个人资料。不要从开发者的数据库创建测试夹具。

## 构建程序与安装器

```powershell
pwsh -NoProfile -File tools/build_release.ps1
```

脚本执行测试、PyInstaller 构建、程序自检、许可收集、安装器生成和 SHA-256 计算。如果 Inno Setup 不在默认位置，可使用 `-InnoCompilerPath` 指定 `ISCC.exe` 的绝对路径。只构建程序目录可使用 `-SkipInstaller`。

产物位于 `dist/LearningModel` 和 `release/artifacts`。PyInstaller 程序目录需要整体保留。数据库、密钥及个人 JSON 不属于构建输入。

脚本保留了 `-PublicRelease` 的拒绝执行保护。开源许可已选定，第三方源码材料已收集，已完成部分 Windows 11 虚拟机验收，当前流程仍只生成预发布草稿，不能把这个开关改为无条件放行。普通构建参数可用于本地测试；正式分发应同时提供源码和许可附件。

## 本次候选包与源码

最终包从本仓库源码重新构建，包含卸载数据选项和中文时间校验。`source-inputs-0.1.0.json` 记录本次 PyInstaller Analysis 中的项目 Python 输入及哈希，`native-binary-inventory.json` 记录原生组件与最终安装包哈希。第三方依赖版本未变。

Release 说明链接到生成对应源码 ZIP 的确切提交。构建记录及验收范围见 RELEASE_ACCEPTANCE.md。没有把该清单宣称为逐字节可重复构建证明。
