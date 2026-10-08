# Development

Working on tai. For how it is built, read [architecture.md](architecture.md);
for the rules that came out of reported failures, read
[AGENTS.md](../AGENTS.md).

## Layout

```text
tai/engine.py       ranker; the W_* weights live at the top and `tai tune` rewrites them
tai/typo.py         typo tolerance for the first word: `dokcer` is `docker`
tai/store.py        SQLite log (WAL), schema, oldest-first loader, history import
tai/paths.py        path liveness, `cd` destinations, "does this line end in a file"
tai/fresh.py        the files a path argument could be — the one-shot half
tai/knowledge.py    read-only CLI discovery from --help / man / __complete
tai/helptext.py     what a tool's help text is read into: verbs, flags, one probe
tai/maintenance.py  the rebuild lock, reclaimable by pid, boot id and age
tai/index.py        generates the two shell indexes
tai/seed.py         the universal cold-start corpus
tai/decisions.py    bounded Choice/Noul decision contract and the safety gate
tai/jev.py          optional hosted Jev adapter (batched; never generates)
tai/predictor.py    one-shot suggest: engine plus the path-liveness policy
tai/cli.py          suggest | jev | record | refresh | update | discover | purge
                    | uninstall | bench | tune | doctor | eval | eval-jev
tai/bench.py        latency, memory, index and stale-path diagnostics
tai/tune.py         coordinate search over the weights (writes engine.py)
tai/evaluate.py     labeled candidate evaluation
tai/evaluate_jev.py the same against a hosted endpoint
tai/__init__.py     nothing: a package root that re-exported its modules cost a
                    circular import, so every caller names the module it wants

plugins/tai.zsh     loader for the zsh plugin: index, lookup, files, menu, widgets
plugins/tai.bash    loader for the bash plugin: index, lookup, files, keys

install.sh          wrapper, rc lines, autosuggestions pause
tests/smoke_env.py          the scratch database, index and imports they share
tests/test_smoke.py         engine, store, path policy, ranking, fresh files
tests/test_smoke_cli.py     learn-on-use, discovery, wrappers, refresh, the index scores
tests/test_smoke_install.py sequence table, history files, safety, lock, install
tests/test_jev.py           the decision contract
tests/plugin_env.py         paths, key names and the index fixture the pty tests share
tests/plugin_pty.py         a real shell on a pty, plus check/probe/Ghosts
tests/plugin_screen.py      a terminal-screen model, so "which cell is selected" is askable
tests/test_plugins.py       lookups: what a word completes to, wrappers, per-shell index
tests/test_plugins_menu.py  the Tab menu: entries, selection, the stem
tests/test_plugins_draw.py  what is drawn: ghost text, hints, colours, load order
tests/test_plugins_files.py a line ending in a file, plus its caps and roots agreement
tests/test_plugins_wide.py  a file name in a double-width script, drawn and highlighted right
tests/test_plugins_index.py a rebuild an open shell picks up, and recording
```

Ten entry points, each a script of assertions that prints what it found, and
each runnable on its own; `./test.sh` runs them all. Three of them share
`smoke_env.py` and six share the `plugin_*` modules, so a scratch database, a
fixture or a key name is written once. Two consequences:

- **A test function that no `main()` calls is not a test.** One sat unrun for a
  while with an expectation the plugin had outgrown — the `region_highlight` span
  of the ghost text — and failed on the first run once it had a `main()` of its
  own. Adding a test means adding the call.
- **The shared modules are the only copy.** The fixture writer, the key names,
  the screen model and the `Session` live in `plugin_*`, the scratch world in
  `smoke_env.py`. A test that defines its own copy of any of those is a second
  answer to a question the harness already answers, and the two will disagree.

### Code style

- Prefer standard-library Python and small, composable modules.
- Keep public functions small and explicit.
- Name functions and files after their responsibility.
- Avoid hidden global state and speculative abstractions.
- Cache expensive discovery, but keep cache invalidation obvious.
- Never silently swallow important errors; handle expected failures safely.
- Keep security-sensitive behavior deterministic in code.
- Do not add dependencies without a clear, substantial benefit.

## Tests

```sh
./test.sh          # everything, ~16s
./test.sh --fast   # skips the pty suite, ~3s
```

The pty suite is ~90% of the runtime and it is the one that matters for anything
touching `plugins/`. It drives real interactive bash and zsh and asserts on what
the terminal actually shows, because calling a plugin's functions from a
non-interactive shell silently skips key bindings, the readline
`READLINE_LINE`/`READLINE_POINT` contract, and prompt-time recording.

