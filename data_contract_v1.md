 # Personal Learning OS 数据契约 data_contract_v1（正式冻结版）

  > 版本：v1.0.2（正式冻结版；2026-09-12 重新签核生效；修订记录见 §6）
> 制定日期：2026-09-12
 > 制定依据：《GLM-5.3-Flash 分阶段执行合同》v1.2（SHA256 074B2BB9D7EF192398FDB4BA0F2CBDA0806501DB4492013AD215EDE73C38C1F5）第 3 节不可变业务契约；P0-02 问题重验证据；《工程审查与改进路线图》2026-09-12
> 覆盖问题：BUG-01、BUG-02、BUG-03、BUG-05、BUG-06、BUG-08、BUG-11
> 效力：本文件获书面签核后，成为阶段 A（A-01～A-05）各任务包实现与验收的唯一语义依据。实现与本文件冲突时，以本文件为准。修改本文件必须由项目所有者出具书面变更单，并在执行台账登记。
> 权威位置：项目根目录 `data_contract_v1.md`。任何实现文件不得复制本契约的规则文本，只能引用条款编号。

## 1. 适用范围与读者

适用代码：`study_app/data/database.py`（写入、回读、导入）、`study_app/data/model_progress_sync.py`、`learning_bkt.py`、`learning_memory.py`、`learning_difficulty.py`、`learning_monitor.py`、`study_app/core/dashboard.py`、`study_app/core/study_phase.py`、`study_app/ai/validation.py`、`study_app/ai/record_parser.py`、`learning_desktop_widget.py` 及未来新增消费者。

读者：执行模型（GLM 或受托代理）、复核者、项目所有者。

## 2. 记录级字段契约（学习记录 dict / `learning_records` 表）

表列以 `study_app/data/schema.sql` 为准（`record_date TEXT NOT NULL`、`subject_name TEXT NOT NULL`、`score REAL`、`raw_json TEXT NOT NULL` 等）。

| 字段 | 类型 | 允许范围 | 缺失语义 | 违例处理 | 条款 |
|---|---|---|---|---|---|
 | `date` | str `YYYY-MM-DD` | 必须是真实日历日期（含闰日） | 必填；缺失即拒绝 | 任何数据库写入之前抛 `ValueError`；无任何部分写入 | 4.3 |
| `subject` | str | 应匹配模型 `subjects.name`（活动或封存均可录入） | 必填；缺失即拒绝 | 同上 | 4.3 |
| `module` | str | 可选 | `NULL` | — | — |
| `topic` | str | 可选 | `NULL` | — | — |
| `activity` | str | 推荐枚举：`review / exercise / class / lecture / quiz / self_test / exam_review / class_exercise / review_exercise`；未识别值原样保留，不拒绝 | 默认 `review` | 空白归一为默认 | 4.7 |
| `source` | str | 推荐枚举：`classroom / class / outside_class / self_study`；未识别值原样保留 | 默认 `outside_class` | 空白归一为默认 | 4.7 |
| `score` | float 或 None | 0–100；仅接受 `int/float`（不含 `bool`）；`NaN/±inf` 拒绝；字符串数值拒绝 | `None` = 无显式分数 | 拒绝写入 | 4.2 |
| `duration_minutes` | float 或 None | > 0；数值约束同 `score` | `None` | 拒绝写入 | 4.2 |
| `note` | str | 任意文本，须通过 `text_integrity` 校验 | `""` | 校验失败拒绝写入 | 3.2.5 |
| `problems` | list[dict] 或缺失 | 元素必须为 dict（见第 3 节）；非法元素（str/int 等）拒绝，不得隐式转字符串蒙混 | 缺失 = 无题目明细 | 拒绝写入 | 4.2 |
| `attachments` | list[dict] 或缺失 | 元素含 `file_path`；其余可选 | 缺失 = 无附件 | — | — |

通用规则（对应执行合同 §3.2.5）：**任一字段校验失败时，本次写入不得留下学习记录、题目明细、附件关系、同步标记或计划状态中的任何一物。**

## 3. 题目级字段契约（problem dict / `problem_attempts` 表）

表列：`title TEXT NOT NULL`、`statement`、`status`、`correctness REAL`、`difficulty_label TEXT`、`difficulty_score REAL`、`error_cause`、`related_topics_json`、`raw_json TEXT NOT NULL`。

