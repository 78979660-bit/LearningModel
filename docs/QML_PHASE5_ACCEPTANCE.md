# 阶段五：资源、首页收尾与运行生命周期

后续发布前收尾已补齐托盘提醒/刷新、付费生成取消与缓存身份处理、构建输入漂移门槛及内部安装器；最新全量与产物状态见 [发布前收尾记录](QML_PRERELEASE_CLOSEOUT.md)。以下保留阶段五原交付的验证与当时剩余范围。

日期：2026-10-05。分支：`codex/qml-washi-phase1`，基线 `f8a4d4a`；在已有迁移修改上增量实施，没有覆盖既有工作、提交或发布。阶段四的凭据身份绑定修复保留。本阶段交付完整 QQuickWindow 的功能与内部 QA 包；尚不签核全应用及正式发行。

## 本阶段已完成

- 首页接入路径注入的 `DailySummaryService` / `DailySummaryBridge`，真实 SQLite 数据生成本地总结；显式点击才调用云端增强，沿用原有校验、预算及审计。QML 不接触数据库或密钥。旧总结函数新增可选设置与数据库参数，默认兼容原入口，审计不会误写实际用户库。
- 每日总结复用旧缓存签名并加入内容指纹；同 ID 记录编辑、数据/学科/设置/日期变化、离页或取消后丢弃旧结果。请求超时/取消时直到线程结束才解锁。模式开关在事务中仅改启用位、保留最新凭据与策略；提交回执和后续刷新分离，失败时开关恢复服务快照。
- `FloatWindow.qml` 使用统一 Theme/ActionButton、独立文字和真实控件。主窗关闭显示浮窗，浮窗关闭恢复主窗；键盘可恢复/退出，`Ctrl+Q` 明确退出。没有 QWidget 嵌入。原生托盘使用 Win32 Shell 通知区、包内 ICO、V4 鼠标/键盘选择消息，清理自有图标句柄；缺托盘时保留浮窗入口。
- 单实例保护复用旧 Python 机制，排空文件使用任务后才释放；后台唤醒经 QObject 实例静态声明的 Qt 槽及 QueuedConnection 在 GUI 线程执行。既有用户明确启用的每周收集任务接入共享后台队列，默认关闭；该队列一次执行一个任务，不阻塞 GUI，长任务可能推迟随后读取。
- 资源白名单共 38 项，新增浮窗与总结面板；spec 显式打包两份中文 Qt 翻译。启动自检使用隔离数据、实际实例化 5 个 required imports，解码 10 个独立 SVG，加载五条路由及所有页签、真实总结、三轮关闭/恢复和明确退出，输出机器可读报告。无网络、无真实凭据。
- `tools/build_qml_qa.ps1` 在新运行目录构建，核对 CPython 3.14.5 x64 和所有锁定依赖，记录环境、脚本/版本/spec/lock/verifier 哈希及受限源码输入清单，调用冻结自检及白名单验证。不递归打包项目或个人数据，不覆盖已有构建。

## 本轮发现并修正的问题

1. 新 imports 诊断原先未等待 QQmlComponent 异步加载，首次会误报模块不可用；改用本地基准 URL，并有界等待 Ready 后实例化。源码和冻结自检均已通过。
2. 单独加载总结面板的测试先锁定了 Qt 原生 Controls 风格，导致后续统一组件出现定制警告；该夹具现在像生产入口一样在首次 Controls import 前设 Basic。未屏蔽 QML 警告。
3. 真实 QML 入口探针发现，浮窗曾显示后明确退出仍拒绝浮窗 Close，导致 `app.exec()` 不返回。明确退出阶段现在放行两窗关闭；正常浮窗 Close 仍为恢复操作。增加真实事件循环回归，真实入口复测成功。
4. 原唤醒连接使用静态 Python 回调，无法明确 Qt receiver 的线程；新增 `WakeBridge.restore` 实例 `@Slot` 和排队连接，测试后台发射后只在 GUI 线程执行，退出期间迟到唤醒被忽略。
5. 构建环境探测的中文路径经控制台编码会失真；探测 JSON 改 ASCII 转义，检查报告路径与真实绝对路径一致。源码清单逐项包含全部资源，含同一行的多个 SVG 和无扩展的 qmldir。
6. 最后复核复现了云端总结缓存已提交、后续回读却报操作失败的问题。现在写前构造安全回执，写后异常或签名失配返回“已生成并缓存，但刷新未完成”，禁止再次生成直到成功刷新。真实 QML 点击测试确认数据库已缓存、页面无失败提示、再次生成被禁用、刷新后恢复；全程使用测试生成器，无外部调用。

## 验证结果

以下仅指本机 Windows、Qt/PySide6 6.11.1、CPython 3.14.5 x64 和锁定依赖；Windows 原生 DPR 1.5，模拟缩放覆盖 1 / 1.5 / 2。全部用隔离测试目录及数据库。

