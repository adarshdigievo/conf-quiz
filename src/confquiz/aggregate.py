from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from statistics import mean, median
from typing import Any

from confquiz.models import Question, QuestionType

WORD_PATTERN = re.compile(r"[\w'-]{2,}", re.UNICODE)
STOP_WORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "but",
    "for",
    "from",
    "have",
    "into",
    "just",
    "more",
    "not",
    "that",
    "the",
    "their",
    "this",
    "was",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "you",
    "your",
}


def _numeric_values(responses: Iterable[Any]) -> list[float]:
    return [float(value) for value in responses if isinstance(value, (int, float)) and math.isfinite(value)]


def _choice_options(question: Question) -> list[dict[str, Any]]:
    if question.type == QuestionType.YES_NO:
        return [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]
    return [option.model_dump(mode="json") for option in question.options]


def _choice_aggregate(question: Question, answers: list[Any]) -> dict[str, Any]:
    options = _choice_options(question)
    counts: Counter[str] = Counter()
    if question.type == QuestionType.MULTIPLE_CHOICE:
        for answer in answers:
            if isinstance(answer, list):
                counts.update(str(value) for value in set(answer))
    else:
        counts.update(str(answer) for answer in answers if isinstance(answer, str))
    total = len(answers)
    return {
        "options": [
            {
                **option,
                "count": counts[option["id"]],
                "percentage": round(counts[option["id"]] * 100 / total, 1) if total else 0,
            }
            for option in options
        ]
    }


def _histogram(question: Question, answers: list[Any]) -> dict[str, Any]:
    values = _numeric_values(answers)
    if not values:
        return {"bins": [], "summary": None}
    minimum = question.minimum if question.minimum is not None else min(values)
    maximum = question.maximum if question.maximum is not None else max(values)
    if minimum == maximum:
        maximum = minimum + 1
    configured_step = question.step or (maximum - minimum) / 10
    bin_width = max(configured_step, (maximum - minimum) / 12)
    bin_count = max(1, min(12, math.ceil((maximum - minimum) / bin_width)))
    bin_width = (maximum - minimum) / bin_count
    bins = [0 for _ in range(bin_count)]
    for value in values:
        index = min(bin_count - 1, max(0, int((value - minimum) / bin_width)))
        bins[index] += 1
    return {
        "bins": [
            {
                "start": round(minimum + index * bin_width, 4),
                "end": round(minimum + (index + 1) * bin_width, 4),
                "count": count,
            }
            for index, count in enumerate(bins)
        ],
        "summary": {
            "mean": round(mean(values), 2),
            "median": round(median(values), 2),
            "minimum": min(values),
            "maximum": max(values),
        },
    }


def _ranking(question: Question, answers: list[Any]) -> dict[str, Any]:
    positions: dict[str, list[int]] = {option.id: [] for option in question.options}
    for answer in answers:
        if not isinstance(answer, list):
            continue
        for index, option_id in enumerate(answer):
            if option_id in positions:
                positions[option_id].append(index + 1)
    ranked = []
    for option in question.options:
        observed = positions[option.id]
        ranked.append(
            {
                "id": option.id,
                "label": option.label,
                "averageRank": round(mean(observed), 2) if observed else None,
                "positionCounts": [observed.count(index + 1) for index in range(len(question.options))],
            }
        )
    ranked.sort(key=lambda item: item["averageRank"] if item["averageRank"] is not None else math.inf)
    return {"ranking": ranked}


def _text_aggregate(
    question: Question,
    response_items: list[tuple[str, Any]],
    approved_ids: set[str],
) -> dict[str, Any]:
    approved = [
        str(answer).strip()
        for uid, answer in response_items
        if uid in approved_ids and isinstance(answer, str) and answer.strip()
    ]
    if question.type == QuestionType.FREE_TEXT:
        return {"texts": approved}
    words: Counter[str] = Counter()
    for answer in approved:
        words.update(
            word.lower()
            for word in WORD_PATTERN.findall(answer)
            if word.lower() not in STOP_WORDS and not word.isdigit()
        )
    return {
        "words": [
            {"text": word, "count": count}
            for word, count in sorted(words.items(), key=lambda item: (-item[1], item[0]))[:60]
        ]
    }


def aggregate_question(
    question: Question,
    responses: dict[str, Any],
    *,
    approved_ids: set[str] | None = None,
    reveal_correct: bool = False,
    suppress: bool = False,
) -> dict[str, Any]:
    response_items = list(responses.items())
    answers = [answer for _, answer in response_items]
    aggregate: dict[str, Any] = {
        "questionId": question.id,
        "type": question.type.value,
        "responseCount": len(answers),
        "suppressed": suppress,
    }
    if suppress:
        return aggregate

    if question.type in {
        QuestionType.SINGLE_CHOICE,
        QuestionType.MULTIPLE_CHOICE,
        QuestionType.YES_NO,
    }:
        aggregate.update(_choice_aggregate(question, answers))
    elif question.type in {QuestionType.SLIDER, QuestionType.RATING, QuestionType.NUMBER}:
        aggregate.update(_histogram(question, answers))
    elif question.type == QuestionType.RANKING:
        aggregate.update(_ranking(question, answers))
    else:
        aggregate.update(_text_aggregate(question, response_items, approved_ids or set()))

    if reveal_correct and question.correct is not None:
        aggregate["correct"] = question.correct
    return aggregate
