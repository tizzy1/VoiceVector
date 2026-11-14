import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/radar/geometry.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
});
const { project, unproject, zoomAt, levelLabel, headingLabel } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);
function close(a, b) {
  assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);
}

test("cursor stays over the same map point through zoom and clamping", () => {
  for (const [width, height] of [[900, 500], [320, 600]]) {
    for (const point of [[10, 10], [width / 2, height / 2], [width - 10, height - 10]]) {
      const view = { x: 5, y: -10, range: 40 };
      for (const factor of [.5, 2, .001, 1000]) {
        const before = unproject(point, view, width, height);
        const afterView = zoomAt(view, point, factor, width, height);
        const after = project(before, afterView, width, height);
        close(after[0], point[0]); close(after[1], point[1]);
        assert.ok(afterView.range >= 5 && afterView.range <= 100);
      }
    }
  }
});
test("map projection round-trips and north is upwards", () => {
  const view = { x: 2, y: -4, range: 20 };
  const pixel = project([16, -12], view, 1000, 500);
  const result = unproject(pixel, view, 1000, 500);
  close(result[0], 16); close(result[1], -12);
  assert.ok(project([2, 0], view, 1000, 500)[1] < 250);
});
test("level and heading labels preserve transition altitude and north", () => {
  assert.equal(levelLabel(4000), "A040");
  assert.equal(levelLabel(6000), "A060");
  assert.equal(levelLabel(7000), "FL070");
  assert.equal(headingLabel(0), "360");
  assert.equal(headingLabel(359.8), "360");
  assert.equal(headingLabel(5), "005");
});
