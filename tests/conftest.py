from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pypdf import PdfWriter

from confquiz.config import load_quiz_config


def create_pdf(path: Path, pages: int = 12) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=1280, height=720)
    with path.open("wb") as output:
        writer.write(output)
    return path


@pytest.fixture
def quiz_files(tmp_path: Path):
    create_pdf(tmp_path / "slides.pdf")
    (tmp_path / "firebase.web.json").write_text(
        json.dumps(
            {
                "apiKey": "public-test-key",
                "authDomain": "example.firebaseapp.com",
                "projectId": "example",
                "appId": "1:123:web:abc",
            }
        ),
        encoding="utf-8",
    )
    config = {
        "schema_version": 1,
        "presentation": {
            "id": "test-talk",
            "title": "Testing live rooms",
            "speaker": "Test Speaker",
            "public_url": "https://example.test/quiz/",
            "slides": {"source": "slides.pdf"},
        },
        "firebase": {
            "web_config": "firebase.web.json",
            "app_check_site_key": "public-site-key",
            "namespace": "confquiz",
        },
        "session": {
            "show_results_on_presenter": False,
            "show_results_on_attendee_devices": True,
            "share_slides_with_attendees": False,
            "minimum_public_responses": 1,
        },
        "questions": [
            {
                "id": "single",
                "after_slide": 1,
                "type": "single_choice",
                "prompt": "Pick one",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "correct": "a",
            },
            {
                "id": "multiple",
                "after_slide": 2,
                "type": "multiple_choice",
                "prompt": "Pick several",
                "max_selections": 2,
                "options": [
                    {"id": "a", "label": "A"},
                    {"id": "b", "label": "B"},
                    {"id": "c", "label": "C"},
                ],
            },
            {"id": "yes", "after_slide": 3, "type": "yes_no", "prompt": "Yes?"},
            {
                "id": "slider",
                "after_slide": 4,
                "type": "slider",
                "prompt": "Slide",
                "min": 0,
                "max": 10,
                "step": 1,
            },
            {"id": "rating", "after_slide": 5, "type": "rating", "prompt": "Rate"},
            {
                "id": "number",
                "after_slide": 6,
                "type": "number",
                "prompt": "Number",
                "min": 0,
                "max": 100,
                "step": 5,
                "correct": {"value": 50, "tolerance": 5},
            },
            {
                "id": "ranking",
                "after_slide": 7,
                "type": "ranking",
                "prompt": "Rank",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            },
            {
                "id": "free-text",
                "after_slide": 8,
                "type": "free_text",
                "prompt": "Reflect",
                "max_length": 100,
            },
            {
                "id": "word-cloud",
                "after_slide": 9,
                "type": "word_cloud",
                "prompt": "Words",
                "max_length": 100,
            },
        ],
    }
    path = tmp_path / "quiz.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path, load_quiz_config(path)
