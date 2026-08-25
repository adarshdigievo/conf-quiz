from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.cloud.firestore_v1 import DELETE_FIELD
from typer.testing import CliRunner

import confquiz.cli as cli
from confquiz.firebase_backend import CodeReleaseResult, FirebaseError, FirebaseStore
from confquiz.models import JoinCodeConfig
from confquiz.session import SessionController


class MemorySnapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self._data = deepcopy(data)
        self.exists = data is not None
        self.id = reference.id

    def to_dict(self):
        return deepcopy(self._data)


class MemoryDocument:
    def __init__(self, database, path):
        self.database = database
        self.path = path
        self.id = path[-1]

    def get(self):
        return MemorySnapshot(self, self.database.documents.get(self.path))

    def collection(self, name):
        return MemoryCollection(self.database, (*self.path, name))

    def set(self, data, merge=False):
        self.database.set(self, data, merge=merge)

    def update(self, data):
        self.database.update(self, data)


class MemoryCollection:
    def __init__(self, database, path):
        self.database = database
        self.path = path

    def document(self, document_id=None):
        if document_id is None:
            self.database.next_id += 1
            document_id = f"session-{self.database.next_id}"
        return MemoryDocument(self.database, (*self.path, document_id))


class MemoryBatch:
    def __init__(self, database):
        self.database = database
        self.operations = []

    def set(self, reference, data, merge=False):
        self.operations.append(("set", reference, deepcopy(data), merge))

    def update(self, reference, data):
        self.operations.append(("update", reference, deepcopy(data), False))

    def commit(self):
        for operation, reference, data, merge in self.operations:
            if operation == "set":
                self.database.set(reference, data, merge=merge)
            else:
                self.database.update(reference, data)


class MemoryFirestore:
    def __init__(self):
        self.documents = {}
        self.next_id = 0

    def collection(self, name):
        return MemoryCollection(self, (name,))

    def batch(self):
        return MemoryBatch(self)

    def set(self, reference, data, *, merge):
        current = deepcopy(self.documents.get(reference.path, {})) if merge else {}
        current.update(deepcopy(data))
        self.documents[reference.path] = current

    def update(self, reference, data):
        current = deepcopy(self.documents[reference.path])
        for key, value in data.items():
            if value is DELETE_FIELD:
                current.pop(key, None)
            else:
                current[key] = deepcopy(value)
        self.documents[reference.path] = current


def make_store(quiz, database):
    store = FirebaseStore.__new__(FirebaseStore)
    store.quiz = quiz
    store.db = database
    store.sessions_name = f"{quiz.firebase.namespace}_sessions"
    store.codes_name = f"{quiz.firebase.namespace}_join_codes"
    store.presentations_name = f"{quiz.firebase.namespace}_presentations"
    store._response_watch = None
    store._participant_watch = None
    return store


def test_end_session_releases_manual_code_for_immediate_reuse(quiz_files):
    _, quiz = quiz_files
    quiz.session.join_code = JoinCodeConfig(mode="manual", value="PYAU26")
    database = MemoryFirestore()
    store = make_store(quiz, database)
    first_controller = SessionController(quiz, page_count=12)

    first_session_id, code = store.create_session(first_controller)
    store.end_session(first_controller)

    code_path = (store.codes_name, code)
    assert database.documents[code_path]["status"] == "ended"
    assert "sessionId" not in database.documents[code_path]

    second_controller = SessionController(quiz, page_count=12)
    second_session_id, second_code = store.create_session(second_controller)

    assert second_code == code == "PYAU26"
    assert second_session_id != first_session_id
    assert database.documents[code_path]["sessionId"] == second_session_id
    assert database.documents[(store.sessions_name, first_session_id)]["status"] == "ended"