Or individually, if you are working on one of them:

```sh
python3 -m py_compile tai/*.py tests/*.py
python3 tests/test_smoke.py
python3 tests/test_smoke_cli.py
python3 tests/test_smoke_install.py
python3 tests/test_jev.py
python3 tests/test_plugins.py            # lookups; the other five entry points
                                         # are test_plugins_{menu,draw,files,wide,index}.py
zsh -n plugins/tai.zsh
bash -n plugins/tai.bash
```

The tests live in `tests/` and find the repository from `__file__`, so each one
runs from any working directory. It always uses a scratch `TAI_DB`, records
nothing by default, and asserts the index fixture is intact at the end — the pty
suite can therefore never write into real history or rebuild the fixture the
other assertions read.

`tests/plugin_screen.py` is a terminal as a grid of columns, because a menu test
has to ask which *cell* is selected rather than which bytes were written. Two
things it models that are easy to miss: a character in a double-width script
takes two columns, the second being a continuation that `lines()` drops and every
other reader can see; and only the escapes a zsh redraw actually emits are
handled, so an unhandled sequence shows up as a wrong screen instead of quietly
passing. Before believing a screen assertion, ask what the screen would have to
be wrong about for the plugin to be innocent — the first wide-character test
failed against a plugin that was right in every column.

### Writing a pty assertion

Every wait must end on something observable — a marker the shell printed, a file
the editor wrote, output that stopped. Never `time.sleep`. A fixed pause costs
that pause on every interaction whether or not it was needed.

Assemble markers *inside* the shell (`_tai_m="__tai_"; _tai_m+="done"`), never in
the Python that writes the command: the shell's own echo of your command contains
the literal text, so a marker the harness can see before the thing it is waiting
for has happened is not a marker. `Session.run` waited for `__tai_done_N__` while
that string sat in the command's own echo, so it returned with the command still
queued — and a test that read a file the command was about to write failed one run
in four and passed the other three. **A marker is evidence only if the thing you
are waiting for cannot appear before the thing you are waiting for.**

A `mark` must not land inside an echo. `s.send("chmod +x ")`, `mark =
len(s.seen)`, `s.write(TAB)` reads as three steps and is two: how much of the echo
has arrived when the mark is taken is a property of the machine, so on a loaded
one the readline listing landed *after* the mark and the assertion failed for a
plugin that was working. Send a line and the key that triggers what you are
waiting for in **one** write, and take the mark before it. The same is true of
everything else with no assertion between it: `probe` typed a prefix, waited for
the shell to go quiet, and *then* sent the keys it was about to test — 20ms a
probe for a pause nobody looked at. Together the two took the pty suite from
18.3s to 11.8s. The `mark` half was latent for a long time and surfaced only
when the completion path grew a `fork` and the timing moved, which is the general
form: **a change that costs time can fail a test that was only ever accidentally
in sync**, and the fix belongs in the test's synchronisation, not in the feature.

A shorter quiet window is not a faster suite, it is a flakier one. The window is
the margin that makes the suite reliable on the machine you did not choose — and a
suite that flakes on a busy one is not comprehensive, it is decorative. Measured:
12ms passed 2 runs in 3 under eight competing loops, 8ms passed 1 in 3; with the
window genuinely at 20ms (see below), 3/3 under 8 loops and 2/2 under 16.

A quiet window that has already elapsed is not a window to wait for again.
`settle` read until a read came back empty, and *every* read ends by timing out,
so a keystroke that produced one burst of output cost **two** windows: 40ms
effective against a constant that said 20ms — which is the kind of number nobody
is measuring, since the measurement below was of a window the suite had never run
with. `_read` now records whether its `select` timed out, and only that door is
silence (`break` is the shell being gone, which is not), so `settle` stops there.
**19.1s → 14.3s, window unchanged at 20ms.** A constant that does not describe the
time actually spent is a claim nothing is checking.

A teardown that cannot fail is a teardown that will not, and it is billed every
time. `Session.close` wrote `exit\n` and waited for EOF; a session that left a
partial line behind — which most did, because a test asserting on a buffer does
not clear it — *appended* `exit` to that line, bash ran `cat tzz_exit`, reported
no such file, and sat there until the deadline. 15s of a 42s suite spent waiting
for shells that were never asked to leave. One `Ctrl-U` first. **A wait with a
deadline is only a backstop, and a deadline that is routinely reached is a bug
being paid for in silence** — profile before shaving anything.

