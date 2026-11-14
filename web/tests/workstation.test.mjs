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
  for (const [name, url] of Object.entries(imports)) outputText = outputText.replaceAll('"' + name + '"', JSON.stringify(url));
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const { overlaps, placeTag } = await import(await compile("../src/radar/labels.ts"));
const { AlertsEventsPanel } = await import(await compile("../src/ui/AlertsEventsPanel.tsx", {
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
}));
test("nearby aircraft get distinct data blocks, clamped to viewport edges", () => {
  const occupied = [];
  for (let i = 0; i < 5; i++) {
    const box = placeTag(400 + i, 300 + i, 180, 900, 600, occupied);
    assert.ok(!occupied.some(other => overlaps(box, other)));
    assert.ok(box.x >= 0 && box.x + box.width <= 900);
    assert.ok(box.y >= 0 && box.y + box.height <= 600);
    occupied.push(box);
  }
  const edge = placeTag(899, 599, 180, 900, 600, []);
  assert.ok(edge.x + edge.width <= 900 && edge.y + edge.height <= 600);
});
test("alert and event pane preserves severity, aircraft targets and simulation times", () => {
  const html = renderToStaticMarkup(createElement(AlertsEventsPanel, {
    state: { aircraft: [{ callsign: "BAW117" }], alerts: [{ kind: "STCA_PRED", message: "Converging traffic",
      callsigns: ["BAW117"], severity: "HIGH" }] },
    events: [{ event: { t: 75 }, feed_text: "Readback corrected" }], onSelect() {},
  }));
  for (const text of ["STCA PRED", "Converging traffic", "BAW117", "01:15", "Readback corrected"]) assert.ok(html.includes(text));
  const waiting = renderToStaticMarkup(createElement(AlertsEventsPanel, { state: null, events: [], onSelect() {} }));
  assert.ok(waiting.includes("Waiting for aircraft state"));
  assert.ok(!waiting.includes("No active alerts"));
});
