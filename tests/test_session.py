from __future__ import annotations

from confquiz.session import SessionController, build_timeline


def test_questions_are_inserted_after_their_slide(quiz_files):
    _, quiz = quiz_files
    timeline = build_timeline(12, quiz.questions)
    assert [(item.kind, item.slide, item.question_id) for item in timeline[:5]] == [
        ("slide", 1, None),
        ("question", None, "single"),
        ("slide", 2, None),
        ("question", None, "multiple"),
        ("slide", 3, None),
    ]


def test_question_state_machine_and_correct_reveal(quiz_files):
    _, quiz = quiz_files
    controller = SessionController(quiz, 12)
    assert controller.phase == "slide" and controller.current_item.slide == 1
    controller.next()
    assert controller.phase == "open" and controller.current_question.id == "single"
    controller.set_responses("single", {"u1": "a"})
    controller.next()
    assert controller.phase == "results"
    assert "correct" not in controller.aggregate()
    controller.next()
    assert controller.phase == "revealed"
    assert controller.aggregate()["correct"] == "a"
    controller.next()
    assert controller.phase == "slide" and controller.current_item.slide == 2


def test_question_without_correct_skips_reveal(quiz_files):
    _, quiz = quiz_files
    controller = SessionController(quiz, 12)
    controller._activate(3)
    assert controller.current_question.id == "multiple"
    controller.next()
    assert controller.phase == "results"
    controller.next()
    assert controller.current_item.slide == 3


def test_moderation_reset_and_restart(quiz_files):
    _, quiz = quiz_files
    controller = SessionController(quiz, 12)
    controller.set_responses("free-text", {"uid": "hello"})
    controller.moderate("free-text", "uid", True)
    assert controller.approved["free-text"] == {"uid"}
    controller.clear_question("free-text")
    assert controller.responses["free-text"] == {}
    assert controller.approved["free-text"] == set()
    controller._activate(1)
    controller.clear_all()
    assert controller.phase == "slide"
    assert controller.current_item.slide == 1


def test_previous_on_first_slide_does_not_open_a_generated_lobby(quiz_files):
    _, quiz = quiz_files
    controller = SessionController(quiz, 12)
    controller.previous()
    assert controller.index == 0
    assert controller.phase == "slide"
    assert controller.current_item.slide == 1


def test_presenter_visibility_controls_are_live_session_state(quiz_files):
    _, quiz = quiz_files
    controller = SessionController(quiz, 12)
    controller.session_id = "session-id"
    controller.join_code = "DEMO26"

    assert controller.show_results_on_presenter is False
    assert controller.share_slides_with_attendees is False
    assert controller.state_document()["shareSlidesWithAttendees"] is False

    controller.toggle_presenter_results()
    controller.toggle_slide_sharing()

    payload = controller.presenter_payload("https://example.test/?code=DEMO26")["session"]
    assert payload["showResultsOnPresenter"] is True
    assert payload["presenterResultsPreference"] is True
    assert payload["presenterResultsForced"] is False
    assert payload["shareSlidesWithAttendees"] is True
    assert controller.state_document()["shareSlidesWithAttendees"] is True
    assert "showResultsOnPresenter" not in controller.state_document()


def test_presenter_results_are_forced_after_voting_closes(quiz_files):
    _, quiz = quiz_files
    controller = SessionController(quiz, 12)
    controller._activate(1)

    open_payload = controller.presenter_payload("https://example.test/")["session"]
    assert open_payload["phase"] == "open"
    assert open_payload["showResultsOnPresenter"] is False
    assert open_payload["presenterResultsForced"] is False

    controller.next()
    closed_payload = controller.presenter_payload("https://example.test/")["session"]
    assert closed_payload["phase"] == "results"
    assert closed_payload["showResultsOnPresenter"] is True
    assert closed_payload["presenterResultsPreference"] is False
    assert closed_payload["presenterResultsForced"] is True

    controller.next()
    revealed_payload = controller.presenter_payload("https://example.test/")["session"]
    assert revealed_payload["phase"] == "revealed"
    assert revealed_payload["showResultsOnPresenter"] is True
    assert revealed_payload["presenterResultsForced"] is True