An assertion about the absence of an event needs a fixture of its own. "bash
writes nothing when disabled" shared one database with the iteration before it,
and recording is detached, so that session's writers were still in flight when
this iteration deleted the file and a straggler landed in the fresh one. It failed
about one run in seven, and only *once the suite got fast enough* for a straggler
to still be running — a speed-up that surfaces a race is a finding, not a failure.
**"Nothing happened" is a claim about everything that could have written, so it has
to be a claim about one thing that cannot**: a path no other session was handed,
not a file the last one was using too.

The same rule applies to the answer, not only to the absence of one. Every shell
wrote its editing buffer to one `/tmp/opencode/tai_line.txt`, so a shell left over
from a killed run wrote there once more and the next `wait_file` returned on *its*
output: `Down steps a normal armed menu` read `sudo git ` for a buffer that only
ever held `tzz_a`, and `docker<Tab>` read `--help` for `docker ps`. Nothing in the
plugin was wrong, and nothing in the assertion could have said so. Each session now
names its own dump file — `tai_line_<pid>_<n>.txt`, exported as `$TAI_TEST_DUMP`
and unlinked when it is read — so the file appearing is evidence about *that*
shell. The general form is the paragraph above read the other way round: **an
answer read from shared state is a claim about every writer, and only a path no
other shell was handed is a claim about one.**

A pty with no winsize is a harness that has not told the truth about the terminal.
`pty.fork` leaves the size at zero, and zsh then hands ZLE a `COLUMNS` of its own
invention (83 on this machine) instead of the 80 columns `Screen` emulates — so a
row painted to fit that number is measured against a fiction, and a width
assertion passes or fails for reasons that have nothing to do with the plugin. The
session now sets the size it emulates with `TIOCSWINSZ`, immediately after the
fork. **Before asserting on a width, ask what the terminal would have to be for
the plugin to be innocent.**

### The plugin index fixture

Every plugin test reads an index `tests/plugin_env.py` builds. That fixture is
the generator: it imports `WORD_KEY_MAX_DEPTH` and `WORD_CANDIDATE_CAP` from
`tai.index` rather than restating them, and `test_fixture_matches_generator`
asserts the two agree on the key set and the candidate lists.

A fixture that is a *plausible stand-in* is worse than no fixture. One that wrote
single-word and trailing-space `_TAI_WORD` keys — neither of which the generator
writes — made wrapper transparency pass in CI while answering nothing on a real
install, because those keys are exactly the ones that lookup reads.

## Working on the shell plugins

**Do not add a process to the keystroke path.** The whole design is that ZLE and
readline do the work. `test -nt` is a builtin and is how the file list is ranked
in zsh; bash pays one `ls -t -1 --zero -N` per root because it has no mtime
glob, and that is the most it should ever pay.

**The menu's four drawing rules** — a cycling menu must not rewrite the line, a
multi-line `POSTDISPLAY` starts on its own line, a row is the remainder and the
entry is what is written, and the selection is painted by ZLE — are in "How the
menu is drawn" below, one reported failure each.

**A rule lives in three places and a test runs all three.** When you change one,
change all three and keep a case in the pty tests (`test_plugins_files.py`) and
in the smoke tests (`test_smoke.py` for the Python rule, `test_smoke_cli.py` for
the wrapper and index ones). See "The shell plugin and the Python path must agree"
in [AGENTS.md](../AGENTS.md).

**Assert colour and geometry on an emulated screen, not on the plugin's
arrays.** The characters can be right while nothing is painted. Most of what is
easy to get wrong in `region_highlight` — offsets counted across the line *and*
the post-display, an exclusive end, one array element per region — is invisible
to a test that reads the arrays.

**Drive a new key through a real pty before you offer it.** When a key "does
nothing", the first question is which *widget* it resolves to, and the second is
what the harness sends rather than what the terminal sends. `bindkey` wants
`'^T'` or `$'\e[Z'`; a raw control byte typed into a command line is a different
string.

### How the menu is drawn

Four rules, one topic, and all four are things the arrays look correct without.
Each was a reported failure; the shape of each bug is what makes the rule.

- **A menu that cycles must not rewrite the line.** zsh's own menu completion
  inserts each entry it lands on, so browsing a menu is also a sequence of
  commits, and choosing between three candidates is impossible. Draw the list,
  leave the buffer alone, and let one key commit. Tab cycles.
