"""A real interactive shell on a pty, and the assertion helpers around it.

Every wait here ends on something observable — a marker the shell printed, a
file the editor wrote, or output that stopped arriving — with a timeout only as
a backstop that fails loudly instead of hanging. A fixed pause costs that pause
on every interaction whether or not it was needed, and is still wrong on a
loaded machine.
"""
import fcntl
import os
import pathlib
import pty
import re
import select
import sqlite3
import struct
import termios
import time

from plugin_env import *  # noqa: F401,F403  (paths, key names, failures list)
from plugin_screen import Screen, Timeout, indent, tail


class Session:
    """An interactive shell on a pty with the tai plugin sourced.

    Every wait here ends on evidence rather than on a guess. `run` waits for a
    marker the shell itself printed, `send` waits for the terminal to go quiet,
    and `_dump` waits for the editor to write the file. Timeouts are backstops
    that raise with the terminal tail attached, so a hang becomes a failure with
    a diagnosis instead of a stall.
    """

    QUIET = 0.02          # seconds of silence that means "the shell caught up"
    DEADLINE = 20.0       # backstop for a single wait

    _next_buf = 0

    def __init__(self, shell: str, env_extra: dict | None = None, source: bool = True):
        # This session's own dump file, named so two shells — in this run or in
        # a run that was killed and left one behind — cannot answer each other's
        # questions. See the note on $TAI_TEST_DUMP in plugin_env.setup().
        Session._next_buf += 1
        self.buf = pathlib.Path(f"/tmp/opencode/tai_line_{os.getpid()}"
                                f"_{Session._next_buf}.txt")
        env = dict(os.environ, TAI_DB=str(DB), PS1="P> ", TERM="xterm-256color",
                   TAI_TEST_DUMP=str(self.buf),
                   TAI_BIN=f"python3 {REPO / 'tai' / 'cli.py'}", EDITOR=str(EDITOR),
                   # Keep the run hermetic: no real history file, and the fixed
                   # fixture index instead of the developer's built one.
                   HISTFILE=str(HISTFILE), TAI_INDEX=str(ZSH_INDEX),
                   # Learning on use would run `--help` on whatever these sessions
                   # type and then rebuild TAI_INDEX, replacing the fixture that
                   # the other assertions read. That is a rebuild landing on the
                   # test's own inputs, not a plugin bug.
                   TAI_NO_LEARN="1",
                   # Recording is off by default for the same reason: a stored
                   # command can trigger the 100-command maintenance rebuild in a
                   # detached process, and that process writes TAI_INDEX. It fires
                   # mid-run, and the test that breaks is whichever one happens to
                   # be reading the index at the time — which looks like a ranking
                   # bug in a test that has nothing to do with it. Only
                   # test_recording() turns this back on.
                   TAI_NO_AUTO_RECORD="1")
        env.update(env_extra or {})
        self.shell = shell
        self.chunks: list[str] = []
        self.seen = ""          # everything read so far
        self.closed = False
        self.quiet = False      # did the last read end in a full window of silence?
        self._marker = 0
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(SHELLS[shell], [shell, "--norc", "-i"]
                      if shell == "bash" else [shell, "-f", "-i"], env)
        # Give the pty the size the screen assertions read. `pty.fork` leaves the
        # winsize at zero, and zsh then hands ZLE a COLUMNS of its own invention
        # (83 on this machine) rather than the width the terminal really has — so
        # a row painted to fit that number wraps a screen of 80 and every width
        # assertion is being measured against a fiction. The screen this module
        # emulates is `Screen.COLS` wide; the pty now says so too.
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", Screen.ROWS, Screen.COLS, 0, 0))
        # One write, one wait. The shell reads the whole chunk and runs the lines
        # in order, so the marker on the last line proves the plugin is sourced and
        # the dump key is bound. Asking for each of those separately cost four round
        # trips per session, which was most of the suite's remaining time.
        self.run("\n".join(self._setup_lines(source)))

    # -- waiting ----------------------------------------------------------
    def _read(self, timeout: float) -> bool:
        """Read whatever has arrived. True when something did.

        `quiet` afterwards says the pty then went silent for a whole `timeout` —
        which is the one piece of evidence `settle` exists to collect, and the
        reason it can stop without asking again. The inner loop leaves by one of
        two doors and only one of them is a window that elapsed: `select` timing
        out is the pty saying nothing arrived, and `break` is the shell being gone,
        which is not silence and must not look like it. It is reset first so the
        flag describes this read and never the one before it.
        """
        self.quiet = False
        got = []
        while select.select([self.fd], [], [], timeout)[0]:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                self.closed = True
                break
            if not chunk:            # EOF: the shell is gone
                self.closed = True
                break
            got.append(chunk)
        else:
            self.quiet = True
        if not got:
            return False
        text = b"".join(got).decode(errors="replace")
        self.chunks.append(text)
        self.seen += text
        return True

    def settle(self, quiet: float | None = None) -> None:
        """Read until the shell has stopped talking.

        This is the honest way to know a keystroke was dealt with: the echo and
        the redraw are the shell answering. It costs one `quiet` interval instead
        of a fixed guess, and slower output is handled for free.

        One window, not two. Reading ends by asking "is anything more coming?", and
        that question is only worth asking again when the last read was cut short
        — a read that ended on a timed-out select has *already* waited out a full
        window in silence, so asking once more buys the same answer for another
        `QUIET`. Instrumented: of 272 zsh settles, 39 read nothing at all and the
        other 233 all ended on an empty read, and the same for bash in 21 and 58.
        That second read was the single largest cost in the suite, at 20ms a time.
        The window itself is unchanged.
        """
        window = self.QUIET if quiet is None else quiet
        while self._read(window):
            if self.quiet:
                return

    def wait_re(self, pattern: str, what: str, timeout: float | None = None,
                since: int = 0) -> None:
        """Block until `pattern` shows up in the terminal output.

        `since` limits the search to output produced after that offset, so a
        pattern that also appears in earlier output — the session's own setup
        lines — cannot match by accident.
        """
        rx = re.compile(pattern)
        end = time.monotonic() + (self.DEADLINE if timeout is None else timeout)
        while not rx.search(self.seen, since):
            left = end - time.monotonic()
            if left <= 0:
                raise Timeout(f"timed out waiting for {what}\n"
                              f"--- terminal tail ---\n{tail(self.seen)}")
            # select returns the moment anything arrives, so this poll interval is
            # only the backstop between reads, never a delay before the first one.
            self._read(min(0.01, left))

    def wait_file(self, path: pathlib.Path, what: str,
                  timeout: float | None = None) -> None:
        """Block until `path` exists and has content."""
        end = time.monotonic() + (self.DEADLINE if timeout is None else timeout)
        while True:
            try:
                if path.exists() and path.stat().st_size:
                    return
            except OSError:
                pass
            if time.monotonic() > end:
                raise Timeout(f"timed out waiting for {what} ({path})\n"
                              f"--- terminal tail ---\n{tail(self.seen)}")
            self._read(0.005)

    # -- writing ----------------------------------------------------------
    def write(self, data: str) -> None:
        """Write bytes and return immediately, with no waiting attached."""
        os.write(self.fd, data.encode())

    def send(self, data: str) -> None:
        """Write keys and wait for the shell to have dealt with them.

        The wait ends on the shell going quiet, not on a fixed pause. Control keys
        echo nothing, so there is nothing to match on; printable text is no better,
        because back-to-back writes arrive as one read and the line editor then
        redraws once at the end instead of per character. Waiting for the *last*
        burst of output to end is the only signal that is true in both cases, and
        it costs one `QUIET` interval rather than a guess.

        A tempting refinement is to wait for the shell's own echo of the text, which
        sounds like real evidence instead of silence. It is slower, not faster:
        measured 245s against 16s for the whole suite. See the note on ZLE and
        KEYTIMEOUT for the other trap in this area.
        """
        self.write(data)
        self.settle()

    def run(self, cmd: str) -> None:
        """Run `cmd` and wait for it to finish.

        `cmd` may be several lines; the shell runs them in order, so a marker on
        the last line is proof the whole block ran. The shell prints the marker
        itself, which is why this is evidence and not a guess. `;` rather than
        `&&`, so a failing command still answers instead of hanging the harness.

        The marker is assembled inside the shell, because the terminal *echoes*
        the command before running it: a marker typed literally appears on screen
        twice, and the first is the echo. Waiting for that one returned while the
        command was still queued — which is why a test that wrote a file here
        sometimes read a file that did not exist yet, in about one run in four.
        `_tai_m="__tai_"; _tai_m+="done"` echoes as two fragments, so the only
        place the whole marker appears is where the shell printed it.
        """
        self._marker += 1
        marker = f"__tai_done_{self._marker}__"
        self.write(f'_tai_m="__tai_"; _tai_m+="done_{self._marker}__"; '
                   f'{cmd}; echo "$_tai_m"\n' if "\n" not in cmd
                   else f'_tai_m="__tai_"; _tai_m+="done_{self._marker}__"\n'
                        f'{cmd}\necho "$_tai_m"\n')
        self.wait_re(re.escape(marker), "a command to finish")

    def _setup_lines(self, source: bool = True) -> list[str]:
        """Everything a session needs, as lines for one write.

        The dump key is installed either way — a session that does not source the
        plugin still has to be able to read the editing buffer, or there is
        nothing to assert on.

        A key that writes the editing buffer out, per shell: bash gets this free,
        C-x C-e hands the line to $EDITOR. zsh has no default binding for it, and
        `zle -N` takes a function *name* rather than a code string, so define a
        real function and bind that. The widget writes the buffer on line 1, ZLE's
        own view of the ghost text on line 2, and the Tab menu on line 3.

        Line 2 and line 3 both go through ${(V)}, which renders a newline as a
        literal \n. A menu is a multi-line POSTDISPLAY, and without that the
        first line of it would spill into the next and push every later reading of
        the dump out by one.

        The key is `^Y`, and the choice is worth 12 seconds of the suite. `^X` is
        a *prefix* in zsh's emacs keymap, so a widget bound there does not
        dispatch straight away: zsh waits out `KEYTIMEOUT` (0.4s by default) to see
        whether a longer sequence is coming. The same widget measures 2ms on `^Y`
        and 401ms on `^X`, and it is a fixed cost per press, not a slow one. Pick
        a key that is not a prefix.

        Every statement is a single line with balanced quotes, because an
        interactive shell is a line editor, not a script: a function body spread
        over several lines puts zsh into a `dquote>` continuation prompt and it
        then reads the *next* command as part of the string it never closed.
        """
        lines = [f"source {PLUGINS[self.shell]}"] if source else []
        if self.shell == "zsh":
            lines.append(
                "tai_test_dump() { print -r -- \"$BUFFER\" > \"$TAI_TEST_DUMP\"; "
                "print -r -- \"SUG=[$_TAI_SUGGESTION] PD=[${(V)POSTDISPLAY}] "
                "RH=[${(j: :)region_highlight}]\" >> \"$TAI_TEST_DUMP\"; "
                "print -r -- \"MENU=[${(V)POSTDISPLAY}] "
                "N=[${#_TAI_MENU}] IDX=[$_TAI_MENU_IDX]\" >> \"$TAI_TEST_DUMP\"; }; "
                "zle -N tai_test_dump; bindkey '^Y' tai_test_dump")
        else:
            lines.append(f"export EDITOR={EDITOR}")
        return lines

    def raw(self) -> str:
        return "".join(self.chunks)

    def _dump(self, clear: bool = True) -> list[str]:
        self.buf.unlink(missing_ok=True)
        # C-x C-e in bash aborts the line, so the buffer is safe to read once.
        # The zsh widget only copies it, so clear the line afterwards.
        self.write(CTRL_X_CTRL_E if self.shell == "bash" else DUMP_KEY_ZSH)
        # Both shells end by writing the dump file: bash through $EDITOR, zsh
        # from the widget itself. That file appearing is the synchronisation
        # point, so there is nothing left to guess here.
        self.wait_file(self.buf, "the line dump")
        if self.shell == "zsh" and clear:
            self.write(CTRL_C)
        self.settle()
        got = self.buf.read_text().rstrip("\n").split("\n")
        self.buf.unlink(missing_ok=True)
        return got

    def line(self) -> str:
        """Read the current editing buffer, then abandon the line."""
        got = self._dump()
        return got[0] if got else ""

    def suggestion(self) -> str:
        """ZLE's current ghost text for the buffer (zsh only)."""
        got = self._dump()
        return got[1] if len(got) > 1 else ""

    def menu(self, clear: bool = True) -> tuple[str, str, int, int]:
        """Read the line and the menu standing on it, in one pass.

        Returns the buffer, the menu as drawn, how many entries it holds, and
        which one is marked. The menu is read the way a user reads it — out of
        POSTDISPLAY, with the newlines spelled out — rather than by reaching into
        the plugin's arrays: a menu with the right entries in the right array and
        the wrong mark on screen is still wrong.

        `clear=False` leaves the line alone, which is what a test that walks the
        selection with Tab needs. Abandoning the line closes the menu, and the
        next Tab would then open a fresh one at the first entry.
        """
        got = self._dump(clear)
        line = got[2] if len(got) > 2 else ""
        found = re.search(r"MENU=\[(.*)\] N=\[(\d+)\] IDX=\[(\d+)\]", line)
        if not found:
            return got[0] if got else "", "", 0, 0
        return got[0], found.group(1), int(found.group(2)), int(found.group(3))

    def noise(self) -> list[str]:
        out = []
        for raw in self.raw().replace("\r", "\n").splitlines():
            line = ANSI.sub("", raw).strip()
            if line and NOISE.search(line) and "source" not in line:
                out.append(line)
        return out

    def close(self) -> None:
        # Clear the line before exiting. `exit\n` alone assumes an empty prompt,
        # and a session that left a partial one behind — which most of them do, a
        # test asserting on a buffer and not clearing it — appended `exit` to it
        # instead: bash ran `cat tzz_exit`, reported no such file, and sat there.
        # The shell then never exited, EOF never arrived, and this loop burned its
        # whole five-second deadline. One Ctrl-U, which both readline and ZLE bind
        # to discard the line, and the shell leaves the moment it is asked to.
        self.write("\x15")        # Ctrl-U
        self.write("exit\n")
        end = time.monotonic() + 5.0
        while not self.closed and time.monotonic() < end:
            self._read(0.05)
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}: {got!r}")
    else:
        print(f"  FAIL {name}:\n         got  {got!r}\n         want {want!r}")
        failures.append(name)


