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
  outputText = outputText.replace(/import "\.\/captions.css";/g, "");
  for (const [name, url] of Object.entries(imports)) {
    outputText = outputText.replaceAll('"' + name + '"', JSON.stringify(url));
  }
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const logicURL = await compile("../src/ui/captions.ts");
const { updateCaptions, latencyMetrics } = await import(logicURL);
const { CaptionStrip } = await import(await compile("../src/ui/CaptionStrip.tsx", {
  "./captions": logicURL, "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
}));
function caption(tx_id, speaker = "CTRL", changes = {}) {
  return { type: "caption", tx_id, speaker, stage: "final", text: tx_id,
    chips: [], words: [], plain_english: "", latency: {}, ...changes };
}
test("updates retain transmission order and keep controller and pilot distinct", () => {
  let messages = [caption("a", "CTRL", { stage: "partial" }), caption("b")];
  messages = updateCaptions(messages, caption("a", "CTRL", { text: "finished" }));
  assert.deepEqual(messages.map(m => m.tx_id), ["a", "b"]);
  assert.equal(messages[0].text, "finished");
  messages = updateCaptions(messages, caption("a", "BAW117"));
  assert.equal(messages.length, 3);
  assert.equal(updateCaptions(messages, caption("a", "CTRL", {
    stage: "partial", text: "late partial",
  }))[0].text, "finished");
});
test("word timing update preserves text and metadata, without mutating input", () => {
  const old = [caption("a", "BAW117", { text: "Left heading 240", latency: { parse_s: .1 } })];
  const snapshot = JSON.stringify(old);
  const updated = updateCaptions(old, caption("a", "BAW117", {
    text: "", stage: "pilot_words", words: [{ w: "Left", t0: 0, t1: .2 }],
  }));
  assert.equal(updated[0].text, "Left heading 240");
  assert.equal(updated[0].latency.parse_s, .1);
  assert.equal(JSON.stringify(old), snapshot);
});
test("latency metrics accept zero and omit invalid or unknown values", () => {
  assert.deepEqual(latencyMetrics({
    parse_s: 0, first_audio_s: NaN, perceived_s: -1, invented: 8,
  }).map(m => m.text), ["Parse 0.00 s"]);
  assert.deepEqual(latencyMetrics({ parse_s: Infinity }), []);
});
test("strip displays three entries, chips and supplied reveals using escaped text", () => {
  const messages = ["one", "two", "three", "four"].map(id => caption(id));
  messages[3].chips = [{ label: "ALT 4000" }];
  messages[3].error_reveal = "<script>planned error</script>";
  const html = renderToStaticMarkup(createElement(CaptionStrip, { messages }));
  assert.doesNotMatch(html, />one</);
  assert.match(html, /ALT 4000/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  const empty = renderToStaticMarkup(createElement(CaptionStrip, { messages: [] }));
  assert.match(empty, /Controller clearances and pilot readbacks will appear here/);
});
test("partial captions avoid live announcements and retention is bounded", () => {
  const html = renderToStaticMarkup(createElement(CaptionStrip, {
    messages: [caption("a", "CTRL", { stage: "partial" })],
  }));
  assert.match(html, /aria-live="off"/);
  let messages = [];
  for (let i = 0; i < 210; i++) messages = updateCaptions(messages, caption(String(i)));
  assert.equal(messages.length, 200);
  assert.equal(messages[0].tx_id, "10");
});
test("pilot word captions follow playback time and fall back to full text without a clock", () => {
  const messages = [caption("timed", "BAW117", {
    text: "Left heading two four zero", stage: "pilot_words",
    words: [{ w: "Left", t0: 0, t1: .2 }, { w: "heading", t0: .3, t1: .6 },
      { w: "two", t0: .7, t1: .9 }],
  })];
  const timed = renderToStaticMarkup(createElement(CaptionStrip, {
    messages, clocks: { timed: .4 },
  }));
  assert.match(timed, /Left heading/);
  assert.doesNotMatch(timed, /two/);
  const fallback = renderToStaticMarkup(createElement(CaptionStrip, { messages }));
  assert.match(fallback, /Left heading two four zero/);
});
