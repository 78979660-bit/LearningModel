# 和纸概念图视觉重构

日期：2026-10-06。分支：`codex/qml-washi-phase1`。

## 2026-10-08 字体统一

以用户选定的首页概念图为字体气质参考，标题、导航、正文、按钮和表单统一为内置 Noto Serif SC。标题通过字号及字重建立层级，正文基准为 16 px，辅助文字由 12 px 提升到 13 px；下拉箭头作为符号仍单独处理。应用级默认字体也统一，避免未显式指定字体的 Qt Quick 控件使用系统无衬线体。

字体来自 [Google Fonts 官方目录](https://github.com/google/fonts/tree/8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5/ofl/notoserifsc)，固定提交 `8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5`。原始 `NotoSerifSC[wght].ttf` 未修改，OFL.txt 随程序分发；字体仅注册在应用进程内，不安装到 Windows 系统字体目录。此字体是对概念图的接近实现，不认定生成图具有可准确识别的原始字体文件。

原生界面与构建测试首轮 27 通过，1 项旧扩展名断言失败；更新字体与许可证类型断言后该项通过。新增字形测试通过，验证窗口及输入框实际解析到 Noto Serif SC，且包含中文、拉丁字母和数字。最终截图 `output/qml-font-preview-20261008/` 覆盖五页及收起菜单，报告无 QML warning。测试日志分别为 `qml-font-tests-20261008.log`、`qml-font-fix-20261008.log`、`qml-font-contract-final-20261008.log`；中间失败日志保留，不计为通过。

## 2026-10-07 纸质感精修

对照最终概念图组的 `04-和纸材质与配色基准.png` 与上一轮真实首页截图，调整共享纸面，覆盖五个主页面：

- 将稀疏长线改为短纤维为主的多尺度交织，增加低对比度细小纸浆颗粒，保留少量乳白长纤维。纸浆明暗控制在很低的强度，避免脏斑与粗糙纸板观感。
- 纹理固定为 960 × 960 的 SVG 栅格化尺寸再平铺，不随卡片宽高拉伸；侧栏、背景、纸卡和按钮共用同一素材。
- 纸卡使用轻微不规则边缘、接近表面的浅阴影，以及少量背景透色。文字本身不降低透明度，也不添加晕染。
- 多行输入区降低白底不透明度，使便笺底色与纤维透出；焦点描边、键盘操作和业务确认保持原有逻辑。
- 边缘 Canvas 仅在尺寸或纸面颜色改变时重绘；无持续动画定时器、实时模糊或新增着色器。尚未对这轮变化单独测量帧率，不据此宣称性能提升。

验证：Windows 原生窗口测试 `test_qml_window.py`、`test_qml_visual_routes.py` 最终 **13 项通过**（47.07 秒）。覆盖菜单、快捷键、路由、便笺预填和三个窗口尺寸；日志为 `output/qml-paper-tests-final-20261007.log`。五页及收起菜单的最终截图保存在 `output/qml-paper-polish-20261007/`，隔离数据运行、DPR 1.0、无外部请求、无 QML warning，后台任务正常退出。逐页检查了实际渲染截图；本轮没有额外进行 Computer Use 手动验收。

当前仍是源码精修，未重新构建安装包。材质更接近薄纸，但不将此记录作为与概念图完全一致的证明。

## 概念图对照重构（第二轮，源码）

本轮在此前 r4 的基础上继续对照最终概念图调整，以下历史 r4 安装器与冻结程序不包含本节变更。本轮没有发布或替换安装器。

- 首页采用约 60/40 分栏，今日安排纸面跨右侧进度和便笺两行；学科与知识点分两级显示，次要指标及每日总结收进可展开区域。
- 便笺正文可以带入学习记录表单，仍需预览、复核后保存；新增窗口回归覆盖“预填成功但未生成提交预览”的状态。
- 计划页以今日任务为主，预算置于右栏，考试估时和详细计划折叠；原记录、OJ、练习 PDF 入口保留。
- 助理的结果与输入归入同一纸面，右侧放上下文、策略和审计；窄窗口变为单列。没有伪造聊天记录或自动外发。
- 图谱节点按真实状态使用淡绿、樱粉、浅灰及藤紫，选择状态以外环标示；证据明细可展开，真实关系和键盘搜索缩放保留。
- 设置按 AI 服务、隐私与策略、数据维护分组，学科引入与可恢复移除维持原确认流程。输入字段保留可见标签。
- 共享主题调整纸纹、字号、按钮和侧栏。纸纹为可重建 SVG；樱花使用新透明 PNG 插画。页面保持真实 QML 控件，未用整张概念图覆盖交互界面。

概念图中的示例时间、知识路径、聊天与进度不作为真实数据填入；当前首页 DTO 没有的时间与路径不虚构。素材及布局按概念图重构，但不宣称像素级复刻。

### 樱花素材来源

`study_app/quick/qml/assets/sakura-corner.png` 使用内置 imagegen 生成，参考最终概念图组的四页总览；保留透明 alpha，并加入运行时和构建资源白名单。旧 `sakura-branch.svg` 保留作矢量素材，不是当前主窗口显示的樱花。未使用 CLI 或用户 API 密钥。

生成提示词：

> Use case: stylized-concept. Asset type: transparent upper-right corner sakura illustration for the Washi Study desktop UI. Generate a standalone illustration, NOT a UI screenshot. Match the cherry blossoms in the supplied reference: elegant Japanese anime background painting, very pale blush-pink and ivory translucent petals, soft diffuse raspberry-pink hearts with NO sharp stamens, subtly shaded overlapping petals with depth, muted mauve-brown natural curved woody branches and a few subdued lavender leaves. A branch enters from the top right, extends left and downward, flowers concentrated along upper/right edges with transparent negative space toward lower left. A few soft falling petals farther left. Calm, luminous, delicate, not photorealistic, not flat geometric five-petal symbols. Composition wide landscape approximately 2:1, sized for a header corner. Genuine transparent alpha background, no paper texture, no text, no UI, no frames, no drop shadow. Input image is style reference only; create only the sakura branch asset.

### 第二轮验证

完整回归首轮为 271 通过、3 原生专用项跳过；新增 PNG 导致旧素材数量断言失败（10 → 11），已更新断言并增加新资源名称检查，修正后的启动探针在原生回归中通过。

| 检查 | 结果 | 项目相对路径 |
| --- | --- | --- |
| Windows 原生窗口、业务确认、快捷键、菜单、快速记录与启动自检 | 40 通过，无 QML warning | `output/qml-concept-native-final.log` |
| 实测 DPR 1.25 / 1.5 / 2.0 的布局、菜单、导航、便笺和预算保存 | 每档 14 通过；倍率由独立 QGuiApplication 读取确认 | `output/qml-concept-verified-dpi-{125,150,200}.log`、`output/qml-concept-dpr-{125,150,200}.json` |
| Computer Use 实际操作 | 五页、菜单收放、图谱证据、设置分区、最大化/还原、正常退出通过；进程 exit=0 | `output/qml-concept-live-20261006/checks.json`、`report.json` |
| 最终完整截图 | 五页及收起菜单，使用隔离创建的三个学科与三条记录；无外部请求 | `output/qml-concept-final-20261006/` |

计数有重叠，不累加作为独立测试总数。原生首轮发现图谱测试固定等待 60ms 不足，改为最多 2 秒等待实际选中节点替换，保留原结果断言。高 DPI 时 Windows 可能将请求窗口限制为屏幕实际可用范围，响应式测试依据真实内容宽度判断单双栏。第一轮 `qml-concept-dpi-1.25/1.5/2.0` 使用的是环境变量倍率，会与系统 150% 相乘；最终上表已经按实测 DPR 校准，不能把旧文件名理解为真实 DPI。

实际操作还发现已确认任务的显示标签回落到“待安排”，已改为“已安排”，并在三档 DPI 的真实预算保存测试中验证。外发许可文字改成窄屏完整换行。Computer Use 验证是操作与视觉检查，没有用它宣称帧率或 profiler 结论。现存 `practice_spec.py` 的 Python `re.split` 弃用警告不属于这轮界面改动。

## 第一轮 r4 历史记录

参考包：`washi-ui-final-concepts-20261004-102734.zip`。首页展开/收起图用于统一导航与首页布局，四页总览用于二级页面分区，材质基准用于配色及纤维纹理。包内说明属于设计参考；示例学科、日期、预算、进度、对话、图谱关系和文案均不作为业务需求。

## 实现边界

- 继续使用独立 `QGuiApplication + QQuickWindow`，保留 Widgets 入口作为回归对照。
- 公共主题集中在 `Theme.qml`：暖米色背景、薰衣草导航、杏色/淡粉辅助面板、海军蓝文字、小圆角和键盘焦点边框。正文使用微软雅黑，标题优先使用系统宋体；缺失字体由 Qt 回退，不附带系统字体。
- `PaperCard` 提供可复用的纸面、内边距与低对比纤维。按钮、表单、导航、图谱节点仍是实际 QML 控件，可缩放并具备键盘入口。
- 和纸、樱花与竹叶是三个独立 SVG，不裁切概念图作整页背景。`tools/design_washi_assets.py` 可从标准库重建矢量素材；不依赖远端服务。
- 首页预算来自 Python DTO 的 `budgetMinutes / plannedMinutes / remainingMinutes`，仅对已确认预算提供数值。未确认预算保留未知状态，不写入示例数字。
- 快速记录进入已有记录表单，继续走解析预览、复核与确认；不引入直接保存、绕过审查的新路径。
- 计划、助理、知识图谱与设置保留各自真实数据及全部业务入口。概念图中没有现成后端能力的按钮、聊天、关系和统计不被虚构。
- 计划页范围卡跨完整内容宽度，预算与详细计划在双栏时顶部对齐；设置页不同高度的卡片也顶部对齐。窄窗口依次排列，不把较短卡片垂直居中到页面中部。
- QML 不读取数据库或密钥；服务、凭据范围、提交事务、提交后刷新提示、可恢复学科移除、后台线程收尾保持现有 Python 边界。

## 资源与验证

三个 SVG 的路径保持不变，继续由 `QML_RESOURCE_FILES` 的共享白名单进入运行时和 PyInstaller 资源。启动自检实际解码素材并检查 Qt 导入、真实隔离数据库与各工作区；新增素材无需新依赖。

`tools/qml_diagnostics/visual_review.py` 用全新隔离 SQLite 和非敏感材料打开完整五页窗口，也可通过 `--capture` 保存五页及收起菜单截图。它不会配置凭据或发起外部请求，退出前等待后台工作结束。此工具用于视觉/交互核验，不替代真实安装生命周期或跨机器验收。

## 最终验证结果

全部使用隔离数据库与非敏感材料，无外部请求、无真实密钥。正式构建环境为 CPython 3.14.5 x64、PySide6/Qt 6.11.1 和锁定依赖。

| 检查 | 结果 | 证据（项目相对路径） |
| --- | --- | --- |
| 全部 QML 服务、桥接、窗口及打包契约，含日志清理和顶部对齐回归 | 271 通过、3 个原生专用项跳过 | `output/qml-ui-all-tests-r4-20261006.log` |
| Windows 原生五页窗口、真实保存/确认、键盘、菜单、刷新、缩放 | 32 通过，QML warning 为零 | `output/qml-ui-native-tests-r4-20261006.log` |
| 125% / 150% / 200% DPI，每档菜单、五页几何、顶部对齐与首页入口 | 每档 12 通过，共 36 项；独立探针确认实际 DPR 为 1.25 / 1.5 / 2.0 | `output/qml-ui-dpi-{125,150,200}-r4-20261006.log` 及 `output/qml-ui-desktop-r4-20261006/dpi-{125,150,200}.json` |
| 自检成功/异常时实际日志句柄关闭与临时目录清理，以及既有启动契约 | 14 通过（与 QML 总数有重叠，不累加） | `output/qml-ui-launcher-cleanup-tests-20261006.log` |
| 源码 Windows 原生自检 | exit=0；临时 profile 不存在 | `output/qml-ui-source-cleanup-selftest-20261006.json` |
| 冻结程序 Windows 原生启动自检 | exit=0、status=ok；临时 profile 已删除；38 项资源、10 个 SVG、5 路由、真实 SQLite、0 QML warning | `output/qml-ui-desktop-r4-20261006/frozen-native-exit.json` 及 `frozen-native-selftest.json` |
| computer-use 实际五页窗口 | 首次菜单收起/键盘展开、当前路由重复点击、Ctrl+1–5、Tab 焦点、F5 刷新、最大化/还原、正常退出均已观察；保留进程句柄确认 exit=0 | `output/qml-ui-desktop-r4-20261006/checks.json`、`launch.json`、`exit.json` |
| 构建、资源与输入一致性 | 2027 个文件；229 源码输入及 6 个构建输入校验通过，无源码漂移 | `output/qml-ui-rebuild-20261006-r4/metadata/run-record.json` |
| 安装器编译与输入一致性 | 内部 QA 安装器已生成，未安装或发布 | `output/qml-ui-rebuild-20261006-r4/installer/installer-record.json` |

两条 Python `re.split` 的既有弃用警告未在本次视觉任务中修改。最终截图位于 `output/qml-ui-final-preview-r4-20261006/`，包含五页和收起菜单；报告确认数据库有 3 条真实隔离记录，预算为通过原服务预览/确认保存的计划。三种窗口尺寸（800×600、1280×820、1600×1000）的五页截图另保留在 `output/qml-ui-native-captures-r4-20261006/`。原生测试独立运行，未同时启动其它 native 截图窗口；测试计数之间存在重叠，不求和宣称总数。

原生回归发现并修复图谱在快速离页时仍执行 `Qt.callLater`、访问已卸载页面方法的问题。图谱适配、数据协调和首页页签导航现在使用所属组件的单次 Timer，销毁时取消；新增快速缩放后立即切页的回归。字体分组间的循环绑定也已消除。曾有一次原生 F5 刷新断言在同时启动独立截图窗口时失败；保持断言不变、停止其它原生窗口后完整串行复跑 36 项通过。后续原生测试应保持独立窗口环境，不同时启动截图探针。

冻结 r2 的原生自检曾写出 `status=ok` 报告，但进程没有退出。computer-use 实际读取错误对话框确认：Windows 的 `study_app.log` 文件句柄仍打开，`TemporaryDirectory` 清理失败，属于 `WinError 32`，不是 access violation。直接自检入口现在在临时目录上下文内 `finally: logging.shutdown()`，成功及异常路径都先关闭日志，再删除隔离 profile。r3/r4 的直接原生自检均以真实进程句柄等待，exit=0 且临时目录已删除；不再仅以 JSON 存在判断完成。r1–r3 的中间产物及日志保留作为历史证据。

## 当前内部产物与启动

最终目录：`output/qml-ui-rebuild-20261006-r4/`；r1–r3 是中间产物，不作为本轮最终交付依据。

- 程序：`dist/LearningModel/LearningModel.exe`，SHA-256 `63a1703534af16d4da67b0861ab97d1c6ecbbfd015dbff78df9a4aa7daa8d317`。
- 安装器：`installer/LearningModel-Setup-0.1.1-x64.exe`，63,436,563 字节，SHA-256 `5a5372234ea25e5eec38753c82bf558d4d8dada6302d99f2ecc0bac76b2a728e`。
- 打开重构界面须传入 `--qml`。根目录的 `启动和纸学习室.cmd` 仅启动本目录程序并带上该参数。常规入口仍为 Widgets 对照，未擅自改变安装快捷方式或默认入口。

源码运行：

```powershell
& './output/qml-phase5-venv-314-20261005/Scripts/python.exe' -m study_app.quick
```

## 尚未完成的验收

`computer-use` 已恢复：此前 Python 源码测试窗口遇到平台 `Computer Use app approval timed out`；随后 LearningModel.exe 的访问成功，已使用该技能直接核验最终 r4 的实际完整 QML 窗口。首次隔离交互探针使用隐藏启动方式，没有可见主窗口；已按探针 PID 与绝对 exe 路径核对后结束该探针，再用正常可见方式重启。它不计入正常退出验证；最终 r4 使用保留句柄的独立交互进程，computer-use 按 Ctrl+Q 后确认窗口消失，宿主获得 exit=0。

安装生命周期、跨机器以及此前真实外部流程的发布门槛继续以 `QML_REAL_ACCEPTANCE.md` 为准。本次局部 QML 生命周期问题的修复及复测，不作为此前 access violation 的根因修复证据。未提交、推送、安装、签名或发布；已有修改和旧 Widgets 回归入口保留。

2026-10-08 字体版已完成本机覆盖安装：output/qml-font-install-20261008/installer/。2030 个安装文件与构建产物哈希一致，8 个当前数据文件安装前后哈希不变；数据备份在 output/local-font-backup-20261008/。桌面快捷方式启动参数仍为 --qml。

## 2026-10-08 页面与纸面精修

在字体版基础上完成以下调整：

- 统一暖米色、浅樱色、灰绿与暖墨色，按钮与图标使用同一套主题色；字体继续使用随包提供的 Noto Serif SC。
- PaperCard 增加纤维纹理强度、细微毛边和低位双层阴影，快速记录卡增加局部卷角；没有新增逐帧纹理计算或持续模糊效果。
- 首页和计划页内容限制为 1200 宽，助理与设置限制为 1000 宽；缩短首页助理入口、计划页页签与详细计划折叠条。
- 刷新移到标题旁，浮窗与退出收进左下角窗口菜单；保留键盘快捷键与可访问名称。
- 学习助理移除常驻右侧说明栏，策略、审计、撤销与预警收进“更多操作”；业务影响预览、外发授权和执行复核仍保留在操作流程中。
- 图谱详情不再拉伸占满整列，搜索、筛选与缩放工具采用自适应紧凑排列。

验证：`output/pytest-layout-final-20261008.log` 中 40 项通过，1 项图谱 F5 刷新断言失败；保持断言及生产代码不变，独立复跑整个 insights 窗口测试 8 项通过（`output/pytest-layout-focus-20261008.log`）。该次 F5 失败未复现，不能仅凭复跑确定原因。新二级入口实际点击、撤销复核、浮窗键盘恢复和退出均已通过。最终五页与收起菜单截图位于 `output/qml-layout-verified-20261008/`，使用隔离数据库，报告为 `status=ok`、零 QML warning，无外部请求。测试包含 800×600、1280×820、1600×1000 三档布局。

本轮已完成本机覆盖安装：`output/qml-layout-install-20261008/installer/LearningModel-Setup-0.1.1-x64.exe`。构建与冻结自检通过，2030 个安装文件与构建产物哈希一致，8 个数据文件安装前后哈希不变，备份在 `output/local-layout-backup-20261008/`。桌面快捷方式已启动该安装目录中的 QML 版本。Computer Use 实际观察了本机学科数据加载后的首页，点击验证左下角窗口菜单、单列助理页与“更多操作”对话框；未修改学习数据或执行策略。详细安装证据见该安装目录的 `local-install-verification.json`。本轮未提交或发布到 GitHub。

## 2026-10-08 第二轮细节修订

刷新改为无常驻边框的独立符号，重绘双箭头图标；按钮的图标和文字作为一个整体居中，输入框明确垂直居中。樱花图层右上对齐，消除等比适配造成的右侧留白，背景覆盖窗口；页面外边距从 32 缩至 20。

首页空计划只保留一个空状态，不再重复解释“预览不代表安排”。记录、OJ、练习、设置、学科管理和总结页删减实现说明。确认前仍显示具体变更、外发内容和需要用户填写的字段。实现语义：预览不会提交，确认后才写入；附件解析、外发授权和策略校验仍由原服务负责；学科管理仍会备份并支持恢复。这些语义无需在普通空页面持续重复。

浮窗为 56×56 图标，支持拖动、双击恢复、键盘 Enter 恢复和右键退出。单击不恢复主窗口。仅显式“收起到浮窗”进入该状态；主窗口关闭调用正常退出流程，后台任务排空与单实例保护释放仍由应用根入口负责。

本轮验证覆盖原生五页、三档窗口尺寸、记录保存/撤销、学科安全流程与生命周期。首轮 55 项中 54 项通过，剩余旧关闭预期已更新；随后 32 项中 31 项通过（同一旧结尾断言），修正后生命周期独立 14 项全部通过。浮窗单击不恢复、双击恢复与 Enter 恢复均已验证。最终五页截图和零 QML warning 报告在 `output/qml-refine-r2-preview-20261008/`，冻结启动自检通过并校验显式收起及关闭退出行为。构建目录为 `output/qml-refine-r2-install-20261008/`。

本轮已覆盖本机安装：2030 个程序文件哈希一致，8 个数据文件安装前后哈希不变，备份在 `output/local-refine-r2-backup-20261008/`。Computer Use 已观察安装版空计划首页：刷新为独立符号，重复提示消失，樱花贴到右上边缘；实际选择“收起到浮窗”后进程仍运行。Computer Use 无法枚举该小型工具浮窗，因此安装版双击未通过此工具实测；原生 QTest 已验证 56×56 窗口、单击不恢复、双击和 Enter 恢复。通过桌面快捷方式唤醒同一实例后，Computer Use 点击标题栏关闭按钮，随后进程检查为 0，确认直接退出。安装验证见 `output/qml-refine-r2-install-20261008/installer/local-install-verification.json`。未发布到 GitHub。

## 2026-10-08 概念图排版与字号对齐

以归档概念图组 `output/washi-ui-final-concepts-20261004-102734` 的展开首页及四页总览为参照，保留此前精简后的业务入口。首页标题使用 64 px（窄窗口 44 px），其他页面标题 48 px（窄窗口 36 px）；卡片标题 24 px，正文 18 px，辅助标签 14 px。导航文字 20 px，行高 58 px；共享表单及按钮高度 48 px。继续使用同一套随包 Noto Serif SC。

首页与计划内容最大宽度调整为 1320 px，首页双列比例为 61:39；加大页头留白和卡片内边距，学习条目采用更清楚的时间、科目、主题层级，主操作固定在卡片底部。右栏进度和记录卡恢复概念图的纵向比例。小窗口采用单列滚动布局，并降低樱花装饰透明度以避免干扰页头文字。增大字体后修复归档对话框的 implicitHeight 绑定循环。

验证：`output/pytest-type-r3-20261008.log` 中 41 项原生窗口测试通过，覆盖三档窗口尺寸、菜单、表单和确认流程。1600×1000 与 800×600 的最终隔离视觉报告分别在 `output/qml-type-r3-final-20261008/` 和 `output/qml-type-r3-small-20261008/`，均 status=ok、无 QML warning。小窗口截图检查后追加樱花透明度调整；冻结自检通过，构建目录 `output/qml-type-r3-install-20261008/`。

本轮已覆盖安装到本机：2030 个程序文件与构建产物哈希一致，8 个学习数据文件安装前后哈希不变。备份位于 `output/local-type-r3-backup-20261008/`，安装核验位于构建目录的 `installer/local-install-verification.json`。Computer Use 已观察安装版首页加载真实学科进度后的排版，并点击进入学习计划页检查标题和表单布局；未填写表单或修改学习数据。未提交或发布到 GitHub。

## 2026-10-08 知识图谱画布修订

参照概念图的主画布与右侧详情布局，压缩页头高度，详情栏限制为 280–310 px，并提供收起详情按钮。搜索和状态筛选保留在画布上方；缩放、查看全图与还原可读大小移至底部，避免工具栏折行挤压画布。

节点直径从 96 增至 120，名称字号增至 18，状态与统计为 12；关系线端点相应调整。画布根据筛选结果实际边界居中。默认缩放不低于 85%，大型课程通过拖动或滚动浏览；显式查看全图可缩小至完整范围，还原可读大小恢复默认策略。保留实际章节与先修关系，不生成虚构关系或掌握状态。

验证：初轮 18 项中 17 项通过；学科切换测试的固定 60 ms 等待连续两次未等到页面协调定时器，改为等待实际 selectedSubjectKey 状态，保持业务断言。最终 insights 原生窗口测试 9 项全部通过，包括新增的六章节画布占比、展开画布、长链默认可读比例、全图/还原和筛选后居中检查；视觉路由其余 10 项此前通过。截图位于 `output/qml-graph-r4-preview-20261008/`，测试日志 `output/pytest-graph-r4-third-20261008.log`。

追加 800×600 隔离完整应用截图检查：`output/qml-graph-r4-small-20261008/knowledge.png` 中节点完整、控件未溢出，报告无 QML warning；此前快速窗口回归截图发生在自适应定时器完成之前，不作为最终小窗口视觉证据。冻结自检和 2030 个打包文件校验通过。

已覆盖安装本机版本：2030 个程序文件哈希与产物一致，8 个学习数据文件安装前后不变；备份 `output/local-graph-r4-backup-20261008/`。Computer Use 实测加载离散数学 16 个章节，收起详情后画布扩展，查看全图后完整显示所有章节，还原恢复可读比例。未修改学习记录，未发布到 GitHub。
