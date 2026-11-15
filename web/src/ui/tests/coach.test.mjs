// Run: node --test web/src/ui/tests  (15 Nov, Sanju)
import assert from "node:assert/strict";
import test from "node:test";
import { mergeCoach } from "../coach.ts";

const rules = { type: "coach", tx_id: "c1", callsign: "BAW117", score: 85, notes: [{ level: "fix", text: "Add the QNH." }], tip: null };

test("Claude's tip lands on the same card; a new transmission replaces it", () => {
  const withTip = mergeCoach(rules, { ...rules, tip: "Add the QNH: QNH 1013." });
  assert.equal(withTip.tip, "Add the QNH: QNH 1013.");
  assert.deepEqual(withTip.notes, rules.notes);
  const next = { ...rules, tx_id: "c2", score: 100, notes: [{ level: "good", text: "Clear." }] };
  assert.equal(mergeCoach(withTip, next).tip, null);
  assert.equal(mergeCoach(null, rules).tx_id, "c1");
});
