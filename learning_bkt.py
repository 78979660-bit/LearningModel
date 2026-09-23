"""
Bayesian Knowledge Tracing layer for the learning monitor.

BKT estimates P(learned) for each topic from observable practice outcomes.
It complements the existing percentage score and spaced-repetition model:
- difficulty scoring answers "how good was this recent practice window?"
- spaced repetition answers "how likely is this topic to be recalled today?"
- BKT answers "given the observed correct/wrong attempts, how likely is this
  topic already learned?"

The v2 layer follows four practical ideas from the KT literature:
- Baker, Corbett & Aleven: estimate slip/guess from context instead of keeping
  them fixed for every item.
- Qiu et al.: time gaps can matter, so mastery is allowed to decay before a new
  observation.
- Piech et al.: sequence context can be useful; here we keep an interpretable
  sequence summary rather than training a neural model with too little data.
- pyBKT: expose parameters as configuration and keep the implementation easy to
  inspect and calibrate.
"""

from __future__ import annotations

from datetime import date, datetime

from learning_difficulty import infer_problem_difficulty
from learning_problem_result import interpret_problem_result


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def clamp(value, lower=0.0, upper=1.0):
    return max(lower, min(upper, float(value)))


def flatten_text(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten_text(item) for item in value)
    return str(value)


def normalize_topic_text(value):
    text = flatten_text(value).lower()
    drop_chars = set(" \t\r\n\u3000·/／、,，;；:：-—_()（）[]【】")
    return "".join(ch for ch in text if ch not in drop_chars)


def topic_matches_text(topic_name, text):
    aliases = topic_aliases(topic_name)
    haystack = normalize_topic_text(text)
    return any(alias and alias in haystack for alias in aliases)


def topic_aliases(topic_name):
    topic = normalize_topic_text(topic_name)
    aliases = {topic}
    if topic == "积分公式综合题":
        aliases.update({"积分与场论公式综合题", "积分公式综合", "三大积分公式综合"})
    if topic == "一致收敛性与逐项运算":
        aliases.update(
            {
                "一致收敛",
                "一致收敛性",
                "函数项级数",
                "逐项求导",
                "逐项积分",
                "逐项运算",
            }
        )
    if topic == "归并与基数排序":
        aliases.update({"归并排序", "基数排序", "二路归并"})
    if topic == "外部排序":
        aliases.update({"外部排序", "外排序", "多路归并"})
    if topic == "哈希表":
        aliases.update({"哈希", "散列表", "散列", "hash", "hashtable", "hashtables"})
    if topic == "哈夫曼树":
        aliases.update({"huffman", "huffmantree"})
    if topic == "复杂度分析":
        aliases.update({"复杂度", "算法复杂度", "递归复杂度", "时间复杂度", "空间复杂度"})
    if topic == "二叉搜索树":
        aliases.update({"bst", "二叉查找树", "二叉排序树"})
    if topic == "算法基础题型":
        aliases.update(
            {
                "kmp",
                "前缀函数",
                "字符串匹配",
                "复杂度分析",
                "算法复杂度",
                "递归复杂度",
                "递归树",
                "主定理",
                "哈希表",
                "散列表",
                "二次探测",
                "二叉搜索树",
                "二叉查找树",
                "二叉排序树",
                "bst",
            }
        )
    if topic == "常数项级数与判敛法":
        aliases.update(
            {
                "级数敛散性",
                "级数判敛",
                "级数判别",
                "正项级数判别",
                "任意项级数",
                "绝对收敛",
                "条件收敛",
                "交错级数",
                "leibniz判别",
                "对数型级数",
                "参数判敛",
            }
        )
    if topic == "正项级数与比较判别法":
        aliases.update(
            {
                "正项级数",
                "比较判别法",
                "比较判别",
                "极限比较",
                "比值判别",
                "根值判别",
                "积分判别",
                "p级数",
                "对数型级数",
            }
        )
    if topic == "无穷级数与部分和":
        aliases.update(
            {
                "部分和",
                "级数求和",
                "望远镜级数",
                "望远镜求和",
                "部分分式",
                "部分分式分解",
                "级数的和",
            }
        )
    if topic == "级数收敛的基本性质":
        aliases.update(
            {
                "收敛的基本性质",
                "收敛性质",
                "必要条件",
                "夹逼",
                "cauchy准则",
                "柯西准则",
                "级数性质",
            }
        )
    if topic == "几何级数":
        aliases.update({"几何级数", "等比级数", "几何级数求和"})
    if topic == "幂级数与收敛半径":
        aliases.update(
            {
                "幂级数",
                "收敛半径",
                "收敛域",
                "端点讨论",
                "系数比较",
                "边界点判断",
            }
        )
    if topic == "函数展开为幂级数":
        aliases.update(
            {
                "函数展开",
                "展开为幂级数",
                "taylor展开",
                "泰勒展开",
                "taylor级数",
                "maclaurin",
                "麦克劳林",
            }
        )
    if topic == "级数综合计算与证明":
        aliases.update(
            {
                "级数综合",
                "级数综合计算",
                "级数证明",
                "求和与展开",
                "级数专项",
                "级数敛散性求和与展开专项",
            }
        )
    return aliases


