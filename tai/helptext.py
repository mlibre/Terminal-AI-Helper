"""Turning CLI help output into candidate command vocabulary.

Pure (or nearly-pure) text-to-structure code: `_run` executes a tool's own
read-only introspection (`--help`, `__complete`), and everything else is
parsing of what those probes print. Discovery policy — what to probe, where
results are cached, when vocabularies get refreshed — lives in
`tai.knowledge`; this module holds no policy and never executes a suggested
command.
"""
from __future__ import annotations

import os
import re
# `subprocess` is imported inside `_run`: this module is loaded by `tai record`
# through tai.knowledge, and the probing it exists for is the only thing that
# spawns a process.

TOOL_TIMEOUT = float(os.environ.get("TAI_HELP_TIMEOUT", "1.5"))
# A second, longer budget for tools that stayed silent. `9router --help` needs
# 2.5s on this machine, because it checks for an update before it prints
# anything, and it would otherwise be recorded as a tool with no vocabulary.
TOOL_TIMEOUT_SLOW = float(os.environ.get("TAI_HELP_TIMEOUT_SLOW", "6.0"))
MAX_HELP_BYTES = 256 * 1024
# Discovery is a batch of independent `--help` runs, so the only limit that
# matters is how many processes are worth having open at once.
DISCOVER_WORKERS = int(os.environ.get("TAI_DISCOVER_WORKERS", "8"))

# The completion protocol the cobra CLI library speaks: `tool __complete <args…>
# <word>` prints one `candidate<TAB>description` line per match on stdout and a
# `:<directive>` line on stderr.
#
# This is the only *machine-readable* statement a CLI makes about its own
# vocabulary. `--help` is prose to be parsed, and a shipped completion script is
# shell code to be executed — shtab names the second as "slow and having
# side-effects" when it chooses to generate a script instead of answer questions.
# Here the tool answers, and what it says is already separated from what it means.
#
# It is opportunistic, not the main path, because very few tools speak it: six
# of the 4106 executables in /usr/bin and /usr/local/bin on the machine this was
# measured on (`docker`, `dockerd`, `gh`, `git-lfs`, `godot`, `sbctl`). What it
# buys where it exists is disproportionate, though — `gh --help` yields no verbs
# at all to the parser above, and `gh __complete ""` yields all 37 of them, each
# with a description.
COMPLETE_TIMEOUT = float(os.environ.get("TAI_COMPLETE_TIMEOUT", "2.0"))
# How many of one tool's subcommands get their flags harvested. Each costs a
# fork, and the forks are not cheap: 9-16ms for docker, 87ms for gh. The history
# chooses which subcommands earn one (see `learn`), and this is the backstop for
# a tool used in more places than that. The flags are capped a second time by
# the index, which is the part that is paid at every shell startup.
SUB_FLAG_CAP = 8

# `man` answers a miss with a sentence rather than a failure, and caching that
# sentence as a tool's vocabulary produces candidates made of the words
# "No manual entry for".
_NO_HELP = re.compile(
    r"^\s*no (manual entry|entry) for\b|\bnot found in the man database\b",
    re.I,
)
# Two bare words at the start of a line, the shape of a subcommand example.
_INVOCATION = re.compile(r"^[a-z][\w.:-]*\s+[a-z][\w.:-]*\s")

# One completion candidate: the name, then a tab, then prose. Only the name is
# wanted — the index has nowhere to put a description — so the description is
# what makes the line recognisable, not what is read from it.
_COMPLETE_LINE = re.compile(r"^([^\t:][^\t]*)\t")
# The `:<bits>` line every reply ends with. It is also how a tool that has no such
# command is told apart from one that has it: `git __complete ""` prints
# `git: '__complete' is not a git command` and **exits 0**, so neither the exit
# status nor the presence of output can be trusted, and this line is the one
# thing a real reply has that an error does not.
_COMPLETE_DIRECTIVE = re.compile(r"^:\d+$", re.M)
# Options every tool has and no user needs suggested.
_META_FLAGS = frozenset({"--help", "-h", "--version", "-v", "--usage", "-V"})


