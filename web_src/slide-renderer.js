const CANCELLED_RENDER = "RenderingCancelledException";

export class SlideRenderer {
  constructor({
    stage,
    loadDocument,
    createElement = (tagName) => document.createElement(tagName),
    pixelRatio = () => window.devicePixelRatio || 1,
    cacheRadius = 1,
  }) {
    this.stage = stage;
    this.loadDocument = loadDocument;
    this.createElement = createElement;
    this.pixelRatio = pixelRatio;
    this.cacheRadius = cacheRadius;
    this.cache = new Map();
    this.documentPromise = null;
    this.layoutKey = null;
    this.generation = 0;
    this.desiredPage = null;
    this.visibleEntry = null;
  }

  get cacheSize() {
    return this.cache.size;
  }

  async show(pageNumber, pageCount) {
    const generation = ++this.generation;
    this.desiredPage = pageNumber;
    const layout = this.#layout();
    if (layout.key !== this.layoutKey) {
      this.#clearCache();
      this.layoutKey = layout.key;
    }
    this.#trimCache(pageNumber, pageCount);

    const cached = this.cache.get(pageNumber);
    if (cached?.ready) {
      this.#display(cached, generation);
      this.#prefetch(pageNumber, pageCount, layout);
      return { cached: true, stale: false };
    }

    this.#setBusy(true);
    if (!this.stage.querySelector("canvas")) {
      const loading = this.createElement("div");
      loading.className = "stage-loading";
      loading.textContent = "Loading slide…";
      this.stage.replaceChildren(loading);
    }

    const entry = this.#ensure(pageNumber, layout);
    try {
      await entry.promise;
    } catch (error) {
      if (generation === this.generation) this.#setBusy(false);
      if (error?.name === CANCELLED_RENDER) return { cached: false, stale: true };
      throw error;
    }

    if (generation !== this.generation || this.desiredPage !== pageNumber) {
      return { cached: false, stale: true };
    }
    this.#display(entry, generation);
    this.#prefetch(pageNumber, pageCount, layout);
    return { cached: false, stale: false };
  }

  deactivate() {
    this.generation += 1;
    this.desiredPage = null;
    this.visibleEntry = null;
    this.#setBusy(false);
  }

  invalidate() {
    this.deactivate();
    this.#clearCache();
    this.layoutKey = null;
  }

  #layout() {
    const width = Math.max(100, this.stage.clientWidth - 2);
    const height = Math.max(100, this.stage.clientHeight - 2);
    const ratio = Math.min(2, this.pixelRatio());
    return { width, height, ratio, key: `${width}x${height}@${ratio}` };
  }

  #document() {
    if (!this.documentPromise) {
      this.documentPromise = Promise.resolve(this.loadDocument()).catch((error) => {
        this.documentPromise = null;
        throw error;
      });
    }
    return this.documentPromise;
  }

  #ensure(pageNumber, layout) {
    const cached = this.cache.get(pageNumber);
    if (cached) return cached;
    const entry = {
      pageNumber,
      ready: false,
      renderTask: null,
      shell: null,
      promise: null,
    };
    entry.promise = this.#render(entry, layout).catch((error) => {
      if (this.cache.get(pageNumber) === entry) this.cache.delete(pageNumber);
      throw error;
    });
    this.cache.set(pageNumber, entry);
    return entry;
  }

  async #render(entry, layout) {
    const pdf = await this.#document();
    const page = await pdf.getPage(entry.pageNumber);
    const base = page.getViewport({ scale: 1 });
    const scale = Math.min(layout.width / base.width, layout.height / base.height);
    const viewport = page.getViewport({ scale });
    const shell = this.createElement("div");
    shell.className = "slide-stage";
    shell.dataset.slide = String(entry.pageNumber);
    const canvas = this.createElement("canvas");
    canvas.width = Math.floor(viewport.width * layout.ratio);
    canvas.height = Math.floor(viewport.height * layout.ratio);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    shell.append(canvas);
    const context = canvas.getContext("2d", { alpha: false });
    entry.renderTask = page.render({
      canvasContext: context,
      viewport,
      transform: layout.ratio === 1 ? null : [layout.ratio, 0, 0, layout.ratio, 0, 0],
    });
    await entry.renderTask.promise;
    entry.shell = shell;
    entry.ready = true;
    return entry;
  }

  #display(entry, generation) {
    if (generation !== this.generation || this.desiredPage !== entry.pageNumber) return;
    if (this.visibleEntry !== entry) this.stage.replaceChildren(entry.shell);
    this.visibleEntry = entry;
    this.#setBusy(false);
  }

  #prefetch(pageNumber, pageCount, layout) {
    for (let distance = 1; distance <= this.cacheRadius; distance += 1) {
      for (const adjacent of [pageNumber - distance, pageNumber + distance]) {
        if (adjacent < 1 || adjacent > pageCount || this.cache.has(adjacent)) continue;
        const entry = this.#ensure(adjacent, layout);
        entry.promise.catch(() => { /* A foreground render will retry and report the error. */ });
      }
    }
    this.#trimCache(pageNumber, pageCount);
  }

  #trimCache(pageNumber, pageCount) {
    const keep = new Set([pageNumber]);
    for (let distance = 1; distance <= this.cacheRadius; distance += 1) {
      if (pageNumber - distance >= 1) keep.add(pageNumber - distance);
      if (pageNumber + distance <= pageCount) keep.add(pageNumber + distance);
    }
    for (const [cachedPage, entry] of this.cache) {
      if (keep.has(cachedPage)) continue;
      try { entry.renderTask?.cancel(); } catch (_) { /* already complete */ }
      this.cache.delete(cachedPage);
      if (this.visibleEntry === entry) this.visibleEntry = null;
    }
  }

  #clearCache() {
    for (const entry of this.cache.values()) {
      try { entry.renderTask?.cancel(); } catch (_) { /* already complete */ }
    }
    this.cache.clear();
    this.visibleEntry = null;
  }

  #setBusy(busy) {
    this.stage.classList.toggle("is-rendering", busy);
    if (busy) this.stage.setAttribute("aria-busy", "true");
    else this.stage.removeAttribute("aria-busy");
  }
}