- **A multi-line `POSTDISPLAY` must start on its own line.** ZLE believes the
  whole post-display is one line. Start a menu after the prompt and a row laid
  out to the terminal width runs off the right edge, the terminal wraps it, the
  right prompt is written over, and ZLE then erases in the wrong place and
  leaves the pieces of the previous menu on screen. A leading newline puts the
  menu at column 0 where the full width is available.
- **A row is what an entry adds, and the whole entry is what it writes.** Ten
  directories under `media/mlibre/B/` drawn with that stem in front of each put
  the same sixteen characters on the screen ten times over — and the line above
  already said it. It also cost columns, because the longest entry sets the
  width of every cell: one 51-character `.iso` name put ten entries into a
  single column on a hundred-column terminal. So the longest prefix every entry
  shares is drawn once, by the prompt line, and a row carries only the
  remainder. Three rules keep that from being a different menu:
  - **A stem has to end at a word boundary**, on `/` or a space. `tzz_a` and
    `tzz_b` share `tzz`, and cutting inside a word leaves `a` and `b` — so a
    shared fragment is not a stem at all, however long it is. Cut to the last
    `/` rather than the last component: `media/x/y1` and `media/x/y2` share
    `media/x/y`, and a stem ending *on* the `y` leaves `1` and `2` as the whole
    of the choice.
  - **`Enter` writes the entry, never the row.** The stem lives in `_TAI_MENU`
    and not in the string that gets sliced, so a change to the drawing cannot
    truncate the line. Assert it: a menu that inserted the suffix it drew would
    be quietly corrupting the line and every other assertion would still pass.
  - **Slice by length, never by pattern.** `${e#$stem}` is wrong because a stem
    taken from a filename can hold `*`, `?` or `[`, and a pattern built from it
    strips the wrong number of characters. The off-by-one in the same area is
    worth naming, since it looked like a working feature: `${rest##*/}` removes
    the delimiter along with everything before it, so the prefix ending *on* it
    is `len - len(tail)` with nothing added. Adding one gives `stemroot/s` and
    every row comes out with a letter bitten off the front — a menu whose
    entries are all wrong and whose selected-cell styling still passes.
- **A selection is drawn by ZLE, not by a character in the text.**
  `region_highlight` reaches the menu's postdisplay, the leading newline and
  every row of it, and the selected cell is drawn in reverse video like any
  other menu's. What it does not tolerate is being written the way the ghost
  text's used to be: **one array element per region, holding `start end attrs`
  in a single string.** Three elements are read as three regions of one
  character each, and nothing is painted at all — a menu whose entries are all
  the right characters and no selection colour, and an argument that "it is a
  widget's postdisplay" explains nothing. Offsets count characters over line and
  post-display together and the end is exclusive, so a cell's start is measured
  while its row is laid out. Two regions over the same characters *combine*
  rather than replacing each other, so a selected directory is one block and not
  a blue name inside a highlighted one.

Assert all four on an emulated screen that keeps attributes, not on the plugin's
arrays (`tests/plugin_screen.py`). The text is right whether or not the colour
is, and every other check passes straight through a menu that draws nothing.

## What to verify

Autocomplete changes:

- known-history completion
- cold-start completion, such as `ls -` → `ls -la`
- a partial word that extends another candidate, such as `ls -l` → `ls -la`
- the right arrow takes the hint in **both** cursor-key modes, and still moves
  the cursor mid-line in both — `ESC [ C` and `ESC O C` sent on purpose
- `Tab` takes the hint, and `Ctrl-Space` (or `Ctrl-T`) lists it — including with
  a hint on screen, and including the `--help` fallback, where `Tab` still lists
