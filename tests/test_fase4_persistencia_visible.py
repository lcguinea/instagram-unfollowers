#!/usr/bin/env python3
"""Fase 4: un scan completo debe quedar persistido, verificable y distribuido.

Causa raiz que cubre: el artefacto que el usuario copia (public/index.html,
generado por `npm run build`) no contenia el codigo de persistencia, y
saveScanSnapshot podia lanzar (cuota) sin avisar. Parte A: artefacto de
distribucion y UI (estatico). Parte B: saveScanSnapshot con localStorage
simulado, sin red. Imprime "FASE4 PERSISTENCIA VISIBLE OK" si todo pasa.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_TS = os.path.join(ROOT, "src", "utils", "scan-snapshot.ts")
SAFETY_TS = os.path.join(ROOT, "src", "utils", "unfollow-safety.ts")
MAIN_TSX = os.path.join(ROOT, "src", "main.tsx")
SEARCHING_TSX = os.path.join(ROOT, "src", "components", "Searching.tsx")
INDEX_HTML = os.path.join(ROOT, "public", "index.html")
TS_JS = os.path.join(ROOT, "node_modules", "typescript", "lib", "typescript.js")

failures = []


def check(condition, message):
    print(("ok   - " if condition else "FAIL - ") + message)
    if not condition:
        failures.append(message)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def static_checks():
    print("# Parte A: distribucion y UI")
    html = read(INDEX_HTML)
    for needle in ("iu_scan_snapshot", "iu_snapshot_history", "storage_write_failed",
                   "Scan saved on this device", "Scan NOT saved"):
        check(needle in html, "public/index.html (lo que se copia) contiene " + needle)
    main = read(MAIN_TSX)
    check(re.search(r"const snapshotSaved = saveScanSnapshot\(results\)", main) is not None,
          "main.tsx usa el resultado de saveScanSnapshot")
    check("persistenceNotice" in main, "main.tsx fija persistenceNotice tras persistir")
    finish = main[main.find("const finishIncomplete"):main.find("const scanOwnerId")]
    check("persistenceNotice" not in finish, "finishIncomplete no fija persistenceNotice")
    searching = read(SEARCHING_TSX)
    check("Scan saved on this device" in searching and "Scan NOT saved" in searching,
          "Searching.tsx muestra confirmacion y aviso de error")


NODE_SCRIPT = r"""
const fs = require("fs"), path = require("path"), assert = require("assert");
const ts = require(process.env.TS_PATH);
const dir = process.env.WORK_DIR;
function compile(src, dst, fix) {
  let out = ts.transpileModule(fs.readFileSync(src, "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText;
  fs.writeFileSync(path.join(dir, dst), fix(out));
}
const constants = fs.readFileSync(path.join(process.env.ROOT, "src/constants/constants.ts"), "utf8");
fs.writeFileSync(path.join(dir, "constants.js"),
  'exports.SCAN_SNAPSHOT_STORAGE_KEY="iu_scan_snapshot";');
compile(process.env.SAFETY_TS, "unfollow-safety.js", s => s);
compile(process.env.SNAPSHOT_TS, "scan-snapshot.js", s => s
  .replace('require("../constants/constants")', 'require("./constants")')
  .replace('require("./unfollow-safety")', 'require("./unfollow-safety")'));
function mockStorage(failWrites) {
  const data = new Map();
  global.localStorage = {
    getItem: k => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { if (failWrites) throw new Error("QuotaExceededError"); data.set(k, String(v)); },
    removeItem: k => data.delete(k),
  };
  return data;
}
const results = [];
function t(name, fn) { try { fn(); results.push({ name, ok: true }); } catch (e) { results.push({ name, ok: false, error: String(e) }); } }
const snap = require(path.join(dir, "scan-snapshot.js"));
const users = [{ id: "12", username: "a" }, { id: "34", username: "b" }];

t("saveScanSnapshot persiste y devuelve ok con la fecha", () => {
  const data = mockStorage(false);
  const r = snap.saveScanSnapshot(users);
  assert.strictEqual(r.ok, true);
  assert.strictEqual(typeof r.completedAt, "number");
  assert.ok(data.has("iu_scan_snapshot"));
  const loaded = snap.loadScanSnapshot();
  assert.strictEqual(loaded.found, true);
  assert.strictEqual(loaded.completedAt, r.completedAt);
  assert.strictEqual(loaded.users.length, 2);
});
t("saveScanSnapshot no lanza si la escritura falla y devuelve ok:false", () => {
  const data = mockStorage(true);
  const r = snap.saveScanSnapshot(users);
  assert.strictEqual(r.ok, false);
  assert.strictEqual(data.size, 0);
});
console.log(JSON.stringify(results));
"""


def functional_checks():
    print("# Parte B: funcional")
    node = shutil.which("node")
    if node is None or not os.path.isfile(TS_JS):
        check(False, "node y node_modules/typescript son necesarios")
        return
    with tempfile.TemporaryDirectory() as work:
        script = os.path.join(work, "run.js")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(NODE_SCRIPT)
        env = dict(os.environ, TS_PATH=TS_JS, SAFETY_TS=SAFETY_TS, SNAPSHOT_TS=SNAPSHOT_TS,
                   WORK_DIR=work, ROOT=ROOT)
        done = subprocess.run([node, script], cwd=work, env=env, capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        check(False, "node fallo: " + done.stderr.strip()[:500])
        return
    for result in json.loads(done.stdout.strip().splitlines()[-1]):
        check(result["ok"], result["name"] + ("" if result["ok"] else ": " + result.get("error", "")))


def main():
    static_checks()
    functional_checks()
    if failures:
        print("\n%d comprobacion(es) fallida(s)" % len(failures))
        return 1
    print("FASE4 PERSISTENCIA VISIBLE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
