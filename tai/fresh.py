"""The files a path argument could be.

The history answers *which file did you use last time*, and that is the wrong
question the moment you download something. The reported case: `chmod +x
Freebuff-0.0.154-linux-x86_64.AppImage` had never been typed, so `chmod +x `
was answered with `chmod +x script` — a file that does not exist here, offered
because it is the only one the index knows. Frequency cannot fix this, and
neither can a longer history: the file the user is about to use is by
definition not in the history yet.

So for a line that ends in a file (`tai.paths.takes_file`), the filesystem is
the vocabulary and the history is only a tiebreak:

  * the files that are actually there, **newest first**, are the candidates;
  * a learned argument that is *still a file here* keeps the top place, because
    you asked for that file before and it exists — that is what stops this from
    making `python3 ~/scripts/train.py` worse;
  * a learned argument that is not a file here is not an answer, which is the
    same rule the index already applies to a command whose path is gone.

Where it looks: the directory you are standing in, and the directories a
download lands in. *Only* those, and the smallness is the point. Reading the
immediate subdirectories as well was tried and removed: it turned `chmod +x `
into `chmod +x tests/__pycache__/test_smoke.cpython-314.pyc` on a machine where a test
had just run, which is a file nobody would ever chmod and the newest thing on
disk besides the AppImage they had just downloaded. A path with a directory part
is a weaker answer than a bare name, and `~/Downloads` is already a root, so the
case that matters — the download you just made — is covered without it.

This is the one-shot path; the shell plugins read the same roots, in the same
order, with their own globs, because a Python process may not touch the
keystroke path. The implementations are kept in step by the same three constants
below and by `TAI_FILE_ROOTS`, which adds roots to both.
"""
from __future__ import annotations

import glob
import os

# How many files one root may contribute, and how many roots are read in all.
# Both are shortlists, not truncation: the way to see more is to type more of
# the word, which narrows every root at once. A downloads folder has a season of
# videos, and a `Tab` that stats all of them is a `Tab` you notice.
PER_ROOT = 8
MAX_ROOTS = 8

# Directories a download lands in. `XDG_DOWNLOAD_DIR` comes first because that is
# the one the user chose; the others are what a machine that has never set it
# uses. Desktop is in the list because a file dragged out of a browser lands
# there just as often as in Downloads.
DOWNLOAD_DIRS = ("$XDG_DOWNLOAD_DIR", "~/Downloads", "~/Download", "~/Desktop")


def extra_roots() -> list[str]:
    """`TAI_FILE_ROOTS`: more places to look, colon separated."""
    out = []
    for item in (os.environ.get("TAI_FILE_ROOTS") or "").split(os.pathsep):
        item = item.strip()
        if item:
            out.append(os.path.expanduser(item))
    return out


def roots(cwd: str = "") -> list[tuple[str, str]]:
    """`(directory, how to write it)` pairs, most relevant first.

    A root is written as a bare name when it is where you already are, as a path
    relative to you when it is next door, and as `~/…` when it is under your home
    directory — which is the form a line should hold, because it survives being
    typed in a directory where the same file is not next to you.

    Directories are absolute, including the current one: a caller may pass a
    `cwd` that is not this process's, and a relative root would then be read from
    the wrong place — which is how `files("", cwd=…)` listed the repository's
    files while asked about an empty scratch directory.
    """
    here = os.path.abspath(cwd or os.getcwd())
    home = os.path.expanduser("~")
    out: list[tuple[str, str]] = [(here, "")]
    seen = {here}
    for raw in extra_roots() + [os.path.expanduser(d) for d in DOWNLOAD_DIRS]:
        path = raw if os.path.isabs(raw) else os.path.join(here, raw)
        path = os.path.normpath(path)
        if path in seen or path == here or not os.path.isdir(path):
            continue
        seen.add(path)
        # `path[len(home):]` already starts with the separator, so `~` is
        # concatenated to it rather than joined: `~` + `/Downloads` is
        # `~/Downloads`, and writing `~/` + `/Downloads` is `~//Downloads`, which
        # still works in a shell and reads as a typo everywhere else.
        out.append((path, f"~{path[len(home):]}/" if path.startswith(home + os.sep)
                    else path + "/"))
        if len(out) >= MAX_ROOTS:
            break
    return out


