# QML 阶段二：学习工作区

2026-10-04。开发分支沿用 `codex/qml-washi-phase1`。已有阶段一与用户工作区修改全部保留；本轮不提交、不发布、不切换默认 Widgets 入口。

**状态：用户复核提出的两项写入问题与已复现的窗口 AV 机制已有源码修正及回归证据。** 阶段二仍作为功能增量交付，外部 AI/OCR、真实桌面 PDF 与正式发布尚未完整验收。下文原验证结果保留为历史证据，后续修正的实现与验证单独记录，不能用重复通过覆盖已有失败。窗口修复的最终证据见本文末节。

## 实现范围

学习计划主路由加载真实 `PlanPage.qml`，包含计划、学习记录、OJ、练习 PDF 四个页签；它们都是完整 QML 窗口内的真实控件，没有嵌入 QWidget，没有静态业务演示。

| 用例 | QML 行为与 Python 服务 |
| --- | --- |
| 今日预算 | 读取实际候选任务、估时与考试日期；填写时间、调整估时，预览选择/排除依据，勾选复核后确认保存。完成状态由数据库回读，写入后同步刷新首页。 |
| 详细计划 | 默认本地生成，增强模型明确选用；预览后复核保存、勾选任务完成、作业正确/有误反馈、每日反馈、归档、所选学科期末复习阶段确认。已有作业结果禁用再次反馈，数据库仍拒绝重复记录。 |
| 学习记录 | 日期、目录、活动/来源、分数、时长、备注/做题情况/错因；多选本机附件或按描述查找并明确加入材料；手动/本地/明确选用增强识别；编辑题面、结果、错因、状态、正确率与难度后确认保存。附件和基础事实改变必须重新预览。 |
| OJ | 真实题目身份注册/更新，显式知识点映射后追加结果、耗时、独立性、提示程度、错因、备注与可选幂等键；展示实际历史、首次通过和耗时改善。这里不连接外部 OJ 平台。 |
| 本地 PDF | 使用本地真实模板和题库生成题目卷、答案卷及 manifest；报告实际题量和不足状态。文件名与不透明 token 返回 QML，Python 检查实际文件后交给系统查看器。 |
| 桌面 PDF 工作流 | 准备并展示完整练习/模拟卷提示词，复制已确认计划的练习上下文；只有复核勾选并点击执行才调用现有 ChatGPT 桌面桥接。预览不会发送；OJ 不走 PDF 工作流。 |

## 架构与数据边界

新增 `quick/workspace_bridge.py` 只接收封闭的操作白名单与 DTO，通过首页拥有的单线程 `AsyncTaskService` 串行执行。`quick/plan_service.py`、`records_service.py`（含 OJService）、`practice_service.py` 是不依赖 Qt 的界面用例服务，明确注入 `db_path/model_path/records_path/today`。QML 不连接 SQLite、不读取凭据、不导入旧 Widgets 页面。

计划、学习阶段、provider、审计、练习提示词和评分链路增加可选路径参数，默认值兼容旧入口。不能在生产适配器中临时替换全局函数。旧记录编辑器中的本地推断规则抽到 `core/record_draft_inference.py`，两种界面共享题号区间、错题归属、难度与分数推断。

预览使用实例内短期 token，并检查目录版本、相关状态或材料快照；输入变化、离开工作区和重新加载会使 QML 复核状态失效。确认 token 不能重复写入。写入成功但后续刷新失败时仍报告“已保存”，避免引导用户重复提交。

数据库、文件提取、OCR、模型、PDF、增强请求及桌面操作均在工作线程执行。取消/超时只代表请求结果结束时，不提前释放忙碌状态；等实际 future 结束后再接收或丢弃结果。关闭窗口和启动自检失败均先停止派发，再由 `wait_for_shutdown()` 排空线程，之后才允许临时目录清理。确认写入不提供假取消。

QML 资源白名单从 20 扩展到 29 个文件，包括四个表单组件和五个工作区页面。主题、真实文本、独立纸纹/樱花/竹叶沿用阶段一，默认 Qt 控件使用统一调色板。自检加载五个主路由和四个工作区页签，检查真实读取与 QML 警告；资源及冻结自检脚本同步更新。

## 验证