def status_prior(status):
    priors = {
        "not_started": 0.04,
        "learning": 0.22,
        "learned_needs_review": 0.46,
        "reviewing": 0.55,
        "mastered": 0.82,
    }
    return priors.get(status, 0.2)


def topic_prior(topic, policy):
    model = policy.get("bkt_model", {})
    blend = clamp(model.get("mastery_prior_blend", 0.65))
    mastery = clamp(topic.get("mastery_prior", topic.get("mastery", status_prior(topic.get("status")))))
    prior = status_prior(topic.get("status"))
    return clamp(blend * mastery + (1 - blend) * prior)


def problem_correctness_fraction(problem):
    return interpret_problem_result(problem)["value"]


def record_correctness_fraction(record):
    score = record.get("score")
    if isinstance(score, (int, float)):
        return clamp(score / 100)

    result = record.get("result") or {}
    correct = result.get("correct")
    wrong = result.get("wrong", 0)
    partial = result.get("partial_wrong", 0)
    total = result.get("total")
    if isinstance(correct, (int, float)) and isinstance(total, (int, float)) and total > 0:
        earned = float(correct) + 0.5 * float(partial or 0)
        return clamp(earned / float(total))
    if isinstance(correct, (int, float)) and isinstance(wrong, (int, float)) and correct + wrong > 0:
        return clamp(float(correct) / float(correct + wrong))

    rating = record.get("self_rating")
    if isinstance(rating, (int, float)):
        return clamp(rating / 100)
    return None


def record_mentions_topic(record, subject_name, topic_name):
    if record.get("subject") != subject_name:
        return False
    if record.get("topic") == topic_name:
        return True
    fields = [
        record.get("topic"),
        record.get("module"),
        record.get("topics"),
        record.get("related_topics"),
        record.get("tags"),
        record.get("note"),
        record.get("error_causes"),
    ]
    return topic_name and topic_matches_text(topic_name, " ".join(flatten_text(field) for field in fields))


def problem_mentions_topic(record, problem, topic_name):
    fields = [
        problem.get("title"),
        problem.get("related_topics"),
        problem.get("difficulty_reasons"),
        problem.get("error_cause"),
        problem.get("note"),
    ]
    problem_text = " ".join(flatten_text(field) for field in fields)
    if topic_name and topic_matches_text(topic_name, problem_text):
        return True
    if problem_text.strip():
        return False
    return record_mentions_topic(record, record.get("subject"), topic_name)


