from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from confquiz.aggregate import aggregate_question
from confquiz.models import Question, QuizConfig


@dataclass(frozen=True)
class TimelineItem:
    kind: Literal["slide", "question"]
    slide: int | None = None
    question_id: str | None = None


def build_timeline(page_count: int, questions: list[Question]) -> list[TimelineItem]:
    by_slide: dict[int, list[Question]] = {}
    for question in questions:
        by_slide.setdefault(question.after_slide, []).append(question)
    items: list[TimelineItem] = []
    for page in range(1, page_count + 1):
        items.append(TimelineItem(kind="slide", slide=page))
        items.extend(
            TimelineItem(kind="question", question_id=question.id) for question in by_slide.get(page, [])
        )
    return items


class SessionController:
    def __init__(self, quiz: QuizConfig, page_count: int) -> None:
        self.quiz = quiz
        self.page_count = page_count
        self.timeline = build_timeline(page_count, quiz.questions)
        self.questions = {question.id: question for question in quiz.questions}
        self.index = 0
        self.phase = "slide"
        self.responses: dict[str, dict[str, Any]] = {question.id: {} for question in quiz.questions}
        self.approved: dict[str, set[str]] = {question.id: set() for question in quiz.questions}
        self.participant_count = 0
        self.join_code = ""
        self.session_id = ""
        self.show_results_on_presenter = quiz.session.show_results_on_presenter
        self.share_slides_with_attendees = quiz.session.share_slides_with_attendees

    @property
    def current_item(self) -> TimelineItem | None:
        if 0 <= self.index < len(self.timeline):
            return self.timeline[self.index]
        return None

    @property
    def current_question(self) -> Question | None:
        item = self.current_item
        if item and item.kind == "question" and item.question_id:
            return self.questions[item.question_id]
        return None

    def _activate(self, index: int) -> None:
        if index < 0:
            self.index = 0
            self.phase = "slide"
            return
        if index >= len(self.timeline):
            self.index = len(self.timeline)
            self.phase = "ended"
            return
        self.index = index
        self.phase = "slide" if self.timeline[index].kind == "slide" else "open"

    def next(self) -> None:
        question = self.current_question
        if question:
            if self.phase == "open":
                self.phase = "results"
                return
            if self.phase == "results" and question.correct is not None:
                self.phase = "revealed"
                return
        self._activate(self.index + 1)

    def previous(self) -> None:
        if self.phase == "ended":
            self._activate(len(self.timeline) - 1)
        else:
            self._activate(self.index - 1)

    def close_question(self) -> None:
        if self.current_question and self.phase == "open":
            self.phase = "results"

    def reveal(self) -> None:
        if self.current_question and self.phase in {"open", "results"}:
            self.phase = "revealed"

    def restart(self) -> None:
        self._activate(0)

    def end(self) -> None:
        self.index = len(self.timeline)
        self.phase = "ended"

    def set_responses(self, question_id: str, responses: dict[str, Any]) -> None:
        self.responses[question_id] = responses

    def set_participant_count(self, count: int) -> None:
        self.participant_count = max(0, count)

    def toggle_presenter_results(self) -> None:
        self.show_results_on_presenter = not self.show_results_on_presenter

    @property
    def presenter_results_forced(self) -> bool:
        """Keep results visible once voting closes, including answer reveal."""
        return self.current_question is not None and self.phase in {"results", "revealed"}

    @property
    def presenter_results_visible(self) -> bool:
        return self.presenter_results_forced or self.show_results_on_presenter

    def toggle_slide_sharing(self) -> None:
        self.share_slides_with_attendees = not self.share_slides_with_attendees

    def moderate(self, question_id: str, uid: str, approved: bool) -> None:
        if approved:
            self.approved[question_id].add(uid)
        else:
            self.approved[question_id].discard(uid)

    def clear_question(self, question_id: str) -> None:
        self.responses[question_id] = {}
        self.approved[question_id] = set()
        if self.current_question and self.current_question.id == question_id:
            self.phase = "open"

    def clear_all(self) -> None:
        for question_id in self.responses:
            self.responses[question_id] = {}
            self.approved[question_id] = set()
        self.restart()

    def state_document(self) -> dict[str, Any]:
        item = self.current_item
        return {
            "status": "ended" if self.phase == "ended" else "running",
            "phase": self.phase,
            "timelineIndex": self.index,
            "activeSlide": item.slide if item and item.kind == "slide" else None,
            "activeQuestionId": item.question_id if item and item.kind == "question" else None,
            "showResultsToAttendees": self.quiz.session.show_results_on_attendee_devices,
            "shareSlidesWithAttendees": self.share_slides_with_attendees,
            "allowAnswerChanges": self.quiz.session.allow_answer_changes,
        }

    def restore_state(self, document: dict[str, Any]) -> None:
        index = document.get("timelineIndex")
        phase = document.get("phase")
        if type(index) is not int or not 0 <= index < len(self.timeline):
            raise ValueError("the saved timeline position is invalid")

        item = self.timeline[index]
        if item.kind == "slide":
            if (
                phase != "slide"
                or document.get("activeSlide") != item.slide
                or document.get("activeQuestionId") is not None
            ):
                raise ValueError("the saved slide state is inconsistent")
        elif (
            phase not in {"open", "results", "revealed"}
            or document.get("activeQuestionId") != item.question_id
            or document.get("activeSlide") is not None
        ):
            raise ValueError("the saved question state is inconsistent")

        share_slides = document.get("shareSlidesWithAttendees")
        if not isinstance(share_slides, bool):
            raise ValueError("the saved slide-sharing preference is invalid")

        self.index = index
        self.phase = phase
        self.share_slides_with_attendees = share_slides

    def aggregate(self, question: Question | None = None, *, public: bool = False) -> dict[str, Any] | None:
        question = question or self.current_question
        if question is None:
            return None
        responses = self.responses[question.id]
        suppress = public and len(responses) < self.quiz.session.minimum_public_responses
        return aggregate_question(
            question,
            responses,
            approved_ids=self.approved[question.id],
            reveal_correct=self.phase == "revealed" and self.current_question == question,
            suppress=suppress,
        )

    def presenter_payload(self, join_url: str) -> dict[str, Any]:
        item = self.current_item
        question = self.current_question
        moderation: list[dict[str, Any]] = []
        if question and question.type.value in {"free_text", "word_cloud"}:
            moderation = [
                {
                    "uid": uid,
                    "label": f"Attendee {uid[:6]}",
                    "text": str(answer),
                    "approved": uid in self.approved[question.id],
                }
                for uid, answer in self.responses[question.id].items()
            ]
        return {
            "presentation": {
                "id": self.quiz.presentation.id,
                "title": self.quiz.presentation.title,
                "speaker": self.quiz.presentation.speaker,
                "theme": self.quiz.presentation.theme.model_dump(mode="json"),
                "pageCount": self.page_count,
            },
            "session": {
                "id": self.session_id,
                "joinCode": self.join_code,
                "joinUrl": join_url,
                "phase": self.phase,
                "timelineIndex": self.index,
                "timelineLength": len(self.timeline),
                "activeSlide": item.slide if item and item.kind == "slide" else None,
                "activeQuestion": (
                    {**question.public_payload(), "hasCorrect": question.correct is not None}
                    if question
                    else None
                ),
                "responseCount": len(self.responses[question.id]) if question else 0,
                "participantCount": self.participant_count,
                "showResultsOnPresenter": self.presenter_results_visible,
                "presenterResultsPreference": self.show_results_on_presenter,
                "presenterResultsForced": self.presenter_results_forced,
                "shareSlidesWithAttendees": self.share_slides_with_attendees,
                "aggregate": self.aggregate(),
                "moderation": moderation,
            },
        }
