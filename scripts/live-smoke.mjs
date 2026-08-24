import { readFile } from "node:fs/promises";

import { deleteApp, initializeApp } from "firebase/app";
import { getAuth, signInAnonymously } from "firebase/auth";
import {
  doc,
  getDoc,
  getFirestore,
  onSnapshot,
  serverTimestamp,
  setDoc,
} from "firebase/firestore";

const [webConfigPath, presenterWebsocketUrl] = process.argv.slice(2);
if (!webConfigPath || !presenterWebsocketUrl) {
  throw new Error("Usage: node scripts/live-smoke.mjs FIREBASE_WEB_JSON PRESENTER_WEBSOCKET_URL");
}

function waitForState(socket, predicate, timeoutMs = 20_000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      socket.removeEventListener("message", onMessage);
      reject(new Error("Timed out waiting for presenter state"));
    }, timeoutMs);
    function onMessage(event) {
      const payload = JSON.parse(String(event.data));
      if (payload.type === "error") {
        clearTimeout(timer);
        socket.removeEventListener("message", onMessage);
        reject(new Error(payload.message));
      } else if (payload.type === "state" && predicate(payload)) {
        clearTimeout(timer);
        socket.removeEventListener("message", onMessage);
        resolve(payload);
      }
    }
    socket.addEventListener("message", onMessage);
  });
}

async function sendAndWait(socket, action, predicate) {
  const state = waitForState(socket, predicate);
  socket.send(JSON.stringify({ action }));
  return state;
}

function waitForDocument(reference, predicate, timeoutMs = 20_000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      unsubscribe();
      reject(new Error(`Timed out waiting for ${reference.path}`));
    }, timeoutMs);
    const unsubscribe = onSnapshot(
      reference,
      (snapshot) => {
        if (predicate(snapshot)) {
          clearTimeout(timer);
          unsubscribe();
          resolve(snapshot);
        }
      },
      (error) => {
        clearTimeout(timer);
        unsubscribe();
        reject(error);
      },
    );
  });
}

const firebaseConfig = JSON.parse(await readFile(webConfigPath, "utf8"));
const app = initializeApp(firebaseConfig, `confquiz-live-smoke-${Date.now()}`);
const database = getFirestore(app);
const auth = getAuth(app);
const socket = new WebSocket(presenterWebsocketUrl);

try {
  const initialLobby = waitForState(socket, (message) => message.session.phase === "lobby");
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("Presenter WebSocket failed")), {
      once: true,
    });
  });
  const lobby = await initialLobby;
  const firstCode = lobby.session.joinCode;
  const firstSessionId = lobby.session.id;
  if (!firstCode || !firstSessionId) throw new Error("Presenter did not create a Firebase room");

  const credential = await signInAnonymously(auth);
  const uid = credential.user.uid;
  const mappingRef = doc(database, "confquiz_join_codes", firstCode);
  const mapping = await getDoc(mappingRef);
  if (!mapping.exists() || mapping.data().sessionId !== firstSessionId) {
    throw new Error("Join code did not resolve to the presenter session");
  }

  const participantRef = doc(
    database,
    "confquiz_sessions",
    firstSessionId,
    "participants",
    uid,
  );
  await setDoc(participantRef, {
    joinedAt: serverTimestamp(),
    lastSeen: serverTimestamp(),
  });

  await sendAndWait(socket, "next", (message) => message.session.activeSlide === 1);
  const open = await sendAndWait(
    socket,
    "next",
    (message) => message.session.phase === "open" && Boolean(message.session.activeQuestion),
  );
  const question = open.session.activeQuestion;
  const answer = question.options?.[0]?.id;
  if (!answer) throw new Error("First live question is not a choice question");

  const responseRef = doc(
    database,
    "confquiz_sessions",
    firstSessionId,
    "questions",
    question.id,
    "responses",
    uid,
  );
  const aggregateRef = doc(
    database,
    "confquiz_sessions",
    firstSessionId,
    "aggregates",
    question.id,
  );
  const aggregated = waitForDocument(
    aggregateRef,
    (snapshot) => snapshot.exists() && snapshot.data().responseCount === 1,
  );
  const presenterCount = waitForState(socket, (message) => message.session.responseCount === 1);
  await setDoc(responseRef, { answer, submittedAt: serverTimestamp() });
  await aggregated;
  await presenterCount;

  await sendAndWait(socket, "close_question", (message) => message.session.phase === "results");
  const revealedAggregate = waitForDocument(
    aggregateRef,
    (snapshot) => snapshot.exists() && snapshot.data().correct === answer,
  );
  await sendAndWait(socket, "reveal", (message) => message.session.phase === "revealed");
  await revealedAggregate;

  const clearedAggregate = waitForDocument(
    aggregateRef,
    (snapshot) => snapshot.exists() && snapshot.data().responseCount === 0,
  );
  await sendAndWait(
    socket,
    "reset_question",
    (message) => message.session.phase === "open" && message.session.responseCount === 0,
  );
  await clearedAggregate;
  if ((await getDoc(responseRef)).exists()) throw new Error("Question reset left a response behind");

  const newLobby = await sendAndWait(
    socket,
    "new_session",
    (message) => message.session.phase === "lobby" && message.session.joinCode !== firstCode,
  );
  const oldMapping = await getDoc(mappingRef);
  if (oldMapping.data()?.status !== "ended" || oldMapping.data()?.sessionId) {
    throw new Error("Old join code was not invalidated");
  }
  const newMapping = await getDoc(
    doc(database, "confquiz_join_codes", newLobby.session.joinCode),
  );
  if (!newMapping.exists() || newMapping.data().sessionId !== newLobby.session.id) {
    throw new Error("New join code was not activated");
  }

  await sendAndWait(socket, "end_session", (message) => message.session.phase === "ended");
  process.stdout.write(
    `Live Firebase smoke passed: anonymous auth, room ${firstCode}, realtime answer, `
      + `aggregate, reveal, reset, new room ${newLobby.session.joinCode}, end.\n`,
  );
} finally {
  socket.close();
  await deleteApp(app);
}
