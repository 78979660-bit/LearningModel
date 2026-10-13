# 构建说明

## 环境

- Windows x64，PowerShell 7。
- CPython 3.14.5 x64（1.0.0 发布构建使用此精确版本）。
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
| `release/artifacts/LearningModel-Setup-1.0.0-x64.exe` | 当前用户范围的 Windows 安装包 |
| `release/artifacts/SHA256SUMS.txt` | 本次生成的安装包校验值 |
| `release/artifacts/verification/` | 构建记录、依赖清单与打包检查材料 |

核对安装包哈希：

```powershell
Get-FileHash -LiteralPath .\release\artifacts\LearningModel-Setup-1.0.0-x64.exe -Algorithm SHA256
```

与同一次构建生成的 `SHA256SUMS.txt` 比较。核对官网下载的安装包时，使用同一 Release 中的校验文件。

常规构建仍生成待验收候选包。`-PublicRelease -UnsignedRelease` 现在只准备本地未签名正式版，不上传、不创建标签或 GitHub Release。必须使用同一候选包的完整验收记录，不能跳过测试或安装器，也不能混入签名参数。

```powershell
& tools/build_release.ps1 -PublicRelease -UnsignedRelease -Version 0.1.2 `
  -PythonPath .venv-build/Scripts/python.exe `
  -BuildDirectory output/qa-qml-unique `
  -AcceptanceRecord '验收记录的绝对路径/acceptance.json' `
  -ReleaseOutputDirectory '全新本地交付目录的绝对路径'
```

`tools/prepare_unsigned_release.py` 核对源码、构建输入、全部分发文件、程序和安装包哈希、回归 XML、原生补测及各项验收证据。证据缺失、尚未通过或属于另一安装包时拒绝准备；不会用旧记录替代新包验收。输出目录必须尚不存在且位于工作区。未签名状态必须明确记录，发布后下载回验仍须另行完成。

## 与公开 v0.1.1 安装包的关系

公开 [v0.1.1 预发布测试版](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.1)提供 Windows 安装包、对应源码 ZIP、第三方源码与许可、验证证据和 Windows 验收工具。使用 `git checkout v0.1.1` 或 `LearningModel-0.1.1-source.zip` 获取对应源码；`main` 是持续更新的开发主线。

运行时源码包含提交 `69c66fb` 中的版本同步和后续修正；发布标签另包含下载入口与发布文档更新。最终安装包中的 123 个项目 Python 输入与附件清单逐项核对一致。文档更新未修改已验收安装包，本轮结果见 [0.1.1 打包验收](RELEASE_ACCEPTANCE_0.1.1.md)。这些记录不构成逐字节可重复构建证明。

## 迁移分支的 QML QA 复现

阶段五提供 `tools/build_qml_qa.ps1`，选择已安装 `requirements-build.lock.txt` 的解释器；`-RequireReleaseRuntime` 要求 Windows x64 CPython 3.14.5。每次使用新的 `RunName`，所有输出位于 `output/<RunName>`，已有目录会被拒绝，不改常用 build/dist 或安装包。

```powershell
& tools/build_qml_qa.ps1 -PythonPath .venv-build/Scripts/python.exe -RunName qa-qml-unique -RequireReleaseRuntime
```

该脚本核对锁定依赖，正式运行环境还拒绝额外依赖。它使用原 spec 的显式资源清单，执行冻结程序隔离自检与发行目录检查。`metadata/environment.json` 保存 Python/Qt/依赖；`source-inputs.json` 保存受限源码、QML 及资源的逐文件 SHA-256；`dist-inputs.json` 保存冻结目录全部文件哈希；`run-record.json` 保存构建脚本、版本资源、spec、安装器配置、lock、verifier 及清单哈希和最终状态。构建结束重新检查源码与构建输入，发生变化会拒绝通过，必须用新 RunName 重建。哈希和环境记录用于追溯，不构成逐字节可重复构建证明。

当前 0.1.2 候选的安装快捷方式使用 `LearningModel.exe --qml` 选择完整 QML 窗口；直接运行无参数程序仍保留 Widgets 对照。候选包没有替代公开 v0.1.1；各历史阶段的验证范围见 [阶段五记录](QML_PHASE5_ACCEPTANCE.md)，本次结果须以 0.1.2 验收记录为准。

发布前收尾新增内部安装器准备工具，要求上一步是通过的正式运行环境构建。它在该运行目录的全新 `installer` 子目录输出候选包，不覆盖旧 dist 或 release/artifacts。安装前状态检查可使用 `-PreflightOnly`；此选项仅检查输入，不生成安装器。

```powershell
& tools/build_qml_installer_qa.ps1 -BuildDirectory output/qa-qml-unique -PreflightOnly
& tools/build_qml_installer_qa.ps1 -BuildDirectory output/qa-qml-unique
```

工具核对源文件、所有冻结文件、清单及安装器配置哈希，拒绝路径越界、符号链接和额外程序文件；编译后再次核对输入。Inno Setup 必须为 7.1.0，可通过 `-InnoCompilerPath` 指定编译器。记录和 SHA-256 位于 `installer`。生成安装器不代表已经执行安装、升级、卸载、重装或干净 Windows 验收；`lifecycleAcceptance` 初始为 pending，完成同一安装包验收后须关联具体证据。当前候选版本为 0.1.2，默认安装快捷方式进入 QML，使用独立文件名保留旧发布附件。

## 与公开 v0.1.0 安装包的关系

[公开预发布版本](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)的安装包从[源码提交 `8aeaf92`](https://github.com/78979660-bit/LearningModel/tree/8aeaf92bc786adc517b8d2569b291cdad79243c1)重新构建并完成所列验收。若要核对该安装包，请先切换到此提交，再参照其中的构建材料；发布附件另有对应源码 ZIP、第三方材料和 SHA-256 校验值。

`main` 是持续更新的开发主线。`v0.1.0` Git 标签仍指向早期提交 `121e68d`，因此复现公开安装包时请使用明确的提交号 `8aeaf92bc786adc517b8d2569b291cdad79243c1`，或发布附件中的 `LearningModel-0.1.0-source.zip`；不要使用 GitHub 自动生成的标签源码 ZIP 代替它。

构建记录、测试结果和已知限制见[该提交的验收说明](https://github.com/78979660-bit/LearningModel/blob/8aeaf92bc786adc517b8d2569b291cdad79243c1/docs/RELEASE_ACCEPTANCE.md)。这些记录不构成逐字节可重复构建证明。
