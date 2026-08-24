import { initializeApp } from "firebase/app";
import { initializeAppCheck, ReCaptchaEnterpriseProvider } from "firebase/app-check";
import { browserLocalPersistence, getAuth, onAuthStateChanged, setPersistence, signInAnonymously } from "firebase/auth";
import {
  doc,
  getDoc,
  getFirestore,
  onSnapshot,
  serverTimestamp,
  setDoc,
} from "firebase/firestore";
import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import { element, renderResults } from "./results.js";

const runtime = window.CONFQUIZ_RUNTIME;
const joinView = document.querySelector('[data-view="join"]');
const liveView = document.querySelector('[data-view="live"]');
const liveContent = document.querySelector("[data-live-content]");
const joinForm = document.querySelector("[data-join-form]");
const joinInput = document.querySelector("#join-code");
const joinMessage = document.querySelector("[data-join-message]");
const joinPanel = document.querySelector("[data-join-panel]");
const homeSlideShell = document.querySelector("[data-home-slide-shell]");
const homeSlideStage = document.querySelector("[data-home-slide-stage]");
const homeSlideFullscreenButton = document.querySelector("[data-home-slide-fullscreen]");
const connection = document.querySelector("[data-connection]");
const roomLabel = document.querySelector("[data-room-label]");
const titleNode = document.querySelector("[data-presentation-title]");
const leaveButton = document.querySelector("[data-leave]");
const attendeeThemeToggle = document.querySelector("[data-attendee-theme-toggle]");
const attendeeThemeIcon = document.querySelector("[data-attendee-theme-icon]");
const attendeeThemeColor = document.querySelector("[data-attendee-theme-color]");
const ATTENDEE_THEME_STORAGE_KEY = "confquiz-attendee-theme";

let joinedCode = "";
let currentSessionId = "";
let currentSession = null;
let currentQuestion = null;
let currentAggregate = null;
let existingAnswer = undefined;
let submitted = false;
let submitAnswer = null;
let previewSocket = null;
let previewState = null;
let unsubscribeSession = null;
let unsubscribeQuestion = null;
let unsubscribeAggregate = null;
let unsubscribeAvailability = null;
let availabilityExpiryTimer = null;
let firebaseUser = null;
let firebaseAuth = null;
let database = null;
let roomAvailable = false;
let sharedPdfDocument = null;
let homeSlideRenderTask = null;
let homeSlideRequest = 0;
let homeSlideResizeTimer = null;
let sharedSlideRenderTask = null;
let sharedSlideShell = null;
let sharedSlidePage = null;
let sharedSlideRequest = 0;
let sharedSlideResizeTimer = null;

GlobalWorkerOptions.workerSrc = runtime.pdfWorkerUrl;

function setConnection(label, state) {
  connection.textContent = label;
  connection.dataset.state = state;
}

function setAttendeeTheme(theme, persist = false) {
  const selected = theme === "dark" ? "dark" : "light";
  const isDark = selected === "dark";
  const iconHref = attendeeThemeIcon.getAttribute("href") || "";
  const iconBase = iconHref.split("#")[0];
  document.body.dataset.attendeeTheme = selected;
  attendeeThemeToggle.setAttribute("aria-pressed", String(isDark));
  attendeeThemeToggle.setAttribute("aria-label", isDark ? "Use light theme" : "Use dark theme");
  attendeeThemeToggle.title = isDark ? "Use light theme" : "Use dark theme";
  attendeeThemeIcon.setAttribute("href", `${iconBase}#${isDark ? "sun" : "moon-stars"}`);
  attendeeThemeColor.content = isDark
    ? "#111315"
    : runtime.presentation?.theme?.accent || "#f5f5f2";
  if (persist) {
    try { localStorage.setItem(ATTENDEE_THEME_STORAGE_KEY, selected); }
    catch { /* local storage can be disabled without blocking the quiz */ }
  }
}

function loadAttendeeTheme() {
  let savedTheme = "light";
  try { savedTheme = localStorage.getItem(ATTENDEE_THEME_STORAGE_KEY) || "light"; }
  catch { /* use the light default when local storage is unavailable */ }
  setAttendeeTheme(savedTheme);
}