| 项目 | 结果与证据 |
| --- | --- |
| 服务/桥接/收尾集成 | 51 passed，`output/qml-phase5-integration-r3-20261005.log` |
| 原生窗口与真实托盘 | 最终退出/唤醒修复后 **39 passed**，`output/qml-phase5-native-r3-20261005.log`；包括注册/删除、ICO 加载/释放、真实 PostMessageW 到 Qt filter、实际 app.exec 退出；没有把合成消息代替托盘注册 |
| 模拟 DPI | 100%、150%、200% 各 10 passed，`output/qml-phase5-dpi-{1,1_5,2}-20261005.log`；查看真实总结和浮窗截图 |
| 最后补修的 DPI/最小窗口 | 100%、200% 各 **7 passed**，`output/qml-phase5-final-dpi-{1,2}-20261005.log`；含 800×600 窗口控件边界与提交回读失败的真实 QML 操作 |
| 面板风格组合回归 | 36 passed / 3 native-only skipped，`output/qml-phase5-style-recheck-20261005.log` |
| 唤醒线程/早退排空 | 2 passed，`output/qml-phase5-wake-thread-20261005.log` |
| 真实 QML 入口 | `output/qml-phase5-source-entry-quick-final2-20261005.json`：QGuiApplication，未导入 QtWidgets，真实数据就绪、16 次 GUI 心跳、托盘注册、第二进程被阻止、socket 唤醒恢复、退出后互斥锁释放 |
| 旧 Widgets 入口对照 | `output/qml-phase5-source-entry-widgets-20261005.json`：QApplication、5 页面、DPR 1.5、明确退出成功 |
| 源码扩展自检 | `output/qml-phase5-source-selftest-r2-20261005.json`：38 资源、5 imports、10 图片、2 翻译、真实记录/总结/完整页签及浮窗循环，0 QML warnings |
| 迁移主体全量回归 | **1331 passed、281 subtests、3 native-only skipped、4 既有弃用警告**，`output/qml-phase5-full-314-r3-20261005.log` |
| 最后生成回执补修与旧总结回归 | 正式 3.14.5/native：**54 passed、36 subtests**，`output/qml-phase5-final-correction-r2-20261005.log`；含服务故障注入、桥接防重复、真实 QML 提示及刷新、窗口退出、旧总结契约 |
| 最终正式环境 QA 冻结 | `output/qml-phase5-314-20261005-r3/metadata/run-record.json`：passed；Python 3.14.5 x64、Qt 6.11.1、21 份锁定依赖，无额外依赖，229 个源码/资源输入哈希与当前源码逐项一致。2027 个发布文件、220901150 字节，白名单/版本/隔离冻结自检通过；构建和验证日志位于该目录 metadata |
| 最终冻结包原生自检 | `output/qml-phase5-frozen-native-selftest-r3-20261005.json`：真实隔离数据/完整路由页签/总结/翻译/imports/图片/浮窗恢复，0 QML warnings；通知区域注册另由 native 测试与真实运行入口验证，自检没有虚称注册 |

全量运行在最后生成回执补修前已收集 Python 模块，因此上述全量结果不计作这两份文件补修后的全量重跑。补修涉及的服务、桥接、QML 行为与旧总结调用链已用最终源码单独完成 54 项回归，再重建并核对冻结包；本轮没有第二次补修后的完整全量运行。

最终 QA 程序为 `output/qml-phase5-314-20261005-r3/dist/LearningModel/LearningModel.exe --qml`。此前 r1/r2 包保留为诊断历史，包含后续修正前的实现，不作为最终交付包。

首轮全量日志 `output/qml-phase5-full-314-20261005.log` 保留风格污染导致的失败，未冒充通过；中途一次全量运行因随后发现退出/唤醒问题而停止，保留 `output/qml-phase5-full-314-final-20261005.log`。原始退出探针 timeout 日志 `source-entry-quick-r2/r3/r4` 也保留。阶段二 access violation 的真实转储、定向对照和静态槽根因证据继续见阶段二修复记录；本轮有限复测不证明所有机器、驱动和负载均稳定。

## 剩余范围及入口

- 真实外部 AI 服务/OCR 材料、用户交互的原生凭据对话框端到端流程仍未完成本轮验收。本地校验及截获请求测试没有消耗真实密钥或联网。
- 旧 Widgets 托盘“今日提醒”气泡及菜单内刷新动作尚未迁移到新托盘，QML 内的真实首页刷新和每日总结可用；新托盘本轮覆盖恢复、浮窗和退出。跨屏拖动、Explorer 重启、睡眠恢复及不同 Windows/显卡仍需人工验证。
- 模拟 DPI 和本机 DPR 已测，不能替代多显示器混合 DPI 现场验收。本阶段没有新安装包、升级/卸载/重装 VM 验收、签名或发布。
- 原迁移方案要求“只有阶段 0–5 验收清单完成后才将常规入口切到 QML”。因此当前常规 launcher 仍走 Widgets；QML 源码用 `python -m study_app.quick`，冻结 QA 用 `LearningModel.exe --qml`。这不等同全应用迁移完成。ChatGPT 桌面 PDF 已按用户要求删除，保留本地练习 PDF，不再作为待验功能。

复现命令（每次新 RunName）：

```powershell
& tools/build_qml_qa.ps1 -PythonPath output/qml-phase5-venv-314-20261005/Scripts/python.exe -RunName qa-phase5-recheck -RequireReleaseRuntime
& output/qml-phase5-venv-314-20261005/Scripts/python.exe -B tools/qml_diagnostics/phase5_runtime_probe.py --mode quick --report output/quick-entry.json
& output/qml-phase5-venv-314-20261005/Scripts/python.exe -B tools/qml_diagnostics/phase5_runtime_probe.py --mode widgets --report output/widgets-entry.json
```
