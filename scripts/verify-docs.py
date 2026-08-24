"""Check that the rendered documentation has no broken internal links."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

CLI_PAGE_PATHS = (
    "reference/cli/confquiz.html",
    "reference/cli/init.html",
    "reference/cli/validate.html",
    "reference/cli/export.html",
    "reference/cli/preview.html",
    "reference/cli/present.html",
    "reference/cli/doctor.html",
    "reference/cli/firebase.html",
    "reference/cli/firebase/scaffold.html",
    "reference/cli/sessions.html",
    "reference/cli/sessions/list.html",
    "reference/cli/sessions/clean.html",
)


class LinkParser(HTMLParser):
    """Collect navigation and asset links from one HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = "href" if tag in {"a", "link"} else "src" if tag in {"img", "script"} else None
        if attribute and values.get(attribute):
            self.links.append(values[attribute] or "")


def candidates(site: Path, page: Path, raw_link: str) -> list[Path]:
    """Return possible filesystem targets for an internal rendered link."""
    parsed = urlsplit(raw_link)
    if parsed.scheme or parsed.netloc or raw_link.startswith(("mailto:", "tel:", "data:")):
        return []
    if not parsed.path:
        return [page]

    path = unquote(parsed.path)
    if path.startswith("/conf-quiz/"):
        target = site / path.removeprefix("/conf-quiz/")
    elif path.startswith("/"):
        target = site / path.lstrip("/")
    else:
        target = page.parent / path

    choices = [target]
    if path.endswith("/"):
        choices.append(target / "index.html")
    elif not target.suffix:
        choices.extend([target.with_suffix(".html"), target / "index.html"])
    return choices


def verify(site: Path) -> list[str]:
    """Return human-readable errors for broken links in a built site."""
    errors: list[str] = []
    html_files = sorted(site.rglob("*.html"))
    if not html_files:
        return [f"No HTML files found in {site}"]

    for page in html_files:
        parser = LinkParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for link in parser.links:
            if ".qmd" in urlsplit(link).path:
                errors.append(f"{page.relative_to(site)} keeps a source .qmd link: {link}")
                continue
            options = candidates(site, page, link)
            if options and not any(option.exists() for option in options):
                errors.append(f"{page.relative_to(site)} -> {link}")

    cli_index = site / "reference/cli/index.html"
    if not cli_index.is_file():
        errors.append("CLI reference index is missing")
        return errors

    cli_parser = LinkParser()
    cli_parser.feed(cli_index.read_text(encoding="utf-8"))
    linked_targets = {
        option.resolve()
        for link in cli_parser.links
        for option in candidates(site, cli_index, link)
        if option.exists()
    }
    for relative_path in CLI_PAGE_PATHS:
        target = site / relative_path
        if not target.is_file():
            errors.append(f"CLI reference page is missing: {relative_path}")
        elif target.resolve() not in linked_targets:
            errors.append(f"CLI index does not link to: {relative_path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="Rendered Great Docs site directory")
    args = parser.parse_args()
    site = args.site.expanduser().resolve()
    errors = verify(site)
    if errors:
        print("Documentation verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Verified internal links in {len(list(site.rglob('*.html')))} HTML pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
