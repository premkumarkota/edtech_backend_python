"""
Unit tests for Study Planner V2 pure logic.

Run from edtech_backend_python/:
  pip install pytest
  pytest tests/test_study_planner_v2.py -v
"""
from datetime import date, timedelta, time as dt_time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.study_plan_engine import (
    calculate_feasibility,
    collect_study_units,
)
from app.services.study_mcq_service import score_mcq_attempt
from app.services.study_rate_limit import check_rate_limit, reset_rate_limits


def _make_chapter(ch_id: int, syllabus_id: int, title: str):
    return SimpleNamespace(
        id=ch_id,
        syllabus_id=syllabus_id,
        title=title,
        order_index=ch_id,
        difficulty="medium",
        estimated_minutes=45,
    )


def _make_syllabus(syl_id: int, title: str):
    return SimpleNamespace(id=syl_id, title=title, category_id=1)


class TestCollectStudyUnitsRequireContent:
    """Chapters without syllabus_contents are skipped when require_content=True."""

    def test_skips_chapters_without_materials(self):
        db = MagicMock()
        syllabus = _make_syllabus(1, "Physics")
        ch_with = _make_chapter(10, 1, "Motion")
        ch_without = _make_chapter(11, 1, "Empty Chapter")

        syllabus_q = MagicMock()
        syllabus_q.filter.return_value.first.return_value = syllabus

        chapter_q = MagicMock()
        chapter_q.filter.return_value.order_by.return_value.all.return_value = [
            ch_with,
            ch_without,
        ]

        content_map = {
            10: [SimpleNamespace(id=101)],
            11: [],
        }
        call_count = {"n": 0}

        def content_query(_model):
            q = MagicMock()

            def filter_impl(*_args, **_kwargs):
                cid = 10 if call_count["n"] == 0 else 11
                call_count["n"] += 1
                q.order_by.return_value.all.return_value = content_map[cid]
                return q

            q.filter.side_effect = filter_impl
            return q

        db.query.side_effect = lambda model: {
            "Syllabus": syllabus_q,
            "Chapter": chapter_q,
        }.get(getattr(model, "__name__", ""), content_query(model))

        units = collect_study_units([1], db, require_content=True)

        assert len(units) == 1
        assert units[0]["chapter_id"] == 10
        assert units[0]["has_materials"] is True
        assert units[0]["content_ids"] == [101]

    def test_includes_all_chapters_when_require_content_false(self):
        db = MagicMock()
        syllabus = _make_syllabus(1, "Maths")
        chapters = [_make_chapter(20, 1, "Algebra"), _make_chapter(21, 1, "No PDF")]

        syllabus_q = MagicMock()
        syllabus_q.filter.return_value.first.return_value = syllabus
        chapter_q = MagicMock()
        chapter_q.filter.return_value.order_by.return_value.all.return_value = chapters

        content_q = MagicMock()
        content_q.filter.return_value.order_by.return_value.all.return_value = []

        db.query.side_effect = lambda model: {
            "Syllabus": syllabus_q,
            "Chapter": chapter_q,
            "SyllabusContent": content_q,
        }[getattr(model, "__name__", "")]

        units = collect_study_units([1], db, require_content=False)

        assert len(units) == 2
        assert all(u["has_materials"] is False for u in units)


class TestCalculateFeasibility:
    """Feasibility dict shape and messaging."""

    REQUIRED_KEYS = {
        "is_feasible",
        "message",
        "daily_topics_avg",
        "total_hours_required",
        "total_days",
        "buffer_days",
    }

    def test_feasible_plan_message_shape(self):
        units = [
            {"adjusted_mins": 60},
            {"adjusted_mins": 45},
            {"adjusted_mins": 30},
        ]
        target = date.today() + timedelta(days=14)
        slots = [
            {"label": "AM", "start": dt_time(9, 0), "end": dt_time(11, 0)},
            {"label": "PM", "start": dt_time(17, 0), "end": dt_time(19, 0)},
        ]

        result = calculate_feasibility(units, target, slots)

        assert self.REQUIRED_KEYS <= set(result.keys())
        assert result["is_feasible"] is True
        assert isinstance(result["message"], str) and len(result["message"]) > 0
        assert result["total_days"] == 14
        assert result["buffer_days"] >= 0

    def test_infeasible_plan_message(self):
        units = [{"adjusted_mins": 60}] * 30
        target = date.today() + timedelta(days=3)
        slots = [{"label": "AM", "start": dt_time(9, 0), "end": dt_time(10, 0)}]

        result = calculate_feasibility(units, target, slots)

        assert result["is_feasible"] is False
        assert "Not enough time" in result["message"]
        assert result["buffer_days"] == 0

    def test_past_target_date(self):
        result = calculate_feasibility([], date.today(), [])
        assert result["is_feasible"] is False
        assert "future" in result["message"].lower()


class TestScoreMcqAttempt:
    """Basic MCQ scoring."""

    def test_all_correct_scores_100(self):
        bank = SimpleNamespace(
            questions=[
                {"question": "Q1", "correct": "A", "explanation": "Because A"},
                {"question": "Q2", "correct": "B", "explanation": "Because B"},
            ]
        )
        answers = [
            {"question_index": 0, "selected": "a"},
            {"question_index": 1, "selected": "B"},
        ]

        score, results = score_mcq_attempt(bank, answers)

        assert score == 100.0
        assert len(results) == 2
        assert all(r["is_correct"] for r in results)

    def test_partial_score_and_missing_answers(self):
        bank = SimpleNamespace(
            questions=[
                {"question": "Q1", "correct": "A", "explanation": ""},
                {"question": "Q2", "correct": "C", "explanation": ""},
                {"question": "Q3", "correct": "D", "explanation": ""},
                {"question": "Q4", "correct": "B", "explanation": ""},
            ]
        )
        answers = [
            {"question_index": 0, "selected": "A"},
            {"question_index": 1, "selected": "B"},
        ]

        score, results = score_mcq_attempt(bank, answers)

        assert score == 25.0
        assert results[0]["is_correct"] is True
        assert results[1]["is_correct"] is False
        assert results[2]["selected"] == ""
        assert results[2]["is_correct"] is False

    def test_empty_bank_returns_zero(self):
        bank = SimpleNamespace(questions=[])
        score, results = score_mcq_attempt(bank, [])
        assert score == 0.0
        assert results == []


class TestRateLimit:
    def setup_method(self):
        reset_rate_limits()

    def test_allows_under_limit(self):
        allowed, msg = check_rate_limit("test:1", max_calls=3, window_hours=1.0)
        assert allowed is True
        assert msg is None

    def test_blocks_over_limit(self):
        for _ in range(3):
            check_rate_limit("test:2", max_calls=3, window_hours=1.0)
        allowed, msg = check_rate_limit("test:2", max_calls=3, window_hours=1.0)
        assert allowed is False
        assert msg and "Rate limit exceeded" in msg
