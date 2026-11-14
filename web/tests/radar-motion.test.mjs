import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/radar/motion.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
});
const { MotionBuffer, vectorEnd } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);
function state(t, changes = {}, aircraftChanges = {}) {
  return { t, paused: false, sim_speed: 1, ...changes,
    aircraft: [{ callsign: "BAW117", x: t, y: -t, alt: 8000 - t * 100,
      hdg: 0, track: 0, gs: 240, ...aircraftChanges }] };
}
test("movement interpolates position, altitude and north-crossing angles", () => {
  const buffer = new MotionBuffer();
  buffer.update(state(0, {}, { hdg: 359, track: 359 }), 0);
  buffer.update(state(1, {}, { hdg: 1, track: 1 }), 1000);
  const ac = buffer.sample(1500)[0];
  assert.equal(ac.x, .5); assert.equal(ac.y, -.5); assert.equal(ac.alt, 7950);
  assert.equal(ac.hdg, 0); assert.equal(ac.track, 0);
  assert.equal(buffer.sample(100000)[0].x, 1);
});
test("pause snaps to authoritative position and resume never rewinds", () => {
  const buffer = new MotionBuffer();
  buffer.update(state(0), 0);
  buffer.update(state(1), 1000);
  buffer.update(state(1, { paused: true }), 1200);
  assert.equal(buffer.sample(50000)[0].x, 1);
  buffer.update(state(1), 50000);
  assert.equal(buffer.sample(50001)[0].x, 1);
  buffer.update(state(2), 51000);
  assert.equal(buffer.sample(51500)[0].x, 1.5);
});
test("2x speed consumes a one-tick buffer in half a wall second", () => {
  const buffer = new MotionBuffer();
  buffer.update(state(0, { sim_speed: 2 }), 0);
  buffer.update(state(1, { sim_speed: 2 }), 500);
  assert.equal(buffer.sample(750)[0].x, .5);
  assert.equal(buffer.sample(1000)[0].x, 1);
});
test("five historical dots exclude future samples and duplicate updates", () => {
  const buffer = new MotionBuffer();
  for (let t = 0; t <= 10; t++) {
    buffer.update(state(t), t * 1000);
    buffer.update(state(t), t * 1000 + 1);
    buffer.sample(t * 1000 + 1000);
  }
  assert.deepEqual(buffer.sample(12000)[0].history.map(p => p.t), [5, 6, 7, 8, 9]);
});
test("reset, disconnect and removal discard old tracks and history", () => {
  const buffer = new MotionBuffer();
  buffer.update(state(10), 0);
  buffer.update(state(11), 1000);
  buffer.update(state(0), 2000);
  assert.equal(buffer.sample(2000)[0].x, 0);
  assert.equal(buffer.sample(2000)[0].history.length, 0);
  buffer.update({ ...state(1), aircraft: [] }, 3000);
  assert.deepEqual(buffer.sample(3500), []);
  buffer.update(null, 4000);
  assert.deepEqual(buffer.sample(4500), []);
  buffer.update(state(30), 5000);
  assert.equal(buffer.sample(5000)[0].x, 30);
  assert.equal(buffer.sample(5000)[0].history.length, 0);
});
test("new aircraft appear immediately with no interpolated flight from another track", () => {
  const buffer = new MotionBuffer();
  buffer.update(state(0), 0);
  buffer.update(state(1, {}, { callsign: "EZY45AB", x: 20 }), 1000);
  assert.equal(buffer.sample(1000)[0].x, 20);
});
test("vectors use ground speed and track in nautical miles", () => {
  assert.deepEqual(vectorEnd({ x: 3, y: 5, track: 0, gs: 240 }, 1), [3, 9]);
  const [x, y] = vectorEnd({ x: 3, y: 5, track: 90, gs: 240 }, 5);
  assert.equal(x, 23); assert.ok(Math.abs(y - 5) < 1e-8);
  assert.deepEqual(vectorEnd({ x: 3, y: 5, track: 180, gs: 0 }, 2), [3, 5]);
});