def _run(args: list[str], timeout: float = TOOL_TIMEOUT,
         cwd: str | None = None) -> tuple[str, str]:
    import subprocess

    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, cwd=cwd,
                           timeout=timeout, check=False, text=True,
                           errors="replace", env={**os.environ, "PAGER": "cat"})
        return p.stdout[:MAX_HELP_BYTES], ""
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except (OSError, ValueError) as e:
        return "", str(e)


def _extract(text: str, name: str) -> tuple[list[str], list[str]]:
    """Extract likely verbs and flags from help text conservatively."""
    verbs: list[str] = []
    flags: list[str] = []
    seen_v, seen_f = set(), set()

    # Usage lines: the most reliable source for subcommands. Prefer the
    # indented command catalog (`  add   ...`) and only use usage tokens as a
    # fallback; usage prose contains words like "ersion" that are not verbs.
    catalog = False
    short: list[str] = []
    for raw in text.splitlines()[:800]:
        line = raw.strip()
        if not line or len(line) > 500:
            continue
        if re.search(r"\b(commands?|actions?)\b", line, re.I):
            catalog = True
        # Catalog entries: whitespace before a description, e.g. "  clone  ..."
        m = re.match(r"^([a-z][a-z0-9_:-]{1,31})\s{2,}\S", line)
        if m and m.group(1) not in seen_v and m.group(1) != name:
            verbs.append(m.group(1))
            seen_v.add(m.group(1))
            catalog = True
        # A line that reads as a command invocation belongs to a subcommand, and
        # its flags are not the tool's own. `9router` documents its commands as
        # `xai video --prompt "..." --output video.mp4`, and harvesting that line
        # offers `9router --prompt`, which the tool does not accept. Option lists
        # start with a dash; prose starts with a capital or a bracket.
        invocation = (not line.startswith(("-", "(", "[", ">", "|")))
        if invocation and _INVOCATION.match(line):
            continue
        for flag in re.findall(r"(?<![\w-])(--[a-zA-Z0-9][a-zA-Z0-9-]{0,40})", line):
            if flag not in seen_f:
                flags.append(flag)
                seen_f.add(flag)
        # Short options matter. A tool whose help documents only "-p, --port"
        # otherwise yields no vocabulary at all, and the user still needs
        # something to accept after `tool -`.
        for flag in re.findall(r"(?<![\w-])(-[A-Za-z][A-Za-z0-9]{0,3})(?![\w-])", line):
            if flag not in seen_f:
                short.append(flag)
                seen_f.add(flag)
    # Long options first, and `--help` last. The index keeps only the first few
    # flags of a tool, and a hint that names the option teaches something while
    # `tool --help` teaches nothing the user could not have typed themselves.
    # The same goes for a short form: `--no-browser` is worth a slot before `-n`.
    meta = {"--help", "-h", "--version", "-v", "--usage", "-V"}
    # Long options, then the short forms, then the meta options — and the last
    # group is taken from *both* lists, because reading it off the already
    # filtered `flags` dropped every long meta option on the floor: `--version`
    # was in neither the kept flags nor the short forms, so `tool --v` had
    # nothing to offer and the tool's own `--help` was the only answer.
    meta_flags = [f for f in flags + short if f in meta]
    flags = ([f for f in flags if f not in meta] + [f for f in short if f not in meta]
             + meta_flags)

    # If no catalog was found, conservatively parse explicit usage lines.
    if not catalog:
        for line in text.splitlines()[:100]:
            if not re.search(r"\b(usage|synopsis)\b", line, re.I):
                continue
            tail = re.sub(r"^(usage|synopsis)\s*:\s*", "", line, flags=re.I)
            # `<file>`, `<path>` and `<command…>` name an argument, not a subcommand,
            # and the word inside them looks like any other — so they are removed
            # before the line is read rather than added to a stop-list that could
            # never be finished.
            tail = re.sub(r"<[^>]*>", " ", tail)
            # Everything from the first `[` on is an option or that option's
            # argument, never a subcommand: `usage: tool add|remove [options]`
            # and `usage: prog [-o outfile] input...` are the same line, and only
            # the part before the bracket is a command in either. Without this
            # cut the argument names become subcommands, which is how `prog
            # outfile` ended up on offer.
            tail = tail.split("[", 1)[0]
            # The guard is `[\w-]` and not `[\\w-]`: inside a raw string the
            # latter is a backslash, the letter `w` and a dash, so it stopped a
            # token *after* the first letter of an option instead of before one —
            # `[-abc]` yielded the verb `bc`, and `[--help]` yielded `elp`.
            for tok in re.findall(r"(?<![\w-])([a-z][a-z0-9_:-]{1,31})(?![\w-])", tail):
                if tok.lower() in {"the", "and", "for", "with", "from", "this", "that", "usage", "options", "flags", "help", "version", "path", "command", "args", "name", "value"}:
                    continue
                if tok not in seen_v and tok != name:
                    verbs.append(tok)
                    seen_v.add(tok)

    # Only add verbs/flags that are plausible command words. A malformed
    # help parser should reduce recall, never inject arbitrary prose as shell
    # candidates.
    return [v for v in verbs if v.isalpha() and v not in {
        "version", "help", "path", "command", "args", "name", "value", "page",
        "file", "directory", "all", "one", "two", "use", "see", "also",
    }][:64], flags[:96]