function setTheme(theme = {}) {
  if (theme.accent) document.documentElement.style.setProperty("--accent", theme.accent);
  if (theme.background) document.documentElement.style.setProperty("--ink", theme.background);
}

function cleanCode(value) {
  return value.toUpperCase().replace(/[^A-Z2-9]/g, "").slice(0, 12);
}

function showJoinError(message) {
  joinMessage.textContent = message;
  joinInput.setAttribute("aria-invalid", message ? "true" : "false");
}

function showLive() {
  if (document.fullscreenElement === homeSlideShell) document.exitFullscreen().catch(() => {});
  document.body.classList.remove("is-home-slide-expanded");
  joinView.hidden = true;
  liveView.hidden = false;
  roomLabel.textContent = `Room ${joinedCode}`;
  titleNode.textContent = runtime.presentation?.title || previewState?.presentation?.title || "Live question";
}

function showJoin() {
  document.body.classList.remove("is-shared-slide", "is-slide-expanded");
  joinView.hidden = false;
  liveView.hidden = true;
  liveContent.replaceChildren();
  requestAnimationFrame(repaintHomeSlide);
}

function setRoomAvailability(available) {
  roomAvailable = Boolean(available);
  joinPanel.hidden = !roomAvailable;
  document.body.classList.toggle("is-room-available", roomAvailable);
  if (!joinedCode) setConnection(roomAvailable ? "Room open" : "Presentation", roomAvailable ? "online" : "offline");
  requestAnimationFrame(repaintHomeSlide);
}

function applyFirebaseAvailability(data) {
  clearTimeout(availabilityExpiryTimer);
  const onlineUntil = data?.onlineUntil?.toMillis?.() || 0;
  const remaining = onlineUntil - Date.now();
  setRoomAvailability(data?.status === "running" && remaining > 0);
  if (data?.status === "running" && remaining > 0) {
    availabilityExpiryTimer = setTimeout(() => setRoomAvailability(false), remaining + 100);
  }
}

function updateHomeSlideFullscreenButton() {
  const active = document.fullscreenElement === homeSlideShell
    || document.body.classList.contains("is-home-slide-expanded");
  homeSlideFullscreenButton.setAttribute("aria-pressed", String(active));
  homeSlideFullscreenButton.setAttribute("aria-label", active ? "Exit slide fullscreen" : "Show slide fullscreen");
  homeSlideFullscreenButton.title = active ? "Exit full screen" : "Full screen";
  homeSlideFullscreenButton.querySelector("span").textContent = active ? "Exit full screen" : "Full screen";
}

async function paintHomeSlide(requestId) {
  if (!sharedPdfDocument) sharedPdfDocument = await getDocument({ url: runtime.slideUrl }).promise;
  const page = await sharedPdfDocument.getPage(1);
  if (requestId !== homeSlideRequest || !homeSlideStage.isConnected) return;
  if (homeSlideRenderTask) {
    try { homeSlideRenderTask.cancel(); } catch (_) { /* already complete */ }
  }
  const base = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(240, homeSlideStage.clientWidth);
  const isFullscreen = document.fullscreenElement === homeSlideShell
    || document.body.classList.contains("is-home-slide-expanded");
  const availableHeight = Math.max(160, homeSlideStage.clientHeight);
  const scale = isFullscreen
    ? Math.min(availableWidth / base.width, availableHeight / base.height)
    : availableWidth / base.width;
  const viewport = page.getViewport({ scale });
  const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(viewport.width * pixelRatio);
  canvas.height = Math.floor(viewport.height * pixelRatio);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "Presentation slide 1");
  homeSlideStage.replaceChildren(canvas);
  homeSlideRenderTask = page.render({
    canvasContext: canvas.getContext("2d", { alpha: false }),
    viewport,
    transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
  });
  try {
    await homeSlideRenderTask.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
  }
}

function repaintHomeSlide() {
  if (joinView.hidden || !homeSlideStage.isConnected) return;
  homeSlideRequest += 1;
  const requestId = homeSlideRequest;
  paintHomeSlide(requestId).catch(() => {
    if (requestId === homeSlideRequest && homeSlideStage.isConnected) {
      homeSlideStage.replaceChildren(element("p", "shared-slide-error", "The presentation could not be loaded."));
    }
  });
}

