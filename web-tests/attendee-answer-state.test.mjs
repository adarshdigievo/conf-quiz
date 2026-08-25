import assert from "node:assert/strict";
import test from "node:test";

import { answerStateFromPreview } from "../web_src/attendee-state.js";

test("preview answer state resets when the next question is unanswered", () => {
  const answered = answerStateFromPreview({
    question: { id: "first" },
    existingAnswer: "a",
  });
  assert.deepEqual(answered, { existingAnswer: "a", submitted: true });

  const unanswered = answerStateFromPreview({
    question: { id: "second" },
    existingAnswer: null,
  });
  assert.deepEqual(unanswered, { existingAnswer: undefined, submitted: false });
});
