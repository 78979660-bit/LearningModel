# GitHub 警示维护记录

检查日期：2026-10-02。基线：`main` 的 `ea90a9c868dac8de5e106c4aeb727bcc77d9e145`。

## 警示状态与来源

| 项目 | 核实结果 |
| --- | --- |
| [预算计划科目生命周期](https://github.com/78979660-bit/LearningModel/pull/2#discussion_r4094609117) | 基线仍可保存已归档或未登记科目的 selected/completed 条目并归档旧计划；本次修复。 |
| [SQLite 只读探测清理](https://github.com/78979660-bit/LearningModel/pull/2#discussion_r4094609207) | 基线在 PRAGMA 或结构查询失败时未显式关闭连接；本次修复。 |
| [AI 子功能保存](https://github.com/78979660-bit/LearningModel/pull/2#discussion_r4094609274) | 两个解析父开关均关闭后仍保留子功能勾选，保存及重新启用时会恢复；本次修复。 |
| [许可证清单路径](https://github.com/78979660-bit/LearningModel/pull/1#discussion_r4092324233) | 基线在读取归档前未校验清单文件名；本次修复。 |
| [Release 草稿状态](https://github.com/78979660-bit/LearningModel/pull/1#discussion_r4092324275) | 历史意见已过时：v0.1.0 当前是公开预发布，`draft=false`。审查线程本身仍未标记解决。 |
| [Dependabot](https://github.com/78979660-bit/LearningModel/security/dependabot) | 警示 API 返回 403，明确说明仓库已禁用 Dependabot alerts；security updates 也为 disabled。无法据此查询当前或历史警示，更不能据此认定依赖安全。 |
| [代码扫描](https://github.com/78979660-bit/LearningModel/security/code-scanning) | API 返回 404 `no analysis found`，没有可读分析结果。 |
| [泄密扫描](https://github.com/78979660-bit/LearningModel/security/secret-scanning) | 已启用，警示 API 返回 0 项；push protection 已启用。未读取任何秘密值。 |
| [Actions](https://github.com/78979660-bit/LearningModel/actions) | 检查时历史仅 2 次 Copilot code review，均成功；没有测试 CI 工作流，没有失败运行。审查成功不代表测试或安全检查通过。 |

上述四条源码意见已由本地回归测试复现；它们是审查发现，不是 GitHub CodeQL 或 Dependabot 漏洞警示。未修改旧 PR 的线程状态。

## 依赖审计

使用 `pip-audit 2.10.1` 的 PyPI advisory 服务检查锁文件中 21 个固定版本，未跳过任何包。`--no-deps --disable-pip` 直接审计清单中的版本，不解析额外依赖，也不审计 Qt、MuPDF 等原生组件的独立公告。

基线审计产生 41 条记录，按 advisory ID 去重为 21 项，也对应 21 个独立 CVE：Pillow 12.1.0 为 19 项，pip 26.1.1 为 2 项。别名及重复数据使原始计数较大，不能将 41 条解释为 41 个独立漏洞。Pillow 是运行时依赖，风险涉及恶意图片、字体、PDF 的内存越界或资源耗尽等；pip 位于锁文件的 build/verification-only 部分，其风险涉及包安装路径处理，未认定它随桌面安装包交付。版本匹配结果不等于每项都能通过本应用实际调用路径利用，此次未逐项进行利用验证。

- 构建锁文件升级为 `Pillow==12.3.0`、`pip==26.2.1`；源码运行依赖最低版本改为 `Pillow>=12.3.0`。
- 修复后，同一审计命令对 21 个版本返回 0 个已知漏洞。此结果限于服务当时提供的公告和清单范围。
- 参考：[Pillow 12.3.0 安全修复说明](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html)、[pip 官方修复记录](https://pip.pypa.io/en/stable/news/)、[pip 安装路径公告](https://github.com/pypa/pip/security/advisories/GHSA-wf93-45jw-7689)、[pip 包 URL 公告](https://github.com/pypa/pip/security/advisories/GHSA-qwm4-qh6w-59xr)。

复核命令：

```powershell
python -m pip_audit -r requirements-build.lock.txt --no-deps --disable-pip --format json
```

## 修复范围

- 预算计划先开启 `BEGIN IMMEDIATE`，在同一写事务中校验 selected/completed 条目的科目，再归档旧计划；校验失败保留旧计划。excluded 条目可继续保存，供展示排除原因。
- SQLite 只读连接在探测异常后显式关闭，并保留原始异常原因。
- 两个解析父开关均关闭时取消三个子功能勾选，保存时过滤禁用项；仍有一个父开关开启时保留用户选择。
- 清单归档只接受非空单一文件名，拒绝绝对路径、目录分隔符、冒号及点目录，并验证解析后的父目录，随后才读取和校验归档哈希。

## 验证与边界

新增 20 项回归测试覆盖科目生命周期、事务保留、异常清理、父子开关保存及归档路径。相同测试在基线源码上为 17 失败、3 通过，修复后为 20 通过。

本轮使用 Windows x64 CPython 3.12.14；完整安装更新后的构建锁文件。发布版本曾使用 CPython 3.14.5，此次未重建安装包，也未验证安装程序、真实外部 AI 或完整 OCR 链路。

全量验证命令与结果：

```text
python -B -m pytest -q -p no:cacheprovider --junitxml full-test-results.xml
1141 passed, 3 warnings, 279 subtests passed in 364.46s
```

JUnit 为 1420 项，0 失败、0 错误、0 跳过。297 个 Python 文件语法检查通过；Git 使用 `cr-at-eol` 识别仓库保留的 CRLF 后，差异空白检查通过。3 个警告均为现有 ChatGPT bridge 中 `datetime.utcnow()` 的弃用警告。

## 追加修复：许可证输出路径边界

2026-10-02 在同一维护分支的 `ef5878a4160eda054adbbb2b0b608d5da80c12e0` 上继续核验。独立复审发现，前一轮只验证归档文件名不足以保护输出路径：清单中的 `...zip` 是单一文件名，但去掉扩展名后变成 `..`；其中的 `LICENSE` 会写入输出目录的父目录，并覆盖已有文件。

本次追加修复：

- 分别验证清单文件名和去掉扩展名后的输出前缀；在打开归档之前拒绝空值、点目录和危险前缀。
- 归档名、输出前缀及成员路径的每一级均使用跨平台名称校验，拒绝 Windows 设备名（含带扩展名及上标数字的 COM/LPT 名）、尾随点/空格、控制字符、路径分隔符、ADS 冒号及其他 Windows 非法字符。仍支持普通 ZIP/tar、`./` 前缀及重复 `/`；tar 的链接条目不解包。
- 在创建输出目录前检查已有父目录；每次写入都重新检查目录与目标文件，并要求解析后的目标位于确定的输出根目录中。拒绝符号链接、Windows reparse point（包括 junction）、非普通目标文件及多链接文件。
- 许可证文本与 `NOTICE-MANIFEST.json` 共用同目录临时文件加原子替换流程，不直接截断已有文件；回归测试逐项确认目录外已有内容保持不变。

新增 `tests/test_source_notices_output_paths.py` 共 123 个参数化用例。在原维护提交的提取器上为 **102 失败、20 通过、1 跳过**；修复后为 **122 通过、1 跳过**。连同原有许可证测试及四类维护回归测试，专项验证为 **146 通过、1 跳过**。跳过项需要原生 Windows junction，云端 Linux 不能替代此项；跨平台 reparse 属性分支模拟测试已通过。现有来源清单中的归档名与 371 条已记录许可证输出路径也全部通过新名称规则。

本轮环境为 **Linux x86-64 云电脑、CPython 3.12.14**，独立虚拟环境完整安装当前构建锁文件，`pip check` 通过。没有使用离线桌面的未推送补丁，也未把历史 Windows 测试结果当作本次追加修改的验证结果。

```text
QT_QPA_PLATFORM=offscreen python -B -m pytest -q -p no:cacheprovider --junitxml=full.xml
修复后：1255 passed, 5 failed, 4 skipped, 3 warnings, 279 subtests passed
原维护提交：1133 passed, 5 failed, 3 skipped, 3 warnings, 279 subtests passed
```

两次全量运行使用同一云端环境和锁定依赖，5 个失败的测试 ID 与失败消息完全一致，均不涉及许可证提取器：

- `AIProviderMigrationTests.test_legacy_explicit_feature_consent_is_preserved`：DPAPI 仅支持 Windows。
- `ReleaseSingleInstanceTests.test_existing_mutex_closes_duplicate_handle_and_reports_secondary` 和 `test_mutex_uses_fixed_learning_model_identity_and_releases_handle`：Linux 的 `ctypes` 不提供测试所模拟的 `WinDLL` 属性。
- `LocalPracticePDFRenderingTests.test_long_statement_flows_across_pages_and_keeps_footer_page_numbers` 和 `test_question_and_answer_pdfs_are_separate_structurally_valid_and_deterministic`：此 Linux 镜像自动选择的 Noto CJK TTC 使用 ReportLab 不支持的 PostScript 轮廓。

原有 3 个跳过项为原生 Windows mutex/DPAPI 验证，追加 1 个跳过项为 junction。3 个警告仍为已有 `datetime.utcnow()` 弃用警告。**本轮不是云端全量全绿，也尚未完成原生 Windows 复测**；没有为得到绿色结果而修改无关代码或屏蔽这些失败。298 个 `.py` 与 1 个 `.pyw` 文件语法解析通过，差异空白检查通过。

输出目录应由运行者独占，不能由不可信进程并发修改。此次防护覆盖恶意归档名/成员以及预先存在的链接；跨平台的路径检查和原子文件替换不等同于对恶意并发父目录交换的无竞态沙箱。修复没有重建安装包、合并或发布，也没有新增测试 CI。

## 现有安装包

[v0.1.0 公开预发布](https://github.com/78979660-bit/LearningModel/releases/tag/v0.1.0)的安装包及项目源代码 ZIP 对应 `8aeaf92bc786adc517b8d2569b291cdad79243c1`，早于 PR2 和本轮维护。此维护分支与依赖更新不会改变已经下载的安装包。旧安装包需要单独核验打包组件、评估风险并重建验收，本轮没有拆包或发布新版。原发布的源码、第三方源包、许可证材料和校验值均保持原样；未来构建应重新收集与新依赖对应的材料并完成验收。

检查未启用、关闭或削弱安全功能，未合并或发布新版。Dependabot 和测试 CI 的配置缺口需要另行授权维护。


## 2026-10-03：合入 v0.1.1 主线并复测

本次以远端维护分支 `6e9f6139f1d5ce3e9c5014200ecf587d42ab00c6` 为起点，合入新获取的 `main` / `v0.1.1` 提交 `f8a4d4a97409622290d98959906b91388d3c812c`。仅 `requirements-build.lock.txt` 的说明头发生文本冲突；解决后保留 v0.1.1 版本语境、Pillow 12.3.0 和 pip 26.2.1，并明确已发布安装包使用各自发布源码提交中的锁文件。主线的计划、仪表盘、学科路由及发布材料均保留；四类维护修复与许可证输出保护源码和回归测试与维护分支原样一致。

验证环境：Linux x86-64 云电脑、CPython 3.12.14、`QT_QPA_PLATFORM=offscreen`，同一虚拟环境中的 21 个版本与合并后的锁文件全部一致，`pip check` 通过。主线对照也使用同一环境，目的是隔离源码合并带来的回归，并非验证主线旧依赖组合。

- 许可证、维护回归及主线改动专项：176 passed、1 skipped、18 subtests passed。
- 加入发布运行时测试的较广专项：182 passed、2 failed、2 skipped、18 subtests passed；两个失败均为 Linux 缺少 `ctypes.WinDLL`，也出现在主线对照中。
- 合并后全量：1261 passed、5 failed、4 skipped、3 warnings、279 subtests passed。
- 新获取主线全量对照：1119 passed、5 failed、3 skipped、3 warnings、279 subtests passed。
- 两次全量的五个失败 ID 和消息逐项完全一致：1 项 Windows DPAPI、2 项 `ctypes.WinDLL`、2 项 Linux Noto CJK TTC 字体加载；未新增合并回归，未屏蔽或修改这些测试。
- 301 个 `.py` / `.pyw` 文件语法检查通过；Git 差异空白检查通过；没有未解决的冲突条目。

全量命令（分别在合并树与主线独立工作树执行）：

```text
QT_QPA_PLATFORM=offscreen python -B -m pytest -q -p no:cacheprovider --junitxml=results.xml
```

本轮没有原生 Windows 复测，因此不能称为全量全绿，也不能替代 Windows junction、DPAPI、mutex、安装包和真实 OCR / 外部 AI 验收。未合并 PR、发布或重建安装包。已发布的 v0.1.1 包不会随此次源码合并而更新，仍需另行决定是否重建及验收。
