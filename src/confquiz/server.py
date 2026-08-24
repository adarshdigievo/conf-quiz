from __future__ import annotations

import asyncio
import io
import json
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import segno
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from confquiz.answers import AnswerError, validate_answer
from confquiz.build import SlideArtifact
from confquiz.firebase_backend import FirebaseStore
from confquiz.models import QuizConfig
from confquiz.session import SessionController

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self' ws: wss: https://*.googleapis.com "
            "https://*.firebaseio.com https://*.firebaseapp.com https://*.google.com; "
            "worker-src 'self' blob:; font-src 'self'; frame-ancestors 'none'",
        )
        return response


def _with_code(url: str, code: str) -> str:
    parsed = urlparse(url)
    query = urlencode(
        [
            *(pair for pair in parse_qsl(parsed.query, keep_blank_values=True) if pair[0] != "code"),
            ("code", code),
        ]
    )
    return urlunparse(parsed._replace(query=query))


def _validate_attendee_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Attendee site URL must be a string")
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Attendee site URL must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Attendee site URL cannot include credentials")
    query = urlencode(
        [pair for pair in parse_qsl(parsed.query, keep_blank_values=True) if pair[0] != "code"]
    )
    return urlunparse(parsed._replace(query=query))


def _template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(files("confquiz").joinpath("templates"))),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )


class PresenterRuntime:
    def __init__(
        self,
        quiz: QuizConfig,
        slides: SlideArtifact,
        *,
        mode: str,
        store: FirebaseStore | None = None,
        local_port: int = 8765,
    ) -> None:
        self.quiz = quiz
        self.slides = slides
        self.mode = mode
        self.store = store
        self.local_port = local_port
        self.controller = SessionController(quiz, slides.page_count)
        self.control_token = secrets.token_urlsafe(32)
        self.presenter_clients: set[WebSocket] = set()
        self.attendee_clients: dict[WebSocket, str] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._availability_task: asyncio.Task | None = None
        self._watched_question: str | None = None
        self.attendee_base_url = (
            f"http://127.0.0.1:{local_port}/attend/"
            if mode == "preview"
            else quiz.presentation.public_url
        )

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self.store:
            await asyncio.to_thread(self.store.create_session, self.controller)
            self.store.watch_participants(self.controller, self._participants_from_thread)
            self._availability_task = asyncio.create_task(self._availability_heartbeat())
        else:
            self.controller.session_id = "preview-session"
            self.controller.join_code = "DEMO26"
        await self._sync_watch()

    async def close(self) -> None:
        if self.store:
            if self._availability_task:
                self._availability_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._availability_task
            await asyncio.to_thread(self.store.mark_presentation_offline, self.controller)
            await asyncio.to_thread(self.store.close)

    async def _availability_heartbeat(self) -> None:
        while self.store:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(self.store.refresh_presentation_availability, self.controller)
            except Exception:
                logger.warning("Could not refresh participant-site availability", exc_info=True)

    def _responses_from_thread(self, question_id: str, responses: dict[str, Any]) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._receive_responses(question_id, responses))
            )

    def _participants_from_thread(self, count: int) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self._receive_participants(count)))

    async def _receive_responses(self, question_id: str, responses: dict[str, Any]) -> None:
        self.controller.set_responses(question_id, responses)
        question = self.controller.questions[question_id]
        if self.store:
            await asyncio.to_thread(self.store.publish_aggregate, self.controller, question)
        await self.broadcast()

    async def _receive_participants(self, count: int) -> None:
        self.controller.set_participant_count(count)
        await self.broadcast_presenter()

    @property
    def join_url(self) -> str:
        return _with_code(self.attendee_base_url, self.controller.join_code)

    async def _sync_watch(self) -> None:
        question = self.controller.current_question
        question_id = question.id if question else None
        if question_id == self._watched_question:
            if question and self.store:
                await asyncio.to_thread(self.store.publish_aggregate, self.controller, question)
            return
        self._watched_question = question_id
        if self.store:
            self.store.stop_response_watch()
            if question_id:
                await asyncio.to_thread(self.store.load_moderation, self.controller, question_id)
                self.store.watch_responses(self.controller, question_id, self._responses_from_thread)
                await asyncio.to_thread(self.store.publish_aggregate, self.controller, question)

    async def _persist_and_broadcast(self) -> None:
        if self.store:
            await asyncio.to_thread(self.store.persist_state, self.controller)
            question = self.controller.current_question
            if question:
                await asyncio.to_thread(self.store.publish_aggregate, self.controller, question)
        await self._sync_watch()
        await self.broadcast()

    async def handle_presenter_action(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        if action == "next":
            self.controller.next()
        elif action == "previous":
            self.controller.previous()
        elif action == "close_question":
            self.controller.close_question()
        elif action == "reveal":
            self.controller.reveal()
        elif action == "restart":
            if self.store:
                await asyncio.to_thread(self.store.clear_all, self.controller)
            else:
                self.controller.clear_all()
        elif action == "reset_question":
            question = self.controller.current_question
            if question:
                if self.store:
                    await asyncio.to_thread(self.store.clear_question, self.controller, question.id)
                else:
                    self.controller.clear_question(question.id)
        elif action == "new_session":
            await self._new_session()
        elif action == "end_session":
            if self.store:
                await asyncio.to_thread(self.store.end_session, self.controller)
            else:
                self.controller.end()
        elif action == "toggle_presenter_results":
            self.controller.toggle_presenter_results()
        elif action == "toggle_slide_sharing":
            self.controller.toggle_slide_sharing()
        elif action == "set_attendee_url":
            if self.mode == "preview":
                raise ValueError("Preview attendees use the local rehearsal server")
            self.attendee_base_url = _validate_attendee_url(message.get("url"))
        elif action == "moderate":
            question = self.controller.current_question
            uid = message.get("uid")
            if question and isinstance(uid, str):
                self.controller.moderate(question.id, uid, bool(message.get("approved")))
                if self.store:
                    await asyncio.to_thread(self.store.save_moderation, self.controller, question.id)
                    await asyncio.to_thread(self.store.publish_aggregate, self.controller, question)
        else:
            raise ValueError(f"Unknown presenter action: {action}")
        await self._persist_and_broadcast()

    async def _new_session(self) -> None:
        if self.store:
            await asyncio.to_thread(self.store.end_session, self.controller)
        self.controller = SessionController(self.quiz, self.slides.page_count)
        self._watched_question = None
        if self.store:
            await asyncio.to_thread(self.store.create_session, self.controller)
            self.store.watch_participants(self.controller, self._participants_from_thread)
        else:
            self.controller.session_id = secrets.token_hex(8)
            self.controller.join_code = "DEMO26"

    def presenter_payload(self) -> dict[str, Any]:
        payload = {"type": "state", **self.controller.presenter_payload(self.join_url)}
        payload["session"]["attendeeBaseUrl"] = self.attendee_base_url
        payload["session"]["attendeeUrlEditable"] = self.mode != "preview"
        return payload

    def attendee_payload(self, uid: str | None = None) -> dict[str, Any]:
        question = self.controller.current_question
        response = None
        if question and uid:
            response = self.controller.responses[question.id].get(uid)
        return {
            "type": "state",
            "session": self.controller.state_document(),
            "joinCode": self.controller.join_code,
            "presentation": {
                "title": self.quiz.presentation.title,
                "speaker": self.quiz.presentation.speaker,
                "theme": self.quiz.presentation.theme.model_dump(mode="json"),
            },
            "question": question.public_payload() if question else None,
            "aggregate": self.controller.aggregate(public=True),
            "existingAnswer": response,
        }

    async def handle_preview_attendee(self, websocket: WebSocket, message: dict[str, Any]) -> None:
        uid = self.attendee_clients[websocket]
        action = message.get("action")
        if action == "join":
            self.controller.set_participant_count(len(set(self.attendee_clients.values())))
        elif action == "submit":
            question = self.controller.current_question
            if not question or self.controller.phase != "open":
                raise AnswerError("This question is not accepting responses")
            cleaned = validate_answer(question, message.get("answer"))
            if not self.quiz.session.allow_answer_changes and uid in self.controller.responses[question.id]:
                raise AnswerError("This response is already final")
            self.controller.responses[question.id][uid] = cleaned
        else:
            raise AnswerError("Unknown attendee action")
        await self.broadcast()

    async def broadcast_presenter(self) -> None:
        payload = self.presenter_payload()
        stale: list[WebSocket] = []
        for websocket in self.presenter_clients:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.presenter_clients.discard(websocket)

    async def broadcast_attendees(self) -> None:
        stale: list[WebSocket] = []
        for websocket, uid in self.attendee_clients.items():
            try:
                await websocket.send_json(self.attendee_payload(uid))
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.attendee_clients.pop(websocket, None)

    async def broadcast(self) -> None:
        await self.broadcast_presenter()
        if self.mode == "preview":
            await self.broadcast_attendees()


def create_app(runtime: PresenterRuntime) -> Starlette:
    templates = _template_environment()
    static_dir = files("confquiz").joinpath("static/assets")

    @asynccontextmanager
    async def lifespan(_app):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    async def presenter_page(request: Request) -> Response:
        if request.query_params.get("token") != runtime.control_token:
            return HTMLResponse("Presenter token required", status_code=403)
        html = templates.get_template("presenter.html.j2").render(
            title=runtime.quiz.presentation.title,
            token=runtime.control_token,
            accent=runtime.quiz.presentation.theme.accent,
        )
        return HTMLResponse(html)

    async def attendee_page(_request: Request) -> Response:
        if runtime.mode != "preview":
            return HTMLResponse("The attendee site is hosted at the configured public URL.", status_code=404)
        html = templates.get_template("attendee.html.j2").render(
            title=runtime.quiz.presentation.title,
            accent=runtime.quiz.presentation.theme.accent,
            asset_prefix="../",
        )
        return HTMLResponse(html)

    async def presenter_runtime(request: Request) -> Response:
        if request.query_params.get("token") != runtime.control_token:
            return Response(
                "throw new Error('Presenter token required');",
                status_code=403,
                media_type="text/javascript",
            )
        payload = {
            "mode": runtime.mode,
            "token": runtime.control_token,
            "websocketUrl": f"ws://{request.url.netloc}/ws/presenter?token={runtime.control_token}",
            "slideUrl": "/slides.pdf",
            "pdfWorkerUrl": "/assets/pdf.worker.min.mjs?v=1.1.0",
            "qrUrl": f"/api/qr.svg?token={runtime.control_token}",
        }
        return Response(
            "window.CONFQUIZ_PRESENTER = " + json.dumps(payload) + ";",
            media_type="text/javascript",
        )

    async def attendee_runtime(request: Request) -> Response:
        payload = {
            "mode": "preview",
            "presentation": {
                "title": runtime.quiz.presentation.title,
                "speaker": runtime.quiz.presentation.speaker,
                "theme": runtime.quiz.presentation.theme.model_dump(mode="json"),
            },
            "previewWebsocketUrl": f"ws://{request.url.netloc}/ws/attendee",
            "slideUrl": "../slides.pdf",
            "pdfWorkerUrl": "../assets/pdf.worker.min.mjs?v=1.1.0",
            "namespace": runtime.quiz.firebase.namespace,
        }
        return Response(
            "window.CONFQUIZ_RUNTIME = " + json.dumps(payload) + ";",
            media_type="text/javascript",
        )

    async def slide_pdf(_request: Request) -> Response:
        if not runtime.slides.local_path:
            return Response(status_code=404)
        return FileResponse(runtime.slides.local_path, media_type="application/pdf")

    async def qr_code(request: Request) -> Response:
        if request.query_params.get("token") != runtime.control_token:
            return Response(status_code=403)
        buffer = io.BytesIO()
        segno.make(runtime.join_url, error="m").save(buffer, kind="svg", scale=6, border=1, dark="#101310")
        return Response(buffer.getvalue(), media_type="image/svg+xml")

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "mode": runtime.mode})

    async def presenter_websocket(websocket: WebSocket) -> None:
        if websocket.query_params.get("token") != runtime.control_token:
            await websocket.close(code=4403)
            return
        origin = websocket.headers.get("origin")
        if origin and urlparse(origin).hostname not in {"127.0.0.1", "localhost", "::1"}:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        runtime.presenter_clients.add(websocket)
        await websocket.send_json(runtime.presenter_payload())
        try:
            while True:
                message = await websocket.receive_json()
                try:
                    await runtime.handle_presenter_action(message)
                except Exception as error:
                    await websocket.send_json({"type": "error", "message": str(error)})
        except WebSocketDisconnect:
            runtime.presenter_clients.discard(websocket)

    async def attendee_websocket(websocket: WebSocket) -> None:
        if runtime.mode != "preview":
            await websocket.close(code=4404)
            return
        await websocket.accept()
        uid = websocket.query_params.get("uid") or secrets.token_hex(8)
        runtime.attendee_clients[websocket] = uid
        runtime.controller.set_participant_count(len(set(runtime.attendee_clients.values())))
        await websocket.send_json(runtime.attendee_payload(uid))
        await runtime.broadcast_presenter()
        try:
            while True:
                message = await websocket.receive_json()
                try:
                    await runtime.handle_preview_attendee(websocket, message)
                except AnswerError as error:
                    await websocket.send_json({"type": "error", "message": str(error)})
        except WebSocketDisconnect:
            runtime.attendee_clients.pop(websocket, None)
            runtime.controller.set_participant_count(len(set(runtime.attendee_clients.values())))
            await runtime.broadcast_presenter()

    routes = [
        Route("/", presenter_page),
        Route("/attend/", attendee_page),
        Route("/presenter-runtime.js", presenter_runtime),
        Route("/runtime-config.js", attendee_runtime),
        Route("/slides.pdf", slide_pdf),
        Route("/api/qr.svg", qr_code),
        Route("/api/health", health),
        WebSocketRoute("/ws/presenter", presenter_websocket),
        WebSocketRoute("/ws/attendee", attendee_websocket),
        Mount("/assets", app=StaticFiles(directory=str(static_dir))),
    ]
    return Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(SecurityHeadersMiddleware)])
