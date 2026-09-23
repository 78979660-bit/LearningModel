# 0.1.0 发布补充材料

日期：2026-09-23。本次收尾重建包含卸载选项和中文输入提示。最终安装包哈希见 RELEASE_ACCEPTANCE.md 和 Release 的 SHA256SUMS.txt。

## 已交付

| 附件 | 内容 |
| --- | --- |
| `LearningModel-0.1.0-third-party-sources.zip` | 35 个上游源码归档，归档共 370,667,752 字节，附版本/来源/哈希清单 |
| `LearningModel-0.1.0-third-party-notices.zip` | 从已验证源码提取的 371 份许可或归属文本及对应清单 |
| `LearningModel-0.1.0-Windows-QA.zip` | 最终候选安装包、兼容 Windows PowerShell 5.1 的验收脚本、人工检查表 |
| `SUPPLEMENT-SHA256SUMS.txt` | 上述三份 ZIP 的 SHA-256 |

这些材料附在仓库的 0.1.0 Release 草稿，草稿只对有权限的账号可见。正式发布时必须保留实际源码附件，不能用只有链接的清单代替。

## 来源覆盖

- PyMuPDF 1.27.2.3 及独立 MuPDF 1.27.2 完整源码发行包；后者包含 thirdparty 目录及构建脚本。
- Qt Base、SVG、ImageFormats 6.11.1，对应实际 Qt DLL 和插件；PySide/Shiboken 6.11.1 完整源码。
- CPython 3.14.5 及 PCbuild/get_externals.bat 指定的 bzip2、libffi、OpenSSL、mpdecimal、SQLite、xz、zlib-ng、zstd 来源。
- opengl32sw.dll 中识别的 Mesa 11.2.2 / LLVM 3.6.2 源码；Qt 构建参考为 https://wiki.qt.io/MesaLlvmpipe 。
- 锁定的 Python 运行依赖和构建工具源发行包。Pillow 内嵌图像库的版权声明保留在其原始 LICENSE 中。

24 个归档比对了 PyPI、Qt Metalink 或 MuPDF 官方 Release 提供的 SHA-256；其余从官方 HTTPS 源下载并记录本地哈希，未宣称额外的签名验证。归档未修改，源码下载和许可提取时未执行第三方构建代码。

Microsoft VC++ Runtime 保留专有再分发身份，不套用项目 AGPL，也不伪造开源源码。相关条款入口：https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution 。本清单不将上游 wheel/DLL 的构建过程宣称为逐字节可重复。

## 验收工具验证范围

- 已在系统 Windows PowerShell 5.1 实际执行 Preflight。
- 识别出当前开发工具和已有用户数据，`eligible=false`。
- 实际执行 Install 阶段的拒绝路径，确认在安装前停止，未创建验收安装状态。
- 源码下载/许可提取的自动测试覆盖坏哈希、路径越界、缓存复用和许可原文保存。
- 没有自动上传测试结果；交付 ZIP 排除了本机预检结果、用户名路径和日志。

Windows 11 虚拟机真实安装、主要界面和卸载重装检查已经执行；结果及未覆盖项目见 [验收说明](RELEASE_ACCEPTANCE.md)。旧开发主机 Preflight 拒绝结果只用于验证工具保护。验收包仍可用于后续独立机器复核。