- one word of the hint on `Ctrl-Right` (`Alt-F` in zsh — bash keeps `Alt-F` as
  readline's `forward-word`), and nothing invented when there is no hint
- the menu is on a line of its own, and a long prompt and a right prompt survive
  it — asserted on an emulated screen
- the selected cell is painted in reverse video, a directory's name in the
  directory colour, and its trailing slash in neither — also on that screen
- `Tab` again moves the selection without changing the line, and wraps at the end
- a word with exactly one completion is completed, not listed
- a lone match that exists only on `PATH`, with no hint behind it, is shown
  before it is written — the line untouched, and `Enter` the second key
- a learned word that does not extend the line is never offered — `cd ..` must
  not complete `cd t`
- a learned name and a path of the same name are one entry, not two
- a `cd` to a directory that was never there is not offered for its prefix
- `cd` answers with a destination, not `cd ..` — and `cd ..` is still on offer,
  still completes `cd .`, and is still the whole answer on a history with no
  destination in it
- a convention the user has never run ranks below every command they have — read
  off the scores a real `tai refresh` writes, since that is the surface both
  plugins compare and the engine never sees
- each shell reads its *own* index, with `TAI_INDEX` naming the zsh file — read
  out of a live shell, since the pty suite sets one `TAI_INDEX` for both and
  would otherwise never notice
- a rebuild that **removes** a command is picked up too, not only one that adds:
  the arrays are emptied before the file is re-sourced
- `Enter` takes the selected entry, not the first one, and does **not** run the
  line; a second `Enter` runs it
- typing any other key puts the menu away
- `Tab` on a word nothing completes for leaves the line alone and does not crash
- `Tab` with nothing to offer falls back to the hint, then to the shell's own
  completion
- a name on `PATH` that cannot be run is not offered as a command
- `TAI_NO_MENU=1` puts `Tab` back the way it was
- an installed command with no history, which must offer `tool --help`
- documented flags of a tool you use, such as `9router --p` → `--port`
- a wrapped command, such as `sudo git` → `sudo git pull --rebase`
- a rebuilt index, which the *running* shell must pick up at the next prompt
- a command that is not installed, which must offer nothing
- a shell started before the index existed, which must stay quiet
- a line of nothing but spaces, which must not raise `bad array subscript`
- unfamiliar installed CLI candidates
- ambiguous and empty-prefix behavior
- zsh and bash behavior
- a command whose path no longer exists is never suggested
- a line that ends in a file is answered from the filesystem, newest first, in
  zsh and in bash alike — and a learned file that still exists keeps its place,
  while a line that does not end in a file is untouched by it
- no unsafe command is shown as the top suggestion
- indexes remain valid after rebuild
- a shell with `set -e` before the `source` line still starts, and still has
  `set -e` afterwards — the plugin is a guest in the rc file, and one statement of
  ours returning non-zero would otherwise end the session. Assert both halves; a
  guard that leaves errexit off is worse than the bug it prevents
- a file that is not an index is refused with one line and a working shell, and
  `tai refresh` makes it acceptable again — the recovery has to work, or the
  refusal is a dead end

CLI and installer changes:

- `tai refresh` imports, rebuilds, and reports a failed build rather than
  swallowing it
- `tai update` finds its own checkout with no folder the user has to name, and
  explains a non-git install instead of crashing
- the installer's own output: it is the first thing a new user reads, and it
  answers one question — is tai live in my shell now. One line per shell
  (`✓ zsh installed`), one command to copy, nothing else. A shell whose rc file
  could not be written says so by name instead of claiming success, and the
  install carries on to the other one
- the installer's take and `tai uninstall`'s give-back, as a pair: install pauses
  zsh-autosuggestions, a second install does not stack a second block, the
  opt-out `TAI_KEEP_AUTOSUGGEST=1` removes the block an earlier install added
  rather than only declining to add another, and uninstall leaves the user's own
  line alone. **Run both as subprocesses with `HOME`, `ZDOTDIR`, `XDG_DATA_HOME`,
  `TAI_DATA_DIR` and `BIN_DIR` redirected, and never call `cmd_uninstall` in the
  test process**: it reads those out of *this* environment, so the in-process
  call deletes the developer's own database, indexes and dotfile instead of the
  sandbox's. It got one assertion away from doing exactly that — the same
  discipline the installer already needs, and the reason the install tests are
  subprocesses in the first place

## Performance

The diagnostics, and what each one is for:

```sh
python3 tai/cli.py bench     # load time, suggest latency, RSS
python3 tai/cli.py eval      # candidate coverage
python3 tai/cli.py doctor    # what is indexed and what is held back
```

Two costs dominate and both are worth checking after a change:

- **Index size is a startup cost**, paid at every shell start. After touching
  `tai/index.py`, compare `ls -l` on the snapshot and time a `source` of it.
- **The keystroke path** must stay fork-free in zsh. A change that adds a
  process there is a regression even if it is fast, because it is the only part
  of the product a user feels on every character.

`tai tune` writes weights back into `tai/engine.py` through a temporary file, so
an interrupted run cannot leave a checkout that will not import.

## Documentation

Documentation is part of the product.

- Keep it short, concrete, and easy to scan.
- Use realistic commands and expected output.
- Explain the architecture only as much as needed to use or modify it.
- Never document features that are not implemented.
- Prefer one obvious path over multiple complicated options.
- Keep README, CLI help, and architecture diagram consistent.
- State limitations honestly, especially around model latency, unknowns, and local data.
