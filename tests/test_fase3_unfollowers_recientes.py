#!/usr/bin/env python3
"""Fase 3: filtro de interfaz "Recent Unfollowers".

Parte A (estatica): comprueba en el codigo fuente que la interfaz ofrece dos
vistas claramente identificadas (la actual de cuentas que no te siguen y
"Recent Unfollowers"), que la vista nueva es solo de consulta (sin checkboxes,
seleccion, whitelist ni acciones de unfollow sobre eventos historicos), que
reutiliza getLatestComparison/getRecentUnfollowers sin recalcular diferencias
ni comparar usernames y que no registra snapshots fuera de la ruta completa.
Parte B (funcional): transpila src/utils/snapshot-history.ts con TypeScript y
ejecuta casos reales con node en un directorio temporal, usando un mock minimo
de localStorage.

Sin red ni dependencias externas. Sale con codigo distinto de cero ante
cualquier fallo e imprime "FASE3 RECIENTES OK" si todo pasa.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_TS = os.path.join(ROOT, "src", "utils", "snapshot-history.ts")
SAFETY_TS = os.path.join(ROOT, "src", "utils", "unfollow-safety.ts")
MAIN_TSX = os.path.join(ROOT, "src", "main.tsx")
SEARCHING_TSX = os.path.join(ROOT, "src", "components", "Searching.tsx")
STATE_TS = os.path.join(ROOT, "src", "model", "state.ts")

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


def strip_comments(source):
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.S)


def extract_block(source, start_marker, opener="=> {"):
    start = source.find(start_marker)
    if start == -1:
        return ""
    open_index = source.find(opener, start) + len(opener) - 1
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
    history = read(HISTORY_TS)
    check(re.search(r"export function getRecentUnfollowers\b", history) is not None,
          "snapshot-history.ts exporta getRecentUnfollowers")
    recent_fn = strip_comments(extract_block(history, "export function getRecentUnfollowers", opener="{"))
    check(recent_fn != "" and "username" not in recent_fn and ".followers" not in recent_fn
          and ".following" not in recent_fn,
          "getRecentUnfollowers no compara usernames ni recalcula diferencias de followers/following")
    latest_fn = strip_comments(extract_block(history, "export function getLatestComparison", opener="{"))
    check("previousSnapshotId" in latest_fn and "currentSnapshotId" in latest_fn,
          "getLatestComparison filtra eventos por el par previousSnapshotId/currentSnapshotId")

    state = read(STATE_TS)
    check("resultsView" in state, "state.ts declara resultsView en ScanningState")

    main = read(MAIN_TSX)
    check(main.count("recordCompleteScan(") == 1,
          "recordCompleteScan sigue llamandose en un unico punto de main.tsx")
    finish_incomplete_block = extract_block(main, "const finishIncomplete = (message: string)")
    check(finish_incomplete_block != "" and "recordCompleteScan(" not in finish_incomplete_block
          and "unfollowHistory" not in finish_incomplete_block,
          "finishIncomplete no registra snapshots ni altera el par de comparacion")

    searching = read(SEARCHING_TSX)
    searching_code = strip_comments(searching)
    check("Recent Unfollowers" in searching, "Searching.tsx ofrece la vista \"Recent Unfollowers\"")
    check("Not Following You" in searching,
          "Searching.tsx identifica la vista actual de cuentas que no te siguen")
    check("getRecentUnfollowers(" in searching_code, "Searching.tsx usa getRecentUnfollowers")
    check("recordCompleteScan" not in searching_code, "Searching.tsx no registra snapshots")
    check("another complete scan" in searching,
          "la vista reciente explica que hace falta otro escaneo completo")
    check("No unfollowers between your last two complete scans" in searching,
          "la vista reciente indica cuando no hubo unfollowers entre los dos ultimos escaneos completos")
    check("can&apos;t be read" in searching, "se conserva el aviso de historial ilegible")

    panel = strip_comments(extract_block(searching, "const RecentUnfollowersPanel"))
    check(panel != "", "Searching.tsx define RecentUnfollowersPanel")
    for forbidden in ("checkbox", "toggleUser", "selectedResults", "setState", "localStorage",
                      "onClick", "onChange", "whitelist"):
        check(forbidden not in panel, "RecentUnfollowersPanel no usa " + forbidden + " (solo consulta)")
    check("event.username ?? event.userId" in panel, "RecentUnfollowersPanel muestra username con fallback al userId")
    check("previousCompletedAt" in panel and "currentCompletedAt" in panel,
          "RecentUnfollowersPanel muestra ambas fechas del intervalo")
    check("youFollowThem === null" in panel and "You still follow them" in panel
          and "You don't follow them" in panel,
          "RecentUnfollowersPanel muestra youFollowThem con sus tres estados")

    check(re.search(r"disabled=\{actionsLocked \|\| showingRecentUnfollowers\}", searching) is not None,
          "el boton de unfollow queda deshabilitado en la vista Recent Unfollowers")
    check(re.search(r"actionsLocked\s*=.*isRestoredSnapshot", searching) is not None,
          "se mantiene el bloqueo de acciones de restauracion (Fase 2)")
    check("getCurrentPageUnfollowers(usersForDisplay, state.page)" in searching
          and "toggleUser(e.currentTarget.checked, user)" in searching,
          "la vista actual mantiene paginacion y seleccion")


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

function rewriteRequire(code) {
  return code
    .replace(/require\("\.\.\/constants\/constants"\)/g, 'require("./constants")')
    .replace(/require\("\.\/unfollow-safety"\)/g, 'require("./unfollow-safety")');
}

const workDir = process.env.WORK_DIR;
fs.writeFileSync(path.join(workDir, "unfollow-safety.js"), compile(process.env.SAFETY_TS));
fs.writeFileSync(path.join(workDir, "constants.js"),
  'exports.SNAPSHOT_HISTORY_STORAGE_KEY = "iu_snapshot_history";');
fs.writeFileSync(path.join(workDir, "snapshot-history.js"), rewriteRequire(compile(process.env.HISTORY_TS)));

class FakeStorage {
  constructor() { this.store = {}; this.writes = 0; }
  getItem(key) { return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null; }
  setItem(key, value) { this.writes += 1; this.store[key] = String(value); }
  removeItem(key) { delete this.store[key]; }
}
global.localStorage = new FakeStorage();
const KEY = "iu_snapshot_history";

const h = require(path.join(workDir, "snapshot-history.js"));

const results = [];
function test(name, fn) {
  try {
    global.localStorage.store = {};
    global.localStorage.writes = 0;
    fn();
    results.push({ name, ok: true });
  } catch (e) {
    results.push({ name, ok: false, error: String(e && e.stack || e) });
  }
}

const OWNER = "999";
const T1 = Date.UTC(2026, 8, 1, 10, 0, 0);
const T2 = Date.UTC(2026, 8, 8, 10, 0, 0);
const T3 = Date.UTC(2026, 8, 15, 10, 0, 0);

function u(id, username) {
  return { id, username };
}

function scan(completedAt, followers, following, extra) {
  return Object.assign({ outcome: "complete", completedAt, ownerId: OWNER, followers, following }, extra || {});
}

function stored() {
  return JSON.parse(global.localStorage.getItem(KEY));
}

// Same contract the UI uses: state.unfollowHistory = getLatestComparison(...),
// then the Recent Unfollowers view renders getRecentUnfollowers(state.unfollowHistory).
function recent(owner) {
  return h.getRecentUnfollowers(h.getLatestComparison(h.loadSnapshotHistory(), owner === undefined ? OWNER : owner));
}

function snapshotIdAt(completedAt) {
  const snap = stored().snapshots.find(s => s.completedAt === completedAt && s.ownerId === OWNER);
  assert.ok(snap, "no snapshot at " + completedAt);
  return snap.id;
}

// 1) menos de dos snapshots completos
test("1) no history: no recent unfollowers, another complete scan is needed", () => {
  const view = recent();
  assert.strictEqual(view.kind, "needs_another_scan");
  assert.strictEqual(view.baselineCompletedAt, null);
  assert.strictEqual(view.events, undefined);
  assert.strictEqual(h.getRecentUnfollowers(undefined).kind, "needs_another_scan");
});

test("1b) a single complete snapshot (baseline) has no recent unfollowers", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const view = recent();
  assert.strictEqual(view.kind, "needs_another_scan");
  assert.strictEqual(view.baselineCompletedAt, T1);
  assert.strictEqual(view.events, undefined);
});

// 2) T1/T2: exactamente los eventos de ese par
test("2) with T1/T2 exactly the events of that pair are shown, with their data", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob"), u("3", "carol")], [u("1", "alice")]), T1);
  h.recordCompleteScan(scan(T2, [u("3", "carol")], [u("1", "alice")]), T2 + 5);
  const view = recent();
  assert.strictEqual(view.kind, "unfollowers");
  assert.strictEqual(view.previousCompletedAt, T1);
  assert.strictEqual(view.currentCompletedAt, T2);
  const s1 = snapshotIdAt(T1);
  const s2 = snapshotIdAt(T2);
  const expected = stored().events.filter(e => e.previousSnapshotId === s1 && e.currentSnapshotId === s2);
  assert.strictEqual(expected.length, 2);
  assert.deepStrictEqual(view.events, expected);
  const byId = {};
  view.events.forEach(e => { byId[e.userId] = e; });
  assert.deepStrictEqual(Object.keys(byId).sort(), ["1", "2"]);
  assert.strictEqual(byId["1"].username, "alice");
  assert.strictEqual(byId["1"].youFollowThem, true);
  assert.strictEqual(byId["2"].youFollowThem, false);
  assert.strictEqual(byId["1"].previousCompletedAt, T1);
  assert.strictEqual(byId["1"].currentCompletedAt, T2);
});

test("2b) only events whose snapshot pair is exactly (penultimate, latest) are shown", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
  const data = stored();
  const s2 = snapshotIdAt(T2);
  const real = data.events[0];
  // An event that claims the latest snapshot but a different previous one is
  // not part of the (T1, T2) pair and must not be shown.
  data.events.push(Object.assign({}, real, { id: "forged>" + s2 + ":7", userId: "7", previousSnapshotId: "forged" }));
  global.localStorage.store[KEY] = JSON.stringify(data);
  assert.strictEqual(h.loadSnapshotHistory().status, "ok");
  const view = recent();
  assert.strictEqual(view.kind, "unfollowers");
  assert.deepStrictEqual(view.events.map(e => e.id), [real.id]);
});

// 3) tras T3 solo T2->T3; T1->T2 permanece almacenado
test("3) after T3 only T2->T3 events are shown and T1->T2 events stay stored", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  h.recordCompleteScan(scan(T2, [u("2", "bob"), u("4", "dan")], []), T2);
  const olderEvents = stored().events;
  assert.deepStrictEqual(olderEvents.map(e => e.userId), ["1"]);
  h.recordCompleteScan(scan(T3, [u("4", "dan")], []), T3);
  const view = recent();
  assert.strictEqual(view.kind, "unfollowers");
  assert.strictEqual(view.previousCompletedAt, T2);
  assert.strictEqual(view.currentCompletedAt, T3);
  assert.deepStrictEqual(view.events.map(e => e.userId), ["2"]);
  view.events.forEach(e => {
    assert.strictEqual(e.previousSnapshotId, snapshotIdAt(T2));
    assert.strictEqual(e.currentSnapshotId, snapshotIdAt(T3));
  });
  const all = stored().events;
  assert.strictEqual(all.length, 2);
  assert.deepStrictEqual(all[0], olderEvents[0], "T1->T2 event must remain stored unchanged");
});

// 4) un T3 incompleto o fallido no desplaza T1/T2
test("4) an incomplete, failed, aborted or invalid T3 does not displace T1/T2", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
  const before = recent();
  const raw = global.localStorage.getItem(KEY);
  const writes = global.localStorage.writes;
  const attempts = [
    scan(T3, [], [], { outcome: "incomplete" }),
    scan(T3, [u("5", "eve")], [], { outcome: "failed" }),
    scan(T3, [u("5", "eve")], [], { outcome: "aborted" }),
    scan(T3, [u("abc", "broken")], []),
    scan(T3, [u("5", "eve")], [], { ownerId: null }),
    scan(T3, [], []),
  ];
  for (const attempt of attempts) {
    const r = h.recordCompleteScan(attempt, T3);
    assert.strictEqual(r.outcome, "rejected", JSON.stringify(attempt));
    assert.deepStrictEqual(r.newEvents, []);
  }
  assert.strictEqual(global.localStorage.getItem(KEY), raw);
  assert.strictEqual(global.localStorage.writes, writes);
  const after = recent();
  assert.deepStrictEqual(after, before);
  assert.strictEqual(after.previousCompletedAt, T1);
  assert.strictEqual(after.currentCompletedAt, T2);
  assert.deepStrictEqual(after.events.map(e => e.userId), ["1"]);
});

// 5) cero eventos entre los dos ultimos snapshots
test("5) two snapshots without unfollows produce the no-unfollowers empty state", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice")], []), T1);
  h.recordCompleteScan(scan(T2, [u("1", "alice"), u("5", "eve")], []), T2);
  const view = recent();
  assert.strictEqual(view.kind, "no_unfollowers");
  assert.strictEqual(view.previousCompletedAt, T1);
  assert.strictEqual(view.currentCompletedAt, T2);
  assert.strictEqual(view.events, undefined);
});

test("5b) zero events T2->T3 hides the older T1->T2 events", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
  h.recordCompleteScan(scan(T3, [u("2", "bob")], []), T3);
  const view = recent();
  assert.strictEqual(view.kind, "no_unfollowers");
  assert.strictEqual(view.previousCompletedAt, T2);
  assert.strictEqual(view.currentCompletedAt, T3);
  assert.strictEqual(stored().events.length, 1, "older event must stay stored");
});

// 6) identidad gobernada por ID normalizado
test("6) two representations of the same normalized id are one identity even if the username changes", () => {
  h.recordCompleteScan(scan(T1, [u(" 1 ", "alice"), u(2, "bob")], [u(1, "alice")]), T1);
  h.recordCompleteScan(scan(T2, [u("1", "alice_renamed"), u(" 2", "bob")], [u(" 1", "alice_renamed")]), T2);
  assert.strictEqual(recent().kind, "no_unfollowers");
  // A different id reusing an old username is a different account.
  h.recordCompleteScan(scan(T3, [u(1, "bob")], [u("1", "bob")]), T3);
  const view = recent();
  assert.strictEqual(view.kind, "unfollowers");
  assert.strictEqual(view.events.length, 1);
  assert.strictEqual(view.events[0].userId, "2");
  assert.strictEqual(view.events[0].username, "bob");
  assert.strictEqual(view.events[0].youFollowThem, false);
});

test("6b) owner is matched by normalized id and other accounts never leak in", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
  h.recordCompleteScan(scan(T3, [u("5", "eve")], [], { ownerId: "12345" }), T3);
  assert.deepStrictEqual(recent(" 999 ").events.map(e => e.userId), ["1"]);
  assert.deepStrictEqual(recent(999).events.map(e => e.userId), ["1"]);
  assert.strictEqual(recent("12345").kind, "needs_another_scan");
  assert.strictEqual(recent(null).kind, "needs_another_scan");
});

// 7) historial ilegible y solo lectura
test("7) unreadable history keeps the unavailable notice and is never written", () => {
  global.localStorage.store[KEY] = "{not json";
  const view = recent();
  assert.strictEqual(view.kind, "unavailable");
  assert.strictEqual(global.localStorage.getItem(KEY), "{not json");
  assert.strictEqual(global.localStorage.writes, 0);
});

test("7b) querying recent unfollowers never writes to storage", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice")], []), T1);
  h.recordCompleteScan(scan(T2, [], [u("9", "zed")]), T2);
  const raw = global.localStorage.getItem(KEY);
  const writes = global.localStorage.writes;
  recent();
  recent();
  assert.strictEqual(global.localStorage.getItem(KEY), raw);
  assert.strictEqual(global.localStorage.writes, writes);
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
    try:
        with tempfile.TemporaryDirectory() as work_dir:
            script = os.path.join(work_dir, "run.js")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(NODE_SCRIPT)
            env = dict(os.environ, TS_PATH=typescript_js, SAFETY_TS=SAFETY_TS,
                       HISTORY_TS=HISTORY_TS, WORK_DIR=work_dir)
            completed = subprocess.run([node, script], cwd=work_dir, env=env,
                                       capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        check(False, "no se pudo ejecutar node (" + str(error) + ")")
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
    print("FASE3 RECIENTES OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