| 字段 | 类型 | 允许范围 | 缺失语义 |
|---|---|---|---|
| `title` | str 非空 | ≤120 字 | 数据层以记录 `topic` 兜底命名（现状保留），不新增字段 |
| `statement` | str | ≤3000 字 | `NULL` |
| `answer_result` | str | 自由文本结果描述（如“全对”“错误”“10题错2”）；仅为推断来源，非权威结果 | 缺失 |
| `status` | str 枚举 | 正确类：`completed/solved/correct/accepted/AC/all_correct`；错误类：`not_solved/wrong/incorrect/failed/WA`；部分类：`partial/partial_wrong/partially_correct` | 缺失 → 交由结果解释链（4.1） |
| `correctness` | float 0–1 | 数值约束同 4.2；存储允许 0–100 形式（读取/写入时归一） | 缺失 → 可由 4.1 链推断 |
| `partial_credit` | float 0–1 | 同上 | 缺失 |
| `difficulty` | str 枚举 | `easy/medium/hard` | 缺失 → 按 `difficulty_score` 派生；两者皆缺 → 推断，仍失败按 `medium`/55 |
| `difficulty_score` | float 0–100 | **`0` 是合法显式难度，必须保留**；数值约束同 4.2 | 缺失 → 推断链 |
| `error_cause` / `note` | str | 自由文本 | `""` |
| `related_topics` | list[str] | 元素必须是 str；非法元素拒绝，不得隐式转字符串 | `[]` |

## 4. 冻结语义（按问题编号，可验收）

### 4.1 题目结果解释链与优先级（BUG-01）

解释优先级自上而下，**高级别存在时低级别一律不参与**：

| 级 | 来源 | 结果 |
|---|---|---|
| P0 | 显式数值：`correctness` 或 `partial_credit` | 归一到 0–1 |
| P1 | `status` 枚举映射 | 正确类=1.0；错误类=0.0；部分类=0.5 |
| P2 | `answer_result`/结果文本推断 | “全对/做对/正确/AC”→1.0；显式“错误/错了/WA”且无部分词→0.0；“N题错M题”→(N−M)/N；“部分/小错/算错/漏”→0.5 |
| P3 | 记录级兜底（仅当 P0–P2 全部缺失）：`score`、`result.correct/total`、`self_rating` | 归一到 0–1，且来源标记 `record_fallback` |

硬性规则：

1. **记录总分在任何情况下不得修改已确定的题目结果**（禁止现 `max(correctness, record_level)` 类抬升；learning_bkt.py:418-424 分支必须移除或限定为“题目结果缺失”场景）。
2. 题目结果**缺失**且记录级兜底命中时，观测必须携带 `result_source="record_fallback"`；A-04 验收矩阵：
   - 错误 + 总分 80 → 保持 0.0（BKT、计分、同步三处一致）；
   - 正确 + 低分 → 保持 1.0；
   - 部分正确 + 任意 → 保持统一比例；
   - 缺失 + 有总分 → 允许兜底并可识别来源。
3. **唯一实现入口**：新建共用解释函数（建议名 `interpret_problem_result(problem, record) -> {value: 0–1 | None, source: P0/P1/P2/P3}`）。BKT、难度计分、`model_progress_sync`、界面回读一律调用该入口，禁止各自复制解释逻辑（呼应执行合同 §3.1.5、文件纪律第 3 条）。

### 4.2 数值边界与难度零值（BUG-06、BUG-11）

1. 所有数值字段仅接受 `int/float` 且 **`bool` 一律拒绝**（`isinstance(v, bool)` 先于数值判断）。
2. `NaN`、`+inf`、`-inf` 一律拒绝；**校验层（`require_number_range`）与解析规范化层（`normalize_llm_problem`）双重拒绝**，拒绝结果不得进入记录写入与模型同步。
3. 字符串形式的数字（`"80"`）不接受；`related_topics` 等列表必须验证元素实际类型。
4. 合法边界 `0 / 1 / 100`（按字段量纲为 `correctness 0/1`、`difficulty_score/score 0/100`）必须按字段语义保留。
 5. **显式难度选择按“字段是否为 None”判断，禁止真值链**（现 `difficulty_score or difficulty_value or …` 必须改为显式 None 检查）：**`difficulty_score=0` 保留为显式 0 分，标签按区间派生为 `easy`（0–39），不得回退 55 或任何关键词推断**。
