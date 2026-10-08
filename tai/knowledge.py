"""CLI knowledge discovery for tools not present in shell history.

This module is deliberately conservative: it only runs read-only discovery
commands (`--help`, `help`, `man`) and never executes a suggested command.
It produces candidate vocabulary for the embedded autocomplete index.

The text-munging half of it — turning help output into verbs and flags —
lives in `tai.helptext`, which is where the conservative parsing rules live.
"""
from __future__ import annotations

import os
import time

from tai.helptext import (DISCOVER_WORKERS, MAX_HELP_BYTES, TOOL_TIMEOUT,
                          TOOL_TIMEOUT_SLOW, _NO_HELP, _complete_candidates,
                          _declared, _extract, _run)

# This module is loaded by `tai record` — once per command the user runs, just
# to answer "has this tool been learned?" — so its module level stays cheap:
# json, shutil, tempfile and subprocess are imported inside the functions that
# use them, and ToolKnowledge is a plain class rather than a dataclass, whose
# machinery (`inspect`, and the futures some of its users pull in) cost more
# than the one question the record path asks of this file.


class ToolKnowledge:
    """What one installed tool says about itself, read-only."""

    __slots__ = ("name", "path", "version", "help_text", "verbs", "flags",
                 "subs", "subverbs", "updated")

    def __init__(self, name: str, path: str = "", version: str = "",
                 help_text: str = "", verbs: list | None = None,
                 flags: list | None = None,
                 subs: dict | None = None,
                 subverbs: dict | None = None, updated: int = 0):
        self.name = name
        self.path = path
        self.version = version
        self.help_text = help_text
        self.verbs = verbs if verbs is not None else []
        self.flags = flags if flags is not None else []
        # Subcommand → the flags that subcommand accepts. Keyed rather than flattened
        # because the position is the whole point: `docker run --blkio-weight` is a
        # command docker rejects, and a single flat flag list for the tool is how it
        # would end up suggested. Empty for every tool that does not answer for
        # itself, which is nearly all of them.
        self.subs = subs if subs is not None else {}
        # Subcommand → the subcommands *it* has. One level only, because that is the
        # depth `gh pr checkout` and `gh repo view` live at and the depth where the
        # forks stop being worth what they answer: every extra level doubles the
        # probes and the index entries, and the third level of `gh` is a flag anyway.
        self.subverbs = subverbs if subverbs is not None else {}
        self.updated = updated

    def as_dict(self) -> dict:
        return {
            "name": self.name, "path": self.path, "version": self.version,
            "help_text": self.help_text, "verbs": self.verbs,
            "flags": self.flags, "subs": self.subs,
            "subverbs": self.subverbs, "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ToolKnowledge":
        # `subs` is read through `or {}` because a row written before these fields
        # existed has no key for them, and `d.get("subs")` on such a row is None
        # rather than {}.
        return cls(
            name=d.get("name", ""), path=d.get("path", ""),
            version=d.get("version", ""), help_text=d.get("help_text", ""),
            verbs=list(d.get("verbs", [])), flags=list(d.get("flags", [])),
            subs={str(k): list(v) for k, v in (d.get("subs") or {}).items()},
            subverbs={str(k): list(v) for k, v in (d.get("subverbs") or {}).items()},
            updated=int(d.get("updated", 0)),
        )



def discover(name: str, timeout: float | None = None,
             probe: list[str] | None = None) -> ToolKnowledge:
    import shutil
    import tempfile

    name = name.strip()
    limit = TOOL_TIMEOUT if timeout is None else timeout
    path = shutil.which(name) or ""
    k = ToolKnowledge(name=name, path=path, updated=int(time.time()))
    if not path:
        return k
    # Probe in a throwaway directory. Not every tool treats `--help` as a pure
    # read: `temporal-service --help` wrote a 565KB SQLite database into the
    # working directory before printing a line, so a discovery run from a
    # project root used to leave that database behind.
    with tempfile.TemporaryDirectory(prefix="tai-probe-") as scratch:
        out, _ = _run([name, "--help"], timeout=limit, cwd=scratch)
        if not out.strip():
            out, _ = _run([name, "help"], timeout=limit, cwd=scratch)
        if not out.strip():
            out, _ = _run(["man", name], timeout=limit, cwd=scratch)
        # Inside the scratch block, and not beside it: `__complete` is still a tool
        # being run, and a tool that writes to its cwd must write to this one. The
        # fixture that proves it writes `probe-marker-in-cwd`, so a probe without
        # `cwd` leaves the file in the caller's directory.
        declared, subs, subverbs = _declared(name, probe or [], cwd=scratch)
    if _NO_HELP.match(out):
        out = ""
    k.help_text = out[:MAX_HELP_BYTES]
    k.version = _version(name, timeout=limit)
    k.verbs, k.flags = _extract(k.help_text, name)
    # The tool's own answer about itself, when it gives one. Declared subcommands
    # go *before* scraped ones and scraped ones the tool did not name are kept
    # after, because both are conventions until the history says otherwise and
    # the index demotes them together — but where the two disagree about what a
    # subcommand is called, the tool is right and the parser is guessing. Both
    # still rank below anything actually run; see `COLD_FACTOR`.
    if declared:
        rest = [v for v in k.verbs if v not in set(declared)]
        k.verbs = (declared + rest)[:96]
        k.subs = subs
        k.subverbs = subverbs
    return k



def _version(name: str, timeout: float | None = None) -> str:
    import tempfile

    out, _ = _run([name, "--version"], timeout=timeout or 0.5,
                  cwd=tempfile.gettempdir())
    return out.strip().splitlines()[0][:300] if out.strip() else ""


def default_tools() -> list[str]:
    return [
        "git", "docker", "docker-compose", "kubectl", "helm", "terraform",
        "mise", "uv", "pipx", "cargo", "npm", "pnpm", "bun", "deno", "go",
        "python", "python3", "node", "ruby", "make", "just", "task", "gh",
        "aws", "gcloud", "az", "ssh", "curl", "jq", "yq", "ffmpeg", "rustc",
        "poetry", "bundle", "composer", "gradle", "mvn", "nix", "npx",
    ]


# Directories the distribution owns. Everything else on PATH is something the
# user (or a tool manager they installed) put there, which is exactly the set
# that has no history behind it yet.
SYSTEM_DIRS = {"/bin", "/sbin", "/usr/bin", "/usr/sbin"}


def path_tools() -> list[str]:
    """Every executable on PATH outside a distribution directory.

    This is the answer to "you suggest nothing for a tool I just installed".
    Scanning all of PATH would also pick up four thousand distro binaries that
    the user either already has in their history or is never going to type, and
    would cost a `--help` run for each. The tools in `~/.local/bin`,
    `~/.npm-global/bin`, `~/.cargo/bin`, `/usr/local/bin` and `/opt` are the
    ones that are installed and unused, so that is the set to learn.
    """
    found: set[str] = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip()
        if not d or d in SYSTEM_DIRS or not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for name in entries:
            path = os.path.join(d, name)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                found.add(name)
    return sorted(found)


def _parallel(names: list[str], workers: int, timeout: float,
              probe: dict[str, list[str]] | None = None) -> list:
    from concurrent.futures import ThreadPoolExecutor
    wanted = probe or {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(names)))) as pool:
        return list(pool.map(
            lambda n: discover(n, timeout=timeout, probe=wanted.get(n)), names))


