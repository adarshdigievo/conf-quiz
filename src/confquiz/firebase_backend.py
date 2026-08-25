from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import DELETE_FIELD
from google.cloud.firestore_v1.base_query import FieldFilter

from confquiz.models import JOIN_CODE_PATTERN, Question, QuizConfig
from confquiz.session import SessionController

JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PRESENTATION_ONLINE_WINDOW = timedelta(minutes=2)


class FirebaseError(RuntimeError):
    """Raised when the live Firebase backend cannot complete an operation."""


@dataclass(frozen=True)
class CodeReleaseResult:
    code: str
    session_id: str | None
    changed: bool


class FirebaseStore:
    def __init__(self, quiz: QuizConfig, credential_path: Path | None = None) -> None:
        self.quiz = quiz
        if firebase_admin._apps:  # Firebase Admin exposes no public lookup for the default app.
            app = firebase_admin.get_app()
        else:
            credential = credentials.Certificate(str(credential_path)) if credential_path else None
            app = firebase_admin.initialize_app(credential)
        self.db = firestore.client(app=app)
        self.sessions_name = f"{quiz.firebase.namespace}_sessions"
        self.codes_name = f"{quiz.firebase.namespace}_join_codes"
        self.presentations_name = f"{quiz.firebase.namespace}_presentations"
        self._response_watch = None
        self._participant_watch = None

    @staticmethod
    def config_hash(quiz: QuizConfig) -> str:
        data = quiz.model_dump(mode="json", by_alias=True)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _session_ref(self, session_id: str):
        return self.db.collection(self.sessions_name).document(session_id)

    def _presentation_ref(self):
        return self.db.collection(self.presentations_name).document(self.quiz.presentation.id)

    @staticmethod
    def _session_marker(session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    def _new_code(self, length: int) -> str:
        return "".join(random.SystemRandom().choice(JOIN_ALPHABET) for _ in range(length))

    def create_session(self, controller: SessionController) -> tuple[str, str]:
        code_config = self.quiz.session.join_code
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self.quiz.session.retention_hours)
        for _ in range(30):
            code = code_config.value if code_config.mode == "manual" else self._new_code(code_config.length)
            code_ref = self.db.collection(self.codes_name).document(code)
            existing = code_ref.get()
            existing_data = existing.to_dict() if existing.exists else {}
            expires_at_value = existing_data.get("expiresAt")
            code_is_active = existing.exists and existing_data.get("status") != "ended" and (
                not isinstance(expires_at_value, datetime) or expires_at_value > now
            )
            if code_is_active:
                if code_config.mode == "manual":
                    raise FirebaseError(f"Manual join code {code} is already active")
                continue
            session_ref = self.db.collection(self.sessions_name).document()
            batch = self.db.batch()
            batch.set(
                session_ref,
                {
                    "presentationId": self.quiz.presentation.id,
                    "presentationTitle": self.quiz.presentation.title,
                    "configHash": self.config_hash(self.quiz),
                    "joinCode": code,
                    "createdAt": firestore.SERVER_TIMESTAMP,
                    "expiresAt": expires_at,
                    **controller.state_document(),
                },
            )
            batch.set(
                code_ref,
                {
                    "sessionId": session_ref.id,
                    "presentationId": self.quiz.presentation.id,
                    "status": "running",
                    "expiresAt": expires_at,
                },
            )
            batch.set(
                self._presentation_ref(),
                {
                    "presentationTitle": self.quiz.presentation.title,
                    "status": "running",
                    "expiresAt": expires_at,
                    "onlineUntil": now + PRESENTATION_ONLINE_WINDOW,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                    "sessionMarker": self._session_marker(session_ref.id),
                },
            )
            for question in self.quiz.questions:
                batch.set(
                    session_ref.collection("questions").document(question.id), question.public_payload()
                )
            batch.commit()
            controller.session_id = session_ref.id
            controller.join_code = code
            return session_ref.id, code
        raise FirebaseError("Could not allocate an unused join code")

    def persist_state(self, controller: SessionController) -> None:
        if not controller.session_id:
            return
        self._session_ref(controller.session_id).update(controller.state_document())

    def publish_aggregate(self, controller: SessionController, question: Question) -> None:
        if not controller.session_id:
            return
        aggregate = controller.aggregate(question, public=True)
        if aggregate is None:
            return
        aggregate["updatedAt"] = firestore.SERVER_TIMESTAMP
        self._session_ref(controller.session_id).collection("aggregates").document(question.id).set(aggregate)

    def save_moderation(self, controller: SessionController, question_id: str) -> None:
        self._session_ref(controller.session_id).collection("moderation").document(question_id).set(
            {
                "approvedResponseIds": sorted(controller.approved[question_id]),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )

    def load_moderation(self, controller: SessionController, question_id: str) -> None:
        snapshot = (
            self._session_ref(controller.session_id).collection("moderation").document(question_id).get()
        )
        if snapshot.exists:
            controller.approved[question_id] = set(snapshot.to_dict().get("approvedResponseIds", []))

    def watch_responses(
        self,
        controller: SessionController,
        question_id: str,
        callback: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self.stop_response_watch()
        collection = (
            self._session_ref(controller.session_id)
            .collection("questions")
            .document(question_id)
            .collection("responses")
        )

        def on_snapshot(snapshots, _changes, _read_time):
            callback(question_id, {snapshot.id: snapshot.to_dict().get("answer") for snapshot in snapshots})

        self._response_watch = collection.on_snapshot(on_snapshot)

    def watch_participants(self, controller: SessionController, callback: Callable[[int], None]) -> None:
        self.stop_participant_watch()
        collection = self._session_ref(controller.session_id).collection("participants")

        def on_snapshot(snapshots, _changes, _read_time):
            callback(len(snapshots))

        self._participant_watch = collection.on_snapshot(on_snapshot)

    def stop_response_watch(self) -> None:
        if self._response_watch:
            self._response_watch.unsubscribe()
            self._response_watch = None

    def stop_participant_watch(self) -> None:
        if self._participant_watch:
            self._participant_watch.unsubscribe()
            self._participant_watch = None

    def clear_question(self, controller: SessionController, question_id: str) -> None:
        response_collection = (
            self._session_ref(controller.session_id)
            .collection("questions")
            .document(question_id)
            .collection("responses")
        )
        self._delete_collection(response_collection)
        self._session_ref(controller.session_id).collection("aggregates").document(question_id).delete()
        self._session_ref(controller.session_id).collection("moderation").document(question_id).delete()
        controller.clear_question(question_id)

    def clear_all(self, controller: SessionController) -> None:
        for question in self.quiz.questions:
            self.clear_question(controller, question.id)
        controller.clear_all()
        self.persist_state(controller)

    def _delete_collection(self, collection, batch_size: int = 200) -> None:
        while True:
            documents = list(collection.limit(batch_size).stream())
            if not documents:
                return
            batch = self.db.batch()
            for document in documents:
                batch.delete(document.reference)
            batch.commit()

    def end_session(self, controller: SessionController) -> None:
        if not controller.session_id:
            return
        controller.end()
        batch = self.db.batch()
        batch.update(self._session_ref(controller.session_id), controller.state_document())
        batch.update(
            self.db.collection(self.codes_name).document(controller.join_code),
            {
                "status": "ended",
                "sessionId": DELETE_FIELD,
                "releasedAt": firestore.SERVER_TIMESTAMP,
            },
        )
        batch.set(
            self._presentation_ref(),
            {
                "presentationTitle": self.quiz.presentation.title,
                "status": "ended",
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "sessionMarker": self._session_marker(controller.session_id),
            },
            merge=True,
        )
        batch.commit()
        self.stop_response_watch()
        self.stop_participant_watch()

    def release_code(self, code: str) -> CodeReleaseResult:
        normalized_code = code.strip().upper()
        if not JOIN_CODE_PATTERN.fullmatch(normalized_code):
            raise FirebaseError("Join code must be 4 to 12 characters using A-Z and 2-9")

        code_ref = self.db.collection(self.codes_name).document(normalized_code)
        code_snapshot = code_ref.get()
        if not code_snapshot.exists:
            raise FirebaseError(f"Join code {normalized_code} does not exist")

        code_data = code_snapshot.to_dict()
        presentation_id = self.quiz.presentation.id
        if code_data.get("presentationId") != presentation_id:
            raise FirebaseError(
                f"Join code {normalized_code} does not belong to presentation {presentation_id}"
            )

        session_id = code_data.get("sessionId")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise FirebaseError(f"Join code {normalized_code} has an invalid session mapping")
        if code_data.get("status") == "ended" and not session_id:
            return CodeReleaseResult(normalized_code, None, changed=False)

        session_ref = None
        session_snapshot = None
        if session_id:
            session_ref = self._session_ref(session_id)
            session_snapshot = session_ref.get()
            if (
                session_snapshot.exists
                and session_snapshot.to_dict().get("presentationId") != presentation_id
            ):
                raise FirebaseError(
                    f"Join code {normalized_code} points to a different presentation's session"
                )

        presentation_ref = self._presentation_ref()
        presentation_snapshot = presentation_ref.get()
        batch = self.db.batch()
        if session_ref is not None and session_snapshot is not None and session_snapshot.exists:
            batch.update(
                session_ref,
                {
                    "status": "ended",
                    "phase": "ended",
                    "activeSlide": None,
                    "activeQuestionId": None,
                },
            )
        batch.update(
            code_ref,
            {
                "status": "ended",
                "sessionId": DELETE_FIELD,
                "releasedAt": firestore.SERVER_TIMESTAMP,
            },
        )
        if (
            session_id
            and presentation_snapshot.exists
            and presentation_snapshot.to_dict().get("sessionMarker")
            == self._session_marker(session_id)
        ):
            batch.set(
                presentation_ref,
                {
                    "status": "ended",
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
        batch.commit()
        return CodeReleaseResult(normalized_code, session_id, changed=True)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        query = (
            self.db.collection(self.sessions_name).order_by("createdAt", direction="DESCENDING").limit(limit)
        )
        return [{"id": document.id, **document.to_dict()} for document in query.stream()]

    def cleanup(self, older_than: datetime) -> int:
        query = self.db.collection(self.sessions_name).where(filter=FieldFilter("createdAt", "<", older_than))
        deleted = 0
        for session in query.stream():
            data = session.to_dict()
            for question in session.reference.collection("questions").stream():
                self._delete_collection(question.reference.collection("responses"))
                question.reference.delete()
            self._delete_collection(session.reference.collection("participants"))
            self._delete_collection(session.reference.collection("aggregates"))
            self._delete_collection(session.reference.collection("moderation"))
            join_code = data.get("joinCode")
            if join_code:
                self.db.collection(self.codes_name).document(join_code).delete()
            session.reference.delete()
            deleted += 1
        return deleted

    def close(self) -> None:
        self.stop_response_watch()
        self.stop_participant_watch()

    def mark_presentation_offline(self, controller: SessionController) -> None:
        if not controller.session_id:
            return
        reference = self._presentation_ref()
        snapshot = reference.get()
        if not snapshot.exists:
            return
        expected_marker = self._session_marker(controller.session_id)
        if snapshot.to_dict().get("sessionMarker") != expected_marker:
            return
        reference.set(
            {
                "status": "ended",
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    def refresh_presentation_availability(self, controller: SessionController) -> None:
        if not controller.session_id:
            return
        reference = self._presentation_ref()
        snapshot = reference.get()
        if not snapshot.exists:
            return
        expected_marker = self._session_marker(controller.session_id)
        if snapshot.to_dict().get("sessionMarker") != expected_marker:
            return
        reference.set(
            {
                "status": "running",
                "onlineUntil": datetime.now(timezone.utc) + PRESENTATION_ONLINE_WINDOW,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
