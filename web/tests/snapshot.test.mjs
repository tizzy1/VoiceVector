import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const source = await readFile(new URL("../src/ui/SnapshotPanel.tsx", import.meta.url), "utf8");
let { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} });
outputText = outputText.replace('import "./snapshot.css";', "")
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")))
  .replace('"react"', JSON.stringify(import.meta.resolve("react")));
const { SnapshotPanel, snapshotTime } = await import("data:text/javascript;base64," + Buffer.from(outputText).toString("base64"));
const status = { source: "demo", phase: "complete", ok: true, stale: false, error: null,
  fetched_at: "2026-11-14T23:00:00Z", source_time: "2026-11-14T22:59:30Z",
  attribution: "Synthetic demo snapshot · no live traffic", counts: { imported: 2, skipped: 5,
    skipped_by_reason: { out_of_area: 5 } } };
const render = (changes = {}) => renderToStaticMarkup(createElement(SnapshotPanel, {
  state: { live_import_available: true, live_import: status }, enabled: true, controls: true, send() {}, ...changes,
}));
test("snapshot controls distinguish synthetic data and display complete provenance and skip counts", () => {
  const html = render();
  for (const text of ["Synthetic demo · offline", "VATSIM · read-only", "SIMULATED", "2 imported",
    "5 skipped", "2026-11-14 22:59:30Z", "out of area", "SNAP"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("disabled="));
});
test("audience view is status-only, unavailable or busy controllers cannot import", () => {
  assert.ok(!render({ controls: false }).includes("<button"));
  assert.ok(render({ enabled: false }).includes('disabled=""'));
  assert.ok(render({ state: null }).includes("unavailable"));
  const busy = render({ state: { live_import_available: true, live_import: { ...status, phase: "fetching" } } });
  assert.ok(busy.includes("Importing snapshot"));
  assert.ok(busy.includes('disabled=""'));
});
test("stale, empty and failed imports remain explicit without displaying error text as markup", () => {
  const html = render({ state: { live_import: { ...status, source: "vatsim", stale: true,
    counts: { imported: 0, skipped: 5, skipped_by_reason: {} } } } });
  assert.ok(html.includes("Stale source snapshot"));
  assert.ok(html.includes("No new matching Gatwick aircraft"));
  const failed = render({ state: { live_import: { ...status, ok: false, error: "<script>timeout</script>" } } });
  assert.ok(failed.includes("&lt;script&gt;"));
  assert.ok(!failed.includes("<script>"));
  assert.equal(snapshotTime(null), "Unavailable");
  assert.equal(snapshotTime("invalid"), "Unavailable");
});
