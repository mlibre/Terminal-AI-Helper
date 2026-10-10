# Development

Working on tai. For how it is built, read [architecture.md](architecture.md);
for the rules that came out of reported failures, read
[AGENTS.md](https://github.com/mlibre/Terminal-AI-Helper/blob/main/AGENTS.md).

## Layout

```text
tai/engine.py       ranker; the W_* weights live at the top and `tai tune` rewrites them;
                    `explain` names the factors behind one candidate's score, pinned to
                    suggest's totals by the web suite
tai/typo.py         typo tolerance for the first word: `dokcer` is `docker`;
                    `shadow_map` ranks a rare near-duplicate line under its stronger twin
tai/store.py        SQLite log (WAL), schema, oldest-first loader, history import
tai/paths.py        path liveness, `cd` destinations, "does this line end in a file"
tai/fresh.py        the files a path argument could be — the one-shot half
tai/web.py          the read-only localhost dashboard: one page, three GET routes
                    (state, suggest, explain)
tai/knowledge.py    read-only CLI discovery from --help / man / __complete
tai/helptext.py     what a tool's help text is read into: verbs, flags, one probe
tai/maintenance.py  the rebuild lock, reclaimable by pid, boot id and age
tai/index.py        generates the two shell indexes
tai/seed.py         the universal cold-start corpus
tai/decisions.py    bounded Choice decision contract and the safety gate
tai/jev.py          optional hosted Jev adapter (batched; never generates)
tai/predictor.py    one-shot suggest: engine plus the path-liveness policy
tai/spool.py        the shells' batched write side: framed spool, drain, flush
tai/engcache.py     the built engine on disk for one-shot paths, keyed on the store
tai/cli.py          suggest | jev | record | flush | refresh | update | upgrade | version
                    | discover | purge | forget | uninstall | bench | tune | web
                    | doctor | eval | eval-jev
tai/bench.py        latency, memory, index and stale-path diagnostics
tai/tune.py         coordinate search over the weights (writes engine.py)
tai/evaluate.py     labeled candidate evaluation (+ evaluate_jev.py, hosted)
tai/__init__.py     nothing: a package root that re-exported its modules once cost
                    a circular import, so every caller names the module it wants

plugins/tai.zsh     loader for the zsh plugin: index, lookup, files, menu, widgets
plugins/tai.bash    loader for the bash plugin: index, lookup, files, keys

install.sh          wrapper, rc lines, plugin zcompile, autosuggestions pause
scripts/build_deb.sh the .deb package CI attaches to every release
docs/               the website: VitePress sources (config in docs/.vitepress/),
                    built by .github/workflows/docs.yml onto GitHub Pages —
                    npm run docs:build; the toolchain is package.json (vitepress
                    only, dev-time, never on the product path)
scripts/make_demo_gif.py the readme demo, re-recorded from a real zsh on a pty:
                    a scratch world, the four beats keystroke by keystroke, and
                    a GitHub-dark renderer with the keycap strip — `pip install
                    pyte pillow` and it runs anywhere the test suite runs
VERSION             the release version; the release workflow tags v<VERSION>
tests/smoke_env.py          the scratch database, index and imports they share
tests/test_smoke.py         engine, store, path policy, ranking, fresh files
tests/test_smoke_cli.py     learn-on-use, discovery, wrappers, refresh, the index scores
tests/test_cli.py           every verb's conversation: what it prints, what it refuses
tests/test_spool.py         the spool: framed records, drain, flush, learning bounds
tests/test_smoke_install.py sequence table, history files, safety, lock, install
tests/test_deb.py           the package: builds, installs its tree, runs
tests/test_jev.py           the decision contract
tests/test_typo.py          the banded distance against a reference, gates, shadows
tests/test_engcache.py      the engine cache: round-trip identity, every fall-back
tests/test_web.py           the dashboard: routes, binding, engine agreement
tests/plugin_env.py         paths, key names and the index fixture the pty tests share
tests/plugin_pty.py         a real shell on a pty, plus check/probe/Ghosts
tests/plugin_screen.py      a terminal-screen model, so "which cell is selected" is askable
tests/test_plugins.py       lookups: completions, wrappers, per-shell index
tests/test_plugins_menu.py  the Tab menu: entries, selection, the stem
tests/test_plugins_draw.py  what is drawn: ghost text, hints, colours, load order
tests/test_plugins_files.py a line ending in a file, its caps and roots agreement
tests/test_plugins_wide.py  double-width file names, drawn and highlighted right
tests/test_plugins_index.py a rebuild an open shell picks up, and recording
tests/test_plugins_units.py systemctl units: the cache, its corruption, the quiet preload
tests/bin/zsh               a compiled zsh for machines with none: TAI_ZSH, then
                            the system's, then this — the pty suite runs anywhere
```

Seventeen entry points, each a script of assertions that prints what it found,
and each runnable on its own; `./tests/test.sh` runs them all. Three share
`smoke_env.py` and seven share the `plugin_*` modules, so a scratch database, a
fixture or a key name is written once. Two consequences: **a test function no
`main()` calls is not a test** — adding a test means adding the call — and
**the shared modules are the only copy**: a test that defines its own fixture,
key names or `Session` is a second answer to a question the harness already
answers, and the two will disagree.

### Code style

Standard-library Python, small composable modules, no hidden global state, no
speculative abstractions, no dependencies without a clear and substantial
benefit. Cache expensive discovery, keep invalidation obvious, never silently
swallow important errors, and keep security-sensitive behavior deterministic
in code.

## Tests

```sh
./tests/test.sh          # everything, on the order of a minute
./tests/test.sh --fast   # skips the pty suite, a fraction of that
```

The pty suite is roughly half the runtime and it is the one that matters for
anything touching `plugins/`: it drives real interactive bash and zsh and
asserts on what the terminal actually shows, because calling a plugin's
functions from a non-interactive shell silently skips key bindings, the
readline contract, and prompt-time recording.

The zsh the pty suite runs is resolved by `tests/plugin_env.zsh_bin()`: the
`TAI_ZSH` variable, then the system's, then `tests/bin/zsh` — a compiled,
statically-linked zsh committed to the repo, so a machine with no zsh and no
network still runs the whole suite. The system zsh wins when there is one.
Each entry point also runs on its own (`python3 tests/test_smoke.py`, …). The
tests find the repository from `__file__`, always use a scratch `TAI_DB` under
`/tmp/tai`, record nothing by default, and assert the index fixture is intact
at the end.

`tests/plugin_screen.py` is a terminal as a grid of columns, because a menu
test has to ask which *cell* is selected rather than which bytes were written.
A character in a double-width script takes two columns, the second a
continuation that `lines()` drops; and only the escapes a zsh redraw actually
emits are handled, so an unhandled sequence shows up as a wrong screen instead
of quietly passing.

### Writing a pty assertion

Every wait must end on something observable — a marker the shell printed, a
file the editor wrote, output that stopped. Never `time.sleep`.

- **Assemble markers inside the shell**, never in the Python that writes the
  command: the shell's own echo contains the literal text, so the marker can
  appear before the thing it marks has happened.
- **Send a line and the key that triggers what you wait for in one write**, and
  take the mark before it. Separate steps cost a quiet window each and race the
  echo. A quiet window that has already elapsed is not a window to wait for
  again — `settle` stops on `_read`'s own record of a select timeout.
- **State a claim about absence must own is not shared.** Each session names
  its own dump file and scratch paths; `Session.close` writes one `Ctrl-U`
  before `exit` so a partial line cannot swallow the exit and burn the
  deadline; and the pty is given the winsize the screen assertions read
  (`TIOCSWINSZ` right after the fork), because a winsize of zero hands ZLE a
  `COLUMNS` of its own invention.

Before believing a screen assertion, ask what the screen would have to be
wrong about for the plugin to be innocent.

### The plugin index fixture

Every plugin test reads an index `tests/plugin_env.py` builds. The fixture
imports `WORD_KEY_MAX_DEPTH` and `WORD_CANDIDATE_CAP` from `tai.index` rather
than restating them, and `test_fixture_matches_generator` asserts the two
agree on the key set and the candidate lists — the fixture is the generator's
own output, not a stand-in, because a stand-in once made wrapper transparency
pass in CI while answering nothing on a real install.

## Working on the shell plugins

**Do not add a process to the keystroke path.** ZLE and readline do the work —
answers travel in globals, not in `$( )`, and a function's stdout is the
terminal. `test -nt` is a builtin and is how the file list is ranked in zsh;
bash pays one `ls -t -1 --zero -N` per root, and that is the most it should
ever pay.

**A rule lives in three places and a test runs all three.** The file-root rule
is `plugins/zsh/files.zsh`, `plugins/bash/files.bash`, `tai/fresh.py`; change
one, change all three, and keep cases in `test_plugins_files.py` and the smoke
suites.

**Assert colour and geometry on an emulated screen, not on the plugin's
arrays.** The characters can be right while nothing is painted: most of what
is easy to get wrong in `region_highlight` — offsets across line *and*
post-display, an exclusive end, one element per region — is invisible to a
test that reads the arrays.

**Drive a new key through a real pty before you offer it.** When a key "does
nothing", the first question is which *widget* it resolves to, and the second
is what the harness sends rather than what the terminal sends.

### How the menu is drawn

Four rules, all things the arrays look correct without, each a reported
failure:

- **A menu that cycles must not rewrite the line.** zsh's own menu completion
  inserts each entry it lands on, so browsing is a sequence of commits. Draw
  the list, leave the buffer alone, let one key commit.
- **A multi-line `POSTDISPLAY` must start on its own line.** ZLE believes the
  whole post-display is one line; a row laid out after the prompt wraps, and
  ZLE then erases in the wrong place. A leading newline puts the menu at
  column 0.
- **A row is what an entry adds, and the whole entry is what it writes.** The
  longest shared prefix is drawn once by the prompt; a row carries only the
  remainder. Three rules keep that from drifting: a stem ends at a word
  boundary *including* the delimiter (`stemroot/`, not `stemroot/s`); `Enter`
  writes the entry, never the row; slice by length, never by pattern — a stem
  from a filename can hold `*`, `?` or `[`.
- **A selection is drawn by ZLE, not by a character in the text.**
  `region_highlight` reaches the menu's postdisplay as **one array element per
  region, holding `start end attrs` in one string**; offsets count characters
  over line and post-display together, the end is exclusive, and two regions
  over the same characters combine, so a selected directory is one block.

Assert all four on an emulated screen that keeps attributes
(`tests/plugin_screen.py`).

## What to verify

Autocomplete changes — the pty suite covers these; keep a case for each new
rule:

- known-history, cold-start (`ls -` → `ls -la`) and prefix-extension
  (`ls -l` → `ls -la`) completion; ambiguous and empty prefixes, in both shells
- the hint keys: `→` takes the hint in **both** cursor-key modes and still
  moves mid-line; one word on `Alt-F`/`Ctrl-Right`; nothing invented when no
  hint exists; `Tab` never takes the hint but opens and cycles the list;
  a single candidate is completed, not listed; `Tab` with nothing falls back
  to the shell's own completion. The menu: own line, prompt survival,
  reverse-video selection, directory colour on the name only, wrap-around,
  `Enter` takes and a second runs, any other key dismisses
- what may be shown: a non-extending learned line is never offered; a learned
  name and a same-named path are one entry; a `cd` destination must be a
  directory from here; a convention ranks below observed usage; a path-gone
  command, an unrunnable `PATH` name, and an unsafe top suggestion never
  appear; `TAI_NO_MENU=1` restores stock `Tab`
- the loose list: three lines, ~50 columns, quiet under a paste, re-armed by
  one typed or removed character, never opened on a history-recalled line
- the file answer: filesystem newest-first in both shells, a learned file that
  still exists keeps its place, roots and caps agree across all three copies
- lifecycle: a rebuild — including removals — is picked up by the *running*
  shell at the next prompt; each shell reads its own index; a foreign index
  file is refused with one line and a working shell; `set -e` before the
  source survives; an installed command with no history offers `tool --help`;
  a wrapped command completes behind the wrapper

CLI and installer changes:

- `tai refresh` imports, rebuilds, and reports a failed build rather than
  swallowing it
- `tai update` finds its own checkout with no folder the user has to name, and
  explains a non-git install instead of crashing
- the installer's output: one line per shell, one command to copy, and a
  failed rc write says so by name instead of claiming success
- the installer's take and `tai uninstall`'s give-back, as a pair: autosuggest
  pause, no stacking, `TAI_KEEP_AUTOSUGGEST=1`, and the user's own line left
  alone. **Run both as subprocesses with `HOME`, `ZDOTDIR`, `XDG_DATA_HOME`,
  `TAI_DATA_DIR` and `BIN_DIR` redirected — never call `cmd_uninstall` in the
  test process**: it reads those out of *this* environment and deletes the
  developer's own data

## Performance

Diagnostics: `python3 tai/cli.py bench` (load time, suggest latency, RSS),
`eval` (candidate coverage), `doctor` (what is indexed and what is held back).

Two costs dominate and both are worth checking after a change:

- **Index size is a startup cost**, paid at every shell start. After touching
  `tai/index.py`, compare `ls -l` on the snapshot and time a `source` of it.
- **The keystroke path** must stay fork-free in zsh and fork-light in bash.
  A change that adds a process there is a regression even if it is fast,
  because it is the only part of the product a user feels on every character.

The one-shot paths (`tai suggest`, `tai web`) load the engine from a disk
cache beside the store (`tai/engcache.py`), keyed on the store's size and
mtime — a new row invalidates it within the same write. `TAI_NO_ENGINE_CACHE=1`
turns it off. **The record path is a spool, not a process per command**: the
plugins append one framed line with `print`/`printf` (measured ~0.01ms) and
ask for `tai flush` when eight records or three idle seconds have piled up —
one interpreter per batch, which drains, resolves repo/branch per distinct
cwd, and runs the same `is_recordable` gate `tai record` applies. `tai record`
itself is the manual write path and the plugin's fallback when the spool file
cannot be written; it hand-parses its five flags and never imports argparse.
Every `tai` command except `version` and `uninstall` drains a non-empty spool
first, so no answer is ever behind the pending records. The knob names are
read from their own public spellings (`TAI_SPOOL_MAX`, `TAI_SPOOL_SECONDS`) —
a `: ${_TAI_SPOOL_MAX:=8}` default asks about the wrong variable and silently
ignores the environment.

Measured shapes the keystroke path now relies on — each is a promise in
[AGENTS.md](https://github.com/mlibre/Terminal-AI-Helper/blob/main/AGENTS.md) whose letter a suite holds; a change here should
re-measure on a fixture big enough to feel:

- **`_tai_lines` works in C-level parameter expansion** — one `(f)` split, one
  `(M)` filter, one cap; a per-line walk cost ~1.9ms per keystroke on a real
  history, and the argmax for a half-typed name runs over at most
  `_TAI_PREFIX_KEYS` heads.
- **The loose scan is three passes and one full scan**, each tier monotone
  under adding letters, so typing a word out pays one full pass — its first
  letter — instead of one per keystroke.
- **The file answer reads a one-second snapshot, not the disk**, with one
  direct listing as the fallback when the snapshot answers nothing.
- **bash sorts `_TAI_FIRST` once, on the first half-typed name** (the sort is
  one fork, kept off the shell's startup), and binary-searches from there.

## Documentation

Keep it short, concrete and scannable, with realistic commands and expected
output; explain the architecture only as much as needed to use or modify it;
never document features that are not implemented; state limitations honestly.
Keep README, CLI help and the architecture consistent.
