import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/app/radioLink.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022,
} });
const { RadioLink } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);
function fixture(role = "radar", problem = null) {
  const sent = [], binary = [], updates = [], received = [], actions = [];
  let options, listener;
  const engine = {
    init: async opts => { options = opts; }, resume: async () => {},
    whenMicReady: async () => {}, micError: () => problem,
    onActivity: cb => { listener = cb; return () => actions.push("unsubscribe"); },
    pttDown: () => { actions.push("down"); options.sendJson({ type: "ptt", state: "down" }); },
    pttUp: () => { actions.push("up"); options.sendJson({ type: "ptt", state: "up" }); },
    handleServerMsg: m => received.push(m), setPlayback: v => actions.push(v),
    setVolume: v => actions.push(v), micLevel: () => .4, pilotWordClock: () => 1.2,
    dispose: () => actions.push("dispose"),
  };
  const activity = [];
  const link = new RadioLink(engine, {
    send: m => { sent.push(m); return true; },
    sendBinary: frame => { binary.push(frame); return true; },
  }, role, m => updates.push(m), m => activity.push(m));
  return { link, sent, binary, updates, received, actions, activity,
    frame: data => options.sendBinary(data), speaker: event => listener(event),
    options: () => options };
}
test("engine gets ordered transport callbacks, messages and playback clocks", async () => {
  const f = fixture();
  await f.link.enable();
  f.link.down(); f.link.down();
  const frame = new ArrayBuffer(3200);
  f.frame(frame); f.link.up(); f.link.up();
  assert.deepEqual(f.sent.map(m => m.state), ["down", "up"]);
  assert.equal(f.binary[0], frame);
  const message = { type: "audio_start", tx_id: "a", callsign: "BAW117" };
  f.link.handle(message); assert.equal(f.received[0], message);
  f.speaker({ speaker: "BAW117", state: "start" });
  assert.equal(f.activity[0].speaker, "BAW117");
  assert.equal(f.link.level(), .4);
  assert.equal(f.link.clock("a"), 1.2);
  f.link.dispose();
});
test("viz enables playback with its own role and never keys the microphone", async () => {
  const f = fixture("viz");
  await f.link.enable(); f.link.down();
  assert.equal(f.options().role, "viz");
  assert.equal(f.options().playback, true);
  assert.equal(f.sent.length, 0);
});
test("microphone denial retains playback and blocks push-to-talk", async () => {
  const f = fixture("radar", "Permission denied");
  await f.link.enable(); f.link.down();
  assert.equal(f.updates.at(-1).enabled, true);
  assert.equal(f.updates.at(-1).micReady, false);
  assert.equal(f.updates.at(-1).error, "Permission denied");
  assert.equal(f.sent.length, 0);
});
test("disconnect disposal releases a keyed transmitter and suppresses further messages", async () => {
  const f = fixture();
  await f.link.enable(); f.link.down(); f.link.dispose();
  f.link.down(); f.link.handle({ type: "audio_end" });
  assert.deepEqual(f.sent.map(m => m.state), ["down", "up"]);
  assert.equal(f.received.length, 0);
  assert.equal(f.link.level(), 0);
});
test("WebSocket connection sends binary frames without JSON encoding", async () => {
  const connectionSource = await readFile(new URL("../src/app/connection.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(connectionSource, { compilerOptions: {
    module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const { connect } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
  const oldSocket = globalThis.WebSocket, oldLocation = globalThis.location;
  const sockets = [];
  class Socket {
    static OPEN = 1;
    readyState = 0;
    sent = [];
    constructor() { sockets.push(this); }
    send(data) { this.sent.push(data); }
    close() { this.onclose?.({ code: 1000 }); }
  }
  globalThis.WebSocket = Socket;
  globalThis.location = { search: "", host: "localhost:8000", protocol: "http:" };
  let connection;
  try {
    connection = connect("radar", () => {}, () => {});
    const frame = new ArrayBuffer(3200);
    assert.equal(connection.sendBinary(frame), false);
    sockets[0].readyState = 1;
    sockets[0].onopen();
    assert.equal(connection.sendBinary(frame), true);
    assert.equal(sockets[0].sent[0], frame);
    connection.send({ type: "ptt", state: "up" });
    assert.deepEqual(JSON.parse(sockets[0].sent[1]), { type: "ptt", state: "up" });
  } finally {
    connection?.close();
    globalThis.WebSocket = oldSocket; globalThis.location = oldLocation;
  }
});