async function toggleHomeSlideFullscreen() {
  if (document.fullscreenElement === homeSlideShell) {
    document.body.classList.remove("is-home-slide-expanded");
    await document.exitFullscreen();
  } else if (document.body.classList.contains("is-home-slide-expanded")) {
    document.body.classList.remove("is-home-slide-expanded");
  } else {
    document.body.classList.add("is-home-slide-expanded");
    if (document.fullscreenEnabled && homeSlideShell.requestFullscreen) {
      homeSlideShell.requestFullscreen().catch(() => { /* expanded layout remains available */ });
    }
  }
  updateHomeSlideFullscreenButton();
  requestAnimationFrame(repaintHomeSlide);
}

async function paintSharedSlide(pageNumber, mount, requestId) {
  if (!sharedPdfDocument) sharedPdfDocument = await getDocument({ url: runtime.slideUrl }).promise;
  const page = await sharedPdfDocument.getPage(pageNumber);
  if (requestId !== sharedSlideRequest || !mount.isConnected) return;
  if (sharedSlideRenderTask) {
    try { sharedSlideRenderTask.cancel(); } catch (_) { /* already complete */ }
  }
  const base = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(240, mount.clientWidth);
  const isFullscreen = document.fullscreenElement?.contains(mount)
    || document.body.classList.contains("is-slide-expanded");
  const availableHeight = Math.max(160, mount.clientHeight);
  const scale = isFullscreen
    ? Math.min(availableWidth / base.width, availableHeight / base.height)
    : availableWidth / base.width;
  const viewport = page.getViewport({ scale });
  const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(viewport.width * pixelRatio);
  canvas.height = Math.floor(viewport.height * pixelRatio);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", `Presentation slide ${pageNumber}`);
  mount.replaceChildren(canvas);
  sharedSlideRenderTask = page.render({
    canvasContext: canvas.getContext("2d", { alpha: false }),
    viewport,
    transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
  });
  try {
    await sharedSlideRenderTask.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
  }
}

function updateSharedSlideFullscreenButton() {
  const button = sharedSlideShell?.querySelector("[data-shared-slide-fullscreen]");
  if (!button) return;
  const active = document.fullscreenElement === sharedSlideShell
    || document.body.classList.contains("is-slide-expanded");
  button.setAttribute("aria-pressed", String(active));
  button.setAttribute("aria-label", active ? "Exit slide fullscreen" : "Show slide fullscreen");
  button.title = active ? "Exit full screen" : "Full screen";
  const label = button.querySelector(".shared-slide-fullscreen-label");
  if (label) label.textContent = active ? "Exit full screen" : "Full screen";
}

function repaintSharedSlide() {
  const mount = sharedSlideShell?.querySelector(".shared-slide-stage");
  if (!sharedSlidePage || !mount?.isConnected) return;
  sharedSlideRequest += 1;
  const requestId = sharedSlideRequest;
  paintSharedSlide(sharedSlidePage, mount, requestId).catch(() => {
    if (requestId === sharedSlideRequest && mount.isConnected) {
      mount.replaceChildren(element("p", "shared-slide-error", "The shared slide could not be loaded."));
    }
  });
}

async function toggleSharedSlideFullscreen() {
  if (!sharedSlideShell) return;
  if (document.fullscreenElement === sharedSlideShell) {
    document.body.classList.remove("is-slide-expanded");
    await document.exitFullscreen();
  } else if (document.body.classList.contains("is-slide-expanded")) {
    document.body.classList.remove("is-slide-expanded");
  } else {
    document.body.classList.add("is-slide-expanded");
    if (document.fullscreenEnabled && sharedSlideShell.requestFullscreen) {
      sharedSlideShell.requestFullscreen().catch(() => { /* expanded layout remains available */ });
    }
  }
  updateSharedSlideFullscreenButton();
  requestAnimationFrame(repaintSharedSlide);
}

