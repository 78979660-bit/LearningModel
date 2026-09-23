# 第三方源码与许可定位

2026-09-23 更新：下列定位工作已落实为 35 个实际源码归档和 371 份许可/归属材料，见 [发布补充材料](RELEASE_SUPPLEMENT.md) 及 `third-party-source-manifest.json`。原 wheel 许可收集结果继续保留作为历史记录；新增 `THIRD_PARTY_LICENSES/upstream` 补齐 Qt/PySide 开源许可文本。

项目许可不覆盖下列上游作品。应按 `requirements-build.lock.txt` 和实际构建清单取得准确版本；链接是收集入口，不表示已将完整对应源码镜像到本仓库。

| 组件 | 版本 | 上游入口 |
| --- | --- | --- |
| PyMuPDF | 1.27.2.3 | https://pypi.org/project/PyMuPDF/1.27.2.3/#files · https://github.com/pymupdf/PyMuPDF |
| MuPDF | 以该 PyMuPDF 构建记录为准 | https://mupdf.com/releases/ · https://github.com/ArtifexSoftware/mupdf |
| PySide6 / Shiboken6 | 6.11.1 | https://download.qt.io/official_releases/QtForPython/ · https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| Qt | 以发行 wheel 的 Qt 版本为准 | https://download.qt.io/official_releases/qt/ |
| Python | 3.14.5 | https://www.python.org/downloads/source/ |
| PyInstaller | 6.22.3 | https://github.com/pyinstaller/pyinstaller |
| 其他 Python 包 | 见锁定文件 | 对应 PyPI 版本页面的 source distribution 和项目仓库 |

`THIRD_PARTY_LICENSES/LICENSE-MANIFEST.json` 是已有构建环境的收集结果。它不是“所有许可证都已齐全”的证明；尤其 PyMuPDF 的简短 COPYING 和 Qt 商业许可提示不应被误认为完整的开源分发材料。

AGPL 官方全文：https://www.gnu.org/licenses/agpl-3.0.html  
PyMuPDF 许可说明：https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
