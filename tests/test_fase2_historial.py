#!/usr/bin/env python3
"""Fase 2: historial local de snapshots completos y deteccion de unfollows.

Parte A (estatica): comprueba en el codigo fuente que el historial solo se
alimenta desde la ruta de escaneo completo (despues de saveScanSnapshot), que
no se registra al restaurar ni en finishIncomplete, y que la interfaz distingue
"no me sigue actualmente" de "dejo de seguirme desde el snapshot anterior".
Parte B (funcional): transpila src/utils/snapshot-history.ts con TypeScript y
ejecuta casos reales con node en un directorio temporal, usando un mock minimo
de localStorage.

Sin red ni dependencias externas. Sale con codigo distinto de cero ante
cualquier fallo e imprime "FASE2 HISTORIAL OK" si todo pasa.
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
CONSTANTS_TS = os.path.join(ROOT, "src", "constants", "constants.ts")

EXPORTED_FUNCTIONS = [
    "buildHistorySnapshot",
    "appendSnapshotToHistory",
    "loadSnapshotHistory",
    "recordCompleteScan",
    "getLatestComparison",
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
    check(os.path.isfile(HISTORY_TS), "existe src/utils/snapshot-history.ts")
    if not os.path.isfile(HISTORY_TS):
        return
    history = read(HISTORY_TS)
    history_code = re.sub(r"//[^\n]*|/\*.*?\*/", "", history, flags=re.S)
    for name in EXPORTED_FUNCTIONS:
        check(re.search(r"export function " + name + r"\b", history) is not None,
              "snapshot-history.ts exporta " + name)
    check("normalizeInstagramId" in history,
          "snapshot-history.ts reutiliza normalizeInstagramId")
    for forbidden in ("fetch(", "document.", "window."):
        check(forbidden not in history_code, "snapshot-history.ts no usa " + forbidden)

    constants = read(CONSTANTS_TS)
    check(re.search(r"SNAPSHOT_HISTORY_STORAGE_KEY\s*=", constants) is not None,
          "constants.ts define SNAPSHOT_HISTORY_STORAGE_KEY")

    state = read(STATE_TS)
    check("unfollowHistory" in state, "state.ts declara unfollowHistory en ScanningState")

    main = read(MAIN_TSX)
    check(re.search(r"import\s*\{[^}]*\brecordCompleteScan\b[^}]*\}\s*from\s*\"\./utils/snapshot-history\"",
                    main) is not None,
          "main.tsx importa recordCompleteScan")
    scan_block = extract_block(main, "const scan = async")
    save_index = scan_block.find("saveScanSnapshot(results)")
    record_index = scan_block.find("recordCompleteScan(")
    check(record_index != -1, "el efecto de escaneo llama a recordCompleteScan")
    check(save_index != -1 and record_index > save_index,
          "recordCompleteScan se llama despues de saveScanSnapshot (misma ruta de escaneo completo)")
    check('outcome: "complete"' in scan_block,
          "el escaneo completo se registra con outcome: \"complete\"")
    finish_incomplete_block = extract_block(main, "const finishIncomplete = (message: string)")
    check(finish_incomplete_block != "" and "recordCompleteScan(" not in finish_incomplete_block,
          "finishIncomplete no registra en el historial")
    initial_state_block = extract_block(main, "function _buildInitialState(): State", opener=": State {")
    check(initial_state_block != "" and "recordCompleteScan(" not in initial_state_block,
          "restaurar el ultimo escaneo no registra un snapshot nuevo")
    check(main.count("recordCompleteScan(") == 1,
          "recordCompleteScan se llama en un unico punto de main.tsx")

    searching = read(SEARCHING_TSX)
    check("Non-Followers" in searching, "Searching.tsx mantiene el filtro \"Non-Followers\" (no me sigue actualmente)")
    check("Unfollowed you since previous snapshot" in searching,
          "Searching.tsx muestra \"Unfollowed you since previous snapshot\"")
    check("youFollowThem" in searching, "Searching.tsx indica si todavia sigues a la cuenta")


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
  constructor() { this.store = {}; this.writes = 0; this.failWrites = false; }
  getItem(key) { return Object.prototype.hasOwnProperty.call(this.store, key) ? this.store[key] : null; }
  setItem(key, value) {
    if (this.failWrites) { throw new Error("QuotaExceededError"); }
    this.writes += 1;
    this.store[key] = String(value);
  }
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
    global.localStorage.failWrites = false;
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
  return { id, username, full_name: "Full " + username };
}

function scan(completedAt, followers, following, extra) {
  return Object.assign({ outcome: "complete", completedAt, ownerId: OWNER, followers, following }, extra || {});
}

function stored() {
  const raw = global.localStorage.getItem(KEY);
  return raw === null ? null : JSON.parse(raw);
}

// a) primer snapshot = baseline sin eventos
test("a) first complete snapshot is a baseline with no events", () => {
  const r = h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], [u("1", "alice")]), T1 + 5);
  assert.strictEqual(r.outcome, "baseline");
  assert.deepStrictEqual(r.newEvents, []);
  const data = stored();
  assert.strictEqual(data.schemaVersion, h.SNAPSHOT_HISTORY_SCHEMA_VERSION);
  assert.strictEqual(data.snapshots.length, 1);
  assert.strictEqual(data.events.length, 0);
  const snap = data.snapshots[0];
  assert.strictEqual(snap.schemaVersion, h.SNAPSHOT_HISTORY_SCHEMA_VERSION);
  assert.strictEqual(snap.completedAt, T1);
  assert.strictEqual(snap.ownerId, OWNER);
  assert.deepStrictEqual(snap.followers, ["1", "2"]);
  assert.deepStrictEqual(snap.following, ["1"]);
  assert.deepStrictEqual(snap.usernames, { "1": "alice", "2": "bob" });
  assert.strictEqual(typeof snap.id, "string");
  const cmp = h.getLatestComparison(h.loadSnapshotHistory(), OWNER);
  assert.strictEqual(cmp.kind, "baseline");
  assert.strictEqual(cmp.currentCompletedAt, T1);
});

// b) A en followers(T1) y ausente en followers(T2) = exactamente un evento nuevo
test("b) follower present at T1 and missing at T2 yields exactly one event with T1/T2", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const r = h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2 + 7);
  assert.strictEqual(r.outcome, "appended");
  assert.strictEqual(r.newEvents.length, 1);
  const ev = r.newEvents[0];
  const data = stored();
  assert.strictEqual(data.events.length, 1);
  assert.deepStrictEqual(data.events[0], ev);
  assert.strictEqual(ev.userId, "1");
  assert.strictEqual(ev.username, "alice");
  assert.strictEqual(ev.previousSnapshotId, data.snapshots[0].id);
  assert.strictEqual(ev.currentSnapshotId, data.snapshots[1].id);
  assert.strictEqual(ev.previousCompletedAt, T1);
  assert.strictEqual(ev.currentCompletedAt, T2);
  assert.strictEqual(ev.detectedAt, T2 + 7);
  assert.strictEqual(ev.notifiedAt, null);
  const cmp = h.getLatestComparison(h.loadSnapshotHistory(), OWNER);
  assert.strictEqual(cmp.kind, "compared");
  assert.strictEqual(cmp.previousCompletedAt, T1);
  assert.strictEqual(cmp.currentCompletedAt, T2);
  assert.strictEqual(cmp.events.length, 1);
  assert.strictEqual(cmp.events[0].userId, "1");
});

// c) una cuenta ausente ya en T1 no se atribuye como unfollow
test("c) account already missing from followers at T1 is never an unfollow", () => {
  // "3" is followed by me but never followed me back: "not following me", not an unfollow.
  h.recordCompleteScan(scan(T1, [u("2", "bob")], [u("3", "carol")]), T1);
  const r = h.recordCompleteScan(scan(T2, [u("2", "bob")], [u("3", "carol")]), T2);
  assert.strictEqual(r.outcome, "appended");
  assert.deepStrictEqual(r.newEvents, []);
  const r3 = h.recordCompleteScan(scan(T3, [u("8", "frank")], [u("3", "carol")]), T3);
  // bob (present at T2) left -> one event; carol (absent at T1 and T2) never counts.
  assert.deepStrictEqual(r3.newEvents.map(e => e.userId), ["2"]);
  assert.strictEqual(stored().events.length, 1);
});

test("c2) only accounts present in the immediately previous snapshot are attributed", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
  const r = h.recordCompleteScan(scan(T3, [u("5", "eve")], []), T3);
  // alice already left between T1 and T2: only bob is new between T2 and T3.
  assert.deepStrictEqual(r.newEvents.map(e => e.userId), ["2"]);
  assert.strictEqual(r.newEvents[0].previousCompletedAt, T2);
  assert.strictEqual(r.newEvents[0].currentCompletedAt, T3);
  assert.strictEqual(stored().events.length, 2);
});

// d) snapshots incompletos / fallidos / abortados / schema incompatible
test("d) incomplete, failed and aborted scans are rejected without touching history", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const before = global.localStorage.getItem(KEY);
  for (const outcome of ["incomplete", "failed", "aborted", undefined, "COMPLETE"]) {
    const r = h.recordCompleteScan(scan(T2, [u("2", "bob")], [], { outcome }), T2);
    assert.strictEqual(r.outcome, "rejected", String(outcome));
    assert.deepStrictEqual(r.newEvents, []);
  }
  assert.strictEqual(global.localStorage.getItem(KEY), before);
  assert.strictEqual(global.localStorage.writes, 1);
});

test("d2) scans without followers data, invalid ids, owner or timestamp are rejected", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice")], []), T1);
  const before = global.localStorage.getItem(KEY);
  const bad = [
    scan(T2, undefined, []),
    scan(T2, null, []),
    scan(T2, [u("1", "alice")], undefined),
    scan(T2, [u("abc", "broken")], []),
    scan(T2, [u(undefined, "noid")], []),
    scan(T2, [u("1", "alice")], [], { ownerId: null }),
    scan(T2, [u("1", "alice")], [], { ownerId: "not-a-number" }),
    scan(NaN, [u("1", "alice")], []),
    scan(0, [u("1", "alice")], []),
  ];
  for (const input of bad) {
    const r = h.recordCompleteScan(input, T2);
    assert.strictEqual(r.outcome, "rejected", JSON.stringify(input));
    assert.deepStrictEqual(r.newEvents, []);
  }
  assert.strictEqual(global.localStorage.getItem(KEY), before);
});

test("d3) snapshot with incompatible schemaVersion is rejected by the pure append", () => {
  const first = h.buildHistorySnapshot(scan(T1, [u("1", "alice")], []));
  assert.strictEqual(first.ok, true);
  const base = h.appendSnapshotToHistory(h.createEmptyHistory(), first.snapshot, T1);
  const second = h.buildHistorySnapshot(scan(T2, [], []));
  const incompatible = Object.assign({}, second.snapshot, { schemaVersion: 2 });
  const r = h.appendSnapshotToHistory(base.history, incompatible, T2);
  assert.strictEqual(r.outcome, "rejected");
  assert.deepStrictEqual(r.newEvents, []);
  assert.strictEqual(r.history, base.history);
  const tampered = Object.assign({}, second.snapshot, { followers: ["1"] });
  assert.strictEqual(h.appendSnapshotToHistory(base.history, tampered, T2).outcome, "rejected",
    "a snapshot whose id does not match its content is rejected");
});

test("d4) older or same-timestamp snapshot is rejected instead of reordered", () => {
  h.recordCompleteScan(scan(T2, [u("1", "alice")], []), T2);
  const before = global.localStorage.getItem(KEY);
  const older = h.recordCompleteScan(scan(T1, [u("2", "bob")], []), T3);
  assert.strictEqual(older.outcome, "rejected");
  const same = h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T3);
  assert.strictEqual(same.outcome, "rejected");
  assert.strictEqual(global.localStorage.getItem(KEY), before);
});

test("d5) empty followers after a non-empty snapshot is rejected (suspicious data-empty response)", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const before = global.localStorage.getItem(KEY);
  const r = h.recordCompleteScan(scan(T2, [], []), T2);
  assert.strictEqual(r.outcome, "rejected");
  assert.deepStrictEqual(r.newEvents, []);
  assert.strictEqual(global.localStorage.getItem(KEY), before);
});

test("d6) a failed storage write reports no events and leaves previous history intact", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice")], []), T1);
  const before = global.localStorage.getItem(KEY);
  global.localStorage.failWrites = true;
  const r = h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
  assert.strictEqual(r.outcome, "rejected");
  assert.deepStrictEqual(r.newEvents, []);
  assert.strictEqual(global.localStorage.getItem(KEY), before);
});

// e) mismo ID con username cambiado no genera evento
test("e) same id with a changed username is not an unfollow; events are keyed by id", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const r = h.recordCompleteScan(scan(T2, [u(" 1 ", "alice_renamed"), u(2, "bob")], []), T2);
  assert.strictEqual(r.outcome, "appended");
  assert.deepStrictEqual(r.newEvents, []);
  // A different id reusing an old username IS a different account.
  const r3 = h.recordCompleteScan(scan(T3, [u("1", "alice_renamed"), u("7", "bob")], []), T3);
  assert.strictEqual(r3.newEvents.length, 1);
  assert.strictEqual(r3.newEvents[0].userId, "2");
  assert.strictEqual(r3.newEvents[0].username, "bob");
});

// f) reprocesar / restaurar no duplica snapshots ni eventos
test("f) reprocessing the same scan is a no-op: no duplicate snapshots or events", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const input = scan(T2, [u("2", "bob")], []);
  const first = h.recordCompleteScan(input, T2);
  assert.strictEqual(first.newEvents.length, 1);
  const afterFirst = global.localStorage.getItem(KEY);
  const writes = global.localStorage.writes;
  const again = h.recordCompleteScan(input, T3);
  assert.strictEqual(again.outcome, "duplicate");
  assert.deepStrictEqual(again.newEvents, []);
  assert.strictEqual(global.localStorage.getItem(KEY), afterFirst);
  assert.strictEqual(global.localStorage.writes, writes, "duplicate must not write");
  const data = stored();
  assert.strictEqual(data.snapshots.length, 2);
  assert.strictEqual(data.events.length, 1);
});

test("f2) re-appending an already stored snapshot through the pure API does not duplicate events", () => {
  const s1 = h.buildHistorySnapshot(scan(T1, [u("1", "alice")], [])).snapshot;
  const s2 = h.buildHistorySnapshot(scan(T2, [], [])).snapshot;
  const s2b = h.buildHistorySnapshot(scan(T2, [u("3", "x")], [])).snapshot;
  let hist = h.appendSnapshotToHistory(h.createEmptyHistory(), s1, T1).history;
  const s2ok = h.buildHistorySnapshot(scan(T2, [u("4", "d")], [])).snapshot;
  const r = h.appendSnapshotToHistory(hist, s2ok, T2);
  assert.strictEqual(r.newEvents.length, 1);
  hist = r.history;
  const again = h.appendSnapshotToHistory(hist, s2ok, T3);
  assert.strictEqual(again.outcome, "duplicate");
  assert.strictEqual(again.history, hist);
  assert.strictEqual(again.history.events.length, 1);
  assert.notStrictEqual(s2.id, s2b.id, "snapshot id depends on content");
  assert.strictEqual(s2ok.id, h.buildHistorySnapshot(scan(T2, [u("4", "renamed")], [])).snapshot.id,
    "snapshot id is deterministic and independent of usernames");
});

test("f3) snapshot retention is bounded and pruning never recreates events", () => {
  let t = T1;
  const followersAt = i => (i % 2 === 0 ? [u("1", "alice"), u("2", "bob")] : [u("2", "bob")]);
  let total = 0;
  for (let i = 0; i < h.MAX_HISTORY_SNAPSHOTS + 3; i++) {
    t += 1000;
    total += h.recordCompleteScan(scan(t, followersAt(i), []), t).newEvents.length;
  }
  const data = stored();
  assert.strictEqual(data.snapshots.length, h.MAX_HISTORY_SNAPSHOTS);
  assert.strictEqual(data.events.length, total);
  const ids = new Set(data.events.map(e => e.id));
  assert.strictEqual(ids.size, data.events.length);
});

test("f4) history is kept under a size budget by pruning oldest snapshots, never the new reference", () => {
  const many = n => { const list = []; for (let i = 1; i <= n; i++) list.push(u(String(1000000 + i), "user_" + i)); return list; };
  const perSnapshot = JSON.stringify(h.buildHistorySnapshot(scan(T1, many(4000), [])).snapshot).length;
  const count = Math.floor(h.MAX_HISTORY_CHARS / perSnapshot) + 2;
  let t = T1;
  for (let i = 0; i < count; i++) {
    t += 1000;
    const r = h.recordCompleteScan(scan(t, many(4000), []), t);
    assert.notStrictEqual(r.outcome, "rejected", r.reason);
  }
  const raw = global.localStorage.getItem(KEY);
  assert.ok(raw.length <= h.MAX_HISTORY_CHARS, "stored history exceeds the budget");
  const data = JSON.parse(raw);
  assert.ok(data.snapshots.length < count);
  assert.strictEqual(data.snapshots[data.snapshots.length - 1].completedAt, t, "latest snapshot must be kept");
});

test("f5) a snapshot that alone exceeds the budget is rejected without writing", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice")], []), T1);
  const before = global.localStorage.getItem(KEY);
  const huge = [];
  const longName = new Array(200).join("x");
  const n = Math.ceil(h.MAX_HISTORY_CHARS / 200) + 10;
  for (let i = 1; i <= n; i++) huge.push(u(String(1000000 + i), longName + i));
  const r = h.recordCompleteScan(scan(T2, huge, []), T2);
  assert.strictEqual(r.outcome, "rejected");
  assert.strictEqual(r.reason, "history_too_large");
  assert.deepStrictEqual(r.newEvents, []);
  assert.strictEqual(global.localStorage.getItem(KEY), before);
});

// g) almacenamiento corrupto / incompatible falla de forma segura
test("g) corrupt JSON, invalid shape or incompatible version fail safely and are never overwritten", () => {
  const cases = [
    "{not json",
    JSON.stringify([]),
    JSON.stringify({ schemaVersion: 1, snapshots: "x", events: [] }),
    JSON.stringify({ schemaVersion: 1, snapshots: [{ id: 1 }], events: [] }),
    JSON.stringify({ schemaVersion: 1, snapshots: [], events: [{ id: "e" }] }),
    JSON.stringify({ schemaVersion: 999, snapshots: [], events: [] }),
  ];
  for (const raw of cases) {
    global.localStorage.store = {};
    global.localStorage.writes = 0;
    global.localStorage.setItem(KEY, raw);
    global.localStorage.writes = 0;
    const loaded = h.loadSnapshotHistory();
    assert.strictEqual(loaded.status, "unreadable", raw);
    assert.strictEqual(h.getLatestComparison(loaded, OWNER).kind, "unavailable");
    const r = h.recordCompleteScan(scan(T2, [u("2", "bob")], []), T2);
    assert.strictEqual(r.outcome, "rejected", raw);
    assert.deepStrictEqual(r.newEvents, []);
    assert.strictEqual(global.localStorage.getItem(KEY), raw, "raw data must be preserved: " + raw);
    assert.strictEqual(global.localStorage.writes, 0);
  }
});

test("g2) missing history loads as empty without writing", () => {
  const loaded = h.loadSnapshotHistory();
  assert.strictEqual(loaded.status, "empty");
  assert.strictEqual(loaded.history.snapshots.length, 0);
  assert.strictEqual(h.getLatestComparison(loaded, OWNER).kind, "none");
  assert.strictEqual(global.localStorage.writes, 0);
});

test("g3) a stored snapshot tampered after writing makes the whole store unreadable", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice")], []), T1);
  const data = stored();
  data.snapshots[0].followers = ["1", "2"];
  const raw = JSON.stringify(data);
  global.localStorage.store[KEY] = raw;
  assert.strictEqual(h.loadSnapshotHistory().status, "unreadable");
  const r = h.recordCompleteScan(scan(T2, [], []), T2);
  assert.strictEqual(r.outcome, "rejected");
  assert.strictEqual(global.localStorage.getItem(KEY), raw);
});

// h) youFollowThem refleja following del snapshot actual
test("h) youFollowThem reflects following in the current snapshot", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob"), u("3", "carol")], [u("1", "alice"), u("2", "bob")]), T1);
  // I still follow alice; I stopped following bob; I never followed carol.
  const r = h.recordCompleteScan(scan(T2, [u("4", "dan")], [u("1", "alice")]), T2);
  const byId = {};
  r.newEvents.forEach(e => { byId[e.userId] = e; });
  assert.deepStrictEqual(Object.keys(byId).sort(), ["1", "2", "3"]);
  assert.strictEqual(byId["1"].youFollowThem, true);
  assert.strictEqual(byId["2"].youFollowThem, false);
  assert.strictEqual(byId["3"].youFollowThem, false);
});

test("owner isolation: snapshots of a different account are never compared", () => {
  h.recordCompleteScan(scan(T1, [u("1", "alice"), u("2", "bob")], []), T1);
  const other = h.recordCompleteScan(scan(T2, [u("5", "eve")], [], { ownerId: "12345" }), T2);
  assert.strictEqual(other.outcome, "baseline");
  assert.deepStrictEqual(other.newEvents, []);
  assert.strictEqual(h.getLatestComparison(h.loadSnapshotHistory(), OWNER).kind, "baseline");
  assert.strictEqual(h.getLatestComparison(h.loadSnapshotHistory(), "12345").kind, "baseline");
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
    if not os.path.isfile(HISTORY_TS):
        check(False, "snapshot-history.ts debe existir para ejecutar las pruebas funcionales")
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
    print("FASE2 HISTORIAL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
