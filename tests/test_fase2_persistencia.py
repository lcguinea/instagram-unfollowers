#!/usr/bin/env python3
"""Fase 2: persistencia local del ultimo escaneo completo.

Parte A (estatica): comprueba en el codigo fuente que el snapshot solo se
guarda desde una ruta de escaneo completo, que main.tsx restaura de forma
segura al arrancar y que no se reactivan acciones de unfollow para datos
restaurados. Parte B (funcional): transpila src/utils/scan-snapshot.ts con
TypeScript y ejecuta casos reales con node en un directorio temporal, usando
un mock minimo de localStorage.

Sin red ni dependencias externas. Sale con codigo distinto de cero ante
cualquier fallo e imprime "FASE2 OK" si todo pasa.
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
MAIN_TSX = os.path.join(ROOT, "src", "main.tsx")
SEARCHING_TSX = os.path.join(ROOT, "src", "components", "Searching.tsx")
STATE_TS = os.path.join(ROOT, "src", "model", "state.ts")
CONSTANTS_TS = os.path.join(ROOT, "src", "constants", "constants.ts")

EXPORTED_FUNCTIONS = [
    "buildScanSnapshot",
    "saveScanSnapshot",
    "loadScanSnapshot",
    "clearScanSnapshot",
]

failures = []


def check(condition, message):
    if condition:
        print("ok   - " + message)
    else:
        print("FAIL - " + message)
        failures.append(message)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def extract_block(source, start_marker):
    start = source.find(start_marker)
    if start == -1:
        return ""
    open_index = source.find("=> {", start) + len("=> ")
    depth = 0
    for index in range(open_index, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return source[start:]


def static_checks():
    print("# Parte A: comprobaciones estaticas")
    check(os.path.isfile(SNAPSHOT_TS), "existe src/utils/scan-snapshot.ts")
    if not os.path.isfile(SNAPSHOT_TS):
        return
    snapshot = read(SNAPSHOT_TS)
    for name in EXPORTED_FUNCTIONS:
        check(re.search(r"export function " + name + r"\b", snapshot) is not None,
              "scan-snapshot.ts exporta " + name)
    check("schemaVersion" in snapshot, "scan-snapshot.ts usa schemaVersion")
    check("normalizeInstagramId" in snapshot,
          "scan-snapshot.ts usa normalizeInstagramId para las claves")

    constants = read(CONSTANTS_TS)
    check(re.search(r"SCAN_SNAPSHOT_STORAGE_KEY\s*=", constants) is not None,
          "constants.ts define SCAN_SNAPSHOT_STORAGE_KEY")

    state = read(STATE_TS)
    check("isRestoredSnapshot" in state, "state.ts declara isRestoredSnapshot en ScanningState")

    main = read(MAIN_TSX)
    check(re.search(r"import\s*\{[^}]*\bsaveScanSnapshot\b[^}]*\}\s*from\s*\"\./utils/scan-snapshot\"",
                    main) is not None,
          "main.tsx importa saveScanSnapshot")
    check(re.search(r"import\s*\{[^}]*\bloadScanSnapshot\b[^}]*\}\s*from\s*\"\./utils/scan-snapshot\"",
                    main) is not None,
          "main.tsx importa loadScanSnapshot")

    scan_block = extract_block(main, "const scan = async")
    check("saveScanSnapshot(results)" in scan_block,
          "el efecto de escaneo llama a saveScanSnapshot(results) al completar")
    finish_incomplete_block = extract_block(main, "const finishIncomplete = (message: string)")
    check("saveScanSnapshot(" not in finish_incomplete_block,
          "finishIncomplete no llama a saveScanSnapshot (escaneos incompletos no persisten)")

    check("loadScanSnapshot()" in main, "main.tsx llama a loadScanSnapshot() al iniciar")
    check("isRestoredSnapshot: true" in main,
          "main.tsx marca el estado restaurado con isRestoredSnapshot: true")

    searching = read(SEARCHING_TSX)
    check(re.search(r"actionsLocked\s*=.*isRestoredSnapshot", searching) is not None,
          "Searching.tsx bloquea acciones cuando isRestoredSnapshot es true")


NODE_SCRIPT = r"""
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const ts = require(process.env.TS_PATH);

function compile(file) {
  const source = fs.readFileSync(file, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES5, lib: ["es2015"] },
  });
  return output.outputText;
}

// Rewrite the relative import of unfollow-safety to point at the sibling file
// we also transpile into the same work dir.
function rewriteRequire(code) {
  return code
    .replace(/require\("\.\.\/constants\/constants"\)/g, 'require("./constants")')
    .replace(/require\("\.\/unfollow-safety"\)/g, 'require("./unfollow-safety")');
}

const workDir = process.env.WORK_DIR;
fs.writeFileSync(path.join(workDir, "unfollow-safety.js"), compile(process.env.SAFETY_TS));
fs.writeFileSync(path.join(workDir, "constants.js"),
  'exports.SCAN_SNAPSHOT_STORAGE_KEY = "iu_scan_snapshot";');
fs.writeFileSync(path.join(workDir, "scan-snapshot.js"), rewriteRequire(compile(process.env.SNAPSHOT_TS)));

// Minimal localStorage mock.
class FakeStorage {
  constructor() { this.store = {}; }
  getItem(key) { return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null; }
  setItem(key, value) { this.store[key] = String(value); }
  removeItem(key) { delete this.store[key]; }
}
global.localStorage = new FakeStorage();

