import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

async function compile(path, imports = {}) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  let { outputText } = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } });
  outputText = outputText.replace('import "./communications.css";', "");
  for (const [name, url] of Object.entries(imports)) outputText = outputText.replaceAll(
    '"' + name + '"', JSON.stringify(url));
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const logic = await compile("../src/ui/communications.ts");
const { updateResponseSamples, responseMedian } = await import(logic);
const captions = await compile("../src/ui/captions.ts");
const { updateCaptions } = await import(captions);
const { CommunicationsPanel } = await import(await compile("../src/ui/CommunicationsPanel.tsx", {
  "./communications": logic, "./captions": captions,
  "react": import.meta.resolve("react"), "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
}));
const caption = (id, seconds, extra = {}) => ({
  type: "caption", tx_id: id, speaker: "BAW117", stage: "pilot_text", text: "Left heading 240",
  chips: [], words: [], plain_english: "", latency: { perceived_s: seconds }, ...extra,
});

test("response p50 uses the latest ten measured pilot transmissions and updates in place", () => {
  let samples = [];
  for (let i = 0; i < 12; i++) samples = updateResponseSamples(samples, caption(String(i), i));
  assert.equal(samples.length, 10);
  assert.equal(samples[0].seconds, 2);
  assert.equal(responseMedian(samples), 6.5);
  const snapshot = JSON.stringify(samples);
  const revised = updateResponseSamples(samples, caption("11", 1, { stage: "pilot_words" }));
  assert.equal(revised.length, 10);
  assert.equal(responseMedian(revised), 5.5);
  assert.equal(JSON.stringify(samples), snapshot);
  assert.equal(responseMedian([]), null);
  assert.equal(responseMedian([samples[0]]), 2);
});
test("invalid metrics and unmeasured word updates never count as responses", () => {
  const samples = updateResponseSamples([], caption("first", 0));
  for (const msg of [caption("ctrl", 4, { speaker: "CTRL" }),
    caption("system", 4, { speaker: "SYSTEM" }), caption("partial", 4, { stage: "partial" }),
    caption("negative", -1), caption("infinity", Infinity), caption("nan", NaN),
    caption("first", undefined, { stage: "pilot_words", latency: {} })]) {
    assert.strictEqual(updateResponseSamples(samples, msg), samples);
  }
  assert.equal(responseMedian(samples), 0);
});
test("history contains more than the bottom strip and preserves supplied explanations safely", () => {
  let messages = [];
  for (let i = 0; i < 40; i++) messages = updateCaptions(messages, caption(String(i), undefined, {
    text: `Instruction ${i}`, latency: {}, plain_english: i === 0 ? "Turn left" : "",
  }));
  messages = updateCaptions(messages, caption("39", 2, {
    stage: "pilot_words", text: "", chips: [{ label: "HDG 240" }], error_reveal: "<script>error</script>",
  }));
  const html = renderToStaticMarkup(createElement(CommunicationsPanel, {
    messages, samples: [{ tx_id: "39", speaker: "BAW117", seconds: 2 }],
  }));
  for (const text of ["Instruction 0", "Instruction 39", "Turn left", "HDG 240", "2.00 s",
    "1 / 10", "40 / 200", "server timing", "&lt;script&gt;"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("<script>"));
});
test("an empty communications panel reports missing measurements rather than zero latency", () => {
  const html = renderToStaticMarkup(createElement(CommunicationsPanel, { messages: [], samples: [] }));
  assert.ok(html.includes("No radio traffic yet"));
  assert.ok(html.includes("—"));
  assert.ok(html.includes("0 / 10"));
  assert.ok(!html.includes("0.00 s"));
});
