#!/usr/bin/env python3
"""Fase 1: seguridad del escaneo y del unfollow manual.

Parte A (estática): comprueba en el código fuente que las protecciones están
cableadas. Parte B (funcional, opcional): transpila src/utils/unfollow-safety.ts
con TypeScript y ejecuta casos reales con node en un directorio temporal.

Sin red ni dependencias externas. Sale con código distinto de cero ante
cualquier fallo e imprime "FASE1 OK" si todo pasa.

La ruta de typescript.js se busca en node_modules/typescript/lib/typescript.js;
puede indicarse otra con la variable de entorno IU_TYPESCRIPT_JS.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAFETY_TS = os.path.join(ROOT, "src", "utils", "unfollow-safety.ts")
MAIN_TSX = os.path.join(ROOT, "src", "main.tsx")
SEARCHING_TSX = os.path.join(ROOT, "src", "components", "Searching.tsx")
UTILS_TS = os.path.join(ROOT, "src", "utils", "utils.ts")

EXPORTED_FUNCTIONS = [
    "normalizeInstagramId",
    "getRawUserId",
    "isWhitelistedId",
    "buildUnfollowConfirmationMessage",
    "classifyUnfollowResponse",
    "isBlockingHttpStatus",
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
    """Returns the arrow function starting at start_marker up to the end of its body."""
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
    print("# Parte A: comprobaciones estáticas")
    check(os.path.isfile(SAFETY_TS), "existe src/utils/unfollow-safety.ts")
    if not os.path.isfile(SAFETY_TS):
        return
    safety = read(SAFETY_TS)
    safety_code = re.sub(r"//[^\n]*|/\*.*?\*/", "", safety, flags=re.S)
    for name in EXPORTED_FUNCTIONS:
        check(re.search(r"export function " + name + r"\b", safety) is not None,
              "unfollow-safety.ts exporta " + name)
    check(re.search(r"export class InstagramHttpError\b", safety) is not None,
          "unfollow-safety.ts exporta la clase InstagramHttpError")
    for code in ("401", "403", "429"):
        check(code in safety, "unfollow-safety.ts contempla el estado " + code)
    for forbidden in ("document.", "window.", "fetch(", "localStorage"):
        check(forbidden not in safety_code, "unfollow-safety.ts no usa " + forbidden)

    main = read(MAIN_TSX)
    check(re.search(r"import\s*\{[^}]*\bclassifyUnfollowResponse\b[^}]*\}\s*from\s*\"\./utils/unfollow-safety\"",
                    main) is not None,
          "main.tsx importa classifyUnfollowResponse")
    unfollow_effect = extract_block(main, "const unfollow = async")
    check(unfollow_effect != "", "main.tsx contiene el efecto de unfollow")
    check("classifyUnfollowResponse(" in unfollow_effect,
          "el efecto de unfollow usa classifyUnfollowResponse")
    check("loadWhitelist()" in unfollow_effect,
          "el efecto de unfollow llama a loadWhitelist()")
    check("response.status" in unfollow_effect,
          "el efecto de unfollow lee response.status")
    check(re.search(r"await fetch\(.*?\);\s*setState\(.{0,400}?unfollowedSuccessfully:\s*true",
                    unfollow_effect, re.S) is None,
          "main.tsx ya no marca unfollowedSuccessfully: true justo después del fetch")
    check("unfollowedSuccessfully: true" not in unfollow_effect,
          "el efecto de unfollow no contiene ningún unfollowedSuccessfully: true literal")
    fetch_list = extract_block(main, "const fetchList = async")
    check("Array.isArray(page.users)" in fetch_list,
          "fetchList no da por buena una página sin lista de usuarios")
    check('throw new Error("csrftoken cookie is null")' not in main,
          "main.tsx ya no lanza una excepción silenciosa si falta csrftoken")

    searching = read(SEARCHING_TSX)
    check("buildUnfollowConfirmationMessage(" in searching,
          "Searching.tsx usa buildUnfollowConfirmationMessage")
    check("Are you sure?" not in searching, "Searching.tsx ya no contiene \"Are you sure?\"")
    check("selectedResults.indexOf(user)" not in searching,
          "Searching.tsx ya no compara la selección por referencia")

    utils = read(UTILS_TS)
    check("getRawUserId(" in utils, "utils.ts usa getRawUserId")
    check("String(raw.pk_id ?? raw.pk)" not in utils,
          "utils.ts ya no usa String(pk_id ?? pk) sin validar")
    check("InstagramHttpError(" in utils, "utils.ts lanza InstagramHttpError")
    check("pk_id ?? " not in main, "main.tsx ya no construye IDs con pk_id ?? pk")


NODE_SCRIPT = r"""
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const ts = require(process.env.TS_PATH);

const source = fs.readFileSync(process.env.SAFETY_TS, "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES5, lib: ["es2015"] },
});
const outFile = path.join(process.env.WORK_DIR, "unfollow-safety.js");
fs.writeFileSync(outFile, output.outputText);
const s = require(outFile);

const results = [];
function test(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (e) {
    results.push({ name, ok: false, error: String(e && e.message || e) });
  }
}

