import assert from "node:assert/strict";
import test from "node:test";

import { splitJoinUrl } from "../web_src/presenter-join.js";

test("presenter join URL separates its memorable address from parameters", () => {
  assert.deepEqual(splitJoinUrl("https://au.py3.in?code=TEST"), {
    base: "https://au.py3.in",
    parameters: "?code=TEST",
  });
  assert.deepEqual(splitJoinUrl("https://au.py3.in/confquiz/"), {
    base: "https://au.py3.in/confquiz/",
    parameters: "",
  });
});