function renderWaiting(phase, session) {
  if (phase === "slide" && session.shareSlidesWithAttendees && session.activeSlide) {
    document.body.classList.add("is-shared-slide");
    if (sharedSlidePage === session.activeSlide && sharedSlideShell?.isConnected) return;
    sharedSlidePage = session.activeSlide;
    sharedSlideRequest += 1;
    const requestId = sharedSlideRequest;
    const shell = element("div", "shared-slide-shell");
    const notice = element("div", "shared-slide-notice");
    const noticeCopy = element("div", "shared-slide-notice-copy");
    noticeCopy.append(
      element("strong", "", "Following the presentation"),
      element("span", "", " · Questions appear automatically."),
    );
    const fullscreenButton = element("button", "shared-slide-fullscreen");
    fullscreenButton.type = "button";
    fullscreenButton.dataset.sharedSlideFullscreen = "";
    fullscreenButton.setAttribute("aria-pressed", "false");
    fullscreenButton.append(
      element("span", "shared-slide-fullscreen-icon", "⛶"),
      element("span", "shared-slide-fullscreen-label", "Full screen"),
    );
    fullscreenButton.querySelector(".shared-slide-fullscreen-icon").setAttribute("aria-hidden", "true");
    fullscreenButton.addEventListener("click", () => {
      toggleSharedSlideFullscreen().catch(() => { /* fullscreen is optional on unsupported devices */ });
    });
    notice.append(element("span", "shared-slide-dot", ""), noticeCopy, fullscreenButton);
    const mount = element("div", "shared-slide-stage");
    mount.append(element("span", "shared-slide-loading", `Loading slide ${session.activeSlide}…`));
    shell.append(notice, mount);
    sharedSlideShell = shell;
    liveContent.replaceChildren(shell);
    updateSharedSlideFullscreenButton();
    paintSharedSlide(session.activeSlide, mount, requestId).catch(() => {
      if (requestId === sharedSlideRequest && mount.isConnected) {
        mount.replaceChildren(element("p", "shared-slide-error", "The shared slide could not be loaded."));
      }
    });
    return;
  }
  document.body.classList.remove("is-shared-slide", "is-slide-expanded");
  sharedSlideRequest += 1;
  sharedSlidePage = null;
  sharedSlideShell = null;
  const shell = element("div", "waiting-state");
  const number = phase === "ended" ? "✓" : "…";
  const heading = phase === "ended" ? "That’s a wrap." : "Stay on this screen.";
  const body = phase === "ended"
    ? "The presenter has ended this room. Thanks for taking part."
    : "The next question will appear here automatically.";
  shell.append(element("span", "waiting-number", number), element("h2", "", heading), element("p", "", body));
  liveContent.replaceChildren(shell);
}

function optionInputs(question, answer) {
  const fieldset = element("fieldset", "answer-fieldset");
  const legend = element("legend", "", "Choose an answer");
  legend.className = "sr-only";
  fieldset.append(legend);
  const isMultiple = question.type === "multiple_choice";
  for (const option of question.options || []) {
    const label = element("label", "answer-option");
    const input = document.createElement("input");
    input.type = isMultiple ? "checkbox" : "radio";
    input.name = isMultiple ? "answer" : "answer";
    input.value = option.id;
    input.checked = isMultiple ? Array.isArray(answer) && answer.includes(option.id) : answer === option.id;
    label.append(input, element("span", "", option.label));
    fieldset.append(label);
  }
  return fieldset;
}

function ratingInputs(question, answer) {
  const wrap = element("fieldset", "answer-fieldset");
  const legend = element("legend", "", "Choose a rating");
  legend.className = "sr-only";
  wrap.append(legend);
  const row = element("div", "rating-row");
  const minimum = Number(question.min);
  const maximum = Number(question.max);
  const values = [];
  for (let value = minimum; value <= maximum; value += Number(question.step || 1)) values.push(value);
  row.style.setProperty("--rating-count", String(values.length));
  for (const value of values) {
    const label = element("label", "rating-option");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "answer";
    input.value = String(value);
    input.checked = Number(answer) === value;
    label.append(input, element("span", "", value));
    row.append(label);
  }
  wrap.append(row);
  return wrap;
}

function rangeInput(question, answer) {
  const wrap = element("div", "range-field");
  const value = answer ?? question.min;
  const output = element("output", "range-value", value);
  const input = document.createElement("input");
  input.type = "range";
  input.name = "answer";
  input.min = question.min;
  input.max = question.max;
  input.step = question.step || 1;
  input.value = value;
  input.setAttribute("aria-label", question.prompt);
  input.addEventListener("input", () => { output.value = input.value; output.textContent = input.value; });
  const labels = element("div", "range-labels");
  labels.append(
    element("span", "", question.labels?.min || question.min),
    element("span", "", question.labels?.max || question.max),
  );
  wrap.append(output, input, labels);
  return wrap;
}