6. 显式难度分支语义表（A-03 验收矩阵）：

| 输入 | 冻结结果 |
|---|---|
| `difficulty_score=0`（int/float） | 保留 0；标签 `easy`；`source=explicit` |
| `difficulty_score=None` / 字段缺失 | 进入推断链；仍失败 → `medium/55`、`source=default_medium` |
| `difficulty=""` / 空白字符串 | 视为缺失，同上 |
| `difficulty="0"`（字符串数值） | 拒绝（字符串不是数值） |
| `difficulty_score=-3 / 120`（越界） | 拒绝写入 |
| `difficulty="easy|medium|hard"（含大小写别名/中文别名）` | 按别名映射 25/55/85 |

7. 越界处理冻结：LLM 输出越界 → 校验层拒绝（现状行为保留）；数据层 `correctness` 允许 0–100 形式归一（`value>1 → value/100`，且 `>100` 拒绝）；**其余数值字段越界一律拒绝，不做静默截断**。

### 4.3 日期语义与历史截止日（BUG-03、BUG-08）

写入侧：

 1. `date` 必须能按 `%Y-%m-%d` 解析**且为真实日历日期**（`2026-02-30` 拒绝，`2028-02-29` 接受）；校验在任何数据库写入（INSERT/DDL）之前完成。
 2. 验证失败抛 `ValueError`；若已开启事务则回滚；学习记录、题目明细、附件、同步标记、计划状态零残留。
3. `record_date` 的规范化只做一次（格式校验 + 原样存储 `YYYY-MM-DD`）。

计算侧（历史/回测）：

4. 一切消费学习记录的计算函数（报告窗口、记忆 recall/half_life、BKT observations、近期覆盖率、加权优先级）**显式接收同一 `as_of_date`**；`date > as_of_date` 的记录在进入计算前截断。
5. `study_phase` 优先级链不得直接调用 `date.today()`（现 study_phase.py:1039 必须改为调用方传入的 `as_of_date`）；同一调用链内禁止未声明的系统日期依赖。
6. 已入库的历史异常日期：读取时产生可定位诊断（`data_warning="invalid_date"`），该记录跳出窗口统计并计数，不得让报告或页面崩溃。
7. 历史回测不读取未来状态；“当前模型按历史记录计算”与“恢复历史模型快照”是两种语义，不得混用。

### 4.4 权威存储与回读（BUG-02）

 1. **唯一规范化点**：`add_learning_record` 与 `import_records_json` 在任何第一条数据库写入之前（是否显式开启事务由实现决定，不变式为：全部校验与规范化必须先于一切写入完成），对整个记录（含全部题目）完成**一次**规范化：题目结果解释（4.1）、难度推断（4.2）、文本完整性校验。规范化失败 → 全量回滚，无部分写入。
2. **同一份规范化结果**同时驱动：`problem_attempts` 行、`learning_records.raw_json`、练习题自动导入、`model_progress_sync`。禁止“先写 raw_json 后补全字段”的顺序（现 database.py:539 vs :544-545、import_records_json :415 vs :421 两处都必须重构为规范化前置）。
3. **权威性冻结**：`raw_json` 存储**规范化后**的记录；关系表是 raw_json 的规范化投影；读取以 raw_json 为主。关系表与 raw_json 不一致时（历史遗留），产生一致性诊断计数，不静默、不批量改写。
4. `raw_json` 损坏或旧版：走 `_rebuild_record_from_normalized_rows` 重建路径并标记 `data_warning="invalid_raw_json"`，进入可诊断兼容路径；不得静默制造证据。
5. 审查发现的 57 条真实历史差异：仅按语义分类（B-05 交付），**不批量覆盖**；本契约签核不构成对历史数据的修改授权。
6. `learning_model_v1.json` 的权威性：课程结构（subjects/modules/topics）以模型 JSON 为导入种子；知识点状态与掌握度由 `model_progress_sync` 依 4.1/4.4 语义维护；调用方不得自行选择权威来源（执行合同 §3.3.3）。

