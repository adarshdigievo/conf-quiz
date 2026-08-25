import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import { splitJoinUrl } from "./presenter-join.js";
import { presenterOptions } from "./presenter-question.js";
import { element, renderResults } from "./results.js";
import { SlideRenderer } from "./slide-renderer.js";

const runtime = window.CONFQUIZ_PRESENTER;
const stage = document.querySelector("[data-stage]");
const connection = document.querySelector("[data-connection]");
const participantCount = document.querySelector("[data-participants]");
const responseCount = document.querySelector("[data-response-count]");
const joinOverlay = document.querySelector("[data-join-overlay]");
const joinStrip = document.querySelector("[data-join-strip]");
const joinCodes = document.querySelectorAll("[data-join-code]");
const joinUrls = document.querySelectorAll("[data-join-url]");
const qrImage = document.querySelector("[data-qr]");
const moderationPanel = document.querySelector("[data-moderation]");
const moderationList = document.querySelector("[data-moderation-list]");
const moderationToggle = document.querySelector("[data-toggle-moderation]");
const resultsToggles = document.querySelectorAll("[data-toggle-results]");
const slideSharingToggle = document.querySelector("[data-toggle-slide-sharing]");
const slideSharingLabel = document.querySelector("[data-slide-sharing-label]");
const themeOptions = document.querySelectorAll("[data-theme-option]");
const chromeToggle = document.querySelector("[data-toggle-chrome]");
const chromeRestore = document.querySelector("[data-restore-chrome]");
const joinToggle = document.querySelector("[data-toggle-join]");
const joinStripToggle = document.querySelector("[data-toggle-join-strip]");
const attendeeUrlForm = document.querySelector("[data-attendee-url-form]");
const attendeeUrlInput = document.querySelector("[data-attendee-url]");
const attendeeUrlHelp = document.querySelector("[data-attendee-url-help]");
const nextLabel = document.querySelector("[data-next-label]");
const progressLabel = document.querySelector("[data-progress-label]");
const progressBar = document.querySelector("[data-progress-bar]");
const toastRegion = document.querySelector("[data-toasts]");

let socket = null;
let state = null;
let reconnectTimer = null;
let themeStorageKey = null;
let lastSyncError = null;

const PRESENTER_THEMES = new Set(["light", "dark", "grey", "navy", "warm", "ocean", "forest"]);

GlobalWorkerOptions.workerSrc = runtime.pdfWorkerUrl;

const slideRenderer = new SlideRenderer({
  stage,
  loadDocument: () => getDocument({ url: runtime.slideUrl }).promise,
});

function setConnection(label, online) {
  connection.textContent = label;
  connection.dataset.state = online ? "online" : "offline";
}

function toast(message) {
  const node = element("div", "toast", message);
  toastRegion.append(node);
  setTimeout(() => node.remove(), 4000);
}

function send(action, extra = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    toast("Presenter connection is offline.");
    return;
  }
  socket.send(JSON.stringify({ action, ...extra }));
}

function accentTextColor(hex) {
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255);
  const [red, green, blue] = channels.map((channel) => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return (0.2126 * red + 0.7152 * green + 0.0722 * blue) > 0.179 ? "#101310" : "#ffffff";
}

function setPresenterTheme(name, persist = false) {
  const theme = PRESENTER_THEMES.has(name) ? name : "light";
  document.body.dataset.presenterTheme = theme;
  themeOptions.forEach((option) => {
    const selected = option.dataset.themeOption === theme;
    option.setAttribute("aria-checked", String(selected));
    option.classList.toggle("is-selected", selected);
  });
  if (persist && themeStorageKey) {
    try { localStorage.setItem(themeStorageKey, theme); } catch (_) { /* storage may be blocked */ }
  }
}

function loadPresenterTheme(presentation) {
  const nextStorageKey = `confquiz-presenter-theme:${presentation.id}`;
  if (themeStorageKey === nextStorageKey) return;
  themeStorageKey = nextStorageKey;
  let savedTheme = null;
  try { savedTheme = localStorage.getItem(themeStorageKey); } catch (_) { /* storage may be blocked */ }
  setPresenterTheme(savedTheme || presentation.theme.preset || "light");
}

function renderEnded(presentation) {
  slideRenderer.deactivate();
  const shell = element("div", "ended-stage");
  shell.append(element("p", "eyebrow", "Session complete"), element("h1", "", "Thanks for joining."));
  shell.append(element("p", "presenter-byline", presentation.title));
  stage.replaceChildren(shell);
}

