export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function numberLabel(value) {
  return Number.isInteger(value) ? String(value) : Number(value).toFixed(1).replace(/\.0$/, "");
}

function isCorrectOption(correct, optionId) {
  return Array.isArray(correct) ? correct.includes(optionId) : correct === optionId;
}

function renderBars(container, aggregate) {
  const bars = element("div", "result-bars");
  for (const option of aggregate.options || []) {
    const row = element("div", "result-bar-row");
    if (isCorrectOption(aggregate.correct, option.id)) row.classList.add("is-correct");
    const label = element("div", "result-bar-label");
    const name = element("span", "", option.label);
    if (row.classList.contains("is-correct")) name.append(element("small", "correct-badge", "Correct"));
    label.append(name, element("strong", "", `${option.count} · ${option.percentage}%`));
    const track = element("div", "result-bar-track");
    const fill = element("div", "result-bar-fill");
    fill.style.width = `${Math.max(0, Math.min(100, option.percentage))}%`;
    track.append(fill);
    row.append(label, track);
    bars.append(row);
  }
  container.append(bars);
}

function renderHistogram(container, aggregate) {
  const bins = aggregate.bins || [];
  if (!bins.length) return;
  const maxCount = Math.max(1, ...bins.map((bin) => bin.count));
  const chart = element("div", "histogram");
  chart.style.setProperty("--bins", String(bins.length));
  for (const bin of bins) {
    const item = element("div", "histogram-bin");
    const column = element("div", "histogram-column");
    column.style.setProperty("--height", `${Math.max(2, (bin.count / maxCount) * 100)}%`);
    column.append(element("strong", "", bin.count));
    item.append(column, element("small", "", `${numberLabel(bin.start)}–${numberLabel(bin.end)}`));
    chart.append(item);
  }
  container.append(chart);
  if (aggregate.summary) {
    const stats = element("div", "result-stats");
    for (const [label, value] of [
      ["Mean", aggregate.summary.mean],
      ["Median", aggregate.summary.median],
      ["Lowest", aggregate.summary.minimum],
      ["Highest", aggregate.summary.maximum],
    ]) {
      const card = element("div", "result-stat");
      card.append(element("span", "", label), element("strong", "", numberLabel(value)));
      stats.append(card);
    }
    container.append(stats);
  }
  if (aggregate.correct !== undefined) {
    const card = element("div", "result-stat");
    const correct = typeof aggregate.correct === "object" ? aggregate.correct.value : aggregate.correct;
    card.append(element("span", "", "Correct answer"), element("strong", "", numberLabel(correct)));
    const stats = container.querySelector(".result-stats") || element("div", "result-stats");
    stats.append(card);
    if (!stats.parentNode) container.append(stats);
  }
}

function renderRanking(container, aggregate) {
  const list = element("div", "ranking-results");
  for (const item of aggregate.ranking || []) {
    const row = element("div", "ranking-result");
    row.append(
      element("strong", "", item.label),
      element("span", "", item.averageRank === null ? "No votes" : `Average ${item.averageRank}`),
    );
    list.append(row);
  }
  container.append(list);
}

function renderTexts(container, aggregate) {
  const grid = element("div", "text-results");
  for (const text of aggregate.texts || []) grid.append(element("blockquote", "text-result-card", text));
  container.append(grid);
}

function renderWordCloud(container, aggregate) {
  const words = aggregate.words || [];
  const cloud = element("div", "word-cloud");
  const max = Math.max(1, ...words.map((word) => word.count));
  for (const word of words) {
    const node = element("span", "", word.text);
    node.style.setProperty("--weight", String(word.count / max));
    node.title = `${word.count} mention${word.count === 1 ? "" : "s"}`;
    cloud.append(node);
  }
  container.append(cloud);
}

export function renderResults(container, question, aggregate) {
  container.replaceChildren();
  container.classList.add("result-shell");
  if (!aggregate || aggregate.suppressed) {
    container.append(
      element(
        "div",
        "result-empty",
        aggregate?.suppressed ? "Results appear after more people respond." : "Waiting for the first response…",
      ),
    );
    return;
  }
  const optionResultsCanStartEmpty = [
    "single_choice",
    "multiple_choice",
    "yes_no",
    "ranking",
  ].includes(question.type);
  if (!aggregate.responseCount && !optionResultsCanStartEmpty) {
    container.append(element("div", "result-empty", "Waiting for the first response…"));
    return;
  }
  if (["single_choice", "multiple_choice", "yes_no"].includes(question.type)) {
    renderBars(container, aggregate);
  } else if (["slider", "rating", "number"].includes(question.type)) {
    renderHistogram(container, aggregate);
  } else if (question.type === "ranking") {
    renderRanking(container, aggregate);
  } else if (question.type === "free_text") {
    renderTexts(container, aggregate);
  } else if (question.type === "word_cloud") {
    renderWordCloud(container, aggregate);
  }
}
