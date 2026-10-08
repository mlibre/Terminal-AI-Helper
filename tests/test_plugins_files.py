"""Lines that end in a file, answered from the filesystem rather than history.

What you are about to use is by definition not in the history yet, so frequency
cannot answer it. These tests drive a real shell with a scratch HOME and check
that a line ending in a path sees the newest file there, while a line that does
not end in a file is left to the index.

    python3 tests/test_plugins_files.py
"""
import os
import pathlib
import re
import shutil
import sys
import time
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from plugin_env import *  # noqa: F401,F403  (constants, fixture writers, setup)
from plugin_screen import *  # noqa: F401,F403  (Screen and the styled-menu helpers)
from plugin_pty import *  # noqa: F401,F403  (Session, Ghosts, check, probe, failures)

from tai.fresh import MAX_ROOTS, PER_ROOT  # noqa: E402  (the numbers the three must share)


def test_zsh_file_arguments() -> None:
    """A path argument is answered by the filesystem, newest file first.

    The reported case: `chmod +x script` is the only `chmod` in the history, so
    `chmod +x ` suggested `chmod +x script` — a file that is not there — while the
    AppImage just downloaded into ~/Downloads went unmentioned. The file you are
    about to use is by definition not in the history yet, so frequency cannot
    answer this and the filesystem has to.

    Both halves are asserted, because the second is the one that would make this
    a regression: `script` *is* a file in the fixture's working directory here,
    and a learned file that still exists must keep winning. Otherwise every
    `cat ~/notes/todo.txt` in a history would be displaced by whatever was
    touched last, and the feature would be a net loss.

    TAI_FILE_ROOTS is set rather than relying on the developer's own
    ~/Downloads: the assertion has to be about these files, on any machine.
    """
    if not SHELLS["zsh"]:
        return
    print("zsh file arguments")
    setup_files()
    env = {"HOME": str(FILE_HOME), "TAI_FILE_ROOTS": str(FILE_DOWNLOADS)}
    s = Session("zsh", env_extra=env)
    s.run(f"cd {FILE_WORK}")

    # The learned file exists here, so it stays the answer. `script` is written
    # into the working directory by this test for exactly this case, and the hint
    # is read after a fresh send each time: a keystroke does not always redraw,
    # so a hint read twice in a row can be the previous line's.
    (FILE_WORK / "script").write_text("#!/bin/sh\n")
    (FILE_WORK / "script").chmod(0o755)
    s.send("chmod +x ")
    check("a learned file that still exists keeps the hint",
          s.suggestion().split(" PD=")[0], "SUG=[script]")

    # Now it is gone — a file the history names, in a directory it is not in —
    # and the file that was actually downloaded is the answer. Written as
    # `~/Downloads/x` rather than an absolute path, so the suggestion is the line
    # a person would type and still works from another directory.
    (FILE_WORK / "script").unlink()
    s.send("chmod +x ")
    check("and when it is not there, the newest file is",
          s.suggestion().split(" PD=")[0], f"SUG=[~/Downloads/{DOWNLOADED}]")

    # Part of the word narrows it. `chmod +x freeb` is looking for a file whose
    # name starts with `freeb`, and `~/Downloads/freebuff…` does not — the file is
    # *in* Downloads, it is not *called* Downloads. So a path from another root
    # cannot answer a word already being typed, which is the same rule the menu
    # has always applied to a candidate that does not extend the word.
    s.send("chmod +x freeb")
    check("a partial word is not answered by a path from elsewhere",
          s.suggestion().split(" PD=")[0], "SUG=[]")
    # Type it the way the file would be reached from this directory, and the same
    # file answers — because now the word really is a prefix of it, and the `~` the
    # user typed is the `~` they get back rather than an expanded path.
    s.send("chmod +x ~/Downloads/freeb")
    check("and does answer it once the word is a prefix",
          s.suggestion().split(" PD=")[0], f"SUG=[uff-{DOWNLOADED[len('freebuff-'):]}]")
    # Typing the directory and stopping there is the shape this is really for:
    # the answer has to be built from what was typed in front of the word, not
    # appended after it, or the line comes out as `chmod +x ~/Downloads/f~/Down…`.
    s.send("chmod +x ~/Downloads/")
    check("and the answer continues the line, not the line twice",
          s.suggestion().split(" PD=")[0], f"SUG=[{DOWNLOADED}]")
    s.write("\x15")

    # Tab cycles the words the filesystem offers — it does not take the hint
    # the way the arrow does. The hint's own key still completes the line.
    s.send("chmod +x ")
    s.write(TAB)
    s.settle()
    line, drawn, size, idx = s.menu()
    check("Tab lists the words rather than taking the hint",
          (line.startswith("chmod +x "), size > 1), (True, True))
    s.write("\x15")
    s.send("chmod +x ")
    s.write(RIGHT)
    s.settle()
    line, drawn, size, idx = s.menu()
    check("the arrow completes it", line, f"chmod +x ~/Downloads/{DOWNLOADED}")

    # The list shows the files *and* what tai learned, files first, so a learned
    # name is never hidden — it is demoted, which is the honest way to say it is
    # less likely than a file that exists right now.
    s.send("chmod +x ")
    s.write(LIST)
    s.settle()
    line, drawn, size, idx = s.menu(clear=False)
    check("the list puts the real file first",
          menu_entries(drawn)[0], f"~/Downloads/{DOWNLOADED}")
    check("and still offers what tai learned", "script" in menu_entries(drawn), True)
    check("leaving the line alone", line, "chmod +x ")
    s.write("\x15")

    # Not every line ends in a file, and a line that does not must be untouched.
    # `git ` learns from the history, `ls ` is not a file argument at all, and
    # neither may be answered with whatever is on disk.
    s.send("git ")
    check("a line that does not end in a file is unaffected",
          s.suggestion().split(" PD=")[0], "SUG=[pull --rebase]")
    s.send("ls ")
    check("nor is a line tai has no reason to think is one",
          s.suggestion().split(" PD=")[0], "SUG=[-la]")

    # `cat` is marked as taking a file, from the fixture's `cat tzz_d` line. The
    # word `tzz_d` matches no file in this directory, so the answer is empty —
    # which is the case that must not become "the newest thing on disk".
    s.send("cat tzz_d")
    check("a word that matches no file gets no answer",
          s.suggestion().split(" PD=")[0], "SUG=[]")

    check("no noise from the file lookup", s.noise(), [])
    s.close()