async function renderSlide(pageNumber) {
  await slideRenderer.show(pageNumber, state?.presentation.pageCount || pageNumber);
}

function phaseLabel(phase) {
  return {
    open: "Voting open",
    results: "Voting closed",
    revealed: "Answer revealed",
  }[phase] || phase;
}

function renderQuestionOptions(question, options) {
  const section = element("section", "question-options-section");
  section.setAttribute("aria-label", "Answer options");
  const heading = question.type === "ranking" ? "Options to rank" : "Answer options";
  const list = element("ol", `question-options${options.length > 8 ? " is-dense" : ""}`);
  for (const option of options) {
    const item = element("li", "question-option");
    item.append(
      element("span", "question-option-marker", option.marker),
      element("span", "question-option-label", option.label),
    );
    list.append(item);
  }
  section.append(element("p", "question-options-label", heading), list);
  return section;
}

function renderQuestion(session) {
  slideRenderer.deactivate();
  const question = session.activeQuestion;
  const options = presenterOptions(question);
  const resultsVisible = Boolean(
    session.showResultsOnPresenter || ["results", "revealed"].includes(session.phase)
  );
  const shell = element("div", "question-stage");
  shell.classList.toggle("results-hidden", !resultsVisible);
  shell.classList.toggle("has-options", !resultsVisible && options.length > 0);
  const promptPane = element("div", "question-prompt-pane");
  const topline = element("div", "question-topline");
  const stats = element("div", "question-audience-stats");
  const joined = element("span", "question-audience-stat");
  joined.append(element("strong", "", session.participantCount), element("span", "", "joined"));
  const answers = element("span", "question-audience-stat");
  answers.append(element("strong", "", session.responseCount), element("span", "", "answers"));
  stats.append(joined, answers);
  topline.append(element("p", "eyebrow", question.type.replaceAll("_", " ")), stats);
  promptPane.append(topline, element("h1", "", question.prompt));
  if (question.description) promptPane.append(element("p", "question-description", question.description));
  if (!resultsVisible && options.length) promptPane.append(renderQuestionOptions(question, options));
  const meta = element("div", "question-meta");
  const chip = element("span", `phase-chip${session.phase === "open" ? " is-open" : ""}`, phaseLabel(session.phase));
  meta.append(chip);
  promptPane.append(meta);

  shell.append(promptPane);
  if (resultsVisible) {
    const resultsPane = element("div", "question-results-pane");
    const heading = element("div", "question-results-heading");
    heading.append(
      element("h2", "", session.phase === "revealed" ? "Answer & results" : "Live results"),
      element("span", "", `${session.participantCount} joined · ${session.responseCount} answers`),
    );
    const results = element("div");
    renderResults(results, question, session.aggregate);
    resultsPane.append(heading, results);
    shell.append(resultsPane);
  }
  stage.replaceChildren(shell);
}

function renderModeration(session) {
  const isText = ["free_text", "word_cloud"].includes(session.activeQuestion?.type);
  moderationToggle.hidden = !isText;
  if (!isText) moderationPanel.hidden = true;
  moderationList.replaceChildren();
  if (!session.moderation?.length) {
    moderationList.append(element("p", "panel-help", "Responses will appear here for approval."));
    return;
  }
  for (const response of session.moderation) {
    const item = element("article", "moderation-item");
    item.append(element("small", "", response.label), element("p", "", response.text));
    const actions = element("div", "moderation-actions");
    const approve = element("button", response.approved ? "is-active" : "", response.approved ? "Approved" : "Approve");
    approve.type = "button";
    approve.addEventListener("click", () => send("moderate", { uid: response.uid, approved: true }));
    const hide = element("button", !response.approved ? "is-active" : "", "Hide");
    hide.type = "button";
    hide.addEventListener("click", () => send("moderate", { uid: response.uid, approved: false }));
    actions.append(approve, hide);
    item.append(actions);
    moderationList.append(item);
  }
}

