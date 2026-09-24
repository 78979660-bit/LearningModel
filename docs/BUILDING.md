# 构建说明

## 环境

- Windows x64，PowerShell 7。
- CPython 3.14.5 x64（公开 v0.1.0 安装包的构建版本）。
- `requirements-build.lock.txt` 中固定的依赖。
- Inno Setup 7.1.0；安装器使用其 ChineseSimplified 语言文件。

在项目根目录创建 `.venv-build` 并安装锁定依赖，命令见 [README](../README.md#从源码运行)。`py -3.14` 会选择已安装的 Python 3.14；构建脚本进一步要求精确版本为 **3.14.5 x64**。不要复制其他机器的虚拟环境。

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

| 产物 | 用途 |
| --- | --- |
| `dist/LearningModel/` | 完整程序目录，用于本地运行和打包 |
| `release/artifacts/LearningModel-Setup-0.1.0-x64.exe` | 当前用户范围的 Windows 安装包 |
| `release/artifacts/SHA256SUMS.txt` | 本次生成的安装包校验值 |
| `release/artifacts/verification/` | 构建记录、依赖清单与打包检查材料 |

核对安装包哈希：

```powershell
Get-FileHash -LiteralPath .\release\artifacts\LearningModel-Setup-0.1.0-x64.exe -Algorithm SHA256
```

与同一次构建生成的 `SHA256SUMS.txt` 比较。核对官网下载的安装包时，使用同一 Release 中的校验文件。

脚本仍拒绝 `-PublicRelease`；这个开关不表示 GitHub Release 的公开状态。上述命令用于本地构建，生成的新文件需要单独测试和核对，不能直接视为与已发布安装包相同。

## 与公开 v0.1.0 安装包的关系

[公开预发布版本](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)的安装包从[源码提交 `8aeaf92`](https://github.com/78979660-bit/LearningModel/tree/8aeaf92bc786adc517b8d2569b291cdad79243c1)重新构建并完成所列验收。若要核对该安装包，请先切换到此提交，再参照其中的构建材料；发布附件另有对应源码 ZIP、第三方材料和 SHA-256 校验值。

`main` 是持续更新的开发主线。`v0.1.0` Git 标签仍指向早期提交 `121e68d`，因此复现公开安装包时请使用明确的提交号 `8aeaf92bc786adc517b8d2569b291cdad79243c1`，或发布附件中的 `LearningModel-0.1.0-source.zip`；不要使用 GitHub 自动生成的标签源码 ZIP 代替它。

构建记录、测试结果和已知限制见[该提交的验收说明](https://github.com/78979660-bit/LearningModel/blob/8aeaf92bc786adc517b8d2569b291cdad79243c1/docs/RELEASE_ACCEPTANCE.md)。这些记录不构成逐字节可重复构建证明。