def probe(shell: str, keys: str, prefix: str = "", want: str = "",
          env: dict | None = None) -> None:
    """Type `prefix` + `keys` in a fresh shell and check the resulting buffer.

    The noise check comes after `close()` on purpose. `close` reads to EOF, so
    every byte the session produced is in hand; checking for stray stderr before
    the shell has finished writing it means a quiet-looking run, and an extra
    drain to compensate was one of the slower parts of the suite.

    The prefix and the keys go in as **one** write. Nothing is asserted between
    them, so waiting for the shell to go quiet after the prefix buys a quiet
    period and no information — and it is a period per interaction, which is
    what most of this suite's time is. The line editor reads the whole chunk in
    order anyway, so the prefix is dealt with before the keys are looked up.
    """
    s = Session(shell, env)
    s.send(prefix + keys)
    got = s.line()
    s.close()
    noise = s.noise()
    check(f"{shell} {prefix!r}+{keys!r}", got, want)
    check(f"{shell} {keys!r} clean", noise, [])
    if noise:
        # A stray message on the terminal is the whole point of the check, so show
        # where it came from. "command not found: xit" is not a diagnosis.
        print(f"       terminal tail:\n{indent(tail(s.raw(), 400))}")


def tabs_to_list(env: dict) -> int:
    """How many Taps — Tab presses — it takes before the matches are on screen.

    Ends on the thing being asserted: the listing arriving. A fixed number of
    presses would pass whether or not the listing was there.
    """
    s = Session("bash", env_extra=env)
    s.run(f"cd {MENU_DIR}")
    s.send("cat tzz")
    presses = 0
    for presses in range(1, 4):
        mark = len(s.seen)
        s.write(TAB)
        s.settle()
        if "tzz_a" in s.raw()[mark:]:
            break
    s.close()
    return presses



