from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from pypdf import PdfWriter
from typer.main import get_command

from confquiz.build import export_static_site, materialize_slides
from confquiz.config import ConfigError, load_firebase_web_config, load_quiz_config
from confquiz.firebase_backend import FirebaseError, FirebaseStore
from confquiz.server import PresenterRuntime, create_app

app = typer.Typer(no_args_is_help=True, help="Build and run Firebase-backed conference quizzes.")
firebase_app = typer.Typer(no_args_is_help=True, help="Generate Firebase configuration files.")
sessions_app = typer.Typer(no_args_is_help=True, help="Inspect and clean live quiz sessions.")
app.add_typer(firebase_app, name="firebase")
app.add_typer(sessions_app, name="sessions")


def _fail(error: Exception) -> None:
    typer.secho(str(error), fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _credential_path(config_path: Path, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit.expanduser().resolve()
    env_value = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_value:
        return Path(env_value).expanduser().resolve()
    candidates = list(config_path.parent.glob("*firebase-adminsdk*.json"))
    return candidates[0].resolve() if len(candidates) == 1 else None


def _load_live(config_path: Path, credential: Path | None = None):
    quiz = load_quiz_config(config_path)
    web = load_firebase_web_config(quiz, config_path)
    credential_path = _credential_path(config_path, credential)
    if credential_path and not credential_path.is_file():
        raise ConfigError(f"Firebase Admin credential does not exist: {credential_path}")
    if credential_path:
        metadata = json.loads(credential_path.read_text(encoding="utf-8"))
        if metadata.get("project_id") != web.projectId:
            raise ConfigError("Firebase Admin credential and web config use different project IDs")
    return quiz, web, credential_path


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="Directory to initialize.")] = Path("."),
    force: Annotated[bool, typer.Option("--force", help="Replace generated example files.")] = False,
) -> None:
    """Create a presentation and a safe Firebase configuration template."""
    target = directory.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    scaffold = files("confquiz").joinpath("scaffold")
    generated_files = [
        ("quiz.yml", "quiz.yml"),
        ("firebase.web.example.json", "firebase.web.example.json"),
    ]
    for source_name, destination_name in generated_files:
        destination = target / destination_name
        if destination.exists() and not force:
            typer.secho(f"Skipped existing {destination.name}", fg=typer.colors.YELLOW)
        else:
            shutil.copyfile(str(scaffold.joinpath(source_name)), destination)
            typer.echo(f"Created {destination}")
    gitignore_path = target / ".gitignore"
    if not gitignore_path.exists():
        shutil.copyfile(str(scaffold.joinpath("gitignore")), gitignore_path)
        typer.echo(f"Created {gitignore_path}")
    else:
        existing = gitignore_path.read_text(encoding="utf-8")
        existing_lines = set(existing.splitlines())
        required_rules = [
            "firebase.web.json",
            "*firebase-adminsdk*.json",
            "firebase-admin.json",
        ]
        missing_rules = [rule for rule in required_rules if rule not in existing_lines]
        if missing_rules:
            separator = "" if not existing or existing.endswith("\n") else "\n"
            addition = (
                "\n# Conf Quiz speaker-local Firebase configuration.\n"
                + "\n".join(missing_rules)
                + "\n"
            )
            with gitignore_path.open("a", encoding="utf-8") as output:
                output.write(separator + addition)
            typer.echo(f"Updated {gitignore_path}")
    pdf_path = target / "sample-slides.pdf"
    if not pdf_path.exists() or force:
        writer = PdfWriter()
        for _ in range(12):
            writer.add_blank_page(width=1280, height=720)
        with pdf_path.open("wb") as output:
            writer.write(output)
        typer.echo(f"Created {pdf_path}")
    typer.echo(
        "Next: copy firebase.web.example.json to firebase.web.json and add your Firebase web app config."
    )


@app.command()
def validate(config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")]) -> None:
    """Validate configuration, Firebase web metadata, PDF integrity, and slide references."""
    try:
        quiz = load_quiz_config(config)
        web = load_firebase_web_config(quiz, config)
        slides = materialize_slides(quiz, config.resolve())
    except (ConfigError, FirebaseError) as error:
        _fail(error)
    typer.secho("Configuration is valid.", fg=typer.colors.GREEN)
    typer.echo(f"Presentation: {quiz.presentation.title}")
    typer.echo(f"Slides: {slides.page_count} pages")
    typer.echo(f"Questions: {len(quiz.questions)}")
    typer.echo(f"Firebase project: {web.projectId}")


@app.command("export")
def export_command(
    config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Static site output directory.")] = Path(
        "dist"
    ),
) -> None:
    """Export the GitHub Pages-compatible attendee site."""
    try:
        quiz = load_quiz_config(config)
        slides = export_static_site(quiz, config.resolve(), output)
    except ConfigError as error:
        _fail(error)
    typer.secho(f"Exported static site to {output.expanduser().resolve()}", fg=typer.colors.GREEN)
    typer.echo(f"Validated {slides.page_count} PDF pages ({slides.sha256[:12]}…)")