def is_observable_practice(record):
    activity = record.get("activity") or ""
    source = record.get("source") or ""
    note = str(record.get("note") or "")
    if activity == "plan_completion" or source == "planned_homework" or note.startswith("学习计划项完成"):
        return False
    if record.get("problems"):
        return True
    if record_correctness_fraction(record) is None:
        return False
    if activity in {"class", "lecture"} and source in {"classroom", "class"}:
        return False
    return activity in {"exercise", "review_exercise", "class_exercise", "quiz", "self_test", "exam_review", "review"}


def time_decay_mastery(probability, days_since_previous, topic, policy):
    model = policy.get("bkt_model", {})
    time_model = model.get("time_effect", {})
    if not time_model.get("enabled", False) or days_since_previous is None:
        return clamp(probability)

    days = max(0.0, float(days_since_previous))
    if days <= 0:
        return clamp(probability)

    base_half_life = float(time_model.get("base_half_life_days", 9.0))
    min_half_life = float(time_model.get("min_half_life_days", 2.0))
    max_decay = float(time_model.get("max_decay_per_gap", 0.35))
    mastery = clamp(topic.get("mastery", 0.25))
    difficulty = clamp(topic.get("difficulty", 0.5))
    half_life = max(min_half_life, base_half_life * (0.75 + mastery) / (0.75 + difficulty))

    retention = 2 ** (-(days / half_life))
    decayed = probability * retention
    floor = float(time_model.get("retention_floor", 0.02))
    return clamp(max(probability * (1 - max_decay), decayed, floor))


def bkt_params(topic, difficulty_score, policy, context=None):
    model = policy.get("bkt_model", {})
    context = context or {}
    base_guess = float(model.get("base_guess", 0.22))
    base_slip = float(model.get("base_slip", 0.12))
    base_learn = float(model.get("base_learn", 0.08))
    max_guess = float(model.get("max_guess", 0.35))
    max_slip = float(model.get("max_slip", 0.3))
    max_learn = float(model.get("max_learn", 0.22))

    d = clamp(difficulty_score / 100)
    topic_difficulty = clamp(topic.get("difficulty", 0.5))
    combined_difficulty = clamp(0.65 * d + 0.35 * topic_difficulty)

    guess = clamp(base_guess * (1 - 0.55 * combined_difficulty), 0.03, max_guess)
    slip = clamp(base_slip + 0.12 * combined_difficulty, 0.03, max_slip)
    learn = clamp(base_learn * (0.8 + 0.7 * combined_difficulty), 0.01, max_learn)

    contextual = model.get("contextual_estimation", {})
    if contextual.get("enabled", False):
        days_gap = context.get("days_since_previous")
        if isinstance(days_gap, (int, float)) and days_gap > 0:
            gap_ratio = min(1.0, float(days_gap) / float(contextual.get("long_gap_days", 7)))
            slip += float(contextual.get("long_gap_slip_boost", 0.06)) * gap_ratio
            guess *= 1 - float(contextual.get("long_gap_guess_discount", 0.1)) * gap_ratio

        attempt_index = int(context.get("attempt_index", 1) or 1)
        if attempt_index > 1:
            learn *= 1 + min(
                float(contextual.get("attempt_learn_boost_cap", 0.35)),
                float(contextual.get("attempt_learn_boost", 0.06)) * (attempt_index - 1),
            )

        source = context.get("source", "")
        activity = context.get("activity", "")
        if source == "classroom" or activity == "class_exercise":
            learn *= float(contextual.get("classroom_learn_multiplier", 0.85))
        elif source in {"outside_class", "self_study"}:
            learn *= float(contextual.get("outside_class_learn_multiplier", 1.08))

        if context.get("has_partial_credit"):
            slip += float(contextual.get("partial_credit_slip_boost", 0.03))
        if context.get("has_error_cause"):
            learn *= float(contextual.get("error_reflection_learn_multiplier", 1.04))

    guess = clamp(guess, 0.03, max_guess)
    slip = clamp(slip, 0.03, max_slip)
    learn = clamp(learn, 0.01, max_learn)
    return guess, slip, learn