function updateControls(session) {
  const phase = session.phase;
  nextLabel.textContent = phase === "open" ? "Close voting"
      : phase === "results" && session.activeQuestion?.hasCorrect ? "Reveal answer"
        : phase === "ended" ? "Ended" : "Next";
  document.querySelector('[data-action="previous"]').disabled = session.timelineIndex <= 0;
  document.querySelector('[data-action="next"]').disabled = phase === "ended";
  const total = Math.max(1, session.timelineLength);
  const current = phase === "ended" ? total : Math.min(total, Math.max(1, session.timelineIndex + 1));
  progressLabel.textContent = phase === "ended" ? "Complete" : `${current} / ${total}`;
  progressBar.style.width = `${Math.min(100, current * 100 / total)}%`;
  const resultsForced = Boolean(
    session.presenterResultsForced ?? ["results", "revealed"].includes(phase)
  );
  const resultsVisible = Boolean(session.showResultsOnPresenter || resultsForced);
  resultsToggles.forEach((toggle) => {
    const label = resultsForced
      ? "Results are always shown after voting closes"
      : resultsVisible ? "Hide live results" : "Show live results";
    toggle.classList.toggle("is-active", resultsVisible);
    toggle.disabled = resultsForced;
    toggle.setAttribute("aria-pressed", String(resultsVisible));
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  });
  const slidesShared = Boolean(session.shareSlidesWithAttendees);
  slideSharingToggle.classList.toggle("is-active", slidesShared);
  slideSharingToggle.setAttribute("aria-pressed", String(slidesShared));
  slideSharingToggle.setAttribute("aria-label", slidesShared ? "Stop sharing slides with attendees" : "Share slides with attendees");
  slideSharingToggle.title = slidesShared ? "Stop sharing slides with attendees" : "Share slides with attendees";
  slideSharingLabel.textContent = slidesShared ? "Stop sharing slides with attendees" : "Share slides with attendees";
}

function toggleChrome() {
  if (!document.fullscreenElement && !document.body.classList.contains("is-chrome-hidden")) return;
  const hidden = document.body.classList.toggle("is-chrome-hidden");
  chromeToggle.setAttribute("aria-pressed", String(hidden));
  chromeToggle.setAttribute("aria-label", hidden ? "Show presenter controls" : "Hide presenter controls");
  chromeToggle.title = hidden ? "Show presenter controls (H)" : "Hide presenter controls (H)";
  slideRenderer.invalidate();
  setTimeout(() => {
    if (state?.session.phase === "slide") {
      renderSlide(state.session.activeSlide).catch((error) => toast(error.message));
    }
  }, 50);
}

function setJoinStrip(visible) {
  joinStrip.hidden = !visible;
  document.body.classList.toggle("is-join-strip-visible", visible);
  joinStripToggle.classList.toggle("is-active", visible);
  joinStripToggle.setAttribute("aria-pressed", String(visible));
  joinStripToggle.setAttribute("aria-label", visible ? "Hide joining information" : "Show joining information");
  joinStripToggle.title = visible ? "Hide joining information" : "Show joining information";
  slideRenderer.invalidate();
  setTimeout(() => {
    if (state?.session.phase === "slide") {
      renderSlide(state.session.activeSlide).catch((error) => toast(error.message));
    }
  }, 50);
}

function setJoinScreen(visible) {
  joinOverlay.hidden = !visible;
  joinToggle.setAttribute("aria-label", visible ? "Hide join code" : "Show join code");
  joinToggle.classList.toggle("is-active", visible);
}

function renderJoinUrl(node, value) {
  if (!node.classList.contains("join-strip-url")) {
    node.textContent = value;
    return;
  }
  const { base, parameters } = splitJoinUrl(value);
  node.replaceChildren(element("strong", "join-strip-url-base", base));
  if (parameters) node.append(element("span", "join-strip-url-params", parameters));
}

