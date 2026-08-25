import assert from "node:assert/strict";
import test from "node:test";

import { SlideRenderer } from "../web_src/slide-renderer.js";

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    if (force) this.values.add(name);
    else this.values.delete(name);
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.className = "";
    this.textContent = "";
    this.clientWidth = 1256;
    this.clientHeight = 620;
    this.replacements = 0;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
    this.replacements += 1;
  }

  querySelector(tagName) {
    for (const child of this.children) {
      if (child.tagName === tagName || child.querySelector?.(tagName)) return child;
    }
    return null;
  }

  getContext() {
    return {};
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function fakePdf(renderPromises = new Map()) {
  const getPageCalls = new Map();
  const renderCalls = new Map();
  return {
    getPageCalls,
    renderCalls,
    async getPage(pageNumber) {
      getPageCalls.set(pageNumber, (getPageCalls.get(pageNumber) || 0) + 1);
      return {
        getViewport({ scale }) {
          return { width: 720 * scale, height: 405 * scale };
        },
        render() {
          renderCalls.set(pageNumber, (renderCalls.get(pageNumber) || 0) + 1);
          return {
            promise: renderPromises.get(pageNumber)?.promise || Promise.resolve(),
            cancel() {
              const pending = renderPromises.get(pageNumber);
              if (!pending) return;
              const error = new Error("cancelled");
              error.name = "RenderingCancelledException";
              pending.reject(error);
            },
          };
        },
      };
    },
  };
}

const flushPromises = () => new Promise((resolve) => setImmediate(resolve));

test("adjacent slides are pre-rendered, reused, and bounded", async () => {
  const stage = new FakeElement("div");
  const pdf = fakePdf();
  const renderer = new SlideRenderer({
    stage,
    loadDocument: () => pdf,
    createElement: (tagName) => new FakeElement(tagName),
    pixelRatio: () => 2,
  });

  await renderer.show(2, 4);
  await flushPromises();

  assert.equal(pdf.renderCalls.get(1), 1);
  assert.equal(pdf.renderCalls.get(2), 1);
  assert.equal(pdf.renderCalls.get(3), 1);
  assert.ok(renderer.cacheSize <= 3);

  const forward = await renderer.show(3, 4);
  const backward = await renderer.show(2, 4);

  assert.equal(forward.cached, true);
  assert.equal(backward.cached, true);
  assert.equal(pdf.getPageCalls.get(2), 1);
  assert.equal(pdf.getPageCalls.get(3), 1);
  assert.ok(renderer.cacheSize <= 3);
});

test("an obsolete PDF render cannot replace a newer slide", async () => {
  const stage = new FakeElement("div");
  const page2 = deferred();
  const page3 = deferred();
  const pdf = fakePdf(new Map([[2, page2], [3, page3]]));
  const renderer = new SlideRenderer({
    stage,
    loadDocument: () => pdf,
    createElement: (tagName) => new FakeElement(tagName),
    pixelRatio: () => 2,
  });

  const oldRender = renderer.show(2, 3);
  await flushPromises();
  const newRender = renderer.show(3, 3);
  await flushPromises();

  page3.resolve();
  await newRender;
  assert.equal(stage.children[0].dataset.slide, "3");

  page2.resolve();
  const obsolete = await oldRender;
  assert.equal(obsolete.stale, true);
  assert.equal(stage.children[0].dataset.slide, "3");
});
