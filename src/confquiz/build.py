from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

import httpx
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pypdf import PdfReader

from confquiz.config import ConfigError, load_firebase_web_config
from confquiz.models import QuizConfig

MAX_PDF_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class SlideArtifact:
    source: str
    local_path: Path | None
    page_count: int
    sha256: str | None


def _is_remote(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def _read_pdf_metadata(path: Path) -> tuple[int, str]:
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ConfigError("PDF exceeds the 100 MiB safety limit")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        page_count = len(PdfReader(str(path)).pages)
    except Exception as error:  # pypdf exposes several parser-specific errors
        raise ConfigError(f"Could not read PDF {path}: {error}") from error
    if page_count < 1:
        raise ConfigError(f"PDF contains no pages: {path}")
    return page_count, digest


def materialize_slides(
    quiz: QuizConfig,
    quiz_path: Path,
    destination: Path | None = None,
) -> SlideArtifact:
    source = quiz.presentation.slides.source
    configured_hash = quiz.presentation.slides.sha256

    if _is_remote(source):
        if not quiz.presentation.slides.fetch_during_export and destination is None:
            raise ConfigError("Remote slides must be fetched for validation and local presentation")
        target = destination or (quiz_path.parent / ".confquiz-cache" / f"{quiz.presentation.id}.pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.stream("GET", source, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                total = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            output.close()
                            target.unlink(missing_ok=True)
                            raise ConfigError("PDF exceeds the 100 MiB safety limit")
                        output.write(chunk)
        except httpx.HTTPError as error:
            target.unlink(missing_ok=True)
            raise ConfigError(f"Could not download slides from {source}: {error}") from error
        local_path = target
    else:
        local_path = quiz.resolve_path(quiz_path, source)
        if not local_path.is_file():
            raise ConfigError(f"Slides PDF does not exist: {local_path}")
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, destination)
            local_path = destination

    page_count, digest = _read_pdf_metadata(local_path)
    if configured_hash and digest.lower() != configured_hash.lower():
        raise ConfigError(f"Slides SHA-256 mismatch: expected {configured_hash}, got {digest}")
    invalid = [question.id for question in quiz.questions if question.after_slide > page_count]
    if invalid:
        raise ConfigError(f"Questions reference slides beyond page {page_count}: {', '.join(invalid)}")
    return SlideArtifact(source=source, local_path=local_path, page_count=page_count, sha256=digest)


def _copy_package_tree(source_name: str, target: Path) -> None:
    source = files("confquiz").joinpath(source_name)
    if not source.is_dir():
        raise ConfigError(
            "Bundled web assets are missing. Run `npm install && npm run build` "
            "before building the Python package."
        )
    shutil.copytree(str(source), target, dirs_exist_ok=True)


def _template_environment() -> Environment:
    template_dir = files("confquiz").joinpath("templates")
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
    )


def export_static_site(quiz: QuizConfig, quiz_path: Path, output: Path) -> SlideArtifact:
    """Write a complete static attendee site.

    Parameters
    ----------
    quiz
        Validated presentation configuration.
    quiz_path
        Path to the YAML file, used to resolve relative inputs.
    output
        Directory that will receive the static site.

    Returns
    -------
    SlideArtifact
        Metadata for the PDF copied into the export.

    Raises
    ------
    ConfigError
        If the PDF, Firebase web configuration, or output cannot be prepared.
    """
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    _copy_package_tree("static/assets", assets)
    slide_artifact = materialize_slides(quiz, quiz_path, assets / "slides.pdf")
    firebase = load_firebase_web_config(quiz, quiz_path)

    runtime = {
        "mode": "firebase",
        "presentation": {
            "id": quiz.presentation.id,
            "title": quiz.presentation.title,
            "speaker": quiz.presentation.speaker,
            "theme": quiz.presentation.theme.model_dump(mode="json"),
        },
        "firebase": firebase.model_dump(exclude_none=True),
        "appCheckSiteKey": quiz.firebase.app_check_site_key,
        "namespace": quiz.firebase.namespace,
        "slideUrl": "assets/slides.pdf",
        "pdfWorkerUrl": "assets/pdf.worker.min.mjs?v=1.1.0",
    }
    (output / "runtime-config.js").write_text(
        "window.CONFQUIZ_RUNTIME = " + json.dumps(runtime, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    template = _template_environment().get_template("attendee.html.j2")
    (output / "index.html").write_text(
        template.render(
            title=quiz.presentation.title,
            accent=quiz.presentation.theme.accent,
            asset_prefix="",
        ),
        encoding="utf-8",
    )
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return slide_artifact