使用 `.venv-qml` 的 CPython 3.12.14 x64 / PySide6 6.11.1。所有自动化业务写入使用独立临时 SQLite、模型及记录文件，不使用用户学习数据，不连接外部 AI，不实际操作 ChatGPT 桌面端。

| 检查 | 结果与证据 |
| --- | --- |
| 完整回归 | **1192 passed、279 subtests passed**；3 条既有 `datetime.utcnow()` 弃用警告。`output/qml-phase2-full-regression-final.log`。旧 Widgets 契约、学科生命周期、provider/audit/计划等既有模块均在此次全量内。 |
| 新服务与实际窗口 | **40 passed**；预算确认、详细计划、勾选、作业反馈、防重写、手动记录复核、OJ 显式映射/提交、实际 PDF 与提示词参数一致性。`output/qml-phase2-service-window-final.log`。 |
| GUI/DPI | `QT_SCALE_FACTOR=1/1.5/2` 三次各 **8 passed**；首次菜单收起、反复开合、重复点击当前路由、高亮、各工作区切换、800×600/1024×768/1600×1000、键盘 Tab/Shift+Tab/Space/Enter、学科键盘选择、F5 刷新、预览失效、真实数据刷新；所有检查均断言无 QML 诊断。`output/qml-phase2-window-scale-*.log`。 |
| 原生 Windows 窗口 | 实测默认 DPR 1.5；首轮 6 项通过，完整 8 项一轮出现 access violation（`output/qml-phase2-native-windows-final.log`）；独立重复导航 2 项通过，完整复测 **8 passed**（`output/qml-phase2-native-windows-recheck.log`）。异常原因未定位，不能据后一次成功宣称窗口稳定性已全部验收。 |
| 单窗口压力探针 | 原生 `QGuiApplication`＋单个 `QQuickWindow`、隔离真实数据：20 轮、100 次路由切换、520 次鼠标操作、200 次 Space/Return、DPR 1.5、0 QML 警告。`output/qml-phase2-native-navigation-probe.json`。这项通过不能证明多窗口测试进程中的崩溃已解决。 |
| 窗口夹具复查 | 测试夹具增加 last-window 退出策略保存/恢复、native exposed 等待和 setup 失败时的 finally 排空；组合回归仍在 `currentPageChanged.emit()` 导航调用链出现 access violation，使用的是 offscreen 平台（`output/qml-phase2-window-harness-regression.log`）。随后将窗口 8 项与 bridge/runtime/self_test 14 项分进程执行，分别通过（`output/qml-phase2-window-harness-isolated.log`、`output/qml-phase2-bridge-shutdown-harness.log`）。不能将这一调整写成已修复根因。 |
| 最后一次定向回归 | 修正测试滚动定位后，bridge/runtime/self_test/window/workspace 同进程 **22 passed**（`output/qml-phase2-window-harness-combined-final.log`），native 两个窗口测试模块 **8 passed**（`output/qml-phase2-native-window-harness-final.log`）。两份失败日志继续保留。 |
| 源码自检 | `QQuickWindow`、5 个主路由、4 个学习工作区、76 个实际内置模板、29 个资源、0 QML 警告。`output/qml-phase2-source-self-test.json`。新配置无学习记录及 OJ 题目，空状态来自实际数据库。 |
| 取消/退出 | 背景任务阻塞时 GUI 定时器仍运行，取消/关闭不接收过期结果；自检超时及 engine 失败排空工作线程后才删除临时目录。全量包含 bridge/runtime/self_test 相应回归。验证脚本强制结束超时进程后也等待进程退出再清理。 |
| 冻结目录 | PyInstaller 全新 work/dist 目录构建成功，`tools/verify_release.ps1 -RunSelfTest` **通过**：2020 files、223652049 bytes、版本 0.1.1；同时验证旧入口依赖与 QML 全部工作区。`output/qml-phase2-frozen-verification.log`。 |
| 资源一致性 | **29/29 SHA256 相同**。`output/qml-phase2-resource-hashes.json`。发布输入仍为明确白名单，没有个人数据、测试 fixture 或整个项目递归打包。 |

