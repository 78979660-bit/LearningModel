# QML 阶段四：设置与学科安全管理

日期：2026-10-05。分支保持 `codex/qml-washi-phase1`，保留既有工作区修改，没有提交、推送或切换正式入口。本阶段使用完整 QGuiApplication / QQuickWindow，新增设置路由的真实服务与数据操作。

## 功能与架构

| 功能 | 实现与业务来源 | 保护边界 |
| --- | --- | --- |
| 服务商、模型、接口、功能、token 与每日预算 | `SettingsService` → 现有 provider/设置仓储 | 严格字段和范围校验；provider、隐私及策略同一事务；事务内读取既有凭据，仅在服务商和实际接口身份相同时保留。身份变化清除旧凭据。接口中的账号、查询参数及片段不进入页面。 |
| 凭据配置与清除 | `SettingsBridge` → Python Windows 系统对话框 → 原 provider 加密存储 | 页面只有凭据状态；没有 QML 密钥字段或读取密钥槽。配置先保存当前服务商；取消不改旧凭据，清除要明确确认。 |
| 隐私、顾问模式、自动执行白名单 | 现有 `learning_assistant_policy` | 模式与布尔值严格校验；实际执行仍由原引擎重新核验授权，默认不新增自动执行授权。 |
| 备份、完整性与 LLM 审计 | `data.backup`、设置审计仓储 | 真正 SQLite 备份与记录导出；SQLite、外键及文本检查；审计不包含原请求/响应错误或凭据。隔离数据的默认备份目录也在该隔离目录内。 |
| 学科引入、可恢复移除、恢复 | `SubjectService` → `core.subject_management` | 手动输入或严格本地目录草稿；先预览影响，再勾选并准确输入完整名称。引入不覆盖旧学科；移除仅归档，保留记录；恢复稳定身份并保持旧计划归档。 |
| 学科能力、历史与投影重试 | 原 catalog / lifecycle / projection outbox | 展示真实能力及审计。重试只对应已证实提交的原操作，不重新写入学科或自动审批其他变更。 |

新增 `settings_service.py`、`subject_service.py`、`settings_bridge.py`、`credential_prompt.py` 与三个 QML 页面。服务构造器只保存显式路径；文件、DB、备份、检查、审批与投影操作均在共享后台队列中执行。QML 调用固定槽并获取 DTO 和进程内不透明句柄。所有 Qt 信号接收器保留静态 `@Slot`；关闭后在事件循环外等待工作结束再清理隔离目录。

凭据输入使用 Windows 原生应用模态对话框，不嵌入 Widgets 页面，也不把密码回填给 QML。调用指定不交给 Windows Credential Manager 保存，由现有 provider 存储负责加密；临时 Win32 输入缓冲区在返回后清零。API 行为依据 [Microsoft CredUIPromptForCredentialsW 文档](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-creduipromptforcredentialsw)。

## 学科安全流程与修复

原 `prepare_management` / `execute_management` 继续负责目录身份、catalog/object version、关联记录/活动及共享计划的影响指纹、确认、备份与校验、原子归档、幂等操作和投影 outbox。桥接层要求与当前页面预览完全一致的句柄；输入、页签、路由或相关数据变化使旧预览失效。文件只先填入草稿，读取 300,001 字节用于执行 300,000 字节上限检查；JSON 严格要求 `name` 和 `modules`，目录继续调用原解析器验证。

真实窗口用例发现，原移除预览对包含 SQLite REAL 的正常记录调用严格 `canonical-json-v1` 会失败。本轮只在影响快照中把有限 REAL 转为带类型标签的 `float.hex()` 精确值，再哈希。审批 canonicalizer 与 changeset schema 仍禁止浮点数；没有四舍五入或放松影响复核。新增正常记录与小幅数值漂移回归，防止预览后变化逃过复核。

提交后回读/投影失败与业务提交失败分开：只有同一操作的持久化事件证明提交时报告已保存，必要时展示投影重试；无法核实结果时提示先刷新检查、不要重复执行。

## 删除 ChatGPT 桌面 PDF 生成功能

