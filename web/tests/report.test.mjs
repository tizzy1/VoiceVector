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
  outputText = outputText.replace('import "./report.css";', "");
  for (const [name, url] of Object.entries(imports)) outputText = outputText.replaceAll(
    '"' + name + '"', JSON.stringify(url));
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const data = await compile("../src/report/reportData.ts");
const { groupFindings, seconds, simTime, valueText } = await import(data);
const { ReportView, ReportPage, ReportDialog } = await import(await compile("../src/report/ReportView.tsx", {
  "./reportData": data, "react": import.meta.resolve("react"),
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
}));
const finding = {
  id: "F1", rule_id: "B1", category: "B_INSTRUCTION", severity: "HIGH", t: 75,
  actor: "CONTROLLER", callsigns: ["BAW117"], title: "Descent below minimum",
  detail: "Clearance below sector minimum.", correct_practice: "Maintain the minimum altitude.",
  evidence: { transcript: "<script>descend</script>" },
};
const report = {
  session_id: "ABCD", duration_s: 125, score: 90,
  score_breakdown: { base: 100, instructions: -15, catches: 5, efficiency: 0 },
  findings: [finding], errors: [{
    id: "E1", callsign: "BAW117", kind: "wrong_value_adjacent", mode: "consistent",
    element_type: "heading", intended: 240, readback_value: 250, flown_value: 250,
    outcome: "caught", caught_by: "CONTROLLER", t_planned: 40, t_caught: 70,
  }],
  stats: { transmissions: 4, pilot_transmissions: 4, landings: 0, departures: 1, go_arounds: 0,
    latency: { perceived_s: { n: 3, p50: 1.25, p90: 2 }, machine_s: { n: 0, p50: null, p90: null } },
    efficiency: { bonus: 0 } },
  narrative: "Debrief cites [F1].", coaching: ["Verify readbacks."],
  timeline: [{ t: 75, type: "finding", actor: "CONTROLLER", callsign: "BAW117", data: { id: "F1" } }],
};
const render = value => renderToStaticMarkup(createElement(ReportView, { report: value, onDownload() {} }));

test("report preserves supplied scoring, findings, evidence, catches, narrative and measured percentiles", () => {
  const html = render(report);
  for (const text of ["90", "-15", "+5", "B · Instruction legality", "F1", "Descent below minimum",
    "Maintain the minimum altitude.", "&lt;script&gt;descend&lt;/script&gt;", "240", "250", "caught",
    "01:10", "Debrief cites [F1].", "Verify readbacks.", "1.25 s", "2.00 s", "02:05", "Download JSON"]) {
    assert.ok(html.includes(text), text);
  }
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes('aria-pressed="true"'));
  assert.ok(html.includes('aria-controls="'));
  assert.ok(html.includes("Simulation time"));
});
test("empty reports distinguish absent measurements, narrative and findings from zero", () => {
  const html = render({ ...report, findings: [], errors: [], timeline: [], stats: {}, narrative: null, coaching: [] });
  for (const text of ["No pilot errors recorded", "No timeline events recorded", "No narrative supplied",
    "No coaching points supplied", "—"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("0.00 s"));
  assert.ok(!html.includes("1.25 s"));
});
test("all six categories remain visible and unknown categories are retained", () => {
  const groups = groupFindings([finding, { ...finding, id: "F2", category: "NEW" }]);
  assert.equal(groups.length, 7);
  assert.equal(groups[1].findings[0].id, "F1");
  assert.equal(groups[6].findings[0].id, "F2");
  assert.equal(groups[0].findings.length, 0);
});
test("formatting keeps zero measurements and arbitrary error values without inventing missing values", () => {
  assert.equal(seconds(0), "0.00 s");
  for (const value of [undefined, null, NaN, Infinity, -1, "1"]) assert.equal(seconds(value), "—");
  assert.equal(valueText(0), "0");
  assert.equal(valueText(null), "—");
  assert.equal(valueText({ level: 4000 }), '{"level":4000}');
  assert.equal(simTime(3661), "61:01");
});
test("dedicated report page handles missing reports and disconnected sessions explicitly", () => {
  const html = renderToStaticMarkup(createElement(ReportPage, {
    report: null, connectionStatus: "Session unavailable", onDownload() {},
  }));
  assert.ok(html.includes("Session unavailable"));
  assert.ok(html.includes("No completed report is available"));
  assert.ok(!html.includes("Hold to talk"));
  const dialog = renderToStaticMarkup(createElement(ReportDialog, {
    report, open: false, href: "/report?session=ABCD", onDownload() {}, onClose() {},
  }));
  assert.ok(dialog.includes('aria-label="Session debrief"'));
  assert.ok(dialog.includes("Close report"));
  assert.ok(dialog.includes("/report?session=ABCD"));
});