def _complete_candidates(out: str) -> list[str] | None:
    """Candidate names from one `__complete` reply.

    None means *this tool has no such command*, which is the answer for almost
    every tool and must be distinguishable from an empty list: `git __complete ""`
    prints one line of usage and exits 0, and reading that as "a tool with no
    subcommands" would silently record a tool as having an answer it does not
    have. The `:<bits>` line is the discriminator — a real reply always ends with
    one, and an error message never does.

    Order is the tool's own, and it is kept: it is the only ordering evidence
    there is, and a tool that lists `run` before `rm` is telling you something.
    """
    if not _COMPLETE_DIRECTIVE.search(out):
        return None
    names: list[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        match = _COMPLETE_LINE.match(line)
        if not match:
            continue
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names or None


def _declared(name: str, probe: list[str],
              cwd: str | None = None,
              timeout: float | None = None) -> tuple[list[str], dict, dict]:
    """`(subcommands, {sub: its flags}, {sub: its own subcommands})`.

    Empty when the tool does not speak the protocol, which is the common case and
    costs one fork to discover. `probe` is the subcommand list to ask about; the
    vocabulary of a subcommand nobody runs is index entries and startup bytes paid
    forever, so the caller's list — built from the history, most-used first — is
    what decides.
    """
    limit = timeout or COMPLETE_TIMEOUT
    out, _ = _run([name, "__complete", ""], timeout=limit, cwd=cwd)
    subs_all = _complete_candidates(out)
    if not subs_all:
        return [], {}, {}
    # A flag is not a subcommand. `docker __complete ""` answers with the
    # subcommand list because the word is empty, but a tool that merges the two
    # would put its own options in the verb list and offer `docker --config` as
    # a thing to type after `docker `.
    subs_all = [s for s in subs_all if not s.startswith("-")]
    # `{"run": ["--detach", …]}` and `{"pr": ["checkout", "checks", …]}` are two
    # different questions and are kept apart, because `gh pr checkout` is a verb
    # and `gh run --detach` is an option and a menu that mixed them would offer
    # `gh pr --detach`. Both are keyed by the command they follow.
    subs: dict[str, list[str]] = {}
    subverbs: dict[str, list[str]] = {}
    for sub in probe[:SUB_FLAG_CAP]:
        if sub not in subs_all:
            continue
        # Its own subcommands first, and asked for first: `gh pr ` is a question
        # about `checkout` and `close`, and the options come after those. The two
        # probes are separate forks because the protocol takes one word at a time.
        reply, _ = _run([name, "__complete", sub, ""], timeout=limit, cwd=cwd)
        nested = _complete_candidates(reply)
        if nested:
            subverbs[sub] = [s for s in nested if not s.startswith("-")]
        reply, _ = _run([name, "__complete", sub, "--"], timeout=limit, cwd=cwd)
        got = _complete_candidates(reply)
        if got:
            subs[sub] = [f for f in got
                         if f.startswith("-") and f not in _META_FLAGS]
    return subs_all, {k: v for k, v in subs.items() if v}, \
        {k: v for k, v in subverbs.items() if v}