def used_subcommands(limit: int = 50000) -> dict[str, list[str]]:
    """`{tool: [subcommand, …]}` for the subcommands the history actually runs.

    This is what decides which subcommands get their flags harvested, and the
    reason is size rather than taste: a flag is an index entry and index bytes
    are paid at every shell startup, forever. `docker run` has 104 of them and
    `docker inspect` has 80, so a tool that harvests every subcommand adds
    thousands of entries for a machine that runs perhaps two of them.

    Most-used first, so the cap spends itself on what the user reaches for. A
    second word that starts with a dash is the argument of the first one, not a
    subcommand — `git --no-pager log` is `git` with options, and `log` here would
    be a verb the tool does not have.
    """
    from collections import Counter
    from tai.store import load_rows
    tally: dict[str, Counter] = {}
    try:
        rows = load_rows(limit)
    except Exception:
        return {}
    for row in rows:
        parts = (row[0] if row else "").split()
        if len(parts) < 2 or parts[1].startswith("-"):
            continue
        tally.setdefault(parts[0], Counter())[parts[1]] += 1
    return {tool: [sub for sub, _ in counts.most_common()]
            for tool, counts in tally.items()}


def discover_many(names: list[str] | None = None,
                  probe: dict[str, list[str]] | None = None) -> list[ToolKnowledge]:
    """Learn the named tools, or the installed ones when `names` is None.

    Runs in parallel because the cost is dominated by tools that ignore
    `--help` and have to be killed at the timeout: serially that is 33s for the
    ~100 tools on a typical desktop, in parallel it is 5s. A second pass gives
    the tools that stayed silent a longer budget, which is what catches a node
    CLI that spends two and a half seconds on an update check before printing
    anything. The runs stay read-only and nothing suggested is ever executed.
    """
    if names is None:
        names = sorted(set(default_tools()) | set(path_tools()))
    import shutil

    todo = [n for n in names if n and shutil.which(n)]
    if not todo:
        return []
    found = _parallel(todo, DISCOVER_WORKERS, TOOL_TIMEOUT, probe)
    silent = [n for n, k in zip(todo, found) if not k.help_text]
    if silent:
        # Fewer workers: these are the slow ones, and they are the last thing
        # standing between a tool and its vocabulary.
        retry = _parallel(silent, max(1, DISCOVER_WORKERS // 2), TOOL_TIMEOUT_SLOW, probe)
        retry_by_name = dict(zip(silent, retry))
        found = [retry_by_name.get(n, k) for n, k in zip(todo, found)]
    return found


def save_many(tools: list[ToolKnowledge]) -> None:
    import json

    from tai.store import session
    with session() as con:
        con.executemany(
            "INSERT INTO cli_tools(name,data,updated) VALUES(?,?,?) ON CONFLICT(name) "
            "DO UPDATE SET data=excluded.data, updated=excluded.updated",
            [(k.name, json.dumps(k.as_dict(), separators=(",", ":")), k.updated)
             for k in tools])
        con.commit()


def load_all() -> list[ToolKnowledge]:
    import json

    from tai.store import session
    try:
        with session() as con:
            rows = con.execute("SELECT data FROM cli_tools ORDER BY name").fetchall()
        return [ToolKnowledge.from_dict(json.loads(row[0])) for row in rows]
    except Exception:
        return []


def is_known(name: str) -> bool:
    """True when this tool's help has already been learned."""
    from tai.store import session
    try:
        with session() as con:
            return con.execute("SELECT 1 FROM cli_tools WHERE name=?",
                               (name,)).fetchone() is not None
    except Exception:
        return False


def candidates_for_prefix(prefix: str, tools: list[ToolKnowledge] | None = None,
                          cap: int = 32) -> list[str]:
    """Generate safe candidate command strings from cached help knowledge."""
    tools = tools if tools is not None else load_all()
    prefix = prefix.strip()
    if not prefix:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        base = tool.name
        if not (base.startswith(prefix) or prefix.startswith(base)):
            continue
        candidates: list[str] = [base]
        if prefix == base or prefix.startswith(base + " ") or prefix == base + " ":
            candidates += [f"{base} {v}" for v in tool.verbs]
            candidates += [f"{base} {v} --help" for v in tool.verbs[:12]]
        if prefix == base or prefix == base + " ":
            candidates += [f"{base} --help"]
        # Flags are valid candidates too: this is what enables `ls -` and
        # `docker --` even when the exact flag has never appeared in history.
        if prefix.startswith(base + " -") or prefix.startswith(base + " --"):
            candidates += [f"{base} {flag}" for flag in tool.flags]
        # …and the flags of the *subcommand being typed*, which is the position
        # `docker run --` is asking about and `docker --` never answers. The
        # candidate is written in full rather than as the prefix plus the flag, so
        # the same `startswith` filter narrows `docker run --p` to the flags that
        # begin `--p` and lets it replace the half-typed word — appending after it
        # would produce `docker run --p--publish`.
        head = prefix.rstrip()
        if head.startswith(base + " ") and not head[len(base) + 1:].startswith("-"):
            sub = head[len(base) + 1:].split(" ", 1)[0]
            # `gh pr ` is a question about `checkout`, so its subcommands come
            # before its options, and both are written in full rather than as the
            # prefix plus the word — the same `startswith` filter then narrows
            # `gh pr c` to the names beginning `c` and lets the answer replace the
            # half-typed one. Appending after it would give `gh pr ccheckout`.
            candidates += [f"{base} {sub} {s}" for s in tool.subverbs.get(sub, ())]
            candidates += [f"{base} {sub} {flag}" for flag in tool.subs.get(sub, ())]
        for c in candidates:
            if c.startswith(prefix) and c not in seen:
                out.append(c)
                seen.add(c)
                if len(out) >= cap:
                    return out
    # Universal cold-start vocabulary, useful even when no tool is installed
    # in PATH and no history entry exists yet.
    from tai.seed import SEED_COMMANDS
    for cmd in SEED_COMMANDS:
        if cmd.startswith(prefix) and cmd not in seen:
            out.append(cmd)
            seen.add(cmd)
            if len(out) >= cap:
                return out
    return out


def learn(tools: list[str] | None = None) -> int:
    """Discover, cache, and return the number of tools learned.

    The history is read first so that the subcommand flags worth harvesting are
    the ones actually run: see `used_subcommands`, and the size argument there.
    """
    probe = used_subcommands()
    discovered = discover_many(tools, probe=probe)
    save_many(discovered)
    return len(discovered)