def files(word: str = "", cwd: str = "") -> list[str]:
    """The files that could be `word`, newest first.

    `word` is a glob, which is how a completion is matched: empty matches every
    file in a root, and `Free` matches the one you mean. Directories are left
    out — the shell's own completion offers those, and a hint that answers `mv
    notes.txt ` with the newest directory on disk is a guess, not an answer.

    Three cases, because a word with a `/` in it is naming where to look rather
    than what to look for, and only the third is a search across roots:

      * `~` is expanded first, so `~/Down` is something that can be globbed at all;
      * an absolute word, or one that already holds a slash, is globbed once,
        where it points. `~/Downloads/freeb` is not `freeb` in every root — it is
        that one path. The answer is the word as it was typed plus what the glob
        added, so the `~` the user typed is the `~` they get back;
      * a bare name is the search, and that is the case this feature is for.
    """
    found: list[tuple[float, str]] = []
    pattern_word = os.path.expanduser(word)
    # A word naming a directory lists what is in it. Without this, `~` globs as
    # `~*` — every *sibling* of the home directory — which matches nothing. The
    # separator goes on the *pattern*, never onto `pattern_word`: that is the
    # length the answer is cut at, and a trailing slash there removes the first
    # character of every file name.
    pattern = (pattern_word.rstrip("/") + "/*") if pattern_word and \
        os.path.isdir(pattern_word) else pattern_word + "*"
    if pattern_word.startswith("/") or "/" in pattern_word:
        # One place, and the answer is the word as typed plus whatever the glob
        # added: the `~` has to be expanded to find anything and has to come back.
        found = _scan(pattern, len(pattern_word), word)
    else:
        for directory, written in roots(cwd):
            found += _scan(os.path.join(directory, (pattern_word or "") + "*"),
                           len(directory) + 1, written)
    found.sort(key=lambda pair: -pair[0])
    return [name for _, name in found]


def _scan(pattern: str, cut: int, written: str) -> list[tuple[float, str]]:
    """`(mtime, name)` for the files a pattern matches.

    `cut` is how much of each matched path to drop and `written` is what goes in
    front of what is left: a root's own writing — bare here, `~/Downloads/`
    there — or the word the user typed, when the word already named a place. The
    two are different lengths by exactly the separator, which is why this is a
    number computed at the call site and not a guess from the shape of the path:
    dropping one character too many turned `~/Downloads/f` + `reebuff-…` into
    `feebuff-…`.

    The cap is on *recency*, not on the order the glob happened to return. glob
    yields names in whatever order the filesystem lists them and `sorted()` made
    that alphabetical, so `sorted(...)[:PER_ROOT]` kept the eight
    alphabetically-first files and threw away the one just downloaded —
    `zzz-thing.AppImage` in a folder of `aaa*.mp4` — before mtime was ever
    considered. That is the whole feature failing at the one moment it exists
    for, in all three implementations at once, because all three globbed
    alphabetically. Ranking is a `stat` away and the answer is already being
    built from mtimes, so the cap is applied after the sort.
    """
    try:
        names = glob.glob(pattern)
    except (OSError, ValueError):
        return []
    out: list[tuple[float, str]] = []
    for name in names:
        if os.path.isdir(name):
            continue            # the shell's own completion offers these
        try:
            mtime = os.path.getmtime(name)
        except OSError:
            continue
        out.append((mtime, f"{written}{name[cut:]}"))
    # Newest first, then the cap. `files()` sorts the roots together afterwards,
    # so this ordering only decides which entries of *this* root survive it.
    out.sort(key=lambda pair: -pair[0])
    return out[:PER_ROOT]