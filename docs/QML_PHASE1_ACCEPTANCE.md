# 和纸学习室 QML 第一阶段实现与验收

本文保存阶段一的历史验收基线。当前学习计划、记录、OJ 与 PDF 的增量和剩余边界见 [阶段二验收](QML_PHASE2_ACCEPTANCE.md)。

日期：2026-10-04（Asia/Shanghai）。基线：`main f8a4d4a`，开始时工作区干净；实施分支：`codex/qml-washi-phase1`。原 `dist/` 与既有发布产物未覆盖。本次新增验证环境与产物均在 `.venv-qml/`、`output/qml-*` 中，不提交用户数据、虚拟环境或二进制。

## 阶段状态

| 阶段 | 状态 | 可核实的完成范围／验收门槛 |
| --- | --- | --- |
| 0 审查与方案 | 完成 | 入口、五个主页面、未路由叶子界面、弹窗、后台任务、耦合、安全流程、目标目录、迁移顺序与验收矩阵见 `QML_MIGRATION_PLAN.md`。 |
| 1 框架与只读首页 | 完成 | 完整 ApplicationWindow/QQuickWindow、统一主题/组件、菜单与五路由、真实首页、异步刷新、隔离数据测试、源码/冻结自检、资源白名单。 |
| 2 计划、记录、OJ、PDF | 尚未迁移 | 后续须完成预算预览确认、保存与反馈、识别后复核保存、OJ 映射及提交、PDF 输出的隔离端到端验收。 |
| 3 助理、图谱、预警 | 尚未迁移 | 后续须验证本地/增强模式、建议预览/确认/撤销、节点选择/缩放、预警处理及审计、关闭与过期结果。 |
| 4 设置、学科生命周期 | 尚未迁移 | 后续须完整复用预览、全名确认、备份、revision/fingerprint 检查、原子事务、可恢复移除/恢复身份与投影重试。 |
| 5 默认入口与发布切换 | 尚未执行 | 基础资源与冻结自检已提前验证；仍需正式 Python 3.14.5 构建、安装生命周期、平台/许可材料与完整业务验收，再决定默认入口切换。 |

阶段一只读首页未迁移原首页的 LLM 开关、AI 每日总结、托盘/悬浮图标，也未启动每周网络采集。其余四页的真实摘要和菜单可达性只证明路由框架可用，不能作为对应业务页面迁移完成的依据。没有 QWidget 嵌入、透明按钮覆盖概念整图或静态示例业务数据。

## 已落地架构

```text
run_study_app.pyw                 # 默认仍是 Widgets；--qml 进入完整 Quick 窗口
study_app/quick/
  __main__.py                    # 独立入口、数据根参数、自动隔离的源码自检
  runtime.py                     # QGuiApplication、引擎、单实例与退出协调
  dashboard_service.py           # Qt 无关的真实服务 → 展示 DTO
  bridge.py                      # QObject 属性/槽、后台任务、刷新合并与错误状态
  resources.py                   # 源码/冻结共用的 20 项明确资源清单
  self_test.py                   # 实际加载窗口、服务与每条路由
  qml/
    Main.qml, Theme.qml, qmldir
    components/                  # 按钮、导航、图标、卡片、状态徽章
    pages/HomePage.qml
    pages/MigrationPage.qml       # 真实摘要＋未迁移声明
    assets/                      # 和纸纤维、樱花、竹叶及七项图标，独立 SVG
study_app/ui/                    # 原 Widgets 保留
study_app/core/, data/, ai/       # 原服务与数据规则保留
```

`DashboardService` 调用既有 `core.dashboard.load_dashboard_state()`、`core.active_subjects` 与 `data.plan_repository`；QML 只调用 `DashboardBridge.refresh/navigate/toggleSidebar`。首页 DTO 包含日期窗口、来源、刷新时刻、活动/归档/记录计数、学科摘要、推荐、风险、已确认今日计划和活动学科最近记录，不包含整个模型、原始记录对象、SQL、文件路径、数据库连接或凭据。

学科摘要区分近期周期评分（无记录显示“暂无记录”）、已覆盖掌握估计、覆盖知识点数量和全课程模型估计。已确认预算计划、旧格式计划、未确认推荐与空计划分别呈现；读取计划失败只降级计划卡片。归档学科从活动卡片、推荐、风险和最近活动中排除；历史总记录计数仍保留其历史。

首次目录/种子库初始化与每次数据计算都在工作线程执行；忙时刷新合并成一次后续读取。目录修订在计算前后校验，失败保留上一快照并显示脱敏错误。关闭首先停止 GUI 轮询、取消结果接收；GUI 事件循环退出后等待实际运行工作结束，再释放与 Widgets 共用的单实例锁。`AsyncTaskService.wait_for_shutdown()` 是新增的进程清理接口，不在活动 GUI 中等待。

## 启动与回退

以下命令在项目根目录执行，当前工作环境已安装锁定依赖：

```powershell
# 读取与原应用相同的用户数据，QML 首页只读；首次启动仍沿用受支持的初始化
& .\.venv-qml\Scripts\python.exe -m study_app.quick
# 自定义数据根须在服务导入前设置
& .\.venv-qml\Scripts\python.exe -m study_app.quick --data-root 'C:\path\to\test-profile'
# 自动创建临时数据根和空旧数据根，不访问默认用户库
& .\.venv-qml\Scripts\python.exe -m study_app.quick --self-test output\qml-self-test.json
# 原 Widgets 对照入口，全部原业务与学科安全操作继续可用
& .\.venv-qml\Scripts\python.exe run_study_app.pyw
# 独立冻结验证目录的 Quick 入口；不带 --qml 则仍运行 Widgets
& .\output\qml-phase1-dist\LearningModel\LearningModel.exe --qml
```

Widgets 与 QML 使用同一个单实例保护；已有一个入口运行时，第二个入口会尝试唤醒已有窗口。对照两种界面时先退出前者。

导航键盘操作：Tab/Shift+Tab 移动焦点，Space/Enter 激活按钮；Ctrl+B 开合菜单；Ctrl+1…5 切换主路由；F5 刷新。菜单按钮在两种状态保持相同位置，图标使用可访问名称与悬停提示。控件使用逻辑像素、换行与滚动布局，正文颜色已提高对比度；系统字体由 Qt 使用，无外部字体被打包。

## 本次验证

测试使用 CPython 3.12.14 x64、PySide6 Essentials/Qt 6.11.1 及 `requirements-build.lock.txt` 中的其余锁定依赖。全局 Python 与真实用户目录没有被用于测试数据。

- 既有业务全量加最初服务/适配层测试：1133 passed、279 subtests passed，3 条既有 `datetime.utcnow()` 弃用警告；该轮发生在最后窗口与异常边界测试补齐前。
- 新增服务/桥接测试：12 passed。覆盖真实 SQLite 刷新、归档过滤、预算/旧格式计划、计划读取失败降级、诊断脱敏、目录修订漂移、初始化一次、慢读取时 GUI timer 响应、刷新合并、提交失败与重试、关闭防止再次提交。
- 窗口＋关闭协调＋通用异步任务相关测试：25 passed。窗口测试实际点击菜单/五项导航、检查首收起和十次快速反转、验证 Tab/Space/Enter/快捷键及 F5 后真实记录显示；退出协调测试验证初始化未结束时不释放单实例锁。
- 离屏 100%/150%/200%：每个独立进程的窗口测试均 2 passed，覆盖 800×600、1024×768、1600×1000 逻辑尺寸；确认像素截图尺寸与 devicePixelRatio 相符。
- Windows 原生平台（当前屏幕 DPR 1.5）窗口测试：2 passed；使用系统字体，真实窗口点击、键盘、缩放与刷新通过。其他物理显示器之间的 DPI 切换尚未实测。
- 源码自检：实际 QQuickWindow、SQLite、20 项资源、五路由、0 QML warnings。空配置自检与非空真实测试库渲染分别验证。
- 冻结目录：`verify_release.ps1 -DistDirectory output\qml-phase1-dist\LearningModel -RunSelfTest` 通过；2011 个文件、223476407 字节，资源白名单、版本、干净数据初始化与完整 QML 探针通过。20 项 QML 资源 SHA256 与源码逐项一致。产物是本地 QA 目录，不是新安装包或公开发行签核。

全量回归：**1142 passed、279 subtests passed、3 条既有弃用警告**。已包含窗口交互、Enter 激活、数据降级/脱敏、任务提交失败、目录修订漂移与初始化关闭协调测试；日志为 `output/qml-final-pytest.log`。

随后截图复核发现复展开时导航图标与文字重叠，已改为明确位置和固定尺寸；800px 小窗口的统计卡片改为同排紧凑布局，并补充图标/文字间距及卡片横向边界断言。最后的 15 项 QML 服务/桥接/窗口/关闭测试通过，150% 与 200% 窗口测试各 2 passed；卡片边界断言另在 200% 的三种尺寸验证通过。冻结目录也随此版 QML 重建并再次自检。此轮未改动 Python 业务规则，新增静态盘点工具已实际运行。

### 打包修复与可复现命令

首次冻结自检失败：依赖扫描从 Codex 的 Poppler PATH 收集了 `icuuc.dll`，该文件只有 ICU 78 的版本后缀符号；本次 QtCore 需要 Windows ICU 的无后缀符号。已在 spec 中将构建进程 PATH 限制为选定 Python 与 Windows 目录，包内不再包含该错误 DLL；系统环境没有被改动。

```powershell
& .\.venv-qml\Scripts\python.exe -m PyInstaller --noconfirm --distpath output\qml-phase1-dist --workpath output\qml-phase1-clean-build packaging\LearningModel.spec
& .\tools\verify_release.ps1 -DistDirectory output\qml-phase1-dist\LearningModel -RunSelfTest
& .\.venv-qml\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

首次使用新的 PATH 约束时采用新的 workpath，避免旧 Analysis 缓存复用。QML 文件通过受控数据白名单保留源文件并打包，QtQml/Quick 的 PyInstaller hooks 收集所需 Qt import 插件；没有整个项目递归打包，也没有 QRC 生成文件。

技术依据：[Qt 的 Python/QML 初始属性教程](https://doc.qt.io/qtforpython-6/tutorials/qmlapp/qmlapplication.html)、[QQuickStyle 加载前设置](https://doc.qt.io/qtforpython-6/PySide6/QtQuickControls2/QQuickStyle.html)、[Windows ICU 系统接口](https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-)。DLL 冲突结论还以本机 PE 导入/导出与重建后自检结果复核。

## 产物与剩余验收

- `output/qml-phase1/scale-{1,1.5,2}/`：隔离真实服务数据的展开、收起、刷新、未迁移页及三种窗口尺寸截图。
- `output/qml-phase1/native-scale-1/`：Windows 原生平台截图；环境倍率 1 叠加屏幕原生 DPR 1.5。
- `output/qml-source-self-test-root.json`：源码隔离启动报告。
- `output/qml-phase1-pyinstaller-clean.log`、`qml-phase1-frozen-verification.log`、`qml-final-pytest.log`：重建、冻结校验和最终回归日志。
- `output/qml-phase1-dist/LearningModel/`：独立 QA 冻结目录。

尚未宣称通过：正式 Python 3.14.5 的新版安装包、Windows 10、跨物理屏幕 DPI 切换、真实外部 AI/OCR、后续页面写操作和安装/升级/卸载生命周期。所有学科引入、可恢复移除与恢复操作仍使用未改写的原服务与 Widgets 安全界面。

## 复审修复（2026-10-04）

- 当前路由重复激活：导航按钮不再自行切换 `checked`，高亮、强调线、字体和无障碍选中状态统一绑定只读路由选中属性。测试实际检查背景高亮与 `Accessible.selected`，覆盖全部五路由、展开/收起、连续鼠标点击、Space、Return 与 Enter；修复前两个窗口回归均失败。
- 自检异常退出：`run_probe()` 的正常、超时和启动失败路径统一关闭桥接层并等待后台任务结束，然后销毁窗口/引擎，最后才返回外层临时目录上下文。超时回归让工作线程持有临时文件，确认返回前线程完成写入并关闭文件、随后目录清理成功；修复前该回归失败。引擎创建失败也验证关闭与等待，测试窗口夹具同步采用退出等待。
- QML 服务、桥接、窗口、正常入口关闭和自检相关测试：19 passed；通用异步任务与自检关闭测试另一次运行 12 passed（含两个重叠自检测试）。新回归共四项。
- 离屏 DPI 100%/150%/200% 窗口测试各 4 passed；Windows 原生窗口测试 4 passed。原有菜单反转、键盘路由、窗口缩放和真实数据刷新断言继续通过。
- 源码隔离自检通过：完整 QQuickWindow、SQLite、20 项资源、五路由、0 QML warnings；报告 `output/qml-review-source-self-test.json`。相关测试日志为 `output/qml-review-*.log`。
- 本地 QA 冻结目录已随修复重建，`verify_release.ps1 -RunSelfTest` 再次通过：2011 个文件、223476642 字节、版本 0.1.1；20 项 QML 资源 SHA256 与源码一致。构建和校验结果更新在 `output/qml-phase1-pyinstaller-clean.log`、`output/qml-phase1-frozen-verification.log`。

本轮仅修复上述导航与自检退出边界，未重跑既有业务全量测试；后续业务页和正式发布验收的迁移状态仍按前表记录。