首次全量曾发现两处新增路径参数影响旧的默认调用形状，已修复并通过上述最终复测；真实窗口联调还修复了 QML 数字到 Python 整数的严格转换、自定义目录学科解析、刷新保留学科范围、增强异常文本脱敏、首次题库初始化，以及动态模板提示词参数不一致。现有推断与评分算法保持共享，测试没有用静态演示替代真实服务。

另一次 native 预算反馈测试失败已确认为点击定位问题：按钮顶部位于窗口 y=819、按钮高 44，而窗口高 820；`QSignalSpy` 证实实际 `clicked` 为 0（`output/qml-phase2-native-budget-click-diagnostic.log`）。测试现在等待 Repeater 刷新后的实时滚动几何，确认整个按钮进入视口再点击，所有工作区按钮都断言真实点击信号。这个修正没有改变生产业务或 QML 控件，也不能解释 access violation。

截图位于 `output/qml-phase2/{scale-1,scale-1.5,scale-2,native-windows}/`。已查看计划、记录与 PDF 工作区截图，修正默认 TabBar 配色及布局绑定循环；真实文字、焦点边框、表单、可滚动提示词与独立装饰层可见。`output/qml-phase2-image-metadata.json` 记录初始逻辑 1280×820 分别生成 1280×820、1920×1230、2560×1640 像素图，原生窗口为 1920×1230。截图数据仅为隔离测试资料。

本轮 QA 冻结目录为 `output/qml-phase2-dist/LearningModel/`，保留阶段一构建，不覆盖原发布产物。启动 `LearningModel.exe --qml` 可进入该 QML 窗口，默认 `LearningModel.exe` 仍进入 Widgets。

可重复执行：

```powershell
& .\.venv-qml\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
& .\.venv-qml\Scripts\python.exe -B -m study_app.quick --self-test output\qml-phase2-source-self-test.json
& .\.venv-qml\Scripts\python.exe -m study_app.quick
# 原 Widgets 对照入口
& .\.venv-qml\Scripts\python.exe run_study_app.pyw
```

## 尚未迁移与验证限制

- Windows 宿主下的 Qt Quick 自动化进程曾出现 native 与 offscreen AV。后续实际转储、最小复现及源码核对定位到实例动态元对象地址重用造成的 QML 属性缓存错位，已通过静态槽声明消除该机制；详见末节的失败对照与修复后验证。验证环境为 CPython 3.12.14 / PySide6 6.11.1；正式 3.14 构建仍需另验。
- 学习助理、建议审批/执行/撤销、知识图谱与预警操作仍为阶段三；设置、备份、学科引入/可恢复移除/恢复仍为阶段四。现有安全流程保留在 Widgets 与原服务中，未重造或绕过其备份、身份、预览、复核与事务约束。
- 考试范围编辑器、计划页旧入口内的其他快捷动作以及历史记录详细编辑仍需逐项对照原界面清单；当前工作区提供上述已列出的用例，不能以菜单路由可见宣称所有旧功能已迁移。
- 增强模型和真实 OCR 依赖未做外部端到端验收；桌面 PDF 的执行/失败/取消使用 fake executor 验证协议，真实登录、发送、下载、外部应用焦点和系统文档查看器需在具备能力的环境另验。
- 冻结目录只作为本地 QA 构建；正式 Python 3.14.5 构建、安装/升级/卸载、托盘、常规入口切换和完整发布验收仍为阶段五。本轮没有创建或发布安装包。