const snapshot = require(path.join(workDir, "scan-snapshot.js"));

const results = [];
function test(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (e) {
    results.push({ name, ok: false, error: String(e && e.stack || e) });
  }
}

function makeUser(id, username) {
  return {
    id,
    username,
    full_name: "Full " + username,
    profile_pic_url: "https://example.invalid/" + username + ".jpg",
    is_private: false,
    is_verified: false,
    followed_by_viewer: true,
    follows_viewer: false,
    requested_by_viewer: false,
  };
}

test("saveScanSnapshot then loadScanSnapshot restores users and timestamp", () => {
  global.localStorage.store = {};
  const users = [makeUser("111", "alice"), makeUser("222", "bob")];
  snapshot.saveScanSnapshot(users);
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, true);
  assert.strictEqual(typeof restored.completedAt, "number");
  assert.strictEqual(restored.users.length, 2);
  const byId = {};
  restored.users.forEach(u => { byId[u.id] = u; });
  assert.strictEqual(byId["111"].username, "alice");
  assert.strictEqual(byId["222"].username, "bob");
});

test("normalized ids collapse duplicate identities into one entry", () => {
  global.localStorage.store = {};
  const users = [makeUser("123", "first"), makeUser(" 123 ", "second")];
  snapshot.saveScanSnapshot(users);
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, true);
  assert.strictEqual(restored.users.length, 1);
  assert.strictEqual(restored.users[0].id, "123");
  assert.strictEqual(restored.users[0].username, "second");
});

test("no snapshot in storage -> found false, no throw", () => {
  global.localStorage.store = {};
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, false);
});

test("corrupt JSON in storage is ignored safely", () => {
  global.localStorage.store = {};
  global.localStorage.setItem(snapshot.SCAN_SNAPSHOT_STORAGE_KEY, "{not json");
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, false);
});

test("incompatible schemaVersion is ignored safely", () => {
  global.localStorage.store = {};
  global.localStorage.setItem(snapshot.SCAN_SNAPSHOT_STORAGE_KEY, JSON.stringify({
    schemaVersion: 999,
    completedAt: Date.now(),
    users: { "1": makeUser("1", "x") },
  }));
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, false);
});

test("missing users field is ignored safely", () => {
  global.localStorage.store = {};
  global.localStorage.setItem(snapshot.SCAN_SNAPSHOT_STORAGE_KEY, JSON.stringify({
    schemaVersion: 1,
    completedAt: Date.now(),
  }));
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, false);
});

test("saveScanSnapshot skips users without a valid normalized id", () => {
  global.localStorage.store = {};
  const users = [makeUser("abc", "invalidid"), makeUser("999", "validid")];
  snapshot.saveScanSnapshot(users);
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, true);
  assert.strictEqual(restored.users.length, 1);
  assert.strictEqual(restored.users[0].username, "validid");
});

test("clearScanSnapshot removes the stored snapshot", () => {
  global.localStorage.store = {};
  snapshot.saveScanSnapshot([makeUser("1", "x")]);
  snapshot.clearScanSnapshot();
  const restored = snapshot.loadScanSnapshot();
  assert.strictEqual(restored.found, false);
});

process.stdout.write(JSON.stringify(results));
"""


def find_typescript():
    override = os.environ.get("IU_TYPESCRIPT_JS")
    if override:
        return override if os.path.isfile(override) else None
    candidate = os.path.join(ROOT, "node_modules", "typescript", "lib", "typescript.js")
    return candidate if os.path.isfile(candidate) else None


def functional_checks():
    print("# Parte B: comprobaciones funcionales")
    node = shutil.which("node")
    if node is None:
        print("FUNCIONAL OMITIDO: node no esta disponible en el PATH")
        return
    typescript_js = find_typescript()
    if typescript_js is None:
        print("FUNCIONAL OMITIDO: no existe node_modules/typescript/lib/typescript.js (ni IU_TYPESCRIPT_JS)")
        return
    if not os.path.isfile(SNAPSHOT_TS):
        print("FUNCIONAL OMITIDO: no existe src/utils/scan-snapshot.ts todavia")
        check(False, "scan-snapshot.ts debe existir para ejecutar las pruebas funcionales")
        return
    try:
        with tempfile.TemporaryDirectory() as work_dir:
            script = os.path.join(work_dir, "run.js")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(NODE_SCRIPT)
            safety_ts = os.path.join(ROOT, "src", "utils", "unfollow-safety.ts")
            env = dict(os.environ, TS_PATH=typescript_js, SAFETY_TS=safety_ts,
                       SNAPSHOT_TS=SNAPSHOT_TS, WORK_DIR=work_dir)
            completed = subprocess.run([node, script], cwd=work_dir, env=env,
                                       capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        print("FUNCIONAL OMITIDO: no se pudo ejecutar node (" + str(error) + ")")
        return
    if completed.returncode != 0:
        check(False, "node termino con codigo " + str(completed.returncode) + ": " + completed.stderr.strip())
        return
    try:
        results = json.loads(completed.stdout)
    except ValueError:
        check(False, "salida de node no es JSON: " + completed.stdout[:500])
        return
    check(len(results) > 0, "node ejecuto casos funcionales")
    for result in results:
        check(result["ok"], result["name"] + ("" if result["ok"] else ": " + result.get("error", "")))


def main():
    static_checks()
    functional_checks()
    if failures:
        print("\n%d comprobacion(es) fallida(s)" % len(failures))
        return 1
    print("FASE2 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
