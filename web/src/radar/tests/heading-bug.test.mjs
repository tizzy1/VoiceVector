// Run: node --test web/src/radar/tests  (15 Nov, Sanju for Bhavin)
import assert from "node:assert/strict";
import test from "node:test";
import { bugHeading, bugTurn } from "../geometry.ts";

test("screen drag direction becomes a heading rounded to 5 degrees (north up, 360 not 000)", () => {
  assert.equal(bugHeading(0, -10), 360);       // straight up
  assert.equal(bugHeading(10, 0), 90);         // right
  assert.equal(bugHeading(0, 10), 180);        // down
  assert.equal(bugHeading(-10, 0), 270);       // left
  assert.equal(bugHeading(10, -10), 45);
  assert.equal(bugHeading(-10, -6.3), 300);    // 302 -> 300
});

test("turn direction is the shortest way round", () => {
  assert.equal(bugTurn(350, 20), "R");
  assert.equal(bugTurn(20, 350), "L");
  assert.equal(bugTurn(90, 270 - 1), "R");
  assert.equal(bugTurn(257, 290), "R");
  assert.equal(bugTurn(10, 290), "L");
});
