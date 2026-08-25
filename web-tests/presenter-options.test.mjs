import assert from "node:assert/strict";
import test from "node:test";

import { presenterOptions } from "../web_src/presenter-question.js";
import { renderResults } from "../web_src/results.js";

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(name) {
    this.values.add(name);
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.classList = new FakeClassList();
    this.className = "";
    this.textContent = "";
    this.style = {};
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  querySelector() {
    return null;
  }
}

globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
};

function allText(node) {
  return [node.textContent, ...node.children.flatMap((child) => allText(child))].join(" ");
}

test("presenter options are available before anyone answers", () => {
  const question = {
    type: "multiple_choice",
    options: [
      { id: "speed", label: "Delivery speed" },
      { id: "quality", label: "Quality" },
    ],
  };

  assert.deepEqual(presenterOptions(question), [
    { id: "speed", label: "Delivery speed", marker: "A" },
    { id: "quality", label: "Quality", marker: "B" },
  ]);
  assert.deepEqual(presenterOptions({ type: "slider", options: question.options }), []);
});

test("zero-response choice results show every option", () => {
  const container = new FakeElement("div");
  const question = {
    type: "single_choice",
    options: [
      { id: "a", label: "First option" },
      { id: "b", label: "Second option" },
    ],
  };
  const aggregate = {
    responseCount: 0,
    suppressed: false,
    options: question.options.map((option) => ({ ...option, count: 0, percentage: 0 })),
  };

  renderResults(container, question, aggregate);

  const text = allText(container);
  assert.match(text, /First option/);
  assert.match(text, /Second option/);
  assert.match(text, /0 · 0%/);
  assert.doesNotMatch(text, /Waiting for the first response/);
});

test("suppressed results remain hidden from attendees", () => {
  const container = new FakeElement("div");

  renderResults(
    container,
    { type: "single_choice", options: [{ id: "a", label: "First option" }] },
    { responseCount: 0, suppressed: true },
  );

  assert.match(allText(container), /Results appear after more people respond/);
});