def update_bkt(prior, observation, guess, slip, learn):
    p = clamp(prior)
    obs = clamp(observation)

    p_correct = p * (1 - slip) + (1 - p) * guess
    post_correct = p if p_correct <= 0 else p * (1 - slip) / p_correct

    p_wrong = p * slip + (1 - p) * (1 - guess)
    post_wrong = p if p_wrong <= 0 else p * slip / p_wrong

    posterior = obs * post_correct + (1 - obs) * post_wrong
    return clamp(posterior + (1 - posterior) * learn)


def record_error_mentions_topic(record, topic_name):
    return topic_name and topic_matches_text(topic_name, flatten_text(record.get("error_causes")))


def iter_topic_observations(
    subject_name,
    topic_name,
    topic,
    records,
    policy,
    as_of_date=None,
):
    cutoff = parse_date(as_of_date) if as_of_date is not None else None
    observations = []
    for record in records:
        if record.get("subject") != subject_name or not is_observable_practice(record):
            continue

        record_date = parse_date(record.get("date"))
        if record_date is None or (cutoff is not None and record_date > cutoff):
            continue
        problems = record.get("problems") or []
        if problems:
            matched_problem = False
            record_fallback_pending = False
            for problem in problems:
                if not problem_mentions_topic(record, problem, topic_name):
                    continue
                interpretation = interpret_problem_result(problem, record)
                if interpretation["source"] == "record_fallback" or interpretation["value"] is None:
                    record_fallback_pending = True
                    continue
                matched_problem = True
                inferred = infer_problem_difficulty(record, problem)
                observations.append(
                    {
                        "record_id": record.get("id"),
                        "date": record_date,
                        "correctness": interpretation["value"],
                        "result_source": interpretation["source"],
                        "difficulty_score": float(inferred.get("difficulty_score", 55)),
                        "title": problem.get("title", record.get("topic", "")),
                        "source": record.get("source", ""),
                        "activity": record.get("activity", ""),
                        "has_partial_credit": "partial_credit" in problem,
                        "has_error_cause": record_error_mentions_topic(record, topic_name),
                    }
                )
            if matched_problem and not record_fallback_pending:
                continue
            if record_fallback_pending or record_mentions_topic(record, subject_name, topic_name):
                interpretation = interpret_problem_result(None, record)
                correctness = interpretation["value"]
                if correctness is not None:
                    observations.append(
                        {
                            "record_id": record.get("id"),
                            "date": record_date,
                            "correctness": correctness,
                            "result_source": "record_fallback",
                            "difficulty_score": float(topic.get("difficulty", 0.55)) * 100,
                            "title": record.get("topic", topic_name),
                            "source": record.get("source", ""),
                            "activity": record.get("activity", ""),
                            "has_partial_credit": False,
                            "has_error_cause": record_error_mentions_topic(record, topic_name),
                        }
                    )
            continue

        if not record_mentions_topic(record, subject_name, topic_name):
            continue
        interpretation = interpret_problem_result(None, record)
        correctness = interpretation["value"]
        if correctness is None:
            continue
        observations.append(
            {
                "record_id": record.get("id"),
                "date": record_date,
                "correctness": correctness,
                "result_source": interpretation["source"],
                "difficulty_score": float(topic.get("difficulty", 0.55)) * 100,
                "title": record.get("topic", topic_name),
                "source": record.get("source", ""),
                "activity": record.get("activity", ""),
                "has_partial_credit": False,
                "has_error_cause": record_error_mentions_topic(record, topic_name),
            }
        )

    observations.sort(key=lambda item: item["date"] or date.min)
    return dedupe_observations(observations)