function applyState(message) {
  state = message;
  const { session, presentation } = message;
  document.body.classList.toggle("is-question-active", ["open", "results", "revealed"].includes(session.phase));
  document.body.classList.toggle("is-live-voting", session.phase === "open");
  participantCount.textContent = session.participantCount;
  responseCount.textContent = session.responseCount;
  joinCodes.forEach((node) => { node.textContent = session.joinCode; });
  joinUrls.forEach((node) => { renderJoinUrl(node, session.joinUrl); });
  qrImage.src = `${runtime.qrUrl}&v=${encodeURIComponent(`${session.id}:${session.joinUrl}`)}`;
  if (document.activeElement !== attendeeUrlInput) attendeeUrlInput.value = session.attendeeBaseUrl || "";
  attendeeUrlInput.disabled = !session.attendeeUrlEditable;
  attendeeUrlForm.querySelector('button[type="submit"]').disabled = !session.attendeeUrlEditable;
  attendeeUrlHelp.textContent = session.attendeeUrlEditable
    ? "The QR code uses this public static site. The room code is added automatically."
    : "Preview attendees use this local server. Set presentation.public_url for live rooms.";
  document.documentElement.style.setProperty("--accent", presentation.theme.accent);
  document.documentElement.style.setProperty("--accent-ink", accentTextColor(presentation.theme.accent));
  loadPresenterTheme(presentation);
  if (session.syncStatus === "error") {
    setConnection("Unsynchronized", false);
    if (session.syncError && session.syncError !== lastSyncError) toast(session.syncError);
    lastSyncError = session.syncError;
  } else if (session.syncStatus === "syncing") {
    setConnection("Syncing", true);
    lastSyncError = null;
  } else {
    setConnection("Live", true);
    lastSyncError = null;
  }
  if (session.phase === "slide") renderSlide(session.activeSlide).catch((error) => toast(error.message));
  else if (["open", "results", "revealed"].includes(session.phase)) renderQuestion(session);
  else renderEnded(presentation);
  renderModeration(session);
  updateControls(session);
}

function connect() {
  clearTimeout(reconnectTimer);
  setConnection("Connecting", false);
  socket = new WebSocket(runtime.websocketUrl);
  socket.addEventListener("open", () => setConnection("Live", true));
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "error") toast(message.message);
    else if (message.type === "state") applyState(message);
  });
  socket.addEventListener("close", () => {
    setConnection("Reconnecting", false);
    reconnectTimer = setTimeout(connect, 1200);
  });
  socket.addEventListener("error", () => setConnection("Connection error", false));
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => {
    const action = button.dataset.action;
    const confirmations = {
      reset_question: "Clear every answer for the current question?",
      restart: "Restart the presentation and permanently clear every response in this room?",
      new_session: "End this room and create a new join code?",
      end_session: "End this room? Attendees will no longer be able to answer.",
    };
    if (confirmations[action] && !window.confirm(confirmations[action])) return;
    send(action);
    const details = button.closest("details");
    if (details) details.open = false;
  });
});

joinToggle.addEventListener("click", () => setJoinScreen(joinOverlay.hidden));
document.querySelector("[data-close-join]").addEventListener("click", () => setJoinScreen(false));
joinStripToggle.addEventListener("click", () => setJoinStrip(joinStrip.hidden));
document.querySelector("[data-hide-join-strip]").addEventListener("click", () => setJoinStrip(false));
resultsToggles.forEach((toggle) => {
  toggle.addEventListener("click", () => send("toggle_presenter_results"));
});
themeOptions.forEach((option) => {
  option.addEventListener("click", () => {
    setPresenterTheme(option.dataset.themeOption, true);
    option.closest("details").open = false;
  });
});
slideSharingToggle.addEventListener("click", () => {
  send("toggle_slide_sharing");
  slideSharingToggle.closest("details").open = false;
});
chromeToggle.addEventListener("click", toggleChrome);
chromeRestore.addEventListener("click", toggleChrome);
moderationToggle.addEventListener("click", () => { moderationPanel.hidden = !moderationPanel.hidden; });
document.querySelector("[data-close-moderation]").addEventListener("click", () => { moderationPanel.hidden = true; });
attendeeUrlForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (attendeeUrlInput.disabled) return;
  send("set_attendee_url", { url: attendeeUrlInput.value });
  attendeeUrlForm.closest("details").open = false;
});
document.querySelector("[data-fullscreen]").addEventListener("click", async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.documentElement.requestFullscreen();
});
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && document.body.classList.contains("is-chrome-hidden")) toggleChrome();
});

document.addEventListener("keydown", (event) => {
  if (["INPUT", "TEXTAREA", "BUTTON", "SUMMARY"].includes(event.target.tagName)) return;
  if (["ArrowRight", " ", "PageDown"].includes(event.key)) { event.preventDefault(); send("next"); }
  else if (["ArrowLeft", "PageUp"].includes(event.key)) { event.preventDefault(); send("previous"); }
  else if (event.key.toLowerCase() === "j") setJoinScreen(joinOverlay.hidden);
  else if (event.key.toLowerCase() === "f") document.querySelector("[data-fullscreen]").click();
  else if (event.key.toLowerCase() === "h") toggleChrome();
});

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state?.session.phase === "slide") {
      slideRenderer.invalidate();
      renderSlide(state.session.activeSlide).catch((error) => toast(error.message));
    }
  }, 150);
});

connect();