test("normalizeInstagramId(123)", () => assert.strictEqual(s.normalizeInstagramId(123), "123"));
test("normalizeInstagramId(' 456 ')", () => assert.strictEqual(s.normalizeInstagramId(" 456 "), "456"));
test("normalizeInstagramId('abc')", () => assert.strictEqual(s.normalizeInstagramId("abc"), null));
test("normalizeInstagramId('')", () => assert.strictEqual(s.normalizeInstagramId(""), null));
test("normalizeInstagramId(undefined)", () => assert.strictEqual(s.normalizeInstagramId(undefined), null));
test("normalizeInstagramId(null)", () => assert.strictEqual(s.normalizeInstagramId(null), null));
test("normalizeInstagramId('undefined')", () => assert.strictEqual(s.normalizeInstagramId("undefined"), null));
test("normalizeInstagramId('null')", () => assert.strictEqual(s.normalizeInstagramId("null"), null));
test("normalizeInstagramId(12.5)", () => assert.strictEqual(s.normalizeInstagramId(12.5), null));

test("getRawUserId prefiere pk_id", () => assert.strictEqual(s.getRawUserId({ pk_id: "111", pk: 222 }), "111"));
test("getRawUserId cae a pk si pk_id falta", () => assert.strictEqual(s.getRawUserId({ pk: 222 }), "222"));
test("getRawUserId cae a pk si pk_id no es válido", () =>
  assert.strictEqual(s.getRawUserId({ pk_id: "undefined", pk: "333" }), "333"));
test("getRawUserId null si ninguno es válido", () =>
  assert.strictEqual(s.getRawUserId({ pk_id: "", pk: undefined }), null));

function expectKind(status, body, kind) {
  const outcome = s.classifyUnfollowResponse(status, body);
  assert.strictEqual(outcome.kind, kind, JSON.stringify(outcome));
  assert.ok(typeof outcome.reason === "string" && outcome.reason.length > 0, "reason vacío");
  return outcome;
}
test("200 + status ok -> success", () => expectKind(200, { status: "ok" }, "success"));
test("200 + status fail -> failed", () => expectKind(200, { status: "fail" }, "failed"));
test("200 + body no JSON -> failed", () => expectKind(200, null, "failed"));
test("200 + body texto -> failed", () => expectKind(200, "<html>", "failed"));
test("401 -> stop", () => assert.ok(/401/.test(expectKind(401, null, "stop").reason)));
test("403 -> stop", () => assert.ok(/403/.test(expectKind(403, { status: "fail" }, "stop").reason)));
test("429 -> stop", () => assert.ok(/429/.test(expectKind(429, null, "stop").reason)));
test("500 -> failed con código", () => assert.ok(/500/.test(expectKind(500, { status: "ok" }, "failed").reason)));
test("isBlockingHttpStatus", () => {
  assert.deepStrictEqual([401, 403, 429].map(s.isBlockingHttpStatus), [true, true, true]);
  assert.deepStrictEqual([200, 400, 404, 500].map(s.isBlockingHttpStatus), [false, false, false, false]);
});

test("isWhitelistedId numérico frente a cadena", () => {
  assert.strictEqual(s.isWhitelistedId(123, [{ id: "123" }]), true);
  assert.strictEqual(s.isWhitelistedId(" 123 ", [{ id: 123 }]), true);
  assert.strictEqual(s.isWhitelistedId(124, [{ id: "123" }]), false);
  assert.strictEqual(s.isWhitelistedId("undefined", [{ id: "undefined" }]), false);
});

test("mensaje de confirmación con total y usernames", () => {
  const message = s.buildUnfollowConfirmationMessage([{ username: "sample_one" }, { username: "sample_two" }]);
  assert.ok(message.includes("2"), message);
  assert.ok(message.includes("sample_one") && message.includes("sample_two"), message);
});
test("mensaje de confirmación limitado a 50 usernames", () => {
  const users = [];
  for (let i = 1; i <= 53; i++) users.push({ username: "user" + i + "x" });
  const message = s.buildUnfollowConfirmationMessage(users);
  assert.ok(message.includes("53"), message);
  assert.ok(message.includes("user50x") && !message.includes("user51x"), "no corta en 50");
  assert.ok(/3 more/.test(message), "no indica las restantes");
});

test("InstagramHttpError conserva status e instanceof", () => {
  const error = new s.InstagramHttpError(429, "x");
  assert.ok(error instanceof s.InstagramHttpError && error instanceof Error);
  assert.strictEqual(error.status, 429);
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
        print("FUNCIONAL OMITIDO: node no está disponible en el PATH")
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
            env = dict(os.environ, TS_PATH=typescript_js, SAFETY_TS=SAFETY_TS, WORK_DIR=work_dir)
            completed = subprocess.run([node, script], cwd=work_dir, env=env,
                                       capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        print("FUNCIONAL OMITIDO: no se pudo ejecutar node (" + str(error) + ")")
        return
    if completed.returncode != 0:
        check(False, "node terminó con código " + str(completed.returncode) + ": " + completed.stderr.strip())
        return
    try:
        results = json.loads(completed.stdout)
    except ValueError:
        check(False, "salida de node no es JSON: " + completed.stdout[:500])
        return
    check(len(results) > 0, "node ejecutó casos funcionales")
    for result in results:
        check(result["ok"], result["name"] + ("" if result["ok"] else ": " + result.get("error", "")))


def main():
    static_checks()
    functional_checks()
    if failures:
        print("\n%d comprobación(es) fallida(s)" % len(failures))
        return 1
    print("FASE1 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
