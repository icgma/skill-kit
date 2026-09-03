#!/usr/bin/env python3
"""One-ticket gate: validate -> (on FAIL) mechanical repair -> re-validate
-> only on PASS show the semantic diff.

    python scripts/verify.py out.docx --baseline work/baseline.docx
    python scripts/verify.py new.docx                # new artifact, no baseline

ONE scope rule, symmetric for checks and repairs (v3):
- WITH --baseline: checks report, and repair fixes, only what the edit
  INTRODUCED relative to the baseline -- pre-existing quirks (ordering,
  hand bullets, unpreserved spaces the author's rendering already
  dropped) stay exactly as they were (pre-existing issues untouched).
- WITHOUT --baseline: everything in the file is your product -- checks
  and repairs run in full (the old --gen behaviour, now automatic).

Replaces the two-command ritual (validate + read --diff) with one call,
and encodes the ordering the two-gate rule always implied: a diff of an
ILLEGAL file is not worth reading -- fix legality first.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import validate as V   # noqa: E402
import vdiff as VD     # noqa: E402

# Windows consoles/pipes default to the legacy ANSI code page (cp1252/cp936):
# the engine's CJK messages must never crash the gate there.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _validate(a) -> tuple:
    """Run validate.run() in-process -> (rc, the exact CLI stdout).
    In-process keeps one interpreter + one schema load for the run;
    per-run state lives in the Report (no module global to reset), and
    render() reproduces validate's stdout byte for byte -- no capture.
    SystemExit (bad zip, unreadable input) maps as the old captured-
    main path did: non-int code -> rc 2 with the message as output."""
    try:
        rep = V.run(a.src, baseline=a.baseline, redline=a.redline,
                    gen=not a.baseline,  # 无基线 = 全文是你的产出,lint 全开
                    max_errors=a.max_errors)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 2
        out = ("" if isinstance(e.code, int) or e.code is None
               else str(e.code) + "\n")
        return rc, out
    return rep.rc, rep.render() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="One-shot gate: validate (on failure, auto mechanical "
                    "repair then re-validate); semantic diff only on PASS")
    ap.add_argument("src", help="the edited .docx or an unpacked directory")
    ap.add_argument("--baseline",
                    help="pre-edit reference (prep's baseline.docx); given = "
                         "edit gate: checks/repairs apply only to what changed "
                         "relative to it, and a diff is shown; omitted = "
                         "new-file gate: the whole file is your product, "
                         "fully checked and repaired")
    ap.add_argument("--redline", action="store_true",
                    help="pass through to validate --redline (revision "
                         "integrity check)")
    ap.add_argument("--no-repair", action="store_true",
                    help="look but do not fix")
    ap.add_argument("--full", action="store_true",
                    help="do not truncate the diff (passes through to "
                         "vdiff --full)")
    ap.add_argument("--max-errors", type=int, default=20)
    a = ap.parse_args()

    # 参数与输入预检:用法错误要在任何修复动作之前拦死(review:
    # argparse 错误曾走进"首验未过 → 自动修复",一个打错的旗标就
    # 改写了文件)
    if a.redline and not a.baseline:
        ap.error("--redline requires --baseline")
    if not Path(a.src).exists():
        raise SystemExit(f"E_INPUT: {a.src} does not exist")
    if a.baseline and not Path(a.baseline).exists():
        raise SystemExit(f"E_INPUT: baseline {a.baseline} does not "
                         "exist | try: prep's work/baseline.docx")

    def _repair() -> int:
        import zipfile as _zf
        try:
            # gen=True 恒定:xml:space 这类"对自己的内容才确定"的修复,
            # 有基线时被 baseline 预算限定在你新增的部分,无基线时全文
            # 都是你的——两种情况它都安全(检查与修复同一作用域)
            k, log = V.repair(Path(a.src), gen=True,
                              baseline=Path(a.baseline) if a.baseline
                              else None)
        except _zf.BadZipFile as e:
            raise SystemExit(f"E_INPUT: {a.src} is not a zip/docx | {e}")
        if k:
            for (part, what), c_ in sorted(log.items()):
                print(f"  · {part}: {what} ×{c_}")
        return k

    # 修复一律前置:有基线时 _defect_budget 把作用域限定在"你新增的
    # 部分",原件自带的毛病一件不碰——旧版"FAIL 才修"是无作用域时代
    # 的保真妥协,作用域化之后,warn 级的自伤(新增内容缺 xml:space)
    # 也该在过门时顺手修掉,而不是 PASS 就放行
    repaired = 0
    if not a.baseline:
        print("no baseline: treating the whole file as a new artifact — "
              "all checks and repairs apply to your own output. If this "
              "is an edit of an existing document, pass --baseline "
              "(usually prep's work/baseline.docx)")
    if not a.no_repair:
        try:
            repaired = _repair()
        except SystemExit as e:
            print(f"auto-repair failed: {e.code}; gate not passed, no diff")
            return 1
        if repaired:
            print(f"applied {repaired} mechanical repair(s) (listed above; "
                  + ("only what the edit introduced was fixed; pre-existing "
                     "issues left untouched"
                     if a.baseline else "the whole file is treated as your "
                     "product")
                  + ")")
    rc, out = _validate(a)
    if rc not in (0, 1):
        # rc==1 才是"文档有违规";其余(argparse=2、not a zip 等)是
        # 用法/环境错误——不谈"内容性违规",原样转达
        print(out, end="" if out.endswith("\n") else "\n")
        print("(argument or input problem, not a document violation; "
              "no diff)")
        return rc if isinstance(rc, int) and rc else 2
    if rc != 0:
        # FAIL 终态:violations 已在 out 里,不出 diff——非法文件的
        # diff 不值得读,先修内容再重跑本命令
        print(out, end="" if out.endswith("\n") else "\n")
        if a.no_repair:
            print("gate not passed, no diff (--no-repair disabled "
                  "auto-repair)")
        else:
            print("mechanical repairs done; the remaining violations are "
                  "content-level — fix them and rerun this command; the "
                  "diff prints automatically once the gate passes")
        return 1

    print(out, end="" if out.endswith("\n") else "\n")
    if not a.baseline:
        print("(no --baseline: legality gate only; a new document has no "
              "'what changed' to check)")
        return 0
    print("── read --diff (what changed; check each line against your "
          "intent list: one extra is collateral damage, one missing means "
          "the edit did not land) ──")
    try:
        d = VD.vdiff(Path(a.baseline), Path(a.src), full=a.full)
    except (SystemExit, Exception) as e:  # noqa: BLE001
        # 门面契约:PASSED 已打印,不能再让一个 diff 侧异常以裸
        # traceback + exit 1 冒充"门没过"(review 实测:baseline 指错
        # 目录时如此)。说清哪一半完成了、下一步补哪半。
        msg = e.code if isinstance(e, SystemExit) else e
        print(f"legality gate PASSED, but diff generation failed: {msg}")
        print(f"acceptance incomplete: check that --baseline points to "
              f"the right file (expected: prep's work/baseline.docx), or "
              f"run it separately: "
              f"python scripts/read.py {a.baseline} --diff {a.src}")
        return 1
    print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