function numberInput(question, answer) {
  const input = document.createElement("input");
  input.className = "number-input";
  input.type = "number";
  input.name = "answer";
  input.min = question.min;
  input.max = question.max;
  if (question.step) input.step = question.step;
  if (answer !== undefined && answer !== null) input.value = answer;
  input.placeholder = question.placeholder || "Enter a number";
  input.required = true;
  return input;
}

function textInput(question, answer) {
  const wrap = element("div", "answer-fieldset");
  const input = document.createElement("textarea");
  input.className = "text-input";
  input.name = "answer";
  input.maxLength = question.max_length;
  input.placeholder = question.placeholder || "Type your response";
  input.required = true;
  input.value = answer || "";
  const count = element("span", "character-count", `${input.value.length}/${question.max_length}`);
  input.addEventListener("input", () => { count.textContent = `${input.value.length}/${question.max_length}`; });
  wrap.append(input, count, element("p", "selection-help", "The presenter approves text before it appears publicly."));
  return wrap;
}

function rankingInput(question, answer) {
  const order = Array.isArray(answer) && answer.length ? [...answer] : question.options.map((option) => option.id);
  const labels = new Map(question.options.map((option) => [option.id, option.label]));
  const list = element("div", "ranking-list");
  list.dataset.ranking = "";
  function paint() {
    list.replaceChildren();
    order.forEach((id, index) => {
      const row = element("div", "ranking-item");
      row.dataset.optionId = id;
      const up = element("button", "rank-button", "↑");
      up.type = "button";
      up.disabled = index === 0;
      up.setAttribute("aria-label", `Move ${labels.get(id)} up`);
      up.addEventListener("click", () => { [order[index - 1], order[index]] = [order[index], order[index - 1]]; paint(); });
      const down = element("button", "rank-button", "↓");
      down.type = "button";
      down.disabled = index === order.length - 1;
      down.setAttribute("aria-label", `Move ${labels.get(id)} down`);
      down.addEventListener("click", () => { [order[index], order[index + 1]] = [order[index + 1], order[index]]; paint(); });
      row.append(element("span", "ranking-position", index + 1), element("strong", "", labels.get(id)), up, down);
      list.append(row);
    });
  }
  paint();
  return list;
}

function readAnswer(form, question) {
  if (question.type === "multiple_choice") {
    return [...form.querySelectorAll('input[name="answer"]:checked')].map((input) => input.value);
  }
  if (["single_choice", "yes_no"].includes(question.type)) {
    return form.querySelector('input[name="answer"]:checked')?.value;
  }
  if (question.type === "rating") {
    const value = form.querySelector('input[name="answer"]:checked')?.value;
    return value === undefined ? undefined : Number(value);
  }
  if (["slider", "number"].includes(question.type)) {
    const value = form.elements.answer.value;
    return value === "" ? undefined : Number(value);
  }
  if (question.type === "ranking") {
    return [...form.querySelectorAll("[data-option-id]")].map((node) => node.dataset.optionId);
  }
  return form.elements.answer.value.trim();
}

function shouldShowResults(question, session) {
  if (!session.showResultsToAttendees || !submitted) return false;
  const visibility = question.results?.attendee_visibility || "live";
  if (visibility === "never") return false;
  if (visibility === "live") return true;
  if (visibility === "after_close") return ["results", "revealed"].includes(session.phase);
  return session.phase === "revealed";
}

