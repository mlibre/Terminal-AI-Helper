"""tai tune — grid-search ranker weights on your history. Stdlib only.

Leave-future-out: train on first 80%, eval next-command + prefix tasks
on last 20%. Optimizes 0.7*prefix_r1 + 0.3*seq_r1.
Writes back best weights into tai/engine.py (W_* constants).
"""
from __future__ import annotations

import os
import pathlib
import random
import re


def _eval(eng, ev_rows, on_path=None) -> tuple[float, dict]:
    r1 = r3 = s1 = s3 = 0
    n = 0
    for i, r in enumerate(ev_rows):
        cmd = (r[0] or "").strip()
        if not cmd or len(cmd) < 3:
            continue
        cut = max(1, len(cmd) // 2)
        sp = cmd.rfind(" ", 0, cut + 4)
        if sp > 1:
            cut = sp + 1
        prev = (ev_rows[i - 1][0] or "").strip() if i > 0 else ""
        res = eng.suggest(prefix=cmd[:cut], cwd=r[1] or "", repo=r[2] or "",
                          branch=r[3] or "", last_commands=[prev] if prev else [],
                          limit=3, on_path=on_path)
        cands = [c["cmd"] for c in res.get("choices", [])]
        n += 1
        if cands[:1] == [cmd]:
            r1 += 1
        if cmd in cands[:3]:
            r3 += 1
        res2 = eng.suggest(prefix="", last_commands=[prev] if prev else [],
                           limit=3, on_path=on_path)
        c2 = [c["cmd"] for c in res2.get("choices", [])]
        if c2[:1] == [cmd]:
            s1 += 1
        if cmd in c2[:3]:
            s3 += 1
    if not n:
        return 0.0, {}
    m = {"prefix_r1": r1 / n, "prefix_r3": r3 / n, "seq_r1": s1 / n,
         "seq_r3": s3 / n, "n": n}
    return 0.7 * m["prefix_r1"] + 0.3 * m["seq_r1"], m


def main(limit: int = 10000, trials: int = 60) -> None:
    import tai.engine as E
    from tai.store import load_rows

    try:
        rows = load_rows(limit)
    except Exception as e:
        from tai.store import schema_note
        print(f"cannot read the history database: {schema_note() or e}")
        return
    if len(rows) < 200:
        print(f"not enough history ({len(rows)} rows, need 200+)")
        return
    cut = int(len(rows) * 0.8)
    train, ev = rows[:cut], rows[cut:]
    ev = ev[:: max(1, len(ev) // 400)][:400]
    print(f"train={len(train)} eval={len(ev)}")

    def build(weights: dict):
        for k, v in weights.items():
            setattr(E, k, v)
        eng = E.Engine()
        eng.build_from_rows(train)
        return eng

    base = {k: getattr(E, k) for k in
            ["W_FREQ", "W_RECENCY", "W_CWD", "W_REPO", "W_BRANCH",
             "W_SUCCESS", "W_INSTALLED", "W_SEQ", "W_HOUR", "W_TOKEN"]}
    # The installed question, answered once for the whole run: every first word
    # the history carries, resolved against PATH here rather than inside the
    # engine (which never touches the filesystem). Without it a tuned
    # W_INSTALLED would be tuned against a signal that was never on.
    import shutil
    _words = set()
    for r in rows:
        c = (r[0] or "").strip()
        if c:
            _words.add(c.split(" ", 1)[0])
    _on_path = {w for w in _words if shutil.which(w)}.__contains__
    best = dict(base)
    score, m = _eval(build(best), ev, on_path=_on_path)
    print(f"base score={score:.3f} { {k: round(v,3) for k,v in m.items() if k!='n'} }")
    rng = random.Random(7)
    # coordinate search: perturb one weight at a time
    keys = list(base.keys())
    for t in range(trials):
        k = keys[t % len(keys)]
        cand = dict(best)
        factor = rng.choice([0.5, 0.7, 1.3, 1.6])
        cand[k] = round(max(0.0, best[k] * factor), 3)
        score_c, _ = _eval(build(cand), ev, on_path=_on_path)
        if score_c > score:
            score, best = score_c, cand
            print(f"  [{t}] {k}={cand[k]} -> {score:.3f}")
    _, m = _eval(build(best), ev, on_path=_on_path)
    print(f"best score={score:.3f} { {k: round(v,3) for k,v in m.items() if k!='n'} }")
    print("weights:", best)
    # Written back through a temporary file, for the reason install.sh edits an
    # rc file that way: this is the source every other module is imported from,
    # and a truncate that fails halfway leaves a checkout that cannot start.
    p = pathlib.Path(__file__).with_name("engine.py")
    src = p.read_text()
    # …and only when every weight was actually found. `re.sub` reports no match
    # as success: an annotation on the constant (`W_RECENCY: float = 2.0`), a tab,
    # or a name the writer and the source spell differently each replace nothing,
    # and the run used to print `wrote …/engine.py` after minutes of search with
    # the ranker exactly as it was. A tune that changed nothing is a tune that
    # rewrote the file and said it worked, so every key must match exactly once
    # or nothing is written and the miss is named.
    missed = []
    for k, v in best.items():
        new, hits = re.subn(rf"^{re.escape(k)} = .*$", f"{k} = {v}", src, flags=re.M)
        if hits != 1:
            missed.append(f"{k} ({hits} matching lines)")
            continue
        src = new
    if missed:
        print(f"tai: not written — no single line to replace for: {', '.join(missed)}")
        print(f"     {p} is unchanged; set the W_* constants there by hand")
        raise SystemExit(1)
    # A weight at zero is not a smaller number, it is a signal switched off, and
    # it is switched off in the source — so say it, while it can still be undone
    # with `git diff`. `base` is the shipped dict and is never mutated (each trial
    # copies it), so it is what "changed" is measured against: the module's own
    # attributes have been overwritten by `setattr` long before this point.
    off = [k for k, v in best.items() if v == 0.0 and base[k] != 0.0]
    if off:
        print(f"tai: these signals are now off: {', '.join(off)}")
    changed = [k for k, v in best.items() if v != base[k]]
    if not changed:
        # Nothing to say and nothing to write: touching the file would dirty a
        # checkout for a result identical to the one it already had.
        print(f"tai: no weight changed, {p} left alone")
        return
    tmp = p.with_suffix(".py.tmp")
    tmp.write_text(src)
    os.replace(tmp, p)
    print(f"wrote {p} ({len(changed)} of {len(best)} weights changed: "
          f"{', '.join(changed) or 'none'})")
