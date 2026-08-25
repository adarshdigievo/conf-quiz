from __future__ import annotations

import asyncio
import copy
import json
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from confquiz.build import export_static_site, materialize_slides
from confquiz.cli import init as init_project
from confquiz.config import ConfigError, load_firebase_web_config, load_quiz_config
from confquiz.server import PresenterRuntime, create_app


class RecordingWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(copy.deepcopy(payload))


class PersistenceStore:
    def __init__(self, *, blocked: bool = False, failing: bool = False):
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.failing = failing
        self.persisted = []
        self.watched_questions = []

    def persist_state(self, controller):
        self.started.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test persistence was not released")
        if self.failing:
            raise ConnectionError("Firestore is unavailable")
        self.persisted.append(copy.deepcopy(controller.state_document()))

    def publish_aggregate(self, _controller, _question):
        return None

    def stop_response_watch(self):
        return None

    def load_moderation(self, _controller, _question_id):
        return None

    def watch_responses(self, _controller, question_id, _callback):
        self.watched_questions.append(question_id)


def activate_slide(controller, page_number):
    controller._activate(
        next(index for index, item in enumerate(controller.timeline) if item.slide == page_number)
    )


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


def test_preview_attendee_answer_is_scoped_to_the_active_question(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    runtime = PresenterRuntime(quiz, slides, mode="preview")
    with (
        TestClient(create_app(runtime)) as client,
        client.websocket_connect(f"/ws/presenter?token={runtime.control_token}") as presenter,
        client.websocket_connect("/ws/attendee?uid=attendee-1") as attendee,
    ):
        presenter.receive_json()
        attendee.receive_json()
        presenter.send_json({"action": "next"})
        receive_until(presenter, lambda state: state["session"]["phase"] == "open")
        receive_until(attendee, lambda state: (state.get("question") or {}).get("id") == "single")

        attendee.send_json({"action": "submit", "answer": "a"})
        answered = receive_until(attendee, lambda state: state.get("existingAnswer") == "a")
        assert answered["question"]["id"] == "single"

        for _ in range(4):
            presenter.send_json({"action": "next"})
        unanswered = receive_until(
            attendee,
            lambda state: (state.get("question") or {}).get("id") == "multiple",
        )
        assert unanswered["existingAnswer"] is None
        assert unanswered["aggregate"]["responseCount"] == 0


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
async def test_live_navigation_broadcasts_before_slow_persistence(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    store = PersistenceStore(blocked=True)
    runtime = PresenterRuntime(quiz, slides, mode="firebase", store=store)
    runtime.controller.session_id = "session-id"
    runtime.controller.join_code = "ROOM26"
    activate_slide(runtime.controller, 10)
    presenter = RecordingWebSocket()
    runtime.presenter_clients.add(presenter)

    await runtime.handle_presenter_action({"action": "next"})

    assert presenter.messages[-1]["session"]["activeSlide"] == 11
    assert presenter.messages[-1]["session"]["syncStatus"] == "syncing"
    assert store.persisted == []
    assert await asyncio.to_thread(store.started.wait, 1)

    store.release.set()
    await asyncio.wait_for(runtime._flush_persistence(), timeout=1)
    assert store.persisted[-1]["activeSlide"] == 11
    assert presenter.messages[-1]["session"]["syncStatus"] == "synced"


@pytest.mark.asyncio
async def test_live_persistence_queue_keeps_rapid_navigation_ordered(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    store = PersistenceStore(blocked=True)
    runtime = PresenterRuntime(quiz, slides, mode="firebase", store=store)
    runtime.controller.session_id = "session-id"
    runtime.controller.join_code = "ROOM26"
    activate_slide(runtime.controller, 10)
    presenter = RecordingWebSocket()
    runtime.presenter_clients.add(presenter)

    await runtime.handle_presenter_action({"action": "next"})
    assert await asyncio.to_thread(store.started.wait, 1)
    await runtime.handle_presenter_action({"action": "next"})
    await runtime.handle_presenter_action({"action": "previous"})

    assert runtime.controller.state_document()["activeSlide"] == 11
    assert presenter.messages[-1]["session"]["activeSlide"] == 11
    store.release.set()
    await asyncio.wait_for(runtime._flush_persistence(), timeout=1)

    assert [state["activeSlide"] for state in store.persisted] == [11, 12, 11]
    assert store.persisted[-1] == runtime.controller.state_document()
    assert presenter.messages[-1]["session"]["syncStatus"] == "synced"


@pytest.mark.asyncio
async def test_live_question_transitions_keep_their_firebase_order(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    store = PersistenceStore()
    runtime = PresenterRuntime(quiz, slides, mode="firebase", store=store)
    runtime.controller.session_id = "session-id"
    runtime.controller.join_code = "ROOM26"

    await runtime.handle_presenter_action({"action": "next"})
    await runtime.handle_presenter_action({"action": "next"})
    await runtime.handle_presenter_action({"action": "next"})
    await asyncio.wait_for(runtime._flush_persistence(), timeout=1)

    assert [state["phase"] for state in store.persisted] == ["open", "results", "revealed"]
    assert runtime.controller.phase == "revealed"


@pytest.mark.asyncio
async def test_live_navigation_continues_when_firebase_is_unavailable(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)
    store = PersistenceStore(failing=True)
    runtime = PresenterRuntime(quiz, slides, mode="firebase", store=store)
    runtime.controller.session_id = "session-id"
    runtime.controller.join_code = "ROOM26"
    activate_slide(runtime.controller, 10)
    presenter = RecordingWebSocket()
    runtime.presenter_clients.add(presenter)

    await runtime.handle_presenter_action({"action": "next"})
    await asyncio.wait_for(runtime._flush_persistence(), timeout=1)

    assert runtime.controller.state_document()["activeSlide"] == 11
    assert presenter.messages[-1]["session"]["activeSlide"] == 11
    assert presenter.messages[-1]["session"]["syncStatus"] == "error"
    assert "Local presenting will continue" in presenter.messages[-1]["session"]["syncError"]


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


@pytest.mark.asyncio
async def test_live_runtime_resumes_instead_of_creating_a_room(quiz_files):
    config_path, quiz = quiz_files
    slides = materialize_slides(quiz, config_path)

    class Store:
        def __init__(self):
            self.resume_codes = []
            self.created = False

        def create_session(self, _controller):
            self.created = True

        def resume_session(self, controller, code):
            self.resume_codes.append(code)
            controller.session_id = "existing-session"
            controller.join_code = "PYAU26"
            controller._activate(1)
            controller.phase = "results"

        def watch_participants(self, _controller, _callback):
            return None

        def stop_response_watch(self):
            return None

        def load_moderation(self, _controller, _question_id):
            return None

        def watch_responses(self, _controller, _question_id, _callback):
            return None

        def publish_aggregate(self, _controller, _question):
            return None

        def refresh_presentation_availability(self, _controller):
            return None

        def mark_presentation_offline(self, _controller):
            return None

        def close(self):
            return None

    store = Store()
    runtime = PresenterRuntime(
        quiz,
        slides,
        mode="firebase",
        store=store,
        resume_code="pyau26",
    )

    await runtime.start()

    assert store.resume_codes == ["pyau26"]
    assert store.created is False
    assert runtime.controller.session_id == "existing-session"
    assert runtime.controller.join_code == "PYAU26"
    assert runtime.controller.current_question.id == "single"
    assert runtime.controller.phase == "results"
    await runtime.close()


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
