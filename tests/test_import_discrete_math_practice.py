from __future__ import annotations

from study_app.data.import_discrete_math_practice import (
    candidate_exercise_starts,
    parse_numbered_questions,
)


def test_parser_keeps_shared_context_and_difficulty_marker() -> None:
    lines = [
        (10, "练习"),
        (10, "1. 判断 p∨q 的真值。"),
        (10, "2. 写出命题的否定。"),
        (11, "练习 3-4 是关于骑士和无赖的共同设定。"),
        (11, "3. A 说 B 是无赖，判断 A 的类型。"),
        (11, "* 4. B 说 A 是骑士，判断 B 的类型。"),
    ]
    starts = candidate_exercise_starts(lines)
    assert starts == [0]
    problems, missing = parse_numbered_questions(lines[starts[0]:])
    assert [item.number for item in problems] == [1, 2, 3, 4]
    assert problems[2].shared_context.startswith("练习 3-4")
    assert problems[3].marker == "*"
    assert missing == []


def test_parser_selects_longest_run_starting_at_one() -> None:
    lines = [
        (1, "1. 正式第一题。"),
        (1, "2. 正式第二题。"),
        (1, "3. 正式第三题。"),
        (2, "1. 后续材料中的孤立编号。"),
    ]
    problems, missing = parse_numbered_questions(lines)
    assert [item.number for item in problems] == [1, 2, 3]
    assert missing == []


def test_parser_preserves_slot_when_one_ocr_number_is_missing() -> None:
    problems, missing = parse_numbered_questions(
        [(1, "1. 第一题。"), (1, "2. 第二题。"), (2, "4. 第四题。"), (2, "5. 第五题。")] 
    )
    assert [item.number for item in problems] == [1, 2, 3, 4, 5]
    assert problems[2].statement.startswith("[OCR 未能可靠提取")
    assert missing == [3]


def test_parser_creates_traceable_records_for_figure_only_range() -> None:
    problems, missing = parse_numbered_questions(
        [
            (20, "练习"),
            (20, "在练习 1-4 中，求所示各图的着色数。"),
            (21, "5. 求图 G 的着色数。"),
        ]
    )
    assert [item.number for item in problems] == [1, 2, 3, 4, 5]
    assert all("参照教材原页" in item.statement for item in problems[:4])
    assert missing == []
