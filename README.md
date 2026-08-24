# Conf Quiz

[![PyPI](https://img.shields.io/pypi/v/conf-quiz.svg)](https://pypi.org/project/conf-quiz/)
[![Python](https://img.shields.io/pypi/pyversions/conf-quiz.svg)](https://pypi.org/project/conf-quiz/)
[![Tests](https://github.com/adarshdigievo/conf-quiz/actions/workflows/ci.yml/badge.svg)](https://github.com/adarshdigievo/conf-quiz/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-111111)](https://adarshdigievo.github.io/conf-quiz/)
[![License: MIT](https://img.shields.io/badge/license-MIT-111111.svg)](https://github.com/adarshdigievo/conf-quiz/blob/main/LICENSE)

Conf Quiz turns a PDF slide deck and a YAML file into an interactive conference
presentation. Attendees join from their phones, answer anonymously, and see
results as the speaker moves through the deck. There are no points or
leaderboards.

The presenter runs locally. The attendee app is a static site that can be
published with GitHub Pages, and Firebase provides the shared live state.

## Features

- Insert questions after specific PDF pages.
- Run single choice, multiple choice, yes/no, slider, rating, number, ranking,
  free-text, and word-cloud questions.
- Show live results, close voting, and reveal an optional correct answer.
- Moderate text before it appears on screen.
- Share the active slide with attendee devices when useful.
- Rehearse the complete flow without connecting to Firebase.
- Export a base-path-safe attendee site for GitHub Pages or any static host.
- Reset a room, generate a new join code, and clean up old sessions from the
  local presenter tools.

## Installation

Conf Quiz supports Python 3.10 and newer.

```bash
pip install conf-quiz
```

With `uv`:

```bash
uv tool install conf-quiz
```

## Quick start

Create a presentation directory:

```bash
confquiz init my-talk
```

This creates `quiz.yml`, a safe Firebase web-config example, and a blank sample
PDF. Copy the example to the local filename used by `quiz.yml`:

```bash
cp my-talk/firebase.web.example.json my-talk/firebase.web.json
```

`firebase.web.json` is ignored by Git. Replace its placeholders with the web
configuration from your own Firebase project, then replace the PDF and edit
`quiz.yml`:

```yaml
schema_version: 1

presentation:
  id: my-talk
  title: "Reliable Python services"
  speaker: "Your name"
  public_url: "https://your-account.github.io/my-talk/"
  slides:
    source: "slides.pdf"

firebase:
  web_config: "firebase.web.json"

questions:
  - id: first-poll
    after_slide: 3
    type: single_choice
    prompt: "Which constraint matters most to your team?"
    options:
      - { id: reliability, label: "Reliability" }
      - { id: speed, label: "Delivery speed" }
      - { id: cost, label: "Cost" }
```

Rehearse locally without Firebase:

```bash
confquiz preview my-talk/quiz.yml
```

The command prints a presenter URL and an attendee URL. The preview room uses
the code `DEMO26` and keeps all data in memory.

When Firebase is configured, validate and export the attendee site:

```bash
confquiz validate my-talk/quiz.yml
confquiz export my-talk/quiz.yml --output my-talk/site
```

Then start the live presenter:

```bash
confquiz present my-talk/quiz.yml --credentials /safe/path/firebase-admin.json
```

The Admin credential stays on the presenter laptop. It is never included in the
exported site.

## Firebase

A live room needs:

- a Cloud Firestore database;
- Anonymous Authentication;
- a Firebase web app configuration;
- a Firebase Admin service-account credential for the presenter; and
- the included Firestore rules and indexes.

Generate the deployment files in your presentation directory:

```bash
confquiz firebase scaffold my-talk
```

Deploy them before sharing the attendee site:

```bash
npx firebase-tools use YOUR_PROJECT_ID
npx firebase-tools deploy --only firestore:rules,firestore:indexes
```

Every speaker supplies their own `firebase.web.json`; the package does not ship
the maintainer's Firebase project. Its browser identifiers are necessarily
included in that speaker's exported attendee site, but the local source file is
ignored to prevent accidental reuse or publication. The Admin JSON is a private
credential: keep it outside the repository and pass its path to
`confquiz present`.

For a GitHub Actions deployment, store the JSON object in a per-presentation
`FIREBASE_WEB_CONFIG` repository secret and create the ignored file during the
build; the deployment guide includes a complete workflow.
See the [Firebase setup guide](https://adarshdigievo.github.io/conf-quiz/user-guide/firebase-setup.html)
for the complete project and App Check setup.

## Presenter and attendee sites

`confquiz present` serves the presenter UI from the speaker's computer. The
attendee site is the static output of `confquiz export`, so it can use a
different host and URL. Set `presentation.public_url` to that address; Conf Quiz
uses it in the join link and QR code.

The exported homepage shows the first PDF page even when no room is running.
When the presenter starts a room, the join form appears above the presentation.

## Documentation

The full documentation covers:

- [installation and the first rehearsal](https://adarshdigievo.github.io/conf-quiz/user-guide/first-quiz.html);
- [the YAML configuration](https://adarshdigievo.github.io/conf-quiz/user-guide/configuration.html);
- [question types](https://adarshdigievo.github.io/conf-quiz/user-guide/question-types.html);
- [GitHub Pages deployment](https://adarshdigievo.github.io/conf-quiz/user-guide/deploy-attendee-site.html);
- [presenter controls](https://adarshdigievo.github.io/conf-quiz/user-guide/presenter-workflow.html);
- [security](https://adarshdigievo.github.io/conf-quiz/user-guide/security.html); and
- [troubleshooting](https://adarshdigievo.github.io/conf-quiz/user-guide/troubleshooting.html).

## Development

```bash
uv sync --extra dev
npm install
npm run build
uv run pytest
npm test
```

The Firestore rules test uses the Firebase Emulator Suite and requires Java:

```bash
npm run test:rules
```

Documentation contributors need Python 3.11 or newer and
[Quarto](https://quarto.org/):

```bash
uv sync --group docs
uv run --group docs great-docs build
```

More detail is in the
[development guide](https://adarshdigievo.github.io/conf-quiz/user-guide/development.html).

## Security

Conf Quiz is intended for anonymous conference participation, not elections,
assessments, or prize-bearing competitions. Anonymous Authentication limits a
browser profile to one response document; it does not prove that one browser
equals one person.

Please report security issues privately to the maintainer instead of opening a
public issue.

## License

Conf Quiz is released under the
[MIT License](https://github.com/adarshdigievo/conf-quiz/blob/main/LICENSE).
Third-party browser assets and their licenses are listed in
[THIRD_PARTY_NOTICES.md](https://github.com/adarshdigievo/conf-quiz/blob/main/THIRD_PARTY_NOTICES.md).
