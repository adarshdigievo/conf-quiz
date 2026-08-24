from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from confquiz.answers import AnswerError, validate_answer
from confquiz.config import load_quiz_config
from confquiz.models import PresentationConfig, Question, ThemeConfig


def test_every_question_type_loads(quiz_files):
    _, quiz = quiz_files
    assert {question.type.value for question in quiz.questions} == {
        "single_choice",
        "multiple_choice",
        "yes_no",
        "slider",
        "rating",
        "number",
        "ranking",
        "free_text",
        "word_cloud",
    }
    assert quiz.questions[4].minimum == 1
    assert quiz.questions[4].maximum == 5


def test_packaged_example_configuration_loads():
    quiz = load_quiz_config(Path("src/confquiz/scaffold/quiz.yml"))
    assert quiz.firebase.web_config == "firebase.web.json"
    yes_no = next(question for question in quiz.questions if question.type.value == "yes_no")
    assert yes_no.correct == "yes"


def test_public_payload_excludes_correct_answer(quiz_files):
    _, quiz = quiz_files
    payload = quiz.questions[0].public_payload()
    assert "correct" not in payload
    assert payload["optionIds"] == ["a", "b"]


@pytest.mark.parametrize(
    ("question_index", "answer", "expected"),
    [
        (0, "a", "a"),
        (1, ["a", "c"], ["a", "c"]),
        (2, "yes", "yes"),
        (3, 7, 7),
        (4, 4, 4),
        (5, 50, 50),
        (6, ["b", "a"], ["b", "a"]),
        (7, "  hello   world  ", "hello world"),
        (8, "realtime useful", "realtime useful"),
    ],
)
def test_answer_validation_for_all_types(quiz_files, question_index, answer, expected):
    _, quiz = quiz_files
    assert validate_answer(quiz.questions[question_index], answer) == expected


@pytest.mark.parametrize(
    ("question_index", "answer"),
    [
        (0, "missing"),
        (1, ["a", "b", "c"]),
        (2, "maybe"),
        (3, 11),
        (4, 2.5),
        (5, 52),
        (6, ["a", "a"]),
        (7, ""),
        (8, "x" * 101),
    ],
)
def test_invalid_answers_are_rejected(quiz_files, question_index, answer):
    _, quiz = quiz_files
    with pytest.raises(AnswerError):
        validate_answer(quiz.questions[question_index], answer)


def test_bad_correct_option_is_rejected():
    with pytest.raises(ValidationError):
        Question.model_validate(
            {
                "id": "bad",
                "after_slide": 1,
                "type": "single_choice",
                "prompt": "Bad",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "correct": "c",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "bad-range",
            "after_slide": 1,
            "type": "rating",
            "prompt": "Bad range",
            "min": 1,
            "max": 5,
            "step": 1,
            "correct": 2.5,
        },
        {
            "id": "bad-bool",
            "after_slide": 1,
            "type": "number",
            "prompt": "Bad boolean",
            "min": 0,
            "max": 1,
            "correct": True,
        },
        {
            "id": "bad-multiple",
            "after_slide": 1,
            "type": "multiple_choice",
            "prompt": "Bad multiple",
            "max_selections": 1,
            "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "correct": ["a", "b"],
        },
    ],
)
def test_invalid_correct_answer_shapes_are_rejected(payload):
    with pytest.raises(ValidationError):
        Question.model_validate(payload)


def test_public_url_must_be_absolute():
    with pytest.raises(ValidationError):
        PresentationConfig.model_validate(
            {
                "id": "talk",
                "title": "Talk",
                "speaker": "Speaker",
                "public_url": "/relative/",
                "slides": {"source": "slides.pdf"},
            }
        )


@pytest.mark.parametrize("preset", ["light", "dark", "grey", "navy", "warm", "ocean", "forest"])
def test_presenter_theme_presets_are_valid(preset):
    assert ThemeConfig(preset=preset).preset == preset