def test_bash_file_arguments() -> None:
    """bash answers a path argument from the filesystem too.

    The same rule as zsh, read through `_tai_complete` rather than through the
    ghost text — bash has no post-display, so the answer arrives as readline's
    own listing when completion is pressed. Asserted on what COMPREPLY holds
    after a completion function call in a real interactive bash, because that is
    the only place readline's contract with the function is visible.

    Both shells answering this is not redundancy: `_tai_query` and `_tai_complete`
    are two implementations of one rule set, and a rule that changed in one and
    not the other is how the two paths drift apart.
    """
    if not SHELLS["bash"]:
        return
    print("bash file arguments")
    setup_files()
    # TAI_COMPLETE_ALL=1 so Tab reaches _tai_complete for `chmod` too. bash binds
    # completion per command name, and out of the box that is only the literal
    # `tai`; without this the last assertion below would be reading readline's
    # own filename completion rather than anything tai did.
    env = {"HOME": str(FILE_HOME), "TAI_FILE_ROOTS": str(FILE_DOWNLOADS),
           "TAI_COMPLETE_ALL": "1"}
    s = Session("bash", env_extra=env)
    s.run(f"cd {FILE_WORK}")

    def offered(line: str) -> list[str]:
        """COMPREPLY for `line`, which is the list readline would show or pick from.

        `_tai_complete` is called with COMP_LINE set by hand rather than through a
        key, because readline only fills COMP_LINE for a function it is
        completing — a `bind -x` command gets an empty one, which is a fact about
        bash rather than about this plugin. The user-visible half is asserted
        below through a real Tab, where the listing readline actually draws.

        The count is written first, so the file is never empty and `wait_file` —
        which waits on the file existing *with content* — is a real
        synchronisation point. Without it, "no candidates" could not be told from
        "the command has not run yet", which is the whole assertion for several
        of the lines below.
        """
        REPLY.unlink(missing_ok=True)
        s.run(f'COMP_LINE={line!r}; COMP_POINT={len(line)}; COMPREPLY=(); '
              f'_tai_complete; printf "%s\\n" "${{#COMPREPLY[@]}}" '
              f'"${{COMPREPLY[@]}}" > {REPLY}')
        s.wait_file(REPLY, f"the completion list for {line!r}")
        got = REPLY.read_text().splitlines()
        return got[1:] if got and got[0].isdigit() else got

    check("the file that was just downloaded is offered",
          f"~/Downloads/{DOWNLOADED}" in offered("chmod +x "), True)
    check("and it is offered first", offered("chmod +x ")[0],
          f"~/Downloads/{DOWNLOADED}")
    check("while what tai learned is still offered",
          "script" in offered("chmod +x "), True)
    check("a partial word is not answered by a path from elsewhere",
          offered("chmod +x freeb"), [])
    check("nor is a line that does not end in a file",
          [c for c in offered("git p") if "/" in c], [])
    check("a word matching no file gets nothing from the filesystem",
          [c for c in offered("cat tzz_d") if "/" in c], [])

    # A learned file that still exists keeps the top place in bash as well,
    # which is what stops this from displacing every absolute path anyone uses.
    (FILE_WORK / "script").write_text("#!/bin/sh\n")
    (FILE_WORK / "script").chmod(0o755)
    check("and a learned file that still exists keeps its place",
          offered("chmod +x ")[0], "script")

    # Through a real Tab, so the claim is one a user would recognise. The ambiguous
    # case is the interesting one — several files, so readline draws a listing
    # rather than choosing — and it is read off the terminal because that is the
    # only place a readline listing exists.
    #
    # The line and the key go in one write, and the mark is taken before it.
    # Splitting them — send the line, read the echo, *then* send Tab — puts the
    # mark in the middle of the echo, and how much of the echo has arrived by
    # then is a property of how fast the machine is: on a slow one the listing
    # lands after the mark and the assertion fails for a plugin that is working.
    # It was latent rather than broken, and only showed up once the completion
    # path grew a fork and the timing moved.
    mark = len(s.seen)
    s.send("chmod +x " + TAB)
    s.settle()
    check("Tab lists the files through readline",
          DOWNLOADED in ANSI.sub("", s.raw()[mark:]), True)
    s.write(CTRL_U)
    s.settle()
    # And the bare partial word is left alone, as in zsh: `freebuff` is not a
    # prefix of `~/Downloads/freebuff…`, so that file is not an answer to it.
    s.send("chmod +x " + DOWNLOADED[:8])
    s.write(TAB)
    s.settle()
    check("while a bare partial word is not completed from another root",
          s.line(), "chmod +x " + DOWNLOADED[:8])
    s.write(CTRL_U)
    s.settle()

    # And the accept path agrees with it. `_tai_query` is what Ctrl-F and the
    # right arrow use, so a rule that changed in one and not the other is how the
    # two drift apart — and the line it returns is the line that gets written.
    (FILE_WORK / "script").unlink()
    check("the arrow key is given the same answer",
          offered("chmod +x ")[0], f"~/Downloads/{DOWNLOADED}")
    s.send("chmod +x " + CTRL_F)
    s.settle()
    check("and accepting writes that line",
          s.line(), f"chmod +x ~/Downloads/{DOWNLOADED}")
    # `git p` completes to `git pull --rebase`, so readline's word is `pull`; the
    # filesystem must contribute nothing to it, and this is the whole claim.
    check("while a line that does not end in a file is untouched by it",
          [c for c in offered("git p") if "/" in c], [])

    check("no noise from the file lookup", s.noise(), [])
    s.close()