def test_release_code_targets_one_room_and_preserves_its_data(quiz_files):
    _, quiz = quiz_files
    database = MemoryFirestore()
    store = make_store(quiz, database)
    session_id = "session-target"
    marker = store._session_marker(session_id)
    code_path = (store.codes_name, "PYAU26")
    session_path = (store.sessions_name, session_id)
    response_path = (
        store.sessions_name,
        session_id,
        "questions",
        "single",
        "responses",
        "attendee-1",
    )
    other_code_path = (store.codes_name, "OTHER2")
    database.documents.update(
        {
            code_path: {
                "sessionId": session_id,
                "presentationId": quiz.presentation.id,
                "status": "running",
                "expiresAt": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            session_path: {
                "presentationId": quiz.presentation.id,
                "status": "running",
                "phase": "open",
                "activeSlide": 4,
                "activeQuestionId": "single",
            },
            response_path: {"answer": "a"},
            other_code_path: {
                "sessionId": "session-other",
                "presentationId": quiz.presentation.id,
                "status": "running",
            },
            (store.presentations_name, quiz.presentation.id): {
                "status": "running",
                "sessionMarker": marker,
            },
        }
    )
    other_code_before = deepcopy(database.documents[other_code_path])

    result = store.release_code(" pyau26 ")

    assert result == CodeReleaseResult(code="PYAU26", session_id=session_id, changed=True)
    assert database.documents[code_path]["status"] == "ended"
    assert "sessionId" not in database.documents[code_path]
    assert database.documents[session_path]["status"] == "ended"
    assert database.documents[session_path]["phase"] == "ended"
    assert database.documents[session_path]["activeSlide"] is None
    assert database.documents[session_path]["activeQuestionId"] is None
    assert database.documents[response_path] == {"answer": "a"}
    assert database.documents[other_code_path] == other_code_before
    assert database.documents[(store.presentations_name, quiz.presentation.id)]["status"] == "ended"


def test_release_code_rejects_a_different_presentation(quiz_files):
    _, quiz = quiz_files
    database = MemoryFirestore()
    store = make_store(quiz, database)
    database.documents[(store.codes_name, "PYAU26")] = {
        "sessionId": "session-other",
        "presentationId": "another-talk",
        "status": "running",
    }

    with pytest.raises(FirebaseError, match="does not belong to presentation test-talk"):
        store.release_code("PYAU26")

    assert database.documents[(store.codes_name, "PYAU26")]["status"] == "running"


def test_release_code_is_idempotent(quiz_files):
    _, quiz = quiz_files
    database = MemoryFirestore()
    store = make_store(quiz, database)
    database.documents[(store.codes_name, "PYAU26")] = {
        "presentationId": quiz.presentation.id,
        "status": "ended",
    }

    result = store.release_code("PYAU26")

    assert result == CodeReleaseResult(code="PYAU26", session_id=None, changed=False)


def test_sessions_release_cli_uses_default_config_and_keeps_data(monkeypatch, quiz_files):
    _, quiz = quiz_files
    calls = {}

    def fake_load_live(config, credential):
        calls["config"] = config
        calls["credential"] = credential
        return quiz, object(), None

    class RecordingStore:
        def __init__(self, loaded_quiz, credential_path):
            calls["quiz"] = loaded_quiz
            calls["credential_path"] = credential_path

        def release_code(self, code):
            calls["code"] = code
            return CodeReleaseResult(code="PYAU26", session_id="session-target", changed=True)

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(cli, "_load_live", fake_load_live)
    monkeypatch.setattr(cli, "FirebaseStore", RecordingStore)

    result = CliRunner().invoke(cli.app, ["sessions", "release", "pyau26"])

    assert result.exit_code == 0, result.output
    assert calls == {
        "config": Path("quiz.yml"),
        "credential": None,
        "quiz": quiz,
        "credential_path": None,
        "code": "pyau26",
        "closed": True,
    }
    assert "Released join code PYAU26." in result.output
    assert "Ended session session-target." in result.output
    assert "No session data was deleted." in result.output
