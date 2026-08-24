from __future__ import annotations

from confquiz.aggregate import aggregate_question


def test_choice_and_multiple_aggregates(quiz_files):
    _, quiz = quiz_files
    single = aggregate_question(quiz.questions[0], {"u1": "a", "u2": "b", "u3": "a"})
    assert single["responseCount"] == 3
    assert single["options"][0]["count"] == 2
    assert single["options"][0]["percentage"] == 66.7

    multiple = aggregate_question(quiz.questions[1], {"u1": ["a", "b"], "u2": ["b"]})
    assert [option["count"] for option in multiple["options"]] == [1, 2, 0]


def test_histogram_summary_and_correct_reveal(quiz_files):
    _, quiz = quiz_files
    result = aggregate_question(
        quiz.questions[5],
        {"u1": 40, "u2": 50, "u3": 60},
        reveal_correct=True,
    )
    assert result["summary"] == {"mean": 50.0, "median": 50.0, "minimum": 40.0, "maximum": 60.0}
    assert result["correct"] == {"value": 50, "tolerance": 5}
    assert sum(item["count"] for item in result["bins"]) == 3


def test_ranking_aggregate(quiz_files):
    _, quiz = quiz_files
    result = aggregate_question(quiz.questions[6], {"u1": ["a", "b"], "u2": ["b", "a"]})
    assert {item["averageRank"] for item in result["ranking"]} == {1.5}


def test_text_requires_approval_and_word_cloud_filters(quiz_files):
    _, quiz = quiz_files
    texts = aggregate_question(
        quiz.questions[7],
        {"u1": "Visible", "u2": "Hidden"},
        approved_ids={"u1"},
    )
    assert texts["texts"] == ["Visible"]

    cloud = aggregate_question(
        quiz.questions[8],
        {"u1": "Realtime data and realtime answers", "u2": "Hidden words"},
        approved_ids={"u1"},
    )
    assert cloud["words"][0] == {"text": "realtime", "count": 2}
    assert all(word["text"] != "and" for word in cloud["words"])


def test_public_suppression_returns_no_distribution(quiz_files):
    _, quiz = quiz_files
    result = aggregate_question(quiz.questions[0], {"u1": "a"}, suppress=True)
    assert result == {"questionId": "single", "type": "single_choice", "responseCount": 1, "suppressed": True}
