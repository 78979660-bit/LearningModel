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

脚本保留了 `-PublicRelease` 的拒绝执行保护。开源许可已选定，第三方源码材料已收集，但干净 Windows 验收尚未实际执行，不能把这个开关改为无条件放行。普通构建参数可用于本地测试；正式分发应同时提供源码和许可附件。

## 与已有 0.1.0 候选包的关系

`source-inputs-0.1.0.json` 列出从保留的 PyInstaller Analysis TOC 提取的 117 个项目 Python 输入及哈希。这些文件与保留构建工作副本逐字节一致。应用资源和打包配置也取自该副本。

开源整理修改了文档、许可材料、构建脚本中的发布状态提示，以及两个旧测试使用的夹具：改为人工构造的测试学科和记录；删除了未被打包的一次性个人资料导入脚本。应用运行源码未修改，没有使用现有个人数据库或根目录学习 JSON。

该清单证明与保留构建副本的对应关系，不是对历史构建全过程的可重复构建证明。正式公开二进制前，建议从已发布提交重新构建并记录提交号、产物哈希和验收结果。
