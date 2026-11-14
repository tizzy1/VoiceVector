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
  outputText = outputText.replace('import "./aircraft-details.css";', "");
  for (const [name, url] of Object.entries(imports)) {
    outputText = outputText.replaceAll('"' + name + '"', JSON.stringify(url));
  }
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const geometry = await compile("../src/radar/geometry.ts");
const { AircraftDetails } = await import(await compile("../src/ui/AircraftDetails.tsx", {
  "../radar/geometry": geometry, "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
}));
const aircraft = {
  callsign: "BAW117", spoken_plain: "British Airways 117", actype: "A320", wake: "M",
  alt: 8000, cleared_alt: 4000, hdg: 275, tgt_hdg: 240, ias: 220, tgt_ias: 210,
  route_name: "GWC1G", next_fix: "WILLO", gs: 230, vs: -1200,
  phase: "VECTORS", squawk: "2000", approach_cleared: null,
  on_frequency: true, transmitting: false, emergency: false,
};
function render(ac, alerts = []) {
  return renderToStaticMarkup(createElement(AircraftDetails, { aircraft: ac, alerts }));
}
test("details compare observed values with assigned clearance", () => {
  const html = render(aircraft);
  for (const text of ["FL080", "A040", "275°", "240°", "220 kt", "210 kt",
    "GWC1G", "WILLO", "On frequency"]) assert.ok(html.includes(text), text);
  const next = render({ ...aircraft, alt: 7000, on_frequency: false });
  assert.ok(next.includes("FL070"));
  assert.ok(next.includes("Off frequency"));
  assert.ok(!next.includes("FL080"));
});
test("unknown assignments stay empty and aircraft alerts stay scoped", () => {
  const html = render({ ...aircraft, tgt_hdg: null, tgt_ias: null }, [
    { callsigns: ["BAW117"], severity: "HIGH", message: "Conflict with EZY45AB" },
    { callsigns: ["OTHER"], severity: "HIGH", message: "Unrelated warning" },
  ]);
  assert.ok(!html.includes("240°"));
  assert.ok(html.includes("—"));
  assert.ok(html.includes("Conflict with EZY45AB"));
  assert.ok(!html.includes("Unrelated warning"));
});
test("clearing selection removes previous aircraft details", () => {
  const html = render(undefined);
  assert.ok(html.includes("Select a radar track"));
  assert.ok(!html.includes("BAW117"));
  assert.ok(!html.includes("<table"));
});
test("imported aircraft remain labelled as simulation with source and timestamps", () => {
  const html = render({ ...aircraft, traffic_source: "vatsim", source_time: "2026-11-14T23:00:00Z",
    fetched_at: "2026-11-14T23:00:15Z", source_attribution: "VATSIM network snapshot" });
  for (const text of ["Imported simulation", "VATSIM", "2026-11-14T23:00:00Z",
    "2026-11-14T23:00:15Z", "VATSIM network snapshot"]) assert.ok(html.includes(text), text);
});