function renderQuestion(question, session) {
  document.body.classList.remove("is-shared-slide", "is-slide-expanded");
  const shell = element("div", "question-shell");
  const resultsVisible = shouldShowResults(question, session);
  shell.dataset.questionType = question.type;
  if (resultsVisible) shell.classList.add("has-results");
  const kicker = element("div", "question-kicker");
  kicker.append(element("span", "", question.type.replaceAll("_", " ")), element("span", "", session.phase === "open" ? "Open now" : "Voting closed"));
  shell.append(kicker, element("h2", "", question.prompt));
  if (question.description) shell.append(element("p", "question-description", question.description));

  if (session.phase === "open") {
    const form = element("form", "answer-form");
    let field;
    if (["single_choice", "multiple_choice", "yes_no"].includes(question.type)) field = optionInputs(question, existingAnswer);
    else if (question.type === "rating") field = ratingInputs(question, existingAnswer);
    else if (question.type === "slider") field = rangeInput(question, existingAnswer);
    else if (question.type === "number") field = numberInput(question, existingAnswer);
    else if (question.type === "ranking") field = rankingInput(question, existingAnswer);
    else if (["free_text", "word_cloud"].includes(question.type)) {
      field = textInput(question, existingAnswer);
    } else {
      throw new Error(`Unsupported question type: ${question.type}`);
    }
    form.append(field);
    if (question.type === "multiple_choice") {
      form.append(element("p", "selection-help", `Choose up to ${question.max_selections}.`));
    }
    const submit = element("button", "primary-button", submitted ? "Update answer" : "Submit answer");
    submit.type = "submit";
    const status = element("p", "submission-status", submitted ? "Answer received. You may update it while voting is open." : "");
    form.append(submit, status);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const answer = readAnswer(form, question);
      if (answer === undefined || (Array.isArray(answer) && !answer.length) || answer === "") {
        status.textContent = "Please provide an answer first.";
        status.style.color = "var(--danger)";
        return;
      }
      submit.disabled = true;
      status.textContent = "Sending…";
      try {
        await submitAnswer(answer);
        existingAnswer = answer;
        submitted = true;
        status.textContent = "Answer received. You may update it while voting is open.";
        status.style.color = "";
        submit.textContent = "Update answer";
      } catch (error) {
        status.textContent = error.message || "Could not submit the answer.";
        status.style.color = "var(--danger)";
      } finally {
        submit.disabled = false;
      }
    });
    shell.append(form);
  } else {
    shell.append(element("p", "submission-status", submitted ? "Your answer was recorded." : "Voting is closed."));
  }

  if (resultsVisible) {
    const results = element("section", "results-section");
    const heading = element("div", "results-heading");
    heading.append(element("h3", "", session.phase === "revealed" ? "Answer & results" : "Live results"), element("span", "", `${currentAggregate?.responseCount || 0} responses`));
    const content = element("div");
    renderResults(content, question, currentAggregate);
    results.append(heading, content);
    shell.append(results);
  }
  liveContent.replaceChildren(shell);
}

function render() {
  if (!currentSession || !joinedCode) return;
  showLive();
  if (!currentQuestion || !["open", "results", "revealed"].includes(currentSession.phase)) {
    renderWaiting(currentSession.phase, currentSession);
  } else {
    renderQuestion(currentQuestion, currentSession);
  }
}

async function connectPreview() {
  const uidKey = "confquiz-preview-uid";
  let uid = localStorage.getItem(uidKey);
  if (!uid) {
    uid = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
    localStorage.setItem(uidKey, uid);
  }
  previewSocket = new WebSocket(`${runtime.previewWebsocketUrl}?uid=${encodeURIComponent(uid)}`);
  previewSocket.addEventListener("open", () => setConnection("Checking room", "offline"));
  previewSocket.addEventListener("close", () => {
    setRoomAvailability(false);
    setConnection("Disconnected", "offline");
  });
  previewSocket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "error") {
      showJoinError(message.message);
      return;
    }
    previewState = message;
    setTheme(message.presentation?.theme);
    setRoomAvailability(message.session?.status === "running");
    if (joinedCode) {
      currentSession = message.session;
      currentQuestion = message.question;
      currentAggregate = message.aggregate;
      if (message.existingAnswer !== undefined && message.existingAnswer !== null) {
        existingAnswer = message.existingAnswer;
        submitted = true;
      }
      render();
    }
  });
  submitAnswer = async (answer) => {
    previewSocket.send(JSON.stringify({ action: "submit", answer }));
  };
}

async function ensureFirebaseUser(auth) {
  await setPersistence(auth, browserLocalPersistence);
  if (auth.currentUser) return auth.currentUser;
  await signInAnonymously(auth);
  return new Promise((resolve, reject) => {
    const stop = onAuthStateChanged(auth, (user) => {
      if (user) { stop(); resolve(user); }
    }, reject);
  });
}

