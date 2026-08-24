from __future__ import annotations

import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
JOIN_CODE_PATTERN = re.compile(r"^[A-Z2-9]{4,12}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    YES_NO = "yes_no"
    SLIDER = "slider"
    RATING = "rating"
    NUMBER = "number"
    RANKING = "ranking"
    FREE_TEXT = "free_text"
    WORD_CLOUD = "word_cloud"


class Option(StrictModel):
    id: str
    label: str = Field(min_length=1, max_length=160)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not ID_PATTERN.fullmatch(value):
            raise ValueError(
                "option id must start with a letter and contain lowercase letters, numbers, or hyphens"
            )
        return value


class RangeLabels(StrictModel):
    minimum: str | None = Field(default=None, alias="min", max_length=80)
    maximum: str | None = Field(default=None, alias="max", max_length=80)


class ResultsConfig(StrictModel):
    display: Literal["bars", "histogram", "summary", "ranking", "cards", "word_cloud"] | None = None
    attendee_visibility: Literal["live", "after_close", "after_reveal", "never"] = "live"
    show_mean: bool = True
    show_median: bool = True


class Question(StrictModel):
    id: str
    after_slide: int = Field(ge=1)
    type: QuestionType
    prompt: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=500)
    options: list[Option] = Field(default_factory=list, max_length=20)
    minimum: float | None = Field(default=None, alias="min")
    maximum: float | None = Field(default=None, alias="max")
    step: float | None = Field(default=None, gt=0)
    labels: RangeLabels | None = None
    max_selections: int | None = Field(default=None, ge=1, le=20)
    max_length: int = Field(default=240, ge=1, le=1000)
    placeholder: str | None = Field(default=None, max_length=160)
    correct: Any | None = None
    results: ResultsConfig = Field(default_factory=ResultsConfig)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not ID_PATTERN.fullmatch(value):
            raise ValueError(
                "question id must start with a letter and contain lowercase letters, numbers, or hyphens"
            )
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> Question:
        option_types = {
            QuestionType.SINGLE_CHOICE,
            QuestionType.MULTIPLE_CHOICE,
            QuestionType.RANKING,
        }
        if self.type in option_types and len(self.options) < 2:
            raise ValueError(f"{self.type.value} requires at least two options")
        if self.type not in option_types and self.options:
            raise ValueError(f"{self.type.value} does not accept options")

        option_ids = [option.id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option ids must be unique within a question")

        range_types = {QuestionType.SLIDER, QuestionType.RATING, QuestionType.NUMBER}
        if self.type in range_types:
            if self.type == QuestionType.RATING:
                self.minimum = 1 if self.minimum is None else self.minimum
                self.maximum = 5 if self.maximum is None else self.maximum
                self.step = 1 if self.step is None else self.step
            elif self.type == QuestionType.SLIDER:
                self.minimum = 0 if self.minimum is None else self.minimum
                self.maximum = 10 if self.maximum is None else self.maximum
                self.step = 1 if self.step is None else self.step
            elif self.minimum is None or self.maximum is None:
                raise ValueError("number questions require min and max")
            if self.minimum is None or self.maximum is None or self.minimum >= self.maximum:
                raise ValueError("min must be smaller than max")
        elif any(value is not None for value in (self.minimum, self.maximum, self.step, self.labels)):
            raise ValueError(f"range fields are not valid for {self.type.value}")

        if self.type == QuestionType.MULTIPLE_CHOICE:
            self.max_selections = self.max_selections or len(self.options)
            if self.max_selections > len(self.options):
                raise ValueError("max_selections cannot exceed the option count")
        elif self.max_selections is not None:
            raise ValueError("max_selections is only valid for multiple_choice")

        if self.correct is not None:
            self._validate_correct(option_ids)

        if self.results.display is None:
            self.results.display = {
                QuestionType.SINGLE_CHOICE: "bars",
                QuestionType.MULTIPLE_CHOICE: "bars",
                QuestionType.YES_NO: "bars",
                QuestionType.SLIDER: "histogram",
                QuestionType.RATING: "histogram",
                QuestionType.NUMBER: "histogram",
                QuestionType.RANKING: "ranking",
                QuestionType.FREE_TEXT: "cards",
                QuestionType.WORD_CLOUD: "word_cloud",
            }[self.type]
        return self

    def _validate_correct(self, option_ids: list[str]) -> None:
        if self.type == QuestionType.SINGLE_CHOICE:
            if not isinstance(self.correct, str) or self.correct not in option_ids:
                raise ValueError("single_choice correct must be an option id")
        elif self.type == QuestionType.MULTIPLE_CHOICE:
            if (
                not isinstance(self.correct, list)
                or not self.correct
                or len(self.correct) != len(set(self.correct))
                or not set(self.correct) <= set(option_ids)
                or len(self.correct) > (self.max_selections or len(option_ids))
            ):
                raise ValueError("multiple_choice correct must contain unique valid option ids")
        elif self.type == QuestionType.YES_NO:
            if self.correct not in {"yes", "no"}:
                raise ValueError("yes_no correct must be 'yes' or 'no'")
        elif self.type in {QuestionType.SLIDER, QuestionType.RATING}:
            if not self._valid_correct_number(self.correct):
                raise ValueError("range question correct must be numeric")
            self._validate_correct_range(float(self.correct))
        elif self.type == QuestionType.NUMBER:
            valid_number = self._valid_correct_number(self.correct)
            valid_tolerance = (
                isinstance(self.correct, dict)
                and set(self.correct) <= {"value", "tolerance"}
                and self._valid_correct_number(self.correct.get("value"))
                and self._valid_correct_number(self.correct.get("tolerance", 0))
                and self.correct.get("tolerance", 0) >= 0
            )
            if not valid_number and not valid_tolerance:
                raise ValueError("number correct must be numeric or {value, tolerance}")
            value = self.correct if valid_number else self.correct["value"]
            self._validate_correct_range(float(value))
        else:
            raise ValueError(f"correct answers are not supported for {self.type.value}")

    @staticmethod
    def _valid_correct_number(value: Any) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    def _validate_correct_range(self, value: float) -> None:
        if self.minimum is not None and value < self.minimum:
            raise ValueError("correct answer must be within the configured range")
        if self.maximum is not None and value > self.maximum:
            raise ValueError("correct answer must be within the configured range")
        if self.step:
            origin = self.minimum or 0
            quotient = (value - origin) / self.step
            if not math.isclose(quotient, round(quotient), abs_tol=1e-7):
                raise ValueError("correct answer must follow the configured step")

    def public_payload(self) -> dict[str, Any]:
        """Return the attendee-visible question definition without its answer key."""
        payload = self.model_dump(by_alias=True, exclude={"correct"}, mode="json")
        if self.type == QuestionType.YES_NO:
            payload["options"] = [
                {"id": "yes", "label": "Yes"},
                {"id": "no", "label": "No"},
            ]
        payload["optionIds"] = [option["id"] for option in payload["options"]]
        return payload


class SlidesConfig(StrictModel):
    source: str = Field(min_length=1)
    fetch_during_export: bool = True
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class ThemeConfig(StrictModel):
    preset: Literal["light", "dark", "grey", "navy", "warm", "ocean", "forest"] = "light"
    accent: str = Field(default="#ff5a36", pattern=r"^#[0-9a-fA-F]{6}$")
    background: str = Field(default="#101310", pattern=r"^#[0-9a-fA-F]{6}$")
    logo: str | None = None


class PresentationConfig(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=200)
    speaker: str = Field(min_length=1, max_length=120)
    public_url: str = Field(min_length=1)
    slides: SlidesConfig
    theme: ThemeConfig = Field(default_factory=ThemeConfig)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not ID_PATTERN.fullmatch(value):
            raise ValueError("presentation id must use lowercase letters, numbers, and hyphens")
        return value

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("public_url must be an absolute HTTP or HTTPS URL")
        return value


class FirebaseSettings(StrictModel):
    web_config: str = "firebase.web.json"
    app_check_site_key: str | None = None
    namespace: Literal["confquiz"] = "confquiz"


class JoinCodeConfig(StrictModel):
    mode: Literal["generated", "manual"] = "generated"
    length: int = Field(default=6, ge=4, le=12)
    value: str | None = None

    @model_validator(mode="after")
    def validate_manual_value(self) -> JoinCodeConfig:
        if self.mode == "manual":
            if not self.value or not JOIN_CODE_PATTERN.fullmatch(self.value.upper()):
                raise ValueError("manual join code must be 4-12 characters using A-Z and 2-9")
            self.value = self.value.upper()
        elif self.value is not None:
            raise ValueError("join code value is only valid in manual mode")
        return self


class SessionConfig(StrictModel):
    join_code: JoinCodeConfig = Field(default_factory=JoinCodeConfig)
    allow_answer_changes: bool = True
    show_results_on_presenter: bool = False
    show_results_on_attendee_devices: bool = True
    share_slides_with_attendees: bool = False
    minimum_public_responses: int = Field(default=1, ge=1, le=100)
    retention_hours: int = Field(default=168, ge=1, le=8760)


class QuizConfig(StrictModel):
    schema_version: Literal[1] = 1
    presentation: PresentationConfig
    firebase: FirebaseSettings = Field(default_factory=FirebaseSettings)
    session: SessionConfig = Field(default_factory=SessionConfig)
    questions: list[Question] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_questions(self) -> QuizConfig:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")
        return self

    def resolve_path(self, config_path: Path, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else (config_path.parent / path).resolve()


class FirebaseWebConfig(StrictModel):
    apiKey: str
    authDomain: str
    projectId: str
    storageBucket: str | None = None
    messagingSenderId: str | None = None
    appId: str
    measurementId: str | None = None
