from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from confquiz.models import FirebaseWebConfig, QuizConfig


class ConfigError(ValueError):
    """Raised when a quiz or Firebase configuration cannot be loaded."""


def load_quiz_config(path: Path) -> QuizConfig:
    """Load and validate a presentation configuration.

    Parameters
    ----------
    path
        Path to the YAML configuration file.

    Returns
    -------
    QuizConfig
        The validated presentation configuration.

    Raises
    ------
    ConfigError
        If the file is missing, the YAML is invalid, or validation fails.
    """
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"Quiz config does not exist: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"Quiz config must contain a YAML object: {path}")
    try:
        return QuizConfig.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(str(error)) from error


def load_firebase_web_config(quiz: QuizConfig, quiz_path: Path) -> FirebaseWebConfig:
    path = quiz.resolve_path(quiz_path.resolve(), quiz.firebase.web_config)
    if not path.is_file():
        message = f"Firebase web config does not exist: {path}"
        example = path.with_name("firebase.web.example.json")
        if path.name == "firebase.web.json" and example.is_file():
            message += (
                ". Copy firebase.web.example.json to firebase.web.json and add your Firebase web app config"
            )
        raise ConfigError(message)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid JSON in {path}: {error}") from error
    try:
        return FirebaseWebConfig.model_validate(raw)
    except ValidationError as error:
        raise ConfigError(str(error)) from error