def dedupe_observations(observations):
    deduped = []
    seen = set()
    for item in observations:
        key = (
            item.get("record_id"),
            item.get("date"),
            normalize_topic_text(item.get("title")),
            round(float(item.get("correctness", 0)), 3),
            round(float(item.get("difficulty_score", 0)), 1),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def group_observations_by_exercise(observations):
    """Group normalized problem observations by their independent practice record."""
    exercise_groups = {}
    for index, observation in enumerate(observations):
        record_id = observation.get("record_id")
        if record_id is not None:
            key = ("record", str(record_id))
        else:
            key = (
                "legacy",
                observation.get("date"),
                observation.get("source", ""),
                observation.get("activity", ""),
            )
            if key == ("legacy", None, "", ""):
                key = ("observation", index)
        exercise_groups.setdefault(key, []).append(observation)
    return exercise_groups


def adaptive_evidence_confidence(observations, policy):
    """Blend a population prior with faster confidence growth for personal streaks.

    The ordinary evidence count remains problem-level. Personal acceleration is
    based on distinct practice records, so many similar questions in one paper do
    not look like several independent study sessions.
    """
    model = policy.get("bkt_model", {})
    config = model.get("personal_evidence_confidence", {})
    population_strength = max(0.1, float(config.get("population_prior_strength", 6.0)))
    count = len(observations)
    base_confidence = count / (count + population_strength) if count else 0.0
    exercise_groups = group_observations_by_exercise(observations)
    if not observations or not config.get("enabled", True):
        return {
            "evidence_confidence": base_confidence,
            "base_evidence_confidence": base_confidence,
            "effective_prior_strength": population_strength,
            "exercise_count": len(exercise_groups),
            "correct_streak": 0,
            "personal_acceleration": 0.0,
        }

    exercises = []
    for items in exercise_groups.values():
        weights = [0.75 + float(item.get("difficulty_score", 55.0)) / 100 for item in items]
        total_weight = sum(weights) or 1.0
        exercises.append(
            {
                "correctness": sum(float(item.get("correctness", 0.0)) * weight for item, weight in zip(items, weights)) / total_weight,
                "difficulty_score": sum(float(item.get("difficulty_score", 55.0)) * weight for item, weight in zip(items, weights)) / total_weight,
            }
        )

    accuracy_threshold = float(config.get("high_accuracy_threshold", 0.85))
    correct_streak = 0
    streak_items = []
    for exercise in reversed(exercises):
        if exercise["correctness"] < accuracy_threshold:
            break
        correct_streak += 1
        streak_items.append(exercise)

    target_streak = max(1.0, float(config.get("target_streak_exercises", 5.0)))
    streak_ratio = min(1.0, correct_streak / target_streak)
    hard_threshold = float(config.get("hard_difficulty_threshold", 65.0))
    hard_span = max(1.0, float(config.get("hard_difficulty_span", 25.0)))
    if streak_items:
        streak_difficulty = sum(item["difficulty_score"] for item in streak_items) / len(streak_items)
        difficulty_bonus = clamp((streak_difficulty - hard_threshold) / hard_span)
    else:
        difficulty_bonus = 0.0

    personal_acceleration = streak_ratio * (0.75 + 0.25 * difficulty_bonus)
    max_reduction = clamp(float(config.get("max_prior_strength_reduction", 0.67)))
    minimum_strength = max(0.1, float(config.get("min_personal_prior_strength", 2.0)))
    effective_strength = max(
        minimum_strength,
        population_strength * (1.0 - max_reduction * personal_acceleration),
    )
    confidence = count / (count + effective_strength)
    return {
        "evidence_confidence": confidence,
        "base_evidence_confidence": base_confidence,
        "effective_prior_strength": effective_strength,
        "exercise_count": len(exercises),
        "correct_streak": correct_streak,
        "personal_acceleration": personal_acceleration,
    }


def topic_bkt_state(
    subject_name,
    module_name,
    topic,
    records,
    policy,
    as_of_date=None,
    *,
    observations=None,
):
    model = policy.get("bkt_model", {})
    if not model.get("enabled", False):
        return None

    topic_name = topic.get("name", "")
    p = topic_prior(topic, policy)
    initial_prior = p
    if observations is None:
        observations = iter_topic_observations(
            subject_name,
            topic_name,
            topic,
            records,
            policy,
            as_of_date=as_of_date,
        )
    else:
        observations = list(observations)
    last_observation = None
    previous_date = None
    for index, observation in enumerate(observations, start=1):
        current_date = observation.get("date")
        days_since_previous = None
        if previous_date is not None and current_date is not None:
            days_since_previous = max((current_date - previous_date).days, 0)
            p = time_decay_mastery(p, days_since_previous, topic, policy)

        context = {
            "attempt_index": index,
            "days_since_previous": days_since_previous,
            "source": observation.get("source", ""),
            "activity": observation.get("activity", ""),
            "has_partial_credit": observation.get("has_partial_credit", False),
            "has_error_cause": observation.get("has_error_cause", False),
        }
        guess, slip, learn = bkt_params(topic, observation["difficulty_score"], policy, context)
        p = update_bkt(p, observation["correctness"], guess, slip, learn)
        previous_date = current_date or previous_date
        last_observation = {
            **observation,
            "guess": guess,
            "slip": slip,
            "learn": learn,
            "days_since_previous": days_since_previous,
        }

    raw_sequence_probability = p
    evidence_accuracy = None
    evidence_confidence = 0.0
    if observations:
        evidence_accuracy = sum(float(item["correctness"]) for item in observations) / len(observations)
        average_difficulty = sum(float(item["difficulty_score"]) for item in observations) / len(observations)
        difficulty_adjusted_accuracy = clamp(evidence_accuracy + (average_difficulty - 60.0) * 0.0015)
        confidence_state = adaptive_evidence_confidence(observations, policy)
        evidence_confidence = confidence_state["evidence_confidence"]
        evidence_estimate = 0.55 * raw_sequence_probability + 0.45 * difficulty_adjusted_accuracy
        p = clamp((1 - evidence_confidence) * initial_prior + evidence_confidence * evidence_estimate)
    else:
        confidence_state = adaptive_evidence_confidence(observations, policy)

    target_mastery = float(model.get("target_mastery", 0.72))
    high_risk_mastery = float(model.get("high_risk_mastery", 0.45))
    if not observations:
        level = "none"
    elif p < high_risk_mastery:
        level = "high"
    elif p < target_mastery:
        level = "medium"
    else:
        level = "none"

    importance = float(topic.get("importance", 0.7))
    difficulty = float(topic.get("difficulty", 0.5))
    priority = max(0.0, target_mastery - p) * (0.65 + importance) * (0.75 + difficulty)

    return {
        "level": level,
        "mastery_probability": p,
        "target_mastery": target_mastery,
        "priority": priority,
        "subject": subject_name,
        "module": module_name,
        "topic": topic_name,
        "observation_count": len(observations),
        "last_observation": last_observation,
        "raw_sequence_probability": raw_sequence_probability,
        "evidence_accuracy": evidence_accuracy,
        "evidence_confidence": evidence_confidence,
        "base_evidence_confidence": confidence_state["base_evidence_confidence"],
        "effective_prior_strength": confidence_state["effective_prior_strength"],
        "evidence_exercise_count": confidence_state["exercise_count"],
        "correct_exercise_streak": confidence_state["correct_streak"],
        "personal_confidence_acceleration": confidence_state["personal_acceleration"],
    }


def bkt_topic_states(model, records, as_of_date=None):
    policy = model.get("warning_policy", {})
    states = []
    for subject in model.get("subjects", []):
        for module in subject.get("modules", []):
            for topic in module.get("topics", []):
                state = topic_bkt_state(
                    subject["name"],
                    module["name"],
                    topic,
                    records,
                    policy,
                    as_of_date=as_of_date,
                )
                if state:
                    states.append(state)
    states.sort(key=lambda item: item["priority"], reverse=True)
    return states
