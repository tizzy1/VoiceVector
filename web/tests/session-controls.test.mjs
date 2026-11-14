import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const source = await readFile(new URL("../src/ui/SessionControls.tsx", import.meta.url), "utf8");
let { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} });
outputText = outputText.replace('import "./session-controls.css";', "")
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { SessionControls } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);
const props = {
  session: { settings: { scenario: "demo", sim_speed: 1, error_rate: "low",
    arr_per_hr: 12, dep_per_hr: 8 } },
  state: { aircraft: [{ callsign: "BAW117", phase: "STAR" }] },
  selected: "BAW117", enabled: true, radioReady: false,
  traffic: { difficulty_presets: { easy: { arr_per_hr: 10, dep_per_hr: 8 },
    medium: { arr_per_hr: 18, dep_per_hr: 14 }, busy: { arr_per_hr: 26, dep_per_hr: 20 } } },
};
function elements(node) {
  if (!node || typeof node !== "object") return [];
  if (Array.isArray(node)) return node.flatMap(elements);
  return [node, ...elements(node.props?.children)];
}
test("controls send exact existing actions and selected callsign", () => {
  const sent = [];
  const nodes = elements(SessionControls({ ...props, send: m => sent.push(m) }));
  nodes.find(n => n.type === "button" && n.props.children === "Busy").props.onClick();
  assert.deepEqual(sent[0], { type: "control", action: "set_traffic",
    params: { arr_per_hr: 26, dep_per_hr: 20 } });
  nodes.find(n => n.type === "button" && n.props.children === "Emergency").props.onClick();
  assert.equal(sent[1].params.callsign, "BAW117");
  assert.equal(sent[1].params.kind, "mayday");
  const selects = nodes.filter(n => n.type === "select");
  selects[0].props.onChange({ target: { value: "2" } });
  selects[1].props.onChange({ target: { value: "med" } });
  assert.equal(sent[2].params.sim_speed, 2);
  assert.equal(sent[3].params.error_rate, "med");
});
test("controls are disabled while disconnected or without a valid target", () => {
  const html = renderToStaticMarkup(createElement(SessionControls, {
    ...props, enabled: false, selected: "", send() {},
  }));
  assert.equal((html.match(/<fieldset disabled=""/g) ?? []).length, 2);
  assert.match(html, /await the radio pipeline/);
});
test("scenario-driven traffic rates cannot be overridden by misleading preset buttons", () => {
  const nodes = elements(SessionControls({ ...props,
    session: { settings: { ...props.session.settings, scenario: "ramp" } }, send() {},
  }));
  for (const name of ["Easy", "Medium", "Busy"]) {
    assert.equal(nodes.find(n => n.type === "button" && n.props.children === name).props.disabled, true);
  }
});