Qt API 核对依据：[FileDialog](https://doc.qt.io/qt-6/qml-qtquick-dialogs-filedialog.html) 的 OpenFiles/selectedFiles，[ComboBox](https://doc.qt.io/qt-6/qml-qtquick-controls-combobox.html) 的 textRole/valueRole；[QGuiApplication](https://doc.qt.io/qt-6/qguiapplication.html#quitOnLastWindowClosed-prop) 的最后窗口退出策略，以及 [QTest](https://doc.qt.io/qt-6/qtest.html) 的窗口 exposed 等待。

## 用户复核后的写入修正

详细计划确认以 `create_study_plan()` 正常返回为提交点，返回稳定 `planId`。提交前及提交调用内的异常仍是失败；提交后的缓存清理、回读与 DTO 生成失败返回 `saved: true` 和非空安全文案 `refreshWarning`，不重试主写入。预算确认、进度勾选、作业反馈、阶段修改和归档也明确各自的提交边界。

`add_learning_record()` 的记录、题目与关联计划完成状态在原有一个事务中提交。如果后续题库处理抛错，底层以 `LearningRecordPostCommitError(record_id)` 明确通知已提交的记录 ID。计划反馈和记录适配器可以直接报告已保存，不依赖另一次可能失败的查询；同时注入后处理与回读失败仍不会误报提交失败。已有作业反馈仍拒绝重复提交，记录预览 token 仍一次性消费。异常细节和私密路径不进入 QML。

工作区 bridge 保留服务的 `refreshWarning`：即使外层 `_load()` 刷新成功，也显示“已保存；后续处理或刷新失败，请手动刷新，勿重复提交”，并按成功提交触发首页刷新、清除复核 token；不会因为外层读取成功而掩盖服务维护失败。

预算确认的可用时间、考试日期、任务估时、旧计划归档、新计划及条目共用一个 `BEGIN IMMEDIATE` 事务，预览版本与指纹校验也在该写锁内完成。仓储新增可选的 `connection` 参数；借用连接时不提交、不关闭，由确认服务统一提交或回滚，旧 Widgets 的默认调用保持原有行为。

故障注入覆盖每个写入步骤完成后失败、条目插入时 SQL trigger 失败，以及所有 SQL 已执行后因 deferred foreign key 在最终 COMMIT 时失败；逐表比较确认输入、旧活动计划和条目均恢复原值。另测提交后回读失败仍返回已保存、预览不能重放，以及提交前失败没有记录或完成状态。

复习模式批量修改仍包含多个业务提交，不承诺整个批次原子性；所有名称、布尔值与当前状态先校验，发生部分成功时返回实际 `changed` 列表及警告，不能将它描述成所有请求均已成功。

修正后的最终全量：**1219 passed、279 subtests passed**，3 条既有 `datetime.utcnow()` 弃用警告，329.76 秒。日志 `output/qml-phase2-write-fixes-deduplicated-full-regression.log`。测试夹具通过模块引用复用，避免 unittest 类被另一个测试模块再次收集；此前带重复收集的定向与全量日志只作历史记录，不作为最终数量依据。

最后独立执行计划、预算事务、记录/OJ 与工作区 bridge 模块，**57 passed**（`output/qml-phase2-write-fault-injection-deduplicated-final.log`）。覆盖缓存清理失败、回读失败、提交后题库处理与回读同时失败、提交前失败、重复提交拒绝，以及每个预算写入阶段和最终 COMMIT 的回滚。

最终窗口回归：offscreen **8 passed**（`output/qml-phase2-write-fixes-offscreen-window-final.log`），native 在子进程异常捕获器下 **8 passed**（`output/qml-phase2-write-fixes-native-window-final.child.log`）。native 先一轮仍遇到预算反馈控件未点击（`output/qml-phase2-write-fixes-native-window.log`）：滚动后的额外事件等待会使位置再次变化。测试现在先等待布局，再重算并同时验证控件完整处于 Flickable 与实际窗口内；点击前后位置和真实信号均记录/断言。这个测试修正仍不能解释 AV。

最终源码自检：29 个资源、5 个主路由、4 个工作区、76 个模板、0 QML 警告（`output/qml-phase2-write-fixes-source-self-test.json`）。从最终生产源码重新生成独立 QA 目录 `output/qml-phase2-write-fixes-final-dist/LearningModel/`，PyInstaller 构建及 `verify_release.ps1 -RunSelfTest` **通过**：2020 files、223656420 bytes、版本 0.1.1（`output/qml-phase2-write-fixes-frozen-verification.log`）。这是本地 QA 冻结目录，原阶段二目录与正式安装包均未覆盖，也没有发布新安装包。

## 窗口崩溃诊断历史

历史 native 与 offscreen 的真实 AV 日志保留。发射 `currentPageChanged` 时进入 Qt/QML 同步绑定与 Loader 页面替换，但现有 Python traceback 没有 native C++ 栈。静态审查未发现明确的悬空导航按钮、已销毁页面接收者或后台回调路径，不能据此判定 Qt 本身有错。

新增本任务专用的诊断材料位于 `output/`，没有加入发布资源：

- `qml_phase2_lifecycle_matrix_probe.py` 在一个 QApplication 中顺序创建/销毁 8 个真实 engine，分别运行带/不带临时 QQmlExpression 的对照及单 engine 对照。每个 engine 使用独立真实测试数据库，关闭 worker 后才删除 engine，再排空 DeferredDelete 与 Python GC。8-engine 的表达式对照完成 40 次路由点击、120 次重复鼠标、120 次键盘、各 1200 次颜色与辅助功能求值，均未触发 AV；这只能说明这些变量单独不足以在本轮复现。
- `qml_debug_child.py` 通过 Windows 子进程调试接口，只对本次启动的测试进程捕获首次/第二次机会异常、模块偏移和 minidump，不修改全局 WER 配置。独立 C helper 的真实 `0xC0000005` 验证已捕获两个机会、模块偏移及 21945 字节 dump（`qml-phase2-debugger-c-av-selfcheck.*`）；这个受控 dump 只验证捕获器。
- 受调试的 native 路由测试 `-vv`/`-q` 各 2 项通过；offscreen 的 bridge/runtime/self_test/window/workspace 前缀 `-vv`/`-q` 各 26 项通过；8-engine 表达式探针也在调试器下通过。对应 `qml-phase2-debugger-native-route*-20261004.*`、`qml-phase2-debugger-offscreen-prefix*-20261004.*`、`qml-phase2-debugger-offscreen-eight-engine-20261004.*`。这些早期运行没有生成真实 Qt 故障 dump；后续实际捕获见下节。
- 原始失败时段为 2026-10-04 本机时间 14:53:02–14:53:21 与 15:08:52–15:09:03。LocalAppData 的 WER 目录不存在；CrashDumps 默认访问受限，随后在这两个时段范围内的只读元数据检索执行成功，没有找到匹配的 `python*.dmp`。没有读取其他应用转储或更改系统配置。

可在项目目录中以捕获器执行原始组合，数据由各测试隔离；如果再现，将留下真实异常地址、模块偏移与转储以供符号化。调试器可能改变时序，通过不能替代根因与修复证据。

```powershell
$env:QT_QPA_PLATFORM = 'windows' # 另以 offscreen 对照
& .\.venv-qml\Scripts\python.exe -B output\qml_debug_child.py --cwd . --log output\qml-av-next.jsonl --dump output\qml-av-next.dmp --timeout 180 -- .venv-qml\Scripts\python.exe -B -u -m pytest -q -p no:cacheprovider tests/test_qml_bridge.py tests/test_qml_runtime.py tests/test_qml_self_test.py tests/test_qml_window.py tests/test_qml_workspace_bridge.py tests/test_qml_workspace_window.py
```

**当时结论（取得完整转储栈与定向复现前）：两项写入问题已有源码修正与故障注入验证；窗口崩溃根因未知，仍阻塞完整验收。** 后续复测通过本身不能改变此结论；下面记录新增的根因和修复证据。

### 窗口 AV 根因与修复（2026-10-04）

已在 Windows 原生窗口压力条件下再次复现真实 AV，而非合成自检。持久化探针 `tools/qml_diagnostics/run_window_probe.py` 复用原始窗口夹具与原始测试函数，加入每次鼠标点击前的 Qt/Python GC；默认使用原阶段一隔离资料，不更换渲染后端、不替换页面或断言。第 0 轮四个用例通过，第 1 轮前两个用例通过，随后重复路由用例（展开状态）崩溃。该压力条件额外加入 GC，不能描述成完全未经改变的历史 pytest 运行。

证据位于 `output/qml-native-gc-diagnostics-20261004.log` 和同名目录：真实进程退出 `-1073741819`（`0xC0000005`）；`access-violation.dmp` 为 1126123 字节，附 `.meta.txt`、初始 `run.json` 和源码 SHA256。Python 调用链再次落在 `currentPageChanged.emit()` → 原始 `click()` → 重复导航测试。故障为写访问，地址 `QtQuickControls2.pyd + 0x871d`，在 Python 字典转 `QVariantMap` 的旧目标内容释放处。使用 Windows 自带 `dbgeng.dll` 的只读工具 `inspect_dump.cpp` 成功恢复属性读取 → QML 绑定求值 → Loader 构建的 C++ 调用链（`output/qml-dbgeng-real-dump-final-inspection-20261004.log`）。初期手工分析误用了 ThreadList 中 `Rva=0` 的无字节栈描述符，得到的 GPU 返回地址无效；正确 MemoryList 栈与 DbgEng 一致。导出符号附近的大偏移只作地址标记，不能当作精确函数名。

实际加载的 Qt DLL 均为 6.11.1.0，PySide/Shiboken 来自同一 `.venv-qml`；没有发现不同 Qt 版本混装。转储的关键是类型错位：Qt6Qml 的布尔读取分支只执行 `mov byte ptr [rsp+48h],0`，读取后取一个字节；PySide 却把同一个地址交给了映射转换函数。布尔槽剩余字节不是映射指针，转换函数对它做引用计数释放而崩溃。问题不是正常 `QVariantMap` 读取忘了构造局部对象。早期单纯 QObject/QJSEngine 对照未复现；`QVariant` / 同导入顺序的 `QVariantMap` 对照均通过，故没有修改生产 DTO 类型来掩盖错位。

根因是两个 `_poll` 未声明 Qt 槽：连接定时器时 PySide 为每个实例追加动态元对象层。该 builder 产物只有 `AllocatedMetaObject` 标记，不带 `DynamicMetaObject`；Qt 6.11.1 的全局 QML 属性缓存以 `QMetaObject*` 为键，缓存它的属性布局。实例销毁会释放 builder，但不会同步清除这条缓存。下一实例重用同一地址时可能拿到旧类布局：`DashboardBridge.busy` 是 index 4 的 bool，而 `WorkspaceBridge.practiceData` 是 index 4 的 `QVariantMap`。QML 按旧布局准备布尔槽，PySide 按新布局调用映射 getter。Qt 有引用计数条件下的缓存清理，不能笼统说缓存永不清理；本机复现证明这里存在地址重用与缓存命中窗口。

已在两个类的 `_poll` 上补 `@Slot()`，使方法在类创建时登记，不再生成这层实例元对象。两个元对象契约回归在修复前失败（实例 methodCount 14、静态 13），修复后通过并完成实际后台刷新/加载。再增加子进程 QML 缓存回归，循环构建/销毁真实的两类桥接对象，断言 `target.busy` 在 false/true 间正确读取，且没有任何映射 getter 被调用。测试默认使用安全 sentinel 避免旧 bug 将映射写入布尔槽；不读取业务资料。桥接、工作区、自检及运行时 **21 passed**（`output/qml-static-meta-cache-regression-20261004.log`）。[Qt 槽声明说明](https://doc.qt.io/qtforpython-6.8/tutorials/basictutorial/signals_and_slots.html#the-slot-class) 也明确建议将信号连接的方法声明为槽。

定向对照保存在 `tools/qml_diagnostics/metaobject_cache_probe.py`。动态控制仅在诊断子进程恢复旧 `_poll` 连接；其余属性声明、名称、类型、索引来自实际源码，map getter 用安全 sentinel 记录错读。两次分别在第 15 / 5 轮捕获 `target.busy` 错调 `workspace_bridge.practiceData`，同时元对象地址与旧 DashboardBridge 相同；静态修复完成 400 轮、800 个实例，无交叉地址重用和错误 getter。允许真正返回 dict 的独立故障控制又复现了 native heap corruption `0xC0000374` 和一次 AV `0xC0000005`（`output/qml-meta-cache-actual-av-control{,-second}-20261004.log`），后者留有真实 dump；故障点在 Qt6Core，不能写成与原窗口 dump 相同的地址。静态 dict 对照同样完成 400 轮而没有错误 getter或崩溃。它验证错位机制能造成 native 故障，而非只有诊断警告。

源码核对：[Qt 全局属性缓存](https://github.com/qt/qtdeclarative/blob/v6.11.1/src/qml/qml/qqmlmetatypedata.cpp)、[Qt 布尔/映射读取](https://github.com/qt/qtdeclarative/blob/v6.11.1/src/qml/jsruntime/qv4qobjectwrapper.cpp)、[PySide 实例槽 builder](https://github.com/pyside/pyside-setup/blob/v6.11.1/sources/pyside6/libpyside/signalmanager.cpp)、[PySide builder 回收](https://github.com/pyside/pyside-setup/blob/v6.11.1/sources/pyside6/libpyside/dynamicqmetaobject.cpp)。真实转储不包含可直接识别 Python getter 名的完整入口参数，因此 getter 名来自最小复现，转储独立证明布尔槽/映射转换不匹配。

诊断源文件已保存在 `tools/qml_diagnostics/`；C VEH 在当前测试进程内捕获异常后继续原有异常处理，不吞掉 AV。栈展开变体保存原上下文、使用系统 unwind 元数据，并比较转储前后的返回槽；合成 C AV 仅用于验证捕获器。独立子进程调试器的超时路径会处理退出调试事件并等待其子进程退出，再返回给外层清理。所有探针使用隔离资料，每轮输出新目录；转储和工具均未加入发布包。

槽声明修改后的复测：

| 检查 | 本轮结果 |
| --- | --- |
| native、原函数、每次鼠标前 Qt/Python GC | 30 轮、**120 个窗口用例通过**，捕获 AV 0，退出 0；`output/qml-native-static-slot-gc-20261004.log`，同名目录 `run.json` 含源码哈希。 |
| offscreen 原始组合及新增两项回归 | **28 passed**，捕获 AV 0，退出 0；`output/qml-offscreen-static-slot-original-20261004.log`。 |
| native 原始组合及新增两项回归 | **28 passed**，捕获 AV 0，退出 0；`output/qml-native-static-slot-original-20261004.log`。 |

窗口夹具完成工作线程排空与临时数据清理，包含完整真实 QML 窗口。没有改用 Qt Widgets 嵌入、隐藏原始断言或调整后端来规避异常。通过结果结合真实转储、旧实现定向失败、修复后对照及源码生命周期证据，用于确认本机已复现 AV 机制的修复；不是只凭一轮未复现下结论。

匹配符号限制仍保留：当前 `QtQuickControls2.pyd` 没有 CodeView/RSDS/PDB identity，本机没有其 PDB 或 WinDbg/cdb CLI。系统 DbgEng 和现有二进制 unwind 元数据足以恢复上述栈与读取指令；无法可靠恢复全部局部变量或给未导出函数精确命名。这里没有将最邻近的导出标签当作源函数，源码通过实际 6.11.1 版本核对。

最终全量回归：**1222 passed、279 subtests passed**，3 条既有 `datetime.utcnow()` 弃用警告，441.64 秒。`output/qml-window-av-fix-full-regression-20261004.log`。所有生产业务改动与之前的写入修复均在本轮覆盖内。

DPI 复核：offscreen `QT_SCALE_FACTOR=1.5/2` 各 **8 passed**（`output/qml-window-av-fix-scale-{1.5,2}-20261004.log`），包含菜单、重复激活高亮、页签、窗口缩放、键盘和实际数据刷新。1 倍由全量及 offscreen 原始组合覆盖；原生 Windows 由上述组合及压力验证覆盖。诊断工具最后补充的 schema/故障控制选项另以缓存回归 **1 passed**（`output/qml-meta-cache-final-test-20261004.log`）核对；生产源码在全量后没有变动。

从修复后的源码生成新 QA 冻结目录 `output/qml-window-av-fix-dist-20261004/LearningModel/`，构建成功，`tools/verify_release.ps1 -RunSelfTest` **通过**：2020 files、223656431 bytes、版本 0.1.1。日志为 `output/qml-window-av-fix-build-20261004.log` / `output/qml-window-av-fix-frozen-verification-20261004.log`。旧 QA/发布目录没有覆盖；未创建或发布安装包。诊断脚本及 minidump 没有加入冻结资源。

最终源码启动自检也通过：完整 QQuickWindow、29 资源、5 个主路由、4 个工作区、76 个模板、0 QML 警告（`output/qml-window-av-fix-source-self-test-20261004.json`）。使用自动创建的隔离数据；旧 Widgets 入口继续保留。

**当前结论：已定位并修正本机 Qt/PySide 6.11.1 上可复现的动态元对象属性缓存错位机制。** 不再把该项记为“仅准备诊断、复测未复现”；原失败日志和转储全部保留。阶段二的真实外部 AI/OCR、桌面 PDF 与正式 3.14/安装包发布验收仍按既有范围另验。
