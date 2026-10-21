# QML 真实验收记录

日期：2026-10-05。分支 `codex/qml-washi-phase1`；保留原有修改。本轮结论：**宿主机部分真实流程已验证并修复一个界面问题；完整发布验收仍未通过。**

2026-10-06 更新：五页视觉重构的最终内部候选为 `output/qml-ui-rebuild-20261006-r4/`，详见 [视觉重构与验证](QML_VISUAL_REBUILD.md)。宿主机 computer-use 已恢复，已直接核验该候选的五页、菜单、键盘、真实数据刷新及正常退出，取得 exit=0。下文真实 AI/OCR 与虚拟机结果属于 2026-10-05 的历史验收；本次 UI 任务没有重跑外部请求或执行客体安装生命周期，不能将旧候选结果当作 r4 的完整发布验收。原虚拟机/Guest Control 状态尚未复核。

## 实测范围与结果

本机 Windows 11 家庭版 25H2，构建 26200；Python 3.14.5 x64 / Qt 6.11.1。全部学习材料由程序绘制的中文合成作业生成，使用独立 SQLite、模型和记录文件。用户授权使用已有服务配置；仅在 Python 内存中读取实际凭据，隔离设置通过原有保护机制保存，结束后清除隔离凭据。原用户数据库 SHA-256 每轮前后相同。没有安装到宿主机或公开发布。

证据根目录：`WORKSPACE/output/release-workflow/github-source/output/qml-real-acceptance-20261005-44ab453f`，后续复测目录追加 `-r2`、`-r3`、`-r4`、`-r5`。

| 项目 | 本轮结果 | 证据与边界 |
| --- | --- | --- |
| 实际 Windows 凭据对话框 | 取消、保存合成密钥、已有密钥下取消均符合预期；保存后 QML 文本和 DTO 不含密钥 | `-r3/real-host-results.json`；真实 CredUIPromptForCredentialsW，未替换提示函数；自动操作只定位本测试进程创建的密码对话框，未操作日常账户。没有人工签署或安装版操作验收 |
| 实际中文 OCR | Tesseract `chi_sim+eng` 执行成功，识别中文学科、题目、答案和时长，无提取警告 | `-r3/合成作业.png` 与结果 JSON；未替换 OCR 或附件提取器 |
| OCR → 真实 AI 识别 → 复核保存 | 实际服务返回 1 道题；预览未写记录，明确保存后隔离库记录从 0 到 1 | `-r3/real-host-results.json`；经实际 RecordsService 和 WorkspaceBridge 后台路径，预览/保存通过桥接调用；附件选取、复核控件未逐项人工操作 |
| 真实助理问答 | 一次有效响应经既有校验采用；随后两次返回响应但结构校验失败，采用本地降级答案 | 成功响应见 `-r3`；两次失败见 `-r4`、`-r5` 及隔离库审计状态 `validation_failed / LLMValidationError`。失败不计为云端通过；未降低校验要求或无界重试 |
| 真实每日总结 | 生成、校验、缓存成功；刷新读取匹配缓存，没有再次请求 | `-r3/real-host-results.json`、`real-summary.png`。此流程不是静态示例或注入生成器 |
| GUI 响应与退出 | r3 三次实际请求期间 GUI 心跳分别 84、57、430；耗时约 2.613、1.760、13.443 秒；任务排空后关闭，无 QML 警告 | 只证明本轮调用期间事件循环继续工作，不等同性能或所有设备稳定性验收 |
| 助理正文修复 | Python 返回的 QVariantList 在 QML 中未必满足 Array.isArray，导致标题有内容而正文为空；已修复列表转换 | `AssistantPage.qml`；对已收到的真实成功响应和真实本地降级答案逐段比对可见正文。见 `actual-body-display-review.json` 和两张 `actual-*-body-fixed.png`。此项明确属于实际响应的离线重放显示核验，不称为修复后的新云端成功 |
| 原生助理回归 | **8 passed** | `assistant-native.log/xml`；本地查询全文、Python 列表和字符串正文、已有助理/图谱交互 |
| QML 综合回归 | **258 passed、3 native-only skipped、2 既有弃用警告** | `qml-regression-r2.log/xml`；三个跳过项另在实际 Windows 平台 **3 passed**，见 `native-tray.log/xml` |
| 验收数据库读取 | **3 passed** | `acceptance-probe.log/xml`；Windows 系统 winsqlite3 只读读取中文路径非空库，SHA-256 不变；缺失或错误结构拒绝通过 |
| 修复后的冻结程序 | 实际原生自检退出 0、status=ok、38 资源、QML 警告 0 | `fixed-frozen-native-self-test.json`；229 源输入/6 配置构建后漂移 0，2027 发布文件。与上一候选相比只有 AssistantPage.qml 的源码/资源输入变化 |