def test_quoted_names() -> None:
    """A name the shell has to be told about is written so the shell agrees.

    The reported case is not exotic: `cat My Document.pdf` completed to the
    right *name* and the wrong *line*, because the name went in bare and the
    shell then read two arguments. It looks like it worked — there is a
    suggestion, the suggestion is the file, and the failure only appears when the
    command runs.

    Both shells, and every key that writes: for bash that is Tab and the arrow,
    and for zsh the arrow (Tab cycles its list instead of writing). bash quotes
    through readline on the Tab path and has to do it itself on the other, which
    is how the two disagreed about the same line.
    """
    for shell in SHELLS:
        if not SHELLS[shell]:
            continue
        print(f"{shell} quotes a name that needs it")
        setup_files()
        s = Session(shell)
        s.run(f"cd {FILE_WORK}")
        (FILE_WORK / "two words.txt").write_text("x")
        # Ctrl-Space lists rather than writing in bash — readline has no menu, so
        # the line is left alone — so it is only asserted for the shell that has
        # one. Every key that *does* write has to write a name the shell can pass.
        keys = [(RIGHT, "the arrow")]
        if shell == "bash":
            keys.append((TAB, "Tab"))
        if shell == "zsh":
            keys.append((CTRL_SPACE, "Ctrl-Space"))
        for key, label in keys:
            s.send("cat two")
            s.write(key)
            check(f"{shell} {label} writes a name a shell passes as one word",
                  s.line().rstrip(), "cat two\\ words.txt")
        # For zsh there is a second case at the prompt line: Tab offers exactly
        # one file, and offering exactly one is taking it.
        if shell == "zsh":
            s.send("cat two")
            s.write(TAB)
            check("zsh Tab takes a lone completion too", s.line().rstrip(),
                  "cat two\\ words.txt")
        # And it is the file that is there, which is the only reason to care.
        (FILE_WORK / "quote'name.txt").write_text("x")
        s.send("cat quote")
        s.write(RIGHT)
        check(f"{shell} quotes an apostrophe too", s.line().rstrip(),
              "cat quote\\'name.txt".rstrip())
        s.close()