def _serve(
    config: Path,
    host: str,
    port: int,
    *,
    live: bool,
    credential: Path | None = None,
) -> None:
    try:
        quiz = load_quiz_config(config)
        if live:
            _, _, credential_path = _load_live(config, credential)
            store = FirebaseStore(quiz, credential_path)
        else:
            store = None
        slides = materialize_slides(quiz, config.resolve())
    except (ConfigError, FirebaseError, ValueError) as error:
        _fail(error)
    runtime = PresenterRuntime(
        quiz,
        slides,
        mode="firebase" if live else "preview",
        store=store,
        local_port=port,
    )
    presenter_url = f"http://{host}:{port}/?token={runtime.control_token}"
    typer.secho("Presenter URL", fg=typer.colors.GREEN, bold=True)
    typer.echo(presenter_url)
    if not live:
        typer.secho("Attendee preview", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"http://{host}:{port}/attend/?code=DEMO26")
    typer.echo("Press Ctrl+C to stop.")
    uvicorn.run(create_app(runtime), host=host, port=port, log_level="info")


@app.command()
def preview(
    config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")],
    host: Annotated[str, typer.Option(help="Local bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Local port.")] = 8765,
) -> None:
    """Run presenter and attendee rehearsal views without Firebase."""
    _serve(config, host, port, live=False)


@app.command()
def present(
    config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")],
    credential: Annotated[
        Path | None,
        typer.Option("--credentials", help="Firebase Admin JSON path; defaults to ADC or local discovery."),
    ] = None,
    host: Annotated[str, typer.Option(help="Local bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Local port.")] = 8765,
) -> None:
    """Start the secure local presenter and a live Firebase room."""
    _serve(config, host, port, live=True, credential=credential)


@app.command()
def doctor(
    config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")],
    credential: Annotated[Path | None, typer.Option("--credentials")] = None,
) -> None:
    """Check the complete local and Firebase setup."""
    try:
        quiz, web, credential_path = _load_live(config, credential)
        slides = materialize_slides(quiz, config.resolve())
        store = FirebaseStore(quiz, credential_path)
        store.list_sessions(limit=1)
        store.close()
        assets = files("confquiz").joinpath("static/assets/attendee.js")
        if not assets.is_file():
            raise ConfigError("Bundled web assets are missing")
    except Exception as error:
        _fail(error)
    typer.secho("All required components are available.", fg=typer.colors.GREEN)
    typer.echo(f"Firebase: {web.projectId} / {web.authDomain}")
    typer.echo(f"Slides: {slides.page_count} pages")
    typer.echo(f"App Check configured: {'yes' if quiz.firebase.app_check_site_key else 'no'}")
    typer.echo(f"Admin credentials: {'available' if credential_path else 'Application Default Credentials'}")


@firebase_app.command("scaffold")
def firebase_scaffold(
    directory: Annotated[Path, typer.Argument(help="Destination directory.")] = Path("."),
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Copy deny-by-default Firestore rules, indexes, and emulator configuration."""
    target = directory.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    scaffold = files("confquiz").joinpath("scaffold")
    for name in ["firebase.json", "firestore.rules", "firestore.indexes.json"]:
        destination = target / name
        if destination.exists() and not force:
            typer.secho(f"Skipped existing {destination.name}", fg=typer.colors.YELLOW)
            continue
        shutil.copyfile(str(scaffold.joinpath(name)), destination)
        typer.echo(f"Created {destination}")


@sessions_app.command("list")
def list_sessions(
    config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")],
    credential: Annotated[Path | None, typer.Option("--credentials")] = None,
) -> None:
    """List recent rooms from Firestore."""
    try:
        quiz, _, credential_path = _load_live(config, credential)
        store = FirebaseStore(quiz, credential_path)
        sessions = store.list_sessions()
        store.close()
    except Exception as error:
        _fail(error)
    if not sessions:
        typer.echo("No sessions found.")
        return
    for session in sessions:
        created = session.get("createdAt")
        typer.echo(
            f"{session['id']}  {session.get('joinCode', '—')}  {session.get('status', 'unknown')}  {created}"
        )


@sessions_app.command("clean")
def clean_sessions(
    config: Annotated[Path, typer.Argument(help="Quiz YAML configuration.")],
    older_than: Annotated[
        int, typer.Option("--older-than", help="Delete sessions older than this many hours.")
    ] = 168,
    credential: Annotated[Path | None, typer.Option("--credentials")] = None,
) -> None:
    """Permanently delete expired session documents and responses."""
    try:
        quiz, _, credential_path = _load_live(config, credential)
        store = FirebaseStore(quiz, credential_path)
        count = store.cleanup(datetime.now(timezone.utc) - timedelta(hours=older_than))
        store.close()
    except Exception as error:
        _fail(error)
    typer.secho(f"Deleted {count} session(s).", fg=typer.colors.GREEN)


# Great Docs reads Click commands. Typer exposes the same command tree through
# this object while the installed console entry point continues to use `app`.
docs_cli = get_command(app)