本轮共 **7 次真实 HTTP 请求**：前两次识别用于修正验收脚本错误，r3 三次覆盖识别/问答/总结，r4 与 r5 各一次问答。前两轮脚本误用活动枚举、页签或回执属性，原始失败记录保留，不计为应用验收通过。r3 自动脚本最初只检查桥接结果而报告 passed，后续截图审查发现正文空白，因此其界面结论被本记录纠正；修复和可见正文验证有独立证据。

自检工具也已修正：当前 `--self-test` 使用临时合成资料，旧 `clean_windows_acceptance.ps1` 错把自检路径/计数当作实际安装用户的数据。现在只用自检确认打包资源，实际卸载/重装计数通过系统只读 SQLite API 核对，不安装 Python；首次空资料仍须在真实 QML 窗口确认。该脚本的真正安装/卸载阶段本轮没有执行。

## 2026-10-05 候选与旧材料

新候选目录：`WORKSPACE/output/release-workflow/github-source/output/qml-real-acceptance-314-20261005-r1`。

- 程序 SHA-256：`aaf3348ae4e10d3948f891b1aebc029c5c868a640a51ff8cc14a5649c1b9bdd4`。
- 安装器 SHA-256：`1107ddbeb5d5182c58edad10e5b6d6b7f9401bb90206e32d60c049db6b96b996`；63415064 字节，Inno 7.1.0，built-and-verified。
- 仍为内部 0.1.1，未签名、未发布、安装生命周期 pending；常规入口仍 Widgets，QML 使用 `--qml`。
- 原 D 盘 handoff 保留为前轮历史交付，**不包含本次正文修复**，不能作为本轮安装验收对象。本次使用 C 盘新候选。
- 35 份第三方源码归档及 200 原生文件已重新核对；新增 UI 修复没有新增运行依赖。哈希核对不代替发行材料签核。
- 本轮没有重跑 1361 项全项目测试；该数字属于上一轮历史基线。本轮使用上表中的定向与综合 QML 回归。

## 尚未完成与实际阻塞

已按用户指引查阅“验收 0.1.1 版本打包”对话并发现 `LearningModel-QA`，UUID `b9363efd-e192-489d-bd67-58b13f745402`，VirtualBox 7.2.16，Guest Additions 同版本，旧验收用户 qauser。原状态为 saved，恢复成功；没有回滚快照、重置用户、导出客体数据库或执行安装。

Guest Control 的测试账户会话启动在 30 秒后超时：`Error starting guest session (current status is: starting)`。随后查询没有活动 Guest Control 会话。保存的 LoggedInUsersList=qauser 不证明桌面目前可交互。

按用户要求使用 [computer-use 技能](USER_HOME/.codex/plugins/cache/openai-bundled/computer-use/26.928.20755/skills/computer-use/SKILL.md) 初始化 `@oai/sky`，报 `failed to write kernel assets: 系统找不到指定的路径。 (os error 3)`；重置 node_repl 并重试仍失败，没有成功取得窗口或截图。遵循该技能 guidance.md 的恢复要求：“retry once, then stop and report that the Windows Computer Use helper may have failed.” 没有通过其他桌面输入接口绕过这个失败，也没有关闭宿主安全设置或强制重启虚拟机。

因此仍待：

- 桌面控制恢复后，对本次新候选执行干净首次安装、既有 0.1.1 升级、只读安装目录启动、非空记录保留卸载/重装、显式清除的两次确认及失败恢复。
- 在安装版实际操作原生凭据、附件选择、识别复核和完整真实 AI 使用流程；本轮成功样本不覆盖所有外部功能、服务商或识别材料。
- 虚拟机/第二机器的实际窗口操作、混合显示器 DPI、跨屏浮窗、Explorer 重启及睡眠恢复。
- 冻结源码、对应分发材料、正式版本与最终发布批准。

当前完整签核结论仍为 **未通过，受环境阻塞**；不能以本机真实请求或新安装器生成替代上述验收。
