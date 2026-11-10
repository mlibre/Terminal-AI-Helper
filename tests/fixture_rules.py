"""The fixture writer's copy of the two rules the index builder applies.

The pty suites write a hand-made index, and two of its maps are decided by
rules the engine owns: which line ends in a file (`takes_file`) and which
words are unambiguously relative paths (`relative_word_set`). This module
mirrors `src/paths.rs` — the one implementation that ships — and the golden
tests keep the two honest: the byte-identical index the Rust builder writes
against the migration fixture is the proof that this copy and the engine
agree.
"""

# The generator's own limits (src/index.rs and src/fresh.rs).
WORD_CANDIDATE_CAP = 20
WORD_KEY_MAX_DEPTH = 8
PER_ROOT = 8
MAX_ROOTS = 8

FILE_LAST = {"chmod", "chown", "chgrp", "cp", "mv", "ln", "install", "."}
FILE_FLAGGED = {
    "bat", "cat", "code", "diff", "du", "file", "head", "less", "md5sum",
    "more", "nano", "nvim", "readlink", "rm", "sha256sum", "sort", "stat",
    "tail", "tar", "unzip", "vim", "wc", "xdg-open", "zip",
}
UNANALYSABLE = set('$`*?[]{}<>|;&()!#\\\n\t"\'')


def unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def is_path_like(token: str) -> bool:
    if not token or token.startswith("-") or "=" in token:
        return False
    if any(ch in UNANALYSABLE for ch in token):
        return False
    if "://" in token:
        return False
    host, sep, _ = token.partition(":")
    if sep and "/" not in host:
        return False
    if token.lstrip("+-").isdigit():
        return False
    if token in (".", ".."):
        return True
    if token.startswith("~"):
        # The fixture carries no `~user` token, so the account question the
        # engine asks is one this copy never reaches.
        return True if (token == "~" or token.startswith("~/")) else False
    if token.startswith("."):
        return not set(token.lstrip(".")) <= set("0123456789.")
    if "/" not in token:
        return False
    head = token.lstrip("~/").split("/", 1)[0]
    if head and set(head) <= set("0123456789.+-:") and any(c.isdigit() for c in head):
        return False
    return True


def takes_file(cmd: str) -> bool:
    tokens = (cmd or "").split()
    if len(tokens) < 2:
        return False
    last = unquote(tokens[-1])
    if last.startswith("-") or any(ch in UNANALYSABLE for ch in last):
        return False
    head = tokens[0]
    if head in FILE_LAST:
        return True
    if head not in FILE_FLAGGED:
        return False
    previous = head
    for token in tokens[1:-1]:
        if not token.startswith("-") and not previous.startswith("-"):
            return False
        previous = token
    return True


def relative_word_set(cmds) -> set:
    out = set()
    for cmd in cmds:
        for token in (cmd or "").split():
            token = unquote(token)
            if token.startswith("~") or token.startswith("/"):
                continue
            if is_path_like(token):
                out.add(token)
    return out
