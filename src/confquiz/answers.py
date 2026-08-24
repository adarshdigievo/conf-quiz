from __future__ import annotations

import math
from typing import Any

from confquiz.models import Question, QuestionType


class AnswerError(ValueError):
    """Raised when a submitted answer does not match its question."""


def validate_answer(question: Question, answer: Any) -> Any:
    option_ids = {option.id for option in question.options}
    if question.type == QuestionType.SINGLE_CHOICE:
        if not isinstance(answer, str) or answer not in option_ids:
            raise AnswerError("Choose one of the available options")
        return answer
    if question.type == QuestionType.MULTIPLE_CHOICE:
        if not isinstance(answer, list) or not answer:
            raise AnswerError("Choose at least one option")
        if len(answer) != len(set(answer)) or not set(answer) <= option_ids:
            raise AnswerError("Selections must be unique available options")
        if len(answer) > (question.max_selections or len(option_ids)):
            raise AnswerError("Too many options selected")
        return answer
    if question.type == QuestionType.YES_NO:
        if answer not in {"yes", "no"}:
            raise AnswerError("Choose yes or no")
        return answer
    if question.type in {QuestionType.SLIDER, QuestionType.RATING, QuestionType.NUMBER}:
        if isinstance(answer, bool) or not isinstance(answer, (int, float)) or not math.isfinite(answer):
            raise AnswerError("Enter a valid number")
        if question.minimum is not None and answer < question.minimum:
            raise AnswerError(f"Answer must be at least {question.minimum:g}")
        if question.maximum is not None and answer > question.maximum:
            raise AnswerError(f"Answer must be at most {question.maximum:g}")
        if question.step:
            origin = question.minimum or 0
            quotient = (float(answer) - origin) / question.step
            if not math.isclose(quotient, round(quotient), abs_tol=1e-7):
                raise AnswerError(f"Answer must use increments of {question.step:g}")
        return answer
    if question.type == QuestionType.RANKING:
        if not isinstance(answer, list) or len(answer) != len(option_ids):
            raise AnswerError("Rank every option")
        if len(answer) != len(set(answer)) or set(answer) != option_ids:
            raise AnswerError("Ranking must contain every option exactly once")
        return answer
    if question.type in {QuestionType.FREE_TEXT, QuestionType.WORD_CLOUD}:
        if not isinstance(answer, str) or not answer.strip():
            raise AnswerError("Enter a response")
        cleaned = " ".join(answer.strip().split())
        if len(cleaned) > question.max_length:
            raise AnswerError(f"Response must be {question.max_length} characters or fewer")
        return cleaned
    raise AnswerError("Unsupported question type")
