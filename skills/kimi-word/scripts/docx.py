#!/usr/bin/env python3
"""Unified entry point for docx-js creation and DOCX validation/repair.

Usage:
  docx.py build <script.js> <output.docx>
  docx.py validate <file.docx>
  docx.py lint <script.js> [more.js ...]
  docx.py md2docx <file.md> [--citation citation.jsonl] [--style footnote|endnote|hyperlink] [--output-dir <dir>]
  docx.py check-docx [start-dir]

build runs: node --check -> docx-js lint -> node script.js output.docx -> auto-fix/validate.

validate delegates to the verify engine (scripts/verify/verify.py) in
no-baseline mode: the whole file is in scope, deterministic defects are
auto-repaired and listed, and the exit code is 0 on PASS / non-zero on
FAIL (or on usage/input errors).

md2docx converts Markdown with platform citation markers ([^123^]) plus a
companion citation.jsonl to .docx with real footnotes/endnotes. Standard
Markdown footnotes ([^id] + definition lines) or plain Markdown go to bare
pandoc instead — see references/md2docx.md.

The docx npm package must be resolvable from the build script's directory
upward (install it in the script's project directory: `npm install docx`).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent


def die(message, hint=None, code=1):
    print(f"Error: {message}", file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)
    sys.exit(code)


def need_cmd(name):
    if shutil.which(name) is None:
        die(f"Missing required command: {name}")


def run(cmd, **kwargs):
    return subprocess.run(cmd, **kwargs)


def run_lint(scripts):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return run([sys.executable, str(SCRIPT_DIR / "lint_docx_js.py"), *scripts], env=env).returncode


def run_validate(file):
    if not Path(file).is_file():
        die(f"DOCX not found: {file}")
    # Single validation engine: verify.py, no baseline = full gen mode
    # (checks and repairs cover the whole file). verify.py manages its
    # own sys.path and exits 0 on PASS / non-zero on FAIL.
    return run([sys.executable, str(SCRIPT_DIR / "verify" / "verify.py"), file]).returncode


def run_md2docx(md, rest):
    need_cmd("pandoc")
    if not md or not Path(md).is_file():
        die(f"Markdown not found: {md}")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPT_DIR / "md2docx") + os.pathsep + env.get("PYTHONPATH", "")
    sys.exit(run([sys.executable, str(SCRIPT_DIR / "md2docx" / "md2docx_convert.py"), md, *rest], env=env).returncode)


# ---------- docx npm package resolution ----------

def _strip_comments(source):
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    return re.sub(r"//.*$", "", source, flags=re.MULTILINE)


def script_looks_like_esm(path):
    src = _strip_comments(Path(path).read_text(encoding="utf-8", errors="replace"))
    return bool(re.search(
        r"\bimport\s+(?:[\s*{]|\w)|\bexport\s+(?:async\s+)?(?:function|class|const|let|var|default|\{)",
        src, flags=re.MULTILINE))


def script_uses_docx_package(path):
    src = _strip_comments(Path(path).read_text(encoding="utf-8", errors="replace"))
    return bool(re.search(
        r"from\s+[\"']docx[\"']|import\s*\([\"']docx[\"']\)|require\s*\(\s*[\"']docx[\"']\s*\)",
        src))


def package_type_is_module(start_dir):
    d = Path(start_dir).resolve()
    while True:
        pkg = d / "package.json"
        if pkg.is_file():
            try:
                return json.loads(pkg.read_text(encoding="utf-8")).get("type") == "module"
            except Exception:
                return False
        if d.parent == d:
            return False
        d = d.parent


def find_docx(start_dir):
    """Walk up from start_dir looking for node_modules/docx (Node's own rule)."""
    d = Path(start_dir).resolve()
    while True:
        if (d / "node_modules" / "docx" / "package.json").is_file():
            return d / "node_modules" / "docx"
        if d.parent == d:
            return None
        d = d.parent


def check_docx(start_dir="."):
    if not Path(start_dir).is_dir():
        print(f"MISSING docx npm package (start directory does not exist: {start_dir}).")
        return 1
    found = find_docx(start_dir)
    if found:
        print(f"FOUND docx: {found}")
        return 0
    script_dir = Path(start_dir).resolve()
    print("MISSING docx npm package.\n")
    print("Install it in the build script's project directory:")
    print(f'  cd "{script_dir}" && npm install docx\n')
    print("Node resolves bare imports from the script directory upward;")
    print("a global or unrelated install is not visible to the build.")
    return 1


# ---------- build ----------

def run_build(script, output):
    need_cmd("node")
    script_path = Path(script)
    if not script_path.is_file():
        die(f"Script not found: {script}")
    if not output:
        die("Usage: docx.py build <script.js> <output.docx>", code=2)

    script_dir = script_path.resolve().parent
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    exec_script = script_path
    temp_script = None
    if (script_path.suffix == ".js" and not package_type_is_module(script_dir)
            and script_looks_like_esm(script_path)):
        fd, tmp = tempfile.mkstemp(prefix=script_path.stem + ".docx-build.",
                                   suffix=".mjs", dir=script_dir)
        os.close(fd)
        shutil.copyfile(script_path, tmp)
        temp_script = Path(tmp)
        exec_script = temp_script

    try:
        print("▶ Syntax check")
        if run(["node", "--check", str(exec_script)]).returncode != 0:
            sys.exit(1)

        print("▶ docx-js lint")
        if run_lint([str(script_path)]) != 0:
            sys.exit(1)

        if script_uses_docx_package(script_path) and not find_docx(script_dir):
            die(
                f'Cannot find npm package "docx" for {script_dir}.',
                hint=f'Install it in the script\'s project directory:\n'
                     f'  cd "{script_dir}" && npm install docx\n'
                     f'Check again with: docx.py check-docx "{script_dir}"')

        print("▶ Generate")
        if run(["node", str(exec_script), str(output_path)]).returncode != 0:
            sys.exit(1)
        if not output_path.is_file():
            die(f"Generation finished but output was not written: {output_path}",
                hint="The script must write process.argv[2].")

        print("▶ Auto-fix and validate")
        if run_validate(str(output_path)) != 0:
            sys.exit(1)

        print(f"✓ Done: {output_path}")
    finally:
        if temp_script is not None:
            temp_script.unlink(missing_ok=True)


USAGE = __doc__


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("help", "-h", "--help"):
        print(USAGE)
        return 0 if args else 0
    cmd, rest = args[0], args[1:]
    if cmd == "build":
        if len(rest) != 2:
            print(USAGE)
            return 2
        run_build(rest[0], rest[1])
        return 0
    if cmd == "validate":
        if len(rest) != 1:
            print(USAGE)
            return 2
        return run_validate(rest[0])
    if cmd == "lint":
        if not rest:
            print(USAGE)
            return 2
        return run_lint(rest)
    if cmd == "md2docx":
        if not rest:
            print(USAGE)
            return 2
        run_md2docx(rest[0], rest[1:])
        return 0
    if cmd == "check-docx":
        return check_docx(rest[0] if rest else ".")
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