### 4.5 数据源与封存（BUG-05）

1. 所有界面（主窗口、悬浮窗、monitor 报告）使用**同一数据服务与同一封存策略**：SQLite（`app_data/learning_app.sqlite`）优先；不可用或为空时回退旧 JSON，且回退必须**可观察**（展示实际来源与原因）。
2. `lifecycle.status=archived` 的学科：保留历史查看与报告中的“已封存/只读”标注；**不得进入**活动提醒、待办、三天窗口标杆比较、计划生成与模拟卷。
3. 同一日期与同一快照下，主界面与悬浮窗的记录集合与风险列表必须一致；禁止悬浮窗独立解释旧 JSON 形成第二活动提醒链。

### 4.6 消费者矩阵（验收对照）

| 消费者 | 必须遵守的条款 |
|---|---|
| `learning_bkt.iter_topic_observations` / `topic_bkt_state` | 4.1（唯一解释入口）；4.3 计算侧 |
| `learning_monitor.difficulty_problem_score / window_score` | 4.1；4.2 |
| `study_app/data/model_progress_sync.py` | 4.1；4.2；4.4 |
| `database.add_learning_record / import_records_json / load_raw_records*` | 4.2；4.3 写入侧；4.4 |
| `ai/validation.py`、`ai/record_parser.py` | 4.2 |
| `core/dashboard.py`、`core/study_phase.py` | 4.3 计算侧；4.5 |
| `learning_desktop_widget.py` | 4.5 |

### 4.7 枚举开放性

`activity` / `source` 为推荐枚举、开放集合：未识别值不拒绝（历史数据含中文值），但空白值归一为默认；影响语义的判定（如“是否课外复习”“是否可观察练习”）只能依赖冻结枚举 + 显式别名表，不得依赖自由文本猜测（B-02/B-03 涉及时另行细化）。

## 5. 覆盖矩阵（完成标准对照）

| 问题 | 语义分支 | 契约条款 |
|---|---|---|
| BUG-01 | 错误×80 / 正确×低分 / 部分×任意 / 缺失×有总分 / 总分不得反超 / 统一入口 | 4.1 |
| BUG-02 | 全对推断在存储与回读一致 / 规范化前置 / 失败无部分写入 / 损坏 raw_json 诊断 / 关系表-回读-模型逐字段一致 / 旧数据兼容 | 4.4 |
 | BUG-03 | 非法日历日期写入前拒绝 / 闰日接受 / 失败零残留 / 历史异常日期读取诊断 | 4.3 写入侧 |
| BUG-05 | 界面同源同快照 / 封存不进活动 / 回退可观察 | 4.5 |
| BUG-06 | NaN/±inf/bool 拒绝 / 0、1、100 保留 / 字符串数组元素 / 拒绝不进同步 | 4.2 |
| BUG-08 | 同一 as_of_date / 未来证据截断（报告、记忆、BKT、覆盖、优先级）/ date.today() 消除 / 回测不读未来 | 4.3 计算侧 |
| BUG-11 | 显式 0 保留 / 缺失、None、空值、字符串数值、越界、非法元素分别处理 | 4.2 |

## 6. 签核

| 项 | 值 |
|---|---|
  | 状态 | 已签核（v1.0.2 冻结生效；经两轮复核整改后重新签核） |
  | 签核人 | 项目所有者（对话指令「签核并进行A-01」，授权执行方代填） |
  | 签核日期 | 2026-09-12（重新签核） |
| 签核后动作 | 台账登记 P0-03 已签核；阶段 A 依 A-01→A-05 顺序派单 |
| 回退方法 | 本文件为新增文档，未签核前删除即可完全回退；已签核后修改须走书面变更单 |
 | 修订记录 | v1.0.1（2026-09-12）：依复核意见——①统一事务时序表述为「全部校验与规范化在任何数据库写入之前完成」（§2/§4.3/§4.4/§5）；②标题改为正式冻结版。无业务语义变更，仅时序表述统一与状态修正。 |
 | 修订记录 | v1.0.2（2026-09-12）：第二轮验收发现制定依据仍绑定合同 v1.1（旧哈希 566CE4A6…），已修正为 v1.2（SHA256 074B2BB9…C1F5）；无业务语义变更。 |
