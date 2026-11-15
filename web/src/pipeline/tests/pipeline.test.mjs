// Run: node --test web/src/pipeline/tests  (15 Nov, Sanju)
import assert from "node:assert/strict";
import test from "node:test";
import { applyMessage, initialState, nodeLook, pointOn, nodeBox, fmtMs } from "../pipelineModel.ts";

const act = (o) => ({ type: "activity", id: "a1", node: "stt", service: "ElevenLabs Scribe v2 Realtime", op: "live transcription",
  phase: "start", status: "ok", ms: null, tx_id: "c1", callsign: null, at_ms: 1, detail: {}, ...o });

test("an API call runs, then glows done with its time, and is counted once", () => {
  let s = initialState();
  s = applyMessage(s, act({}), 0);
  assert.equal(nodeLook(s.nodes.stt, 10, s.mayday, "stt"), "running");
  assert.equal(s.apiCalls, 1);
  s = applyMessage(s, act({ phase: "end", ms: 412 }), 500);
  assert.equal(nodeLook(s.nodes.stt, 600, s.mayday, "stt"), "done");
  assert.equal(nodeLook(s.nodes.stt, 5000, s.mayday, "stt"), "idle");
  assert.equal(s.services["ElevenLabs Scribe v2 Realtime"].calls, 1);
  assert.equal(s.apiCalls, 1);
  assert.equal(s.log[0].ms, 412);
  assert.equal(s.log.length, 1);                         // the "calling…" row was replaced
});

test("a packet runs along the wire from the previous stage", () => {
  let s = initialState();
  s = applyMessage(s, act({ id: "m", node: "mic", service: "Browser microphone", op: "push-to-talk" }), 0);
  s = applyMessage(s, act({}), 100);
  assert.deepEqual(s.packets.map(p => [p.from, p.to]), [["mic", "stt"]]);
  s = applyMessage(s, act({ id: "x", node: "readback", phase: "instant", ms: 0.3 }), 200);   // not connected to stt
  assert.equal(s.packets.filter(p => p.to === "readback").length, 0);
});

test("a mayday turns the voice path red and raises the banner", () => {
  let s = initialState();
  s = applyMessage(s, act({ id: "e", node: "pilot", op: "MAYDAY", phase: "instant", status: "emergency", callsign: "BAW117" }), 0);
  assert.equal(s.mayday.callsign, "BAW117");
  s = applyMessage(s, act({ id: "r", node: "readback", phase: "instant", ms: 0.3 }), 5);
  s = applyMessage(s, act({ id: "v", node: "voice", service: "fal Chatterbox", op: "mayday" }), 10);
  assert.equal(nodeLook(s.nodes.voice, 20, s.mayday, "voice"), "emergency");
  assert.deepEqual(s.packets.map(p => [p.from, p.to, p.emergency]), [["readback", "voice", true]]);
  assert.ok(s.log.some(r => r.status === "emergency" && r.op === "MAYDAY"));
});

test("failed calls show as errors; warm-ups do not fire packets", () => {
  let s = initialState();
  s = applyMessage(s, act({ id: "w", node: "claude", op: "warm-up" }), 0);
  s = applyMessage(s, act({ id: "w", node: "claude", op: "warm-up", phase: "end", status: "error", ms: 3000 }), 10);
  assert.equal(nodeLook(s.nodes.claude, 20, s.mayday, "claude"), "error");
  assert.equal(s.packets.length, 0);
  assert.equal(Object.values(s.services)[0].errors, 1);
});

test("captions, applied instructions and response time are picked up", () => {
  let s = initialState();
  s = applyMessage(s, { type: "caption", tx_id: "c1", speaker: "CTRL", stage: "final", text: "Speedbird 117 turn left heading 240" }, 0);
  s = applyMessage(s, { type: "caption", tx_id: "p1", speaker: "BAW117", stage: "pilot_text", text: "Left 240, Speedbird 117" }, 0);
  s = applyMessage(s, { type: "event", event: { type: "instruction_applied", callsign: "BAW117" }, feed_text: "" }, 0);
  s = applyMessage(s, { type: "trace", trace: { tx_id: "c1", speaker: "CTRL", reply_callsign: "BAW117", stages: [], total_ms: 1130, final: true } }, 0);
  assert.equal(s.ctrlText, "Speedbird 117 turn left heading 240");
  assert.equal(s.pilotCallsign, "BAW117");
  assert.equal(nodeLook(s.nodes.apply, 10, s.mayday, "apply"), "done");
  assert.equal(s.lastResponseMs, 1130);
});

test("geometry: packets start and end on the node edges; times format", () => {
  const [x0, y0] = pointOn("mic", "stt", 0), b = nodeBox("mic");
  assert.equal(x0, b.x + b.w); assert.equal(y0, b.cy);
  const [x1] = pointOn("mic", "stt", 1);
  assert.equal(x1, nodeBox("stt").x);
  assert.equal(fmtMs(0.42), "0.4 ms"); assert.equal(fmtMs(412), "412 ms"); assert.equal(fmtMs(2310), "2.31 s");
});