按用户本轮要求，移除 QML/Widgets 中的桌面 PDF 执行按钮和调用、后台生成/取消/退出清理接口、桌面桥接 Python 模块与三个 PowerShell 自动化助手，以及 spec/冻结白名单中的相应资源。移除该功能专用测试，混合测试保留其他业务并更新删除回归。

本地练习题目卷、答案卷、生成记录、PDF 导出与普通提示词/上下文手动复制保留。删除仅针对项目代码和资源；没有清理用户已生成 PDF、旧安装包或历史验收日志。此前文档中的桌面 PDF 未验收条目保留为历史记录，当前不再以该链路阻塞后续验收。

## 验证记录

环境：Python 3.12.14 / PySide6 6.11.1，使用 `.venv-qml` 与隔离临时数据。下表保留阶段四首次交付的验证历史；它不包含随后发现的凭据安全补修，补修结果单独记录在下一节。

| 验证 | 最终结果 | 证据 |
| --- | --- | --- |
| 全量回归，offscreen / scale 1 | **1276 项、277 项子测试通过**，410.40 秒 | [全量 R2 日志](../output/qml-phase4-full-regression-20261005-r2.log) |
| Windows native 实际窗口 | **19 项通过**，61.19 秒 | [native R3 日志](../output/qml-phase4-final-native-window-20261005-r3.log) |
| offscreen / 150% DPI | **19 项通过**，43.83 秒 | [150% R2 日志](../output/qml-phase4-final-scale-1.5-window-20261005-r2.log) |
| offscreen / 200% DPI | **19 项通过**，41.98 秒 | [200% R2 日志](../output/qml-phase4-final-scale-2-window-20261005-r2.log) |
| 源码启动自检 | 完整 `QQuickWindow`、36 项资源、5 条真实页面路由；设置两页签、真实 SQLite 数据与完整性检查通过；**0 QML 警告** | [自检 JSON](../output/qml-phase4-source-self-test-20261005-r2.json) |
| 新 QA 冻结目录及启动自检 | **通过**，2024 个文件、223796182 字节，版本 0.1.1 | [构建 R2 日志](../output/qml-phase4-freeze-20261005-r2.log)、[冻结验证 R2 日志](../output/qml-phase4-frozen-verification-20261005-r2.log) |

窗口用例覆盖首次及反复收放菜单、重复点击当前路由、页面切换、真实数据刷新、键盘操作、800×600 / 1600×1000 缩放，以及设置持久化、取消/确认清除测试凭据、真实备份与完整性检查、目录导入、引入→写入真实记录→移除→恢复和关联页面刷新。服务与桥接用例覆盖事务中途失败回滚、提交后刷新失败、失效预览、后台线程取消/退出排空和投影重试。凭据均为隔离测试值，不使用用户真实密钥。

本轮曾复现设置页销毁后 `Qt.callLater` 仍调用页面方法的 QML 警告，以及快照同步尚未完成就允许编辑的时序问题。已改为页面拥有的 `Timer`，销毁时取消回调，快照同步期间禁用表单。失败记录保留在 [native R2 日志](../output/qml-phase4-final-native-window-20261005-r2.log)；修复后的最终 native / DPI 组合及全量回归通过。另一次缩放断言早于 Qt 布局更新，测试现在等待下一次实际布局并保留原几何断言。