def _plugin_limits(path: pathlib.Path, name: str) -> int:
    """The number a shell plugin holds for `_TAI_<name>`, read from its source."""
    found = re.search(rf"_TAI_{name}(?::=|=)(\d+)", path.read_text())
    assert found, f"{path.name} does not define _TAI_{name}"
    return int(found.group(1))


def test_the_three_implementations_hold_the_same_limits() -> None:
    """`tai/fresh.py`, `files.zsh` and `files.bash` agree on the two limits.

    Three implementations of one rule is already too many; what makes it
    survivable is that they are pinned to the same numbers, and the pins are
    read out of the sources rather than restated here — a restatement is a
    fourth copy, and a fourth copy is how the cap became alphabetical in all
    three at once and stayed alphabetical in all three afterwards.
    """
    repo = pathlib.Path(__file__).resolve().parent.parent
    for part in ("plugins/zsh/index.zsh", "plugins/bash/files.bash"):
        path = repo / part
        check(f"{path.name} reads at most PER_ROOT files from one root",
              _plugin_limits(path, "FILE_PER_ROOT"), PER_ROOT)
        check(f"{path.name} reads at most MAX_ROOTS roots",
              _plugin_limits(path, "FILE_ROOTS_MAX"), MAX_ROOTS)
    print("the three implementations hold the same limits")