class Ghosts:
    """One zsh session, asked for many prefixes.

    The dump widget copies the buffer out instead of running it, and `_dump`
    clears the line afterwards, so a session survives being read. That matters
    because a session costs a shell fork and a plugin load: asking for the ghost
    text of one prefix per shell paid that for every single assertion, and it
    was the largest remaining cost in the suite.
    """

    def __init__(self) -> None:
        self.s = Session("zsh")

    def of(self, prefix: str) -> str:
        mark = len(self.s.seen)
        self.s.send(prefix)
        # Wait for the shell to echo the prefix before asking for the ghost.
        # Sending the dump key straight after the prefix raced ZLE's redraw: the
        # widget could run before the line had been redrawn for the new text,
        # and the answer was the *previous* prefix's suggestion. That is what made
        # this group fail at random rather than deterministically. The echo is
        # the observable event: it cannot happen before ZLE has displayed the
        # line, and the ghost text is recomputed on that same redraw.
        if prefix:
            self.s.wait_re(re.escape(prefix[-1]), "the prefix echo", since=mark)
        got = self.s.suggestion().split(" PD=")[0]
        if not got.startswith("SUG=["):
            return f"<no suggestion: {self.s.noise()}>"
        return got[len("SUG=["):-1]

    def close(self) -> None:
        self.s.close()


