"""
Memory and spaced-repetition model for the learning monitor.

The model is inspired by three ideas:
- Ebbinghaus/Murre-Dros: forgetting is a time-decay curve rather than a fixed
  "days since review" threshold.
- Settles-Meeder HLR: recall probability is p = 2^(-lag / half_life), and
  half-life is estimated from learner/item features.
- Tabibian et al.: review priority should depend on current recall probability,
  so items near or below the target recall are scheduled first.
"""

from __future__ import annotations

import math
from datetime import date, datetime


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_outside_class_review(record):
    activity = record.get("activity") or record.get("activity_type") or ""
    source = record.get("source") or ""
    tags = record.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    review_activities = {
        "review",
        "exercise",
        "quiz",
        "exam_review",
        "self_test",
        "错题",
        "复习",
        "练习",
    }
    classroom_activities = {"class", "lecture", "课堂", "听课"}

    if source in {"outside_class", "self_study", "课外"}:
        return True
    if source in {"classroom", "class", "课堂"}:
        return False
    if activity in classroom_activities:
        return False
    if activity in review_activities:
        return True
    if any(tag in {"outside_class", "self_study", "课外复习", "复习", "练习"} for tag in tags):
        return True
    return False


def record_score(record):
    if isinstance(record.get("score"), (int, float)):
        return float(record["score"])

    result = record.get("result", {})
    correct = result.get("correct")
    total = result.get("total")
    if isinstance(correct, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return 100 * correct / total

    if isinstance(record.get("self_rating"), (int, float)):
        return float(record["self_rating"])

    return None


def record_mentions_topic(record, subject_name, topic_name):
    if record.get("subject") != subject_name:
        return False
    if record.get("topic") == topic_name:
        return True
    fields = [
        record.get("topics"),
        record.get("related_topics"),
        record.get("tags"),
        record.get("note"),
        record.get("error_causes"),
        # A review record may be broad while its individual problems carry the
        # precise knowledge-point mapping. Use those mappings to refresh only
        # the topics that were actually practised.
        record.get("problems"),
    ]
    text = " ".join(_flatten_text(field) for field in fields)
    return topic_name and topic_matches_text(topic_name, text)


def normalize_topic_text(value):
    text = _flatten_text(value).lower()
    drop_chars = set(" \t\r\n\u3000·/／、,，;；:：-—_()（）[]【】")
    return "".join(ch for ch in text if ch not in drop_chars)


def topic_matches_text(topic_name, text):
    aliases = topic_aliases(topic_name)
    haystack = normalize_topic_text(text)
    return any(alias and alias in haystack for alias in aliases)


def topic_aliases(topic_name):
    topic = normalize_topic_text(topic_name)
    aliases = {topic}
    if topic == "哈希表":
        aliases.update({"哈希", "散列表", "散列", "hash", "hashtable", "hashtables"})
    if topic == "哈夫曼树":
        aliases.update({"huffman", "huffmantree"})
    if topic == "复杂度分析":
        aliases.update({"复杂度", "算法复杂度", "递归复杂度", "时间复杂度", "空间复杂度", "递归树", "主定理"})
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


def _flatten_text(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def topic_review_records(records, subject_name, topic_name, as_of_date=None):
    cutoff = parse_date(as_of_date) if as_of_date is not None else None
    matched = []
    for record in records:
        if not record_mentions_topic(record, subject_name, topic_name):
            continue
        if not is_outside_class_review(record):
            continue
        try:
            record_date = parse_date(record.get("date"))
        except (TypeError, ValueError):
            record_date = None
        if record_date and (cutoff is None or record_date <= cutoff):
            matched.append((record_date, record))
    matched.sort(key=lambda item: item[0])
    return matched


def topic_outcome_stats(reviews):
    scored = []
    for _, record in reviews:
        score = record_score(record)
        if score is not None:
            scored.append(score)
    if not scored:
        return {
            "attempt_count": len(reviews),
            "success_rate": None,
            "last_score": None,
        }
    return {
        "attempt_count": len(reviews),
        "success_rate": sum(scored) / (100 * len(scored)),
        "last_score": scored[-1] / 100,
    }


def estimate_half_life(topic, reviews, policy, mastery_override=None):
    model = policy.get("spaced_repetition_model", {})
    weights = model.get("half_life_weights", {})
    base = float(model.get("base_half_life_days", 1.2))
    min_h = float(model.get("min_half_life_days", 0.25))
    max_h = float(model.get("max_half_life_days", 120))

    stats = topic_outcome_stats(reviews)
    review_count = stats["attempt_count"]
    success_rate = stats["success_rate"]
    last_score = stats["last_score"]
    mastery = float(topic.get("mastery", 0) if mastery_override is None else mastery_override)
    difficulty = float(topic.get("difficulty", 0.5))

    x = (
        float(weights.get("bias", 0.0))
        + float(weights.get("mastery", 1.15)) * mastery
        + float(weights.get("review_count_log", 0.55)) * math.log2(1 + review_count)
        + float(weights.get("success_rate", 0.9)) * (success_rate if success_rate is not None else 0.55)
        + float(weights.get("last_score", 0.35)) * (last_score if last_score is not None else 0.55)
        - float(weights.get("difficulty", 0.75)) * difficulty
    )
    half_life = base * (2**x)
    return max(min_h, min(max_h, half_life))


def recall_probability(days_since_review, half_life):
    if days_since_review is None:
        return None
    if half_life <= 0:
        return 0.0
    return 2 ** (-(max(days_since_review, 0) / half_life))


def initial_recall(topic):
    initial_risk = float(topic.get("forgetting_risk", 0))
    return max(0.0, min(1.0, 1 - initial_risk))


_BKT_STATE_UNSET = object()


def topic_memory_state(
    subject_name,
    module_name,
    topic,
    records,
    today,
    policy,
    *,
    bkt_state=_BKT_STATE_UNSET,
):
    today = parse_date(today)
    reviews = topic_review_records(
        records,
        subject_name,
        topic.get("name", ""),
        as_of_date=today,
    )
    model = policy.get("spaced_repetition_model", {})
    target_recall = float(model.get("target_recall", 0.78))
    high_risk_recall = float(model.get("high_risk_recall", 0.45))
    mastery_override = None
    resolved_bkt_state = bkt_state
    if resolved_bkt_state is _BKT_STATE_UNSET:
        try:
            from learning_bkt import topic_bkt_state

            resolved_bkt_state = topic_bkt_state(
                subject_name,
                module_name,
                topic,
                records,
                policy,
                as_of_date=today,
            )
        except Exception:
            resolved_bkt_state = None
    if resolved_bkt_state and resolved_bkt_state.get("observation_count", 0):
        mastery_override = float(resolved_bkt_state["mastery_probability"])

    if reviews:
        last_review = reviews[-1][0]
        days_since = max((today - last_review).days, 0)
        half_life = estimate_half_life(topic, reviews, policy, mastery_override)
        recall = recall_probability(days_since, half_life)
        source = "half_life_regression"
    else:
        last_review = None
        days_since = None
        half_life = estimate_half_life(topic, reviews, policy, mastery_override)
        recall = initial_recall(topic)
        source = "initial_prior"

    recall = max(0.0, min(1.0, recall))
    risk = 1 - recall
    importance = float(topic.get("importance", 0.7))
    difficulty = float(topic.get("difficulty", 0.5))
    urgency = max(0.0, target_recall - recall)
    priority = urgency * (0.65 + importance) * (0.75 + difficulty)

    if recall <= high_risk_recall:
        level = "high"
    elif recall < target_recall:
        level = "medium"
    else:
        level = "none"

    return {
        "level": level,
        "risk": risk,
        "recall": recall,
        "priority": priority,
        "subject": subject_name,
        "module": module_name,
        "topic": topic.get("name", ""),
        "last_review": last_review,
        "days_since": days_since,
        "half_life": half_life,
        "review_count": len(reviews),
        "source": source,
        "target_recall": target_recall,
    }