def test_the_cap_is_on_recency_in_both_shells() -> None:
    """The eight offered are the eight *newest*, in zsh and in bash alike.

    The Python side has always asserted this — `sorted(glob(...))[:PER_ROOT]` is
    alphabetical, so a downloads folder of more than eight files kept the
    alphabetically first and dropped the file that had just arrived, which is the
    one thing this feature exists to offer, and all three implementations did it
    because all three globbed. Only the Python side was pinned, though: a shell
    that lost its `(om)` or its `--zero -N` sort would have failed nothing here,
    since every other assertion in this file looks at the newest file, which is
    first under either ordering.

    So the name that decides it is `zzz…`: alphabetically last, newest by mtime.
    Eight files are not enough — the cap has to bite — and each is written with
    its own timestamp so recency is a fact and not an accident of the order the
    test happened to create them in.
    """
    print("the per-root cap is on recency")
    setup_files()
    env = {"HOME": str(FILE_HOME), "TAI_FILE_ROOTS": str(FILE_DOWNLOADS)}
    names = [f"cap-{i:02d}-file.txt" for i in range(10)]
    names[PER_ROOT] = "zzz-just-arrived.txt"      # newest, and last by name
    base = time.time()
    for i, name in enumerate(names):
        path = FILE_DOWNLOADS / name
        path.write_text("x")
        # Ascending with i, and the replacement written last, so `zzz…` is both
        # the newest and — by name — the one a sort would drop.
        os.utime(path, (base + i * 60, base + i * 60))
    os.utime(FILE_DOWNLOADS / "zzz-just-arrived.txt", (base + 3600, base + 3600))
    for shell in ("zsh", "bash"):
        if not SHELLS[shell]:
            continue
        # Two sessions: taking the answer writes to the line, and the list is
        # asked for on a line that still ends in a word.
        s = Session(shell, env_extra=env)
        try:
            s.run(f"cd {FILE_WORK}")
            s.send("chmod +x ")
            if shell == "zsh":
                got = s.suggestion().split(" PD=")[0]
            else:
                s.write(CTRL_F)
                s.settle()
                got = f"SUG=[{s.line()[len('chmod +x '):]}]"
            check(f"{shell} offers the newest file, not the first by name",
                  got, "SUG=[~/Downloads/zzz-just-arrived.txt]")
            check("no noise", s.noise(), [])
        finally:
            s.close()
        listed = Session(shell, env_extra=env)
        try:
            listed.run(f"cd {FILE_WORK}")
            # A word that matches several files *in the root being read*, so the
            # cap is what decides the list: the oldest is `cap-00` and it is
            # dropped, `cap-09` is the newest of them and it is not.
            if shell == "zsh":
                listed.send("chmod +x ~/Downloads/cap-")
                listed.write(CTRL_SPACE)
                listed.settle()
                shown = menu_entries(listed.menu(clear=False)[1])
                offered = [e for e in shown if "cap-" in e]
                check("zsh lists the capped files", len(offered), PER_ROOT)
                check("zsh and the newest is first", offered[:1], ["cap-09-file.txt"])
            else:
                # bash: ask `_tai_complete` for COMPREPLY with COMP_LINE set by
                # hand, the way the existing test does — the cap lives in that
                # reply, before readline ever draws anything.
                reply = pathlib.Path("/tmp/tai/tai_cap_reply.txt")
                reply.unlink(missing_ok=True)
                listed.run(f"COMP_LINE='chmod +x ~/Downloads/cap-'; "
                           f"COMP_POINT=${{#COMP_LINE}}; COMPREPLY=(); _tai_complete; "
                           f"printf '%s\\n' \"${{COMPREPLY[@]}}\" > {reply}")
                listed.wait_file(reply, "the capped reply list")
                replies = reply.read_text().splitlines()
                check("bash offers the cap's worth", len(replies), PER_ROOT)
                check("bash and the newest of them is first", replies[:1],
                      ["~/Downloads/cap-09-file.txt"])
                check("bash and the oldest is dropped",
                      [r for r in replies if r.endswith("cap-00-file.txt")], [])
            check("no noise", listed.noise(), [])
        finally:
            listed.close()
    for name in names:
        (FILE_DOWNLOADS / name).unlink(missing_ok=True)


def main() -> int:
    setup()
    test_zsh_file_arguments()
    test_bash_file_arguments()
    test_quoted_names()
    test_the_three_implementations_hold_the_same_limits()
    test_the_cap_is_on_recency_in_both_shells()
    check_fixture_intact("the run")
    print("\nOK — lines ending in a file, newest first, quoted where it has to be."
          if not failures else f"\nFAILED ({len(failures)}): {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
