from __future__ import annotations

import math
from datetime import date

import pytest

from study_app.core.local_practice_spec import (
    LocalPracticePaperSpec,
    LocalPracticeSpecError,
    safe_filename_component,
)


def make_spec(**overrides):
    values = {
        "subject": " 高等数学 ",
        "template_id": " CALC-SERIES ",
        "topic": " 幂级数  端点 ",
        "target_difficulty": 60,
        "question_count": 6,
        "paper_date": "2026-09-17",
        "title": "",
    }
    values.update(overrides)
    return LocalPracticePaperSpec.create(**values)


def test_request_is_normalized_and_canonical() -> None:
    spec = make_spec()
    assert spec.subject == "高等数学"
    assert spec.topic == "幂级数 端点"
    assert spec.title == "高等数学 本地练习卷"
    assert spec.canonical_json() == (
        '{"paper_date":"2026-09-17","question_count":6,"subject":"高等数学",'
        '"target_difficulty":60,"template_id":"CALC-SERIES",'
        '"title":"高等数学 本地练习卷","topic":"幂级数 端点"}'
    )
    assert make_spec(paper_date=date(2026, 9, 17)) == spec


@pytest.mark.parametrize("value", [True, False, "60", math.nan, math.inf, -0.1, 100.1, None])
def test_invalid_difficulty_is_rejected(value) -> None:
    with pytest.raises(LocalPracticeSpecError):
        make_spec(target_difficulty=value)


@pytest.mark.parametrize("value", [True, "6", 0, 31, 2.5, None])
def test_invalid_question_count_is_rejected(value) -> None:
    with pytest.raises(LocalPracticeSpecError):
        make_spec(question_count=value)


@pytest.mark.parametrize("value", ["2026-02-30", "2026-9-7", "", 20260917])
def test_invalid_date_is_rejected(value) -> None:
    with pytest.raises(LocalPracticeSpecError):
        make_spec(paper_date=value)


def test_safe_file_stem_blocks_windows_names_and_path_characters() -> None:
    assert safe_filename_component("CON") == "_CON"
    assert safe_filename_component('  高数:级数 / 练习*卷?  ') == "高数_级数_练习_卷"
    spec = make_spec(subject="高数/级数")
    stem = spec.file_stem("a" * 64)
    assert stem == "高数_级数_2026-09-17_aaaaaaaaaaaa"
    assert "/" not in stem and "\\" not in stem


def test_invalid_paper_id_is_rejected() -> None:
    with pytest.raises(LocalPracticeSpecError):
        make_spec().file_stem("not-a-sha")
