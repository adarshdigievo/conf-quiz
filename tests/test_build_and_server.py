from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from confquiz.build import export_static_site, materialize_slides
from confquiz.cli import init as init_project
from confquiz.config import ConfigError, load_firebase_web_config, load_quiz_config
from confquiz.server import PresenterRuntime, create_app


def receive_until(websocket, predicate, attempts: int = 6):
    for _ in range(attempts):
        payload = websocket.receive_json()
        if predicate(payload):
            return payload
    raise AssertionError("WebSocket did not publish the expected state")


def test_export_is_static_and_contains_no_answer_key(quiz_files, tmp_path: Path):
    config_path, quiz = quiz_files
    output = tmp_path / "site"
    artifact = export_static_site(quiz, config_path, output)
    assert artifact.page_count == 12
    assert (output / "index.html").is_file()
    assert (output / "assets" / "attendee.js").is_file()
    assert (output / "assets" / "slides.pdf").is_file()
    index = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/attendee.css?v=1.6.0"' in index
    assert index.count("Responses are anonymous.") == 1
    assert 'data-home-slide-stage' in index
    assert 'src="runtime-config.js"' in index
    runtime = (output / "runtime-config.js").read_text(encoding="utf-8")
    assert "correct" not in runtime
    assert "public-test-key" in runtime
    assert '"slideUrl":"assets/slides.pdf"' in runtime
    assert '"pdfWorkerUrl":"assets/pdf.worker.min.mjs?v=1.1.0"' in runtime
    assert (output / ".nojekyll").is_file()