async function connectFirebase() {
  setConnection("Checking room", "offline");
  const app = initializeApp(runtime.firebase);
  if (runtime.appCheckSiteKey) {
    initializeAppCheck(app, {
      provider: new ReCaptchaEnterpriseProvider(runtime.appCheckSiteKey),
      isTokenAutoRefreshEnabled: true,
    });
  }
  database = getFirestore(app);
  firebaseAuth = getAuth(app);
  const availabilityRef = doc(
    database,
    `${runtime.namespace}_presentations`,
    runtime.presentation.id,
  );
  unsubscribeAvailability = onSnapshot(availabilityRef, (snapshot) => {
    const data = snapshot.exists() ? snapshot.data() : null;
    applyFirebaseAvailability(data);
  }, () => {
    setRoomAvailability(false);
    setConnection("Presentation", "offline");
  });
  submitAnswer = async (answer) => {
    if (!currentSessionId || !currentQuestion || !firebaseUser) throw new Error("Not connected to a room");
    const responseRef = doc(
      database,
      `${runtime.namespace}_sessions`, currentSessionId,
      "questions", currentQuestion.id,
      "responses", firebaseUser.uid,
    );
    await setDoc(responseRef, { answer, submittedAt: serverTimestamp() });
  };
}

function stopFirebaseListeners() {
  for (const stop of [unsubscribeSession, unsubscribeQuestion, unsubscribeAggregate]) if (stop) stop();
  unsubscribeSession = unsubscribeQuestion = unsubscribeAggregate = null;
}

async function loadFirebaseQuestion(questionId) {
  if (unsubscribeQuestion) unsubscribeQuestion();
  if (unsubscribeAggregate) unsubscribeAggregate();
  currentQuestion = null;
  currentAggregate = null;
  existingAnswer = undefined;
  submitted = false;
  if (!questionId) { render(); return; }
  const questionRef = doc(database, `${runtime.namespace}_sessions`, currentSessionId, "questions", questionId);
  const aggregateRef = doc(database, `${runtime.namespace}_sessions`, currentSessionId, "aggregates", questionId);
  const responseRef = doc(database, `${runtime.namespace}_sessions`, currentSessionId, "questions", questionId, "responses", firebaseUser.uid);
  const ownResponse = await getDoc(responseRef);
  if (ownResponse.exists()) {
    existingAnswer = ownResponse.data().answer;
    submitted = true;
  }
  unsubscribeQuestion = onSnapshot(questionRef, (snapshot) => {
    currentQuestion = snapshot.exists() ? { id: snapshot.id, ...snapshot.data() } : null;
    render();
  }, (error) => setConnection(error.code || "Question error", "offline"));
  unsubscribeAggregate = onSnapshot(aggregateRef, (snapshot) => {
    currentAggregate = snapshot.exists() ? snapshot.data() : null;
    render();
  }, (error) => setConnection(error.code || "Results error", "offline"));
}

async function joinFirebase(code) {
  setConnection("Joining", "offline");
  firebaseUser = await ensureFirebaseUser(firebaseAuth);
  const codeRef = doc(database, `${runtime.namespace}_join_codes`, code);
  const mapping = await getDoc(codeRef);
  if (!mapping.exists() || mapping.data().status !== "running" || !mapping.data().sessionId) {
    throw new Error("That room is not active. Check the code and try again.");
  }
  currentSessionId = mapping.data().sessionId;
  const participantRef = doc(database, `${runtime.namespace}_sessions`, currentSessionId, "participants", firebaseUser.uid);
  const participant = await getDoc(participantRef);
  const presence = participant.exists()
    ? { lastSeen: serverTimestamp() }
    : { joinedAt: serverTimestamp(), lastSeen: serverTimestamp() };
  await setDoc(participantRef, presence, { merge: true });
  let previousQuestionId = null;
  unsubscribeSession = onSnapshot(
    doc(database, `${runtime.namespace}_sessions`, currentSessionId),
    async (snapshot) => {
      if (!snapshot.exists()) {
        currentSession = { phase: "ended" };
        render();
        return;
      }
      currentSession = snapshot.data();
      const questionId = currentSession.activeQuestionId || null;
      if (questionId !== previousQuestionId) {
        previousQuestionId = questionId;
        await loadFirebaseQuestion(questionId);
      }
      setConnection(navigator.onLine ? "Live" : "Offline", navigator.onLine ? "online" : "offline");
      render();
    },
    (error) => setConnection(error.code || "Session error", "offline"),
  );
}

