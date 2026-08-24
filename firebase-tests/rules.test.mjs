import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { after, before, beforeEach, test } from "node:test";

import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  serverTimestamp,
  setDoc,
  Timestamp,
  updateDoc,
} from "firebase/firestore";

const projectId = "demo-confquiz";
const sessionId = "session-1";
const questionId = "single";
let environment;

function sessionRef(database) {
  return doc(database, "confquiz_sessions", sessionId);
}

function questionRef(database, id = questionId) {
  return doc(database, "confquiz_sessions", sessionId, "questions", id);
}

function responseRef(database, uid, id = questionId) {
  return doc(
    database,
    "confquiz_sessions",
    sessionId,
    "questions",
    id,
    "responses",
    uid,
  );
}

before(async () => {
  environment = await initializeTestEnvironment({
    projectId,
    firestore: { rules: await readFile("firestore.rules", "utf8") },
  });
});

beforeEach(async () => {
  await environment.clearFirestore();
  await environment.withSecurityRulesDisabled(async (context) => {
    const database = context.firestore();
    const expiresAt = Timestamp.fromMillis(Date.now() + 3_600_000);
    await setDoc(doc(database, "confquiz_join_codes", "ABC234"), {
      sessionId,
      status: "running",
      expiresAt,
    });
    await setDoc(doc(database, "confquiz_presentations", "demo-talk"), {
      presentationTitle: "Demo talk",
      status: "running",
      expiresAt,
      onlineUntil: expiresAt,
    });
    await setDoc(sessionRef(database), {
      status: "running",
      phase: "open",
      activeQuestionId: questionId,
      allowAnswerChanges: true,
      expiresAt,
    });
    await setDoc(questionRef(database), {
      type: "single_choice",
      optionIds: ["a", "b"],
    });
    await setDoc(questionRef(database, "slider"), {
      type: "slider",
      min: 0,
      max: 10,
      step: 2,
    });
    await setDoc(questionRef(database, "multiple"), {
      type: "multiple_choice",
      optionIds: ["a", "b", "c"],
      max_selections: 2,
    });
    await setDoc(questionRef(database, "ranking"), {
      type: "ranking",
      optionIds: ["a", "b", "c"],
    });
    await setDoc(doc(database, "confquiz_sessions", sessionId, "aggregates", questionId), {
      responseCount: 0,
    });
  });
});

after(async () => {
  await environment.cleanup();
});

test("join-code lookup requires authentication and never permits listing", async () => {
  const anonymous = environment.unauthenticatedContext().firestore();
  const attendee = environment.authenticatedContext("attendee-1").firestore();
  await assertFails(getDoc(doc(anonymous, "confquiz_join_codes", "ABC234")));
  const mapping = await assertSucceeds(
    getDoc(doc(attendee, "confquiz_join_codes", "ABC234")),
  );
  assert.equal(mapping.data().sessionId, sessionId);
  await assertFails(getDocs(collection(attendee, "confquiz_join_codes")));
});

test("presentation availability permits one public lookup but no listing or writes", async () => {
  const visitor = environment.unauthenticatedContext().firestore();
  const status = await assertSucceeds(
    getDoc(doc(visitor, "confquiz_presentations", "demo-talk")),
  );
  assert.equal(status.data().status, "running");
  await assertFails(getDocs(collection(visitor, "confquiz_presentations")));
  await assertFails(
    setDoc(doc(visitor, "confquiz_presentations", "demo-talk"), { status: "ended" }),
  );
});

test("an attendee can create and refresh only their own presence", async () => {
  const attendee = environment.authenticatedContext("attendee-1").firestore();
  const own = doc(attendee, "confquiz_sessions", sessionId, "participants", "attendee-1");
  const other = doc(attendee, "confquiz_sessions", sessionId, "participants", "attendee-2");

  await assertSucceeds(
    setDoc(own, { joinedAt: serverTimestamp(), lastSeen: serverTimestamp() }),
  );
  await assertSucceeds(setDoc(own, { lastSeen: serverTimestamp() }, { merge: true }));
  await assertFails(
    setDoc(other, { joinedAt: serverTimestamp(), lastSeen: serverTimestamp() }),
  );
  await assertFails(getDoc(other));
});