def test_preview_websockets_drive_question_and_response(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    runtime = PresenterRuntime(quiz, slides, mode="preview")
    app = create_app(runtime)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok", "mode": "preview"}
        attendee_html = client.get("/attend/").text
        assert 'href="../assets/attendee.css?v=1.6.0"' in attendee_html
        assert 'src="../runtime-config.js"' in attendee_html
        preview_runtime = json.loads(client.get("/runtime-config.js").text.split(" = ", 1)[1].rstrip(";"))
        assert preview_runtime["slideUrl"] == "../slides.pdf"
        assert preview_runtime["pdfWorkerUrl"] == "../assets/pdf.worker.min.mjs?v=1.1.0"
        with (
            client.websocket_connect(f"/ws/presenter?token={runtime.control_token}") as presenter,
            client.websocket_connect("/ws/attendee?uid=attendee-1") as attendee,
        ):
            initial_presenter = presenter.receive_json()["session"]
            assert initial_presenter["phase"] == "slide"
            assert initial_presenter["activeSlide"] == 1
            assert initial_presenter["attendeeUrlEditable"] is False
            assert initial_presenter["attendeeBaseUrl"] == "http://127.0.0.1:8765/attend/"
            initial_attendee = attendee.receive_json()
            assert initial_attendee["session"]["activeSlide"] == 1

            presenter.send_json({"action": "toggle_slide_sharing"})
            shared_state = receive_until(
                presenter, lambda state: state["session"].get("shareSlidesWithAttendees") is True
            )
            assert shared_state["session"]["shareSlidesWithAttendees"] is True
            attendee_shared = receive_until(
                attendee, lambda state: state["session"].get("shareSlidesWithAttendees") is True
            )
            assert attendee_shared["session"]["activeSlide"] == 1

            presenter.send_json({"action": "toggle_presenter_results"})
            results_state = receive_until(
                presenter, lambda state: state["session"].get("showResultsOnPresenter") is True
            )
            assert results_state["session"]["showResultsOnPresenter"] is True

            presenter.send_json({"action": "next"})
            question_state = receive_until(presenter, lambda state: state["session"]["phase"] == "open")
            assert question_state["session"]["phase"] == "open"
            assert question_state["session"]["activeQuestion"]["id"] == "single"
            attendee_state = receive_until(
                attendee, lambda state: (state.get("question") or {}).get("id") == "single"
            )
            assert attendee_state["question"]["id"] == "single"

            attendee.send_json({"action": "submit", "answer": "a"})
            updated = receive_until(
                attendee,
                lambda state: (state.get("aggregate") or {}).get("responseCount") == 1,
            )
            assert updated["aggregate"]["responseCount"] == 1
            presenter_updated = receive_until(
                presenter,
                lambda state: (state["session"].get("aggregate") or {}).get("responseCount") == 1,
            )
            assert presenter_updated["session"]["aggregate"]["options"][0]["count"] == 1


def test_presenter_token_is_required(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    runtime = PresenterRuntime(quiz, slides, mode="preview")
    with TestClient(create_app(runtime)) as client:
        assert client.get("/").status_code == 403
        assert client.get(f"/?token={runtime.control_token}").status_code == 200
        runtime_script = client.get(f"/presenter-runtime.js?token={runtime.control_token}")
        assert runtime_script.status_code == 200
        presenter_config = json.loads(runtime_script.text.split(" = ", 1)[1].rstrip(";"))
        assert presenter_config["mode"] == "preview"
        assert presenter_config["pdfWorkerUrl"].endswith("?v=1.1.0")


@pytest.mark.asyncio
async def test_live_attendee_url_can_be_changed_from_presenter(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    runtime = PresenterRuntime(quiz, slides, mode="firebase")
    runtime.controller.session_id = "session-id"
    runtime.controller.join_code = "ROOM26"

    initial = runtime.presenter_payload()["session"]
    assert initial["attendeeUrlEditable"] is True
    assert initial["joinUrl"] == "https://example.test/quiz/?code=ROOM26"

    await runtime.handle_presenter_action(
        {"action": "set_attendee_url", "url": "https://slides.example/conf/?theme=dark&code=OLD"}
    )
    updated = runtime.presenter_payload()["session"]
    assert updated["attendeeBaseUrl"] == "https://slides.example/conf/?theme=dark"
    assert updated["joinUrl"] == "https://slides.example/conf/?theme=dark&code=ROOM26"

    with pytest.raises(ValueError, match="absolute HTTP or HTTPS URL"):
        await runtime.handle_presenter_action({"action": "set_attendee_url", "url": "/relative"})


@pytest.mark.asyncio
async def test_live_runtime_marks_presentation_offline_when_it_closes(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)

    class Store:
        def __init__(self):
            self.marked_offline = False
            self.closed = False

        def create_session(self, controller):
            controller.session_id = "session-id"
            controller.join_code = "ROOM26"

        def watch_participants(self, _controller, _callback):
            return None

        def stop_response_watch(self):
            return None

        def mark_presentation_offline(self, controller):
            assert controller.session_id == "session-id"
            self.marked_offline = True

        def close(self):
            self.closed = True

        def refresh_presentation_availability(self, _controller):
            return None

    store = Store()
    runtime = PresenterRuntime(quiz, slides, mode="firebase", store=store)
    await runtime.start()
    await runtime.close()
    assert store.marked_offline is True
    assert store.closed is True


def test_packaged_firebase_scaffold_matches_repository_files():
    root = Path(".")
    scaffold = Path("src/confquiz/scaffold")
    assert (root / "firestore.rules").read_text() == (scaffold / "firestore.rules").read_text()
    for name in ["firebase.json", "firestore.indexes.json"]:
        assert json.loads((root / name).read_text()) == json.loads((scaffold / name).read_text())


def test_init_uses_a_speaker_local_ignored_firebase_config(tmp_path: Path):
    target = tmp_path / "talk"
    target.mkdir()
    (target / ".gitignore").write_text("speaker-notes.txt\n", encoding="utf-8")

    init_project(target, force=True)

    quiz_text = (target / "quiz.yml").read_text(encoding="utf-8")
    ignore_text = (target / ".gitignore").read_text(encoding="utf-8")
    assert 'web_config: "firebase.web.json"' in quiz_text
    assert (target / "firebase.web.example.json").is_file()
    assert not (target / "firebase.web.json").exists()
    quiz = load_quiz_config(target / "quiz.yml")
    with pytest.raises(ConfigError, match="Copy firebase.web.example.json"):
        load_firebase_web_config(quiz, target / "quiz.yml")

    assert "speaker-notes.txt" in ignore_text
    assert "firebase.web.json" in ignore_text
