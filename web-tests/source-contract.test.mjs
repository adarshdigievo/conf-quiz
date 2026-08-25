import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

const attendee = await readFile("web_src/attendee.js", "utf8");
const presenter = await readFile("web_src/presenter.js", "utf8");
const attendeeCss = await readFile("web_src/attendee.css", "utf8");
const presenterCss = await readFile("web_src/presenter.css", "utf8");
const presenterTemplate = await readFile("src/confquiz/templates/presenter.html.j2", "utf8");
const attendeeTemplate = await readFile("src/confquiz/templates/attendee.html.j2", "utf8");

test("all question types have attendee controls", () => {
  for (const type of [
    "single_choice", "multiple_choice", "yes_no", "slider", "rating",
    "number", "ranking", "free_text", "word_cloud",
  ]) assert.match(attendee, new RegExp(type));
});

test("user content is not injected as HTML", () => {
  assert.doesNotMatch(attendee, /innerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(presenter, /innerHTML|insertAdjacentHTML|document\.write/);
});

test("presenter includes keyboard, moderation, reset, fullscreen, and visibility controls", () => {
  for (const marker of [
    "ArrowRight", "moderate", "reset_question", "new_session", "requestFullscreen",
    "toggle_presenter_results", "toggle_slide_sharing", "toggleChrome", "set_attendee_url",
    "data-restore-chrome", "setJoinStrip",
  ]) {
    assert.match(presenter, new RegExp(marker));
  }
});

test("presenter starts on the PDF timeline without a generated lobby", () => {
  assert.doesNotMatch(presenter, /renderLobby|phase === "lobby"/);
});

test("presenter has a single state-driven reveal control", () => {
  assert.match(presenter, /phase === "results".*"Reveal answer"/s);
  assert.doesNotMatch(presenter, /querySelector\('\[data-action="reveal"\]'/);
});

test("PDF.js receives an explicit URL parameter", () => {
  assert.match(presenter, /getDocument\(\{ url: runtime\.slideUrl \}\)/);
  assert.match(attendee, /getDocument\(\{ url: runtime\.slideUrl \}\)/);
  assert.match(attendee, /shareSlidesWithAttendees/);
});

test("shared attendee slides are fullscreenable and responsive", () => {
  assert.match(attendee, /requestFullscreen/);
  assert.match(attendee, /fullscreenchange/);
  assert.match(attendee, /sharedSlideFullscreen/);
  assert.match(attendeeCss, /\.shared-slide-shell:fullscreen/);
  assert.match(attendeeCss, /is-slide-expanded/);
  assert.match(attendeeCss, /@media \(max-width: 34rem\)/);
  assert.match(attendeeCss, /\.attendee-body\.is-shared-slide/);
  assert.doesNotMatch(attendeeCss, /gradient\s*\(/);
});

test("attendee offers a persistent light and dark theme toggle beside live status", () => {
  assert.match(attendeeTemplate, /data-attendee-theme-toggle/);
  assert.match(attendeeTemplate, /data-attendee-theme-icon[^>]*#moon-stars/);
  assert.match(attendee, /confquiz-attendee-theme/);
  assert.match(attendee, /function setAttendeeTheme/);
  assert.match(attendee, /document\.body\.dataset\.attendeeTheme/);
  assert.match(attendeeCss, /data-attendee-theme="dark"/);
  assert.match(attendeeCss, /color-scheme:\s*dark/);
});

test("attendee questions use phone, tablet, and laptop layouts", () => {
  assert.match(attendeeCss, /\.live-card\s*\{\s*max-width:\s*76rem/);
  assert.match(attendeeCss, /@media \(min-width: 48rem\)/);
  assert.match(attendeeCss, /@media \(min-width: 64rem\)/);
  assert.match(attendeeCss, /\.question-shell\[data-question-type="single_choice"\]/);
  assert.match(attendeeCss, /\.question-shell\.has-results\s*\{[^}]*grid-template-areas:/s);
  assert.match(attendeeCss, /\.attendee-body\.is-shared-slide \.live-card\s*\{[^}]*max-width:\s*none/s);
  assert.match(attendee, /shell\.dataset\.questionType = question\.type/);
  assert.match(attendee, /shell\.classList\.add\("has-results"\)/);
});

test("attendee homepage leads with slide one and only opens joining for a live room", () => {
  assert.match(attendeeTemplate, /data-home-slide-stage/);
  assert.match(attendeeTemplate, /data-join-panel hidden/);
  assert.match(attendeeTemplate, /Enter the code on screen\. Responses are anonymous\./);
  assert.equal(attendeeTemplate.match(/Responses are anonymous\./g)?.length, 1);
  assert.doesNotMatch(attendeeTemplate, /Answers are anonymous|Anonymous responses/);
  assert.match(attendee, /getPage\(1\)/);
  assert.match(attendee, /is-room-available/);
  assert.match(attendee, /_presentations/);
  assert.match(attendee, /onlineUntil/);
  assert.match(attendeeCss, /\.home-slide-shell:fullscreen/);
});

test("presenter participant count remains visible during questions", () => {
  assert.match(presenterTemplate, /presenter-participant-count/);
  assert.doesNotMatch(presenterCss, /is-question-active\s+\.presenter-participant-count\s*\{[^}]*display:\s*none/s);
});

test("presenter centers the complete persistent join instruction", () => {
  assert.match(presenterTemplate, /class="join-strip-content"/);
  assert.match(presenterTemplate, /class="join-strip-url" data-join-url/);
  assert.match(presenterCss, /--join-strip-height:/);
  assert.match(presenterCss, /\.join-strip-content\s*\{[^}]*display:\s*flex[^}]*justify-content:\s*center/s);
  assert.match(presenterCss, /\.join-strip \[data-join-code\]\s*\{[^}]*height:\s*calc\(100% - \.2rem\)[^}]*font-size:\s*clamp/s);
  assert.match(presenter, /splitJoinUrl\(value\)/);
  assert.match(presenterCss, /\.join-strip-url-base\s*\{[^}]*font-weight:\s*850/s);
  assert.match(presenterCss, /\.join-strip-url-params\s*\{[^}]*color:\s*var\(--muted\)[^}]*font-size:\s*\.82em/s);
});

test("presenter shows option questions before the first response", () => {
  assert.match(presenter, /presenterOptions\(question\)/);
  assert.match(presenter, /renderQuestionOptions\(question, options\)/);
  assert.match(presenterCss, /\.question-options\s*\{/);
  assert.match(presenterCss, /\.question-option-marker\s*\{/);
});

test("presenter keeps secondary controls out of the default toolbar", () => {
  const menuStart = presenterTemplate.indexOf('class="session-menu-popover"');
  const shareControl = presenterTemplate.indexOf("data-toggle-slide-sharing");
  assert.ok(menuStart >= 0 && shareControl > menuStart, "slide sharing belongs in the session menu");
  assert.match(presenterTemplate, /bootstrap-icons\.svg#bar-chart-line/);
  assert.doesNotMatch(presenterTemplate, /bootstrap-icons\.svg#eye/);
  assert.match(presenterTemplate, /fullscreen-only-control/);
  assert.match(presenterCss, /:fullscreen \.fullscreen-only-control\s*\{\s*display:\s*inline-flex/);
  assert.match(presenter, /fullscreenchange/);
  assert.doesNotMatch(presenter, /Controls hidden|press H to restore/);
});

test("presenter forces readable results after close and reveal", () => {
  assert.match(presenter, /\["results", "revealed"\]\.includes\(session\.phase\)/);
  assert.match(presenter, /presenterResultsForced/);
  assert.match(presenterCss, /\.question-results-pane[^}]*color:\s*var\(--ink\)[^}]*background:\s*var\(--results-bg\)/s);
  assert.doesNotMatch(presenterCss, /\.question-results-pane[^}]*background:\s*#171b17/);
});

test("presenter offers fullscreen results access and theme presets", () => {
  assert.match(presenterTemplate, /class="floating-results-button"[^>]*data-toggle-results/);
  assert.match(presenterCss, /:fullscreen \.presenter-body\.is-chrome-hidden\.is-live-voting \.floating-results-button\s*\{\s*display:\s*grid/);
  for (const theme of ["light", "dark", "grey", "navy", "warm", "ocean", "forest"]) {
    assert.match(presenterTemplate, new RegExp(`data-theme-option="${theme}"`));
    if (theme !== "light") assert.match(presenterCss, new RegExp(`data-presenter-theme="${theme}"`));
  }
  assert.match(presenter, /confquiz-presenter-theme/);
});

test("production bundles and PDF worker exist", async () => {
  for (const path of [
    "src/confquiz/static/assets/attendee.js",
    "src/confquiz/static/assets/presenter.js",
    "src/confquiz/static/assets/pdf.worker.min.mjs",
    "src/confquiz/static/assets/bootstrap-icons.svg",
  ]) assert.ok((await stat(path)).size > 100, `${path} should be built`);
});
