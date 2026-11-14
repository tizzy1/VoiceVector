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
  outputText = outputText.replace('import "./presenter.css";', "");
  for (const [name, url] of Object.entries(imports)) outputText = outputText.replaceAll(
    '"' + name + '"', JSON.stringify(url));
  return "data:text/javascript;base64," + Buffer.from(outputText).toString("base64");
}
const logic = await compile("../src/ui/presenter.ts");
const { presenterKey } = await import(logic);
const { PresenterConsole } = await import(await compile("../src/ui/PresenterConsole.tsx", {
  "./presenter": logic, "react": import.meta.resolve("react"),
  "react/jsx-runtime": import.meta.resolve("react/jsx-runtime"),
}));
const key = (value, extra = {}) => ({ key: value, repeat: false, ctrlKey: false,
  altKey: false, metaKey: false, isComposing: false, defaultPrevented: false, ...extra });

test("only implemented presenter shortcuts are recognized, including capital D/J", () => {
  for (const value of ["1", "4", "8", "d", "D", "j", "J"]) {
    assert.equal(presenterKey(key(value), false), value.toLowerCase());
  }
  for (const value of ["2", "3", "5", "6", "7", "r", " ", "Escape"]) {
    assert.equal(presenterKey(key(value), false), null);
  }
});
test("typing, composition, held keys and browser shortcuts never fire presenter actions", () => {
  for (const value of ["1", "4", "8", "d", "j"]) {
    assert.equal(presenterKey(key(value), true), null);
    for (const flag of ["repeat", "ctrlKey", "altKey", "metaKey", "isComposing", "defaultPrevented"]) {
      assert.equal(presenterKey(key(value, { [flag]: true }), false), null);
    }
  }
});
test("shortcut help starts collapsed with an accessible toggle and judge indicator", () => {
  const props = { enabled: true, radioReady: true, reportPending: false,
    reportReady: false, judge: false, onJudge() {}, onBeat() {} };
  const html = renderToStaticMarkup(createElement(PresenterConsole, props));
  assert.ok(html.includes("Demo shortcuts · D"));
  assert.ok(html.includes('aria-expanded="false"'));
  assert.ok(html.includes("aria-controls="));
  assert.ok(!html.includes("Reset seeded demo"));
  const judge = renderToStaticMarkup(createElement(PresenterConsole, { ...props, judge: true }));
  assert.ok(judge.includes("· Judge"));
});
