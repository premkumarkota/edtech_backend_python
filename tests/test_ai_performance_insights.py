"""Unit tests for combined Mock Test + AI Planner weak-area insights."""
from app.services.ai_study_planner_service import build_performance_insights


def test_combines_mock_and_planner_scores_per_subject():
    insights = build_performance_insights([
        {"subject": "Physics", "score_pct": 80, "source": "mock_test"},
        {"subject": "Physics", "score_pct": 40, "source": "planner_quiz",
         "chapter": "Thermodynamics", "topic": "Heat engines"},
        {"subject": "Maths", "score_pct": 92, "source": "mock_test"},
    ])

    physics = next(s for s in insights["subject_averages"] if s["subject"] == "Physics")
    assert physics["mock_test_avg"] == 80
    assert physics["planner_quiz_avg"] == 40
    assert physics["attempts"] == 2

    weak_names = [w["subject"] for w in insights["weak_areas"]]
    assert "Physics" in weak_names
    assert "Maths" not in weak_names

    physics_weak = next(w for w in insights["weak_areas"] if w["subject"] == "Physics")
    assert "AI Planner quizzes" in physics_weak["why"]
    assert "Mock Tests" not in physics_weak["why"]

    assert insights["topic_weak_areas"][0]["topic"] == "Heat engines"
    assert insights["topic_weak_areas"][0]["score_pct"] == 40


def test_lists_all_weak_subjects_from_both_sources():
    insights = build_performance_insights([
        {"subject": "Chemistry", "score_pct": 45, "source": "mock_test"},
        {"subject": "Biology", "score_pct": 50, "source": "planner_quiz", "topic": "Cells"},
        {"subject": "English", "score_pct": 88, "source": "mock_test"},
        {"subject": "English", "score_pct": 91, "source": "planner_quiz"},
    ])
    weak = {w["subject"] for w in insights["weak_areas"]}
    assert weak == {"Chemistry", "Biology"}
    chem = next(w for w in insights["weak_areas"] if w["subject"] == "Chemistry")
    assert chem["planner_quiz_avg"] is None
    bio = next(w for w in insights["weak_areas"] if w["subject"] == "Biology")
    assert bio["mock_test_avg"] is None


def test_empty_records_are_safe():
    insights = build_performance_insights([])
    assert insights["subject_averages"] == []
    assert insights["weak_areas"] == []
    assert insights["topic_weak_areas"] == []