def rows(db: pathlib.Path) -> list[str]:
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        found = [r[0] for r in con.execute("select cmd from commands order by id")]
    except sqlite3.Error:
        found = []
    con.close()
    return found


def await_rows(db: pathlib.Path, expected: list[str],
               timeout: float = 15.0) -> list[str]:
    """The typed commands that have *not* been recorded yet, once they land.

    The plugin's record runs in the background, so the rows appear some time
    after the prompt returns. Polling for them is both faster than a fixed pause
    and honest: it is the only wait here that ends on the thing being asserted.
    """
    end = time.monotonic() + timeout
    missing = expected
    while True:
        got = rows(db)
        missing = [c for c in expected if c not in got]
        if not missing or time.monotonic() > end:
            return missing
        time.sleep(0.01)


def check_fixture_intact(where: str) -> None:
    """Fail loudly if something replaced the hand-written index.

    Every assertion in the pty suite depends on the fixture, and the plugin
    re-reads it from disk. Anything that writes it — the 100-command maintenance
    rebuild, a learn-on-use rebuild — turns a later assertion into a mystery,
    because the test that actually broke ran several tests ago. Naming the
    overwrite is worth more than the assertion it costs.
    """
    for path in (ZSH_INDEX, BASH_INDEX):
        head = path.read_text(errors="replace").splitlines()[0] if path.exists() else ""
        check(f"{where} left the index fixture intact", head.startswith("# tai"), True)