async function join(code) {
  code = cleanCode(code);
  if (code.length < 4) throw new Error("Enter the complete room code.");
  if (runtime.mode === "preview") {
    if (!previewState || previewState.joinCode !== code) throw new Error("That preview room is not active.");
    joinedCode = code;
    currentSession = previewState.session;
    currentQuestion = previewState.question;
    currentAggregate = previewState.aggregate;
    previewSocket.send(JSON.stringify({ action: "join" }));
  } else {
    joinedCode = code;
    await joinFirebase(code);
  }
  setConnection("Live", "online");
  history.replaceState(null, "", `${location.pathname}?code=${encodeURIComponent(code)}`);
  render();
}

attendeeThemeToggle.addEventListener("click", () => {
  setAttendeeTheme(document.body.dataset.attendeeTheme === "dark" ? "light" : "dark", true);
});

joinInput.addEventListener("input", () => { joinInput.value = cleanCode(joinInput.value); showJoinError(""); });
homeSlideFullscreenButton.addEventListener("click", () => {
  toggleHomeSlideFullscreen().catch(() => { /* fullscreen is optional on unsupported devices */ });
});
joinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = joinForm.querySelector("button");
  button.disabled = true;
  showJoinError("");
  try { await join(joinInput.value); }
  catch (error) { showJoinError(error.message || "Could not join the room."); }
  finally { button.disabled = false; }
});

leaveButton.addEventListener("click", () => {
  if (document.fullscreenElement === sharedSlideShell) document.exitFullscreen().catch(() => {});
  document.body.classList.remove("is-slide-expanded");
  joinedCode = "";
  currentSessionId = "";
  currentSession = currentQuestion = currentAggregate = null;
  existingAnswer = undefined;
  submitted = false;
  sharedSlideRequest += 1;
  sharedSlidePage = null;
  sharedSlideShell = null;
  stopFirebaseListeners();
  history.replaceState(null, "", location.pathname);
  showJoin();
  joinInput.focus();
});

window.addEventListener("online", () => {
  setConnection(joinedCode ? "Live" : roomAvailable ? "Room open" : "Presentation", joinedCode || roomAvailable ? "online" : "offline");
});
window.addEventListener("offline", () => setConnection("Offline", "offline"));
window.addEventListener("resize", () => {
  clearTimeout(homeSlideResizeTimer);
  homeSlideResizeTimer = setTimeout(repaintHomeSlide, 150);
  clearTimeout(sharedSlideResizeTimer);
  sharedSlideResizeTimer = setTimeout(repaintSharedSlide, 150);
});
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement) {
    document.body.classList.remove("is-home-slide-expanded", "is-slide-expanded");
  }
  updateHomeSlideFullscreenButton();
  updateSharedSlideFullscreenButton();
  requestAnimationFrame(repaintHomeSlide);
  requestAnimationFrame(repaintSharedSlide);
});

async function boot() {
  if (!runtime) throw new Error("Conf Quiz runtime configuration is missing");
  loadAttendeeTheme();
  setTheme(runtime.presentation?.theme);
  updateHomeSlideFullscreenButton();
  repaintHomeSlide();
  if (runtime.mode === "preview") await connectPreview();
  else await connectFirebase();
  const code = cleanCode(new URLSearchParams(location.search).get("code") || "");
  if (code) {
    setRoomAvailability(true);
    joinInput.value = code;
    const waitForPreview = runtime.mode === "preview"
      ? new Promise((resolve) => {
          const timer = setInterval(() => { if (previewState) { clearInterval(timer); resolve(); } }, 20);
          setTimeout(() => { clearInterval(timer); resolve(); }, 2000);
        })
      : Promise.resolve();
    await waitForPreview;
    try { await join(code); }
    catch (error) { showJoinError(error.message); }
  }
}

boot().catch((error) => {
  setConnection("Setup error", "offline");
  showJoinError(error.message || "Could not start the quiz application.");
});