本轮最终窗口测试没有 access violation 或 QML 警告。阶段二的静态槽、元对象缓存和对象生命周期回归继续纳入全量测试；其真实转储及根因证据见 [阶段二后续修复记录](QML_PHASE2_ACCEPTANCE.md#窗口崩溃诊断历史)。这些结果限定于上述环境和用例，不表示所有机器、驱动及负载均已验证。

测试数量包含新增阶段四用例与桌面 PDF 专用测试删除后的结果，不能与历史阶段直接累计。首次交付 QA 目录 `output/qml-phase4-dist-20261005-r2` 不包含下述凭据修复，已不作为当前验收版本。新补修目录使用 `--qml` 启动完整 QML 窗口，未替换旧 Widgets 默认入口。

## 凭据身份安全补修（2026-10-05）

用户复现的 P1 成立：DeepSeek→自定义接口，以及自定义接口 A→B 保存时无条件继承 `current.api_key`。两个隔离请求拦截回归在修复前均失败，证据见 [修复前复现日志](../output/qml-phase4-credential-repro-20261005.log)。拦截替代实际传输，不联网、不使用真实密钥。

- 凭据身份包含服务商（及其协议）和实际 API 根地址。scheme、主机、端口及路径变化均属于身份变化；仅去除首尾空白和请求层同样去除的末尾斜杠。模型、预算及其他非身份设置变化保留凭据。
- 设置服务在原 `BEGIN IMMEDIATE` 事务内复核身份，只保留同一身份的密钥；身份变化会同时清空明文及加密凭据，返回 `credentialReset`，页面显示尚未配置并要求重新配置。后续审计请求在传输前被配置检查阻止。中途失败会连同凭据重置一起回滚。
- provider 存储增加 `api_key_identity`；加载发现绑定身份与当前配置不符时不解密、不返回旧密钥。旧的未绑定配置首次读取时绑定到其当前保存身份；无法据此追溯修复前是否曾被错配。
- 原生凭据弹窗和清除操作携带不含密钥的身份指纹，在后台写事务内复核。弹窗期间其他写入者改变接口时，旧弹窗不能向新地址写入或清除凭据。QML 中未保存的接口编辑也会禁用配置/清除并提示先保存；Qt Widgets 对照入口会清空身份变化后的旧输入值。

| 补修验证 | 结果 | 证据 |
| --- | --- | --- |
| 最终全量回归，offscreen / scale 1 | **1292 项、281 项子测试通过**，419.67 秒；较首次交付增加 16 项测试和 4 项子测试 | [全量日志](../output/qml-phase4-credential-full-20261005.log) |
| 服务、桥接、provider 迁移、Widgets、隐私及发行基础定向回归 | **88 项、11 项子测试通过**，25.70 秒；随后增加的 5 个身份边界参数用例纳入最终全量 | [定向 R2 日志](../output/qml-phase4-credential-focused-20261005-r2.log) |
| 实际 QML 设置及导航窗口，offscreen / scale 1 | **11 项通过**，23.74 秒 | [窗口日志](../output/qml-phase4-credential-window-20261005.log) |
| Windows native 实际窗口 | **11 项通过**，28.40 秒 | [native 日志](../output/qml-phase4-credential-native-20261005.log) |
| offscreen / 200% DPI | **11 项通过**，23.31 秒 | [200% 日志](../output/qml-phase4-credential-scale2-20261005.log) |
| 源码自检 | **通过**，36 项资源、5 条路由、0 QML 警告；隔离 SQLite、设置与完整性检查通过 | [源码自检 JSON](../output/qml-phase4-credential-source-self-test-20261005.json) |
| 新补修 QA 冻结目录及启动自检 | **通过**，2024 个文件、223798536 字节，版本 0.1.1 | [构建日志](../output/qml-phase4-credential-freeze-20261005.log)、[冻结验证日志](../output/qml-phase4-credential-frozen-verification-20261005.log) |

上述补修验证均已完成，最终窗口日志没有 QML 警告或 access violation；原有阶段验收边界仍保留。新补修 QA 可执行文件为 `output/qml-phase4-credential-dist-20261005/LearningModel/LearningModel.exe --qml`；仅供内部验证，没有生成安装包或发布。

## 剩余范围

- 托盘、浮窗、首页每日 AI 总结与完整窗口显隐生命周期还未迁移；旧 Widgets 入口继续可用。
- 真实外部 AI、OCR 的端到端验收仍未完成；桌面 ChatGPT PDF 已删除，不再作为待验收功能。
- 系统凭据弹窗的 ABI/取消/缓冲区处理及通过注入测试密钥进行真实保存、清除可自动验证；真实系统弹窗的人工输入和外部供应商连通性没有代替用户验证。
- QA 冻结目录不替代正式 Python 3.14.5 发行环境，也不代表生成新安装包、发布或阶段五完成。
