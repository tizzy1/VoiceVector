import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function compile(path, imports = {}) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  let { outputText } = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022,
  } });
  for (const [name, url] of Object.entries(imports)) outputText = outputText.replaceAll('"' + name + '"', JSON.stringify(url));
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const dataURL = await compile("../src/viz/sceneData.ts");
const { position, FT_TO_M, FlightHistory, aircraftMesh, modelMatrix, activeAircraft, wrapSpeech } = await import(dataURL);
const { CameraDirector, initialPose, overviewPose, aircraftPose } = await import(await compile("../src/viz/camera.ts", { "./sceneData": dataURL }));
const data = { _meta: { origin: { lat: 51.150672, lon: -.171944 } } };
const ac = { callsign: "BAW117", x: 5, y: -10, alt: 8000, hdg: 75, track: 78, flow: "ARR", phase: "VECTORS" };
const state = (t, aircraft = [ac], changes = {}) => ({ t, aircraft, ...changes });
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);

test("3D projection matches the simulator's local NM origin and converts feet to metres", () => {
  const origin = position(0, 0, 0, data);
  assert.deepEqual(origin, [-.171944, 51.150672, 0]);
  const p = position(60 * Math.cos(data._meta.origin.lat * Math.PI / 180), 60, 10000, data);
  near(p[0], origin[0] + 1); near(p[1], origin[1] + 1); near(p[2], 3048);
});
test("blueprint mesh is a finite 3D shape and heading rotation points the nose correctly", () => {
  const mesh = aircraftMesh(), positions = mesh.attributes.POSITION.value, normals = mesh.attributes.NORMAL.value;
  assert.ok(positions.length > 100 && positions.length % 9 === 0);
  assert.equal(positions.length, normals.length);
  assert.ok([...positions, ...normals].every(Number.isFinite));
  const zs = [...positions].filter((_, i) => i % 3 === 2);
  assert.ok(Math.max(...zs) > 0 && Math.min(...zs) < 0);
  // Model nose is +Y: north points north, east points east, south points south.
  for (const [heading, east, north] of [[0, 0, 2], [90, 2, 0], [180, 0, -2], [270, -2, 0]]) {
    const m = modelMatrix(heading, 2); near(m[4], east); near(m[5], north);
  }
});
test("three-minute trails retain true altitude, bound history, reset and remove departed tracks", () => {
  const history = new FlightHistory();
  for (let t = 0; t <= 240; t++) history.update(state(t, [{ ...ac, x: t / 100, alt: 8000 + t }]));
  const segments = history.segments(state(240), data);
  assert.equal(segments.length, 180);
  near(segments.at(-1).end[2], 8240 * FT_TO_M);
  assert.ok(segments[0].color[3] < segments.at(-1).color[3]);
  history.update(state(240));
  assert.equal(history.segments(state(240), data).length, 180);
  history.update(state(0));
  assert.equal(history.segments(state(0), data).length, 0);
  history.update(state(1, []));
  assert.equal(history.segments(state(1), data).length, 0);
  history.update(null);
  assert.equal(activeAircraft([{ ...ac, phase: "QUEUED" }, { ...ac, phase: "LANDED" }, ac]).length, 1);
});
test("camera locking targets actual aircraft altitude and chase uses its ground track", () => {
  const locked = aircraftPose(ac, data), chase = aircraftPose(ac, data, true);
  near(locked.position[2], ac.alt * FT_TO_M);
  near(chase.bearing, ac.track);
  assert.ok(chase.pitch > locked.pitch && chase.zoom > locked.zoom);
  const overview = overviewPose([ac], data, 1440, 900);
  assert.ok(Object.values(overview).flat().every(Number.isFinite));
  assert.deepEqual(overview.position, [0, 0, 0]);
});
test("camera transitions take 1.8 seconds, cross north by shortest arc, and support reduced motion", () => {
  const camera = new CameraDirector();
  camera.manual({ ...initialPose, bearing: 350 });
  const goal = { ...initialPose, bearing: 10, zoom: 13 };
  const first = camera.step(goal, "lock:BAW117", 0, false);
  near(first.bearing, 350);
  const middle = camera.step(goal, "lock:BAW117", 900, false);
  near(middle.bearing, 360);
  const reduced = camera.step(goal, "chase:BAW117", 1000, true);
  assert.deepEqual(reduced, goal);
  for (const bearing of [-730, 710]) {
    camera.manual({ ...initialPose, bearing });
    camera.step(goal, "lock:BAW117", 2000, false);
    near(camera.step(goal, "lock:BAW117", 2900, false).bearing, bearing + 10);
  }
});
test("radio director holds each pilot for six seconds and recovers when a target disappears", () => {
  const camera = new CameraDirector(), aircraft = [ac, { ...ac, callsign: "EZY45AB" }];
  assert.equal(camera.radioTarget(["BAW117"], aircraft, 0), "BAW117");
  assert.equal(camera.radioTarget(["EZY45AB"], aircraft, 3000), "BAW117");
  assert.equal(camera.radioTarget(["EZY45AB"], aircraft, 6001), "EZY45AB");
  assert.equal(camera.radioTarget([], aircraft, 9000), "EZY45AB");
  assert.equal(camera.radioTarget(["BAW117"], [ac], 9001), "BAW117");
});
test("speech bubble wrapping preserves every spoken token including long words and numbers", () => {
  const text = "Speedbird one one seven turn left heading two four zero descend altitude four thousand";
  assert.equal(wrapSpeech(text).split(/\s+/).join(" "), text);
  assert.equal(wrapSpeech("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 10), "ABCDEFGHIJKLMNOPQRSTUVWXYZ");
});