test("responses are private, typed, bounded, and limited to the open question", async () => {
  const attendee = environment.authenticatedContext("attendee-1").firestore();
  const other = environment.authenticatedContext("attendee-2").firestore();

  await assertSucceeds(
    setDoc(responseRef(attendee, "attendee-1"), {
      answer: "a",
      submittedAt: serverTimestamp(),
    }),
  );
  await assertSucceeds(getDoc(responseRef(attendee, "attendee-1")));
  await assertFails(getDoc(responseRef(other, "attendee-1")));
  await assertFails(
    setDoc(responseRef(other, "attendee-2"), {
      answer: "not-an-option",
      submittedAt: serverTimestamp(),
    }),
  );
  await assertFails(
    setDoc(responseRef(other, "attendee-2", "slider"), {
      answer: 11,
      submittedAt: serverTimestamp(),
    }),
  );

  await environment.withSecurityRulesDisabled(async (context) => {
    await updateDoc(sessionRef(context.firestore()), { phase: "results" });
  });
  await assertFails(
    setDoc(responseRef(other, "attendee-2"), {
      answer: "b",
      submittedAt: serverTimestamp(),
    }),
  );
});

test("answer updates obey allowAnswerChanges", async () => {
  const attendee = environment.authenticatedContext("attendee-1").firestore();
  const own = responseRef(attendee, "attendee-1");
  await assertSucceeds(
    setDoc(own, { answer: "a", submittedAt: serverTimestamp() }),
  );
  await assertSucceeds(
    setDoc(own, { answer: "b", submittedAt: serverTimestamp() }),
  );

  await environment.withSecurityRulesDisabled(async (context) => {
    await updateDoc(sessionRef(context.firestore()), { allowAnswerChanges: false });
  });
  await assertFails(
    setDoc(own, { answer: "a", submittedAt: serverTimestamp() }),
  );
});

test("steps and unique list selections are enforced", async () => {
  const attendee = environment.authenticatedContext("attendee-1").firestore();
  const admin = async (activeQuestionId) => environment.withSecurityRulesDisabled(
    async (context) => updateDoc(sessionRef(context.firestore()), { activeQuestionId }),
  );

  await admin("slider");
  await assertFails(
    setDoc(responseRef(attendee, "attendee-1", "slider"), {
      answer: 3,
      submittedAt: serverTimestamp(),
    }),
  );
  await assertSucceeds(
    setDoc(responseRef(attendee, "attendee-1", "slider"), {
      answer: 4,
      submittedAt: serverTimestamp(),
    }),
  );

  await admin("multiple");
  await assertFails(
    setDoc(responseRef(attendee, "attendee-1", "multiple"), {
      answer: ["a", "a"],
      submittedAt: serverTimestamp(),
    }),
  );

  await admin("ranking");
  await assertFails(
    setDoc(responseRef(attendee, "attendee-1", "ranking"), {
      answer: ["a", "a", "b"],
      submittedAt: serverTimestamp(),
    }),
  );
  await assertSucceeds(
    setDoc(responseRef(attendee, "attendee-1", "ranking"), {
      answer: ["b", "a", "c"],
      submittedAt: serverTimestamp(),
    }),
  );
});

test("public aggregates are readable but attendee writes and raw lists are denied", async () => {
  const attendee = environment.authenticatedContext("attendee-1").firestore();
  const aggregate = doc(
    attendee,
    "confquiz_sessions",
    sessionId,
    "aggregates",
    questionId,
  );
  await assertSucceeds(getDoc(aggregate));
  await assertFails(setDoc(aggregate, { responseCount: 99 }));
  await assertFails(
    getDocs(
      collection(
        attendee,
        "confquiz_sessions",
        sessionId,
        "questions",
        questionId,
        "responses",
      ),
    ),
  );
});
