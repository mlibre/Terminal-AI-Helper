# Development

Working on tai. For how it is built, read [architecture.md](architecture.md);
for the rules that came out of reported failures, read
[AGENTS.md](https://github.com/mlibre/Terminal-AI-Helper/blob/main/AGENTS.md).

## Layout

The program is one Rust binary (`cargo build --release`); the shell plugins
stay shell, because ZLE and readline cannot be anything else.

```text
src/main.rs         the entry: argv in, exit code out
src/cli.rs          the verbs: suggest | record | flush | refresh | update | version |
                    discover | purge | forget | uninstall | bench | tune | doctor |
                    web | eval | jev, and the installer's two rc-editing verbs
src/engine.rs       ranker; the W_* weights live at the top, `tai tune` writes the best
                    ones to weights.json and every startup applies them
src/typo.rs         typo tolerance for the first word: `dokcer` is `docker`;
                    `shadow_map` ranks a rare near-duplicate line under its stronger
                    twin, case folded into the comparison
src/store.rs        SQLite log (WAL), schema, oldest-first loader, history import
src/paths.rs        path liveness, `cd` destinations, "does this line end in a file"
src/fresh.rs        the files a path argument could be — the one-shot half
src/web.rs          the read-only localhost dashboard: one page, three GET routes
                    (state, suggest, explain)
src/knowledge.rs    read-only CLI discovery from --help / man / __complete
src/helptext.rs     what a tool's help text is read into: verbs, flags, one probe
src/maintenance.rs  the rebuild lock, reclaimable by pid, boot id and age
src/index.rs        generates the two shell indexes
src/seed.rs         the universal cold-start corpus
src/decisions.rs    bounded Choice decision contract and the safety gate
src/jev.rs          optional hosted Jev adapter (batched; never generates)
src/predictor.rs    one-shot suggest: engine plus the path-liveness policy
src/spool.rs        the shells' batched write side: framed spool, drain, flush
src/engcache.rs     the built engine on disk for one-shot paths, keyed on the store
src/bench.rs        latency, memory, index and stale-path diagnostics
src/tune.rs         coordinate search over the weights (writes weights.json)
src/evaluate.rs     labeled candidate evaluation (+ evaluate_jev.rs, hosted)
build.rs            bakes the VERSION file into the binary's own name

plugins/tai.zsh     the zsh entry: sources the parts in plugins/zsh/
plugins/tai.bash    the bash entry: sources the parts in plugins/bash/
bin/npm-postinstall stages the binary (platform package, release download,
                    or cargo build), then hands over to install.sh
install.sh          the three doors, one install; builds or fetches the binary
scripts/build_deb.sh  compiles and packages /usr/bin/tai + the plugins
## Code style

Idiomatic Rust, small composable modules, no hidden global state, no
speculative abstractions, no dependencies without a clear and substantial
benefit. Cache expensive discovery, keep invalidation obvious, never silently
swallow important errors, and keep security-sensitive behavior deterministic
in code.

## The suites

```text
tests/test.sh               every suite, fastest-failing first (--fast skips the pty)
tests/golden/               the index bytes the Python builder wrote against the
                            migration fixture; the Rust builder must match them
tests/golden_index.rs       the fixture, rebuilt and byte-compared to the goldens
tests/cli_conversation.rs   every verb's conversation: what it prints, what it refuses
tests/web_server.rs         the dashboard: routes, binding, read-only
tests/install_smoke.rs      install, wiring, retarget, autosuggest, uninstall,
                            the sequence rules, the JEV gate against a hostile model
tests/pkg_npm.rs            the npm door: what ships, the postinstall guards, the
                            whole hook with the binary staged
tests/pkg_deb.rs            the package: architecture, contents, the binary runs
tests/support/mod.rs        the pty harness: paths, key names, the index fixture,
                            a real shell on a pty, Session/probe/Ghosts, the store reads
tests/support/screen.rs     a terminal-screen model, so "which cell is selected" is askable
tests/plugin_*.rs           the pty suites, one per theme (lookups, case, menu, draw,
                            isearch, storm, files, wide, index, units)
tests/fakebin/              the PATH the golden and install tests pin, so the
                            installed-command bonus is decided by tools this
                            checkout carries and the bytes are the same everywhere
```

Rust unit tests live beside the code they hold (`src/typo.rs`, `src/paths.rs`,
`src/store.rs`, `src/engine.rs`, `src/spool.rs`, `src/decisions.rs`,
`src/helptext.rs`): the banded distance against a textbook matrix over four
thousand pairs, the recordable gate, the import cursors, the spool framing,
the shadow gates, the help-text shapes the index reads.
## Writing a pty assertion

Every wait must end on something observable — a marker the shell printed, a
file the editor wrote, output that stopped. Never a fixed sleep.

- **Assemble markers inside the shell**, never in the test code that writes the
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

Every plugin test reads an index `tests/support/mod.rs` builds. The fixture
uses the same `WORD_KEY_MAX_DEPTH` and `WORD_CANDIDATE_CAP` values the builder
ships rather than restating them, and the golden suite asserts the two agree
byte for byte — the fixture is the generator's own shape, not a stand-in,
because a stand-in once made wrapper transparency pass in CI while answering
nothing on a real install.

## Working on the shell plugins

**Do not add a process to the keystroke path.** ZLE and readline do the work —
answers travel in globals, not in `$( )`, and a function's stdout is the
terminal. `test -nt` is a builtin and is how the file list is ranked in zsh;
bash pays one `ls -t -1 --zero -N` per root, and that is the most it should
ever pay.

**A rule lives in three places and a test runs all three.** The file-root rule
is `plugins/zsh/files.zsh`, `plugins/bash/files.bash`, `src/fresh.rs`; change
one, change all three, and keep cases in `tests/plugin_files.rs` and the smoke
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
(`tests/support/screen.rs`).

## What to verify

Autocomplete changes — the pty suite covers these; keep a case for each new
rule:

- known-history, cold-start (`ls -` → `ls -la`) and prefix-extension
  (`ls -l` → `ls -la`) completion; ambiguous and empty prefixes, in both shells
- the hint keys: `→` takes the hint in **both** cursor-key modes and still
  moves mid-line; one word on `Alt-F`/`Ctrl-Right`; nothing invented when no
  hint exists; `Tab` never takes the hint but opens and cycles the list;
  a single candidate is completed, not listed — a single *loose* row never
  is: the key arms it instead, `Enter` takes it, because a loose row replaces
  the whole line and is not a completion of the word (`cat z` keeps
  `cat tzz_d` a choice); `Tab` with nothing falls back
  to the shell's own completion. The menu: own line, prompt survival,
  reverse-video selection, directory colour on the name only, wrap-around,
  `Enter` takes and a second runs, any other key dismisses; a commit that
  leaves the line one bare word writes the space after it — a directory's
  slash and a whole learned line take none, and the committed line is not
  glimpsed back (`_TAI_MENU_DISMISS`)
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

Diagnostics: `tai bench` (load time, suggest latency, RSS),
`eval` (candidate coverage), `doctor` (what is indexed and what is held back).

Two costs dominate and both are worth checking after a change:

- **Index size is a startup cost**, paid at every shell start. After touching
  `src/index.rs`, compare `ls -l` on the snapshot and time a `source` of it.
- **The keystroke path** must stay fork-free in zsh and fork-light in bash.
  A change that adds a process there is a regression even if it is fast,
  because it is the only part of the product a user feels on every character.

The one-shot paths (`tai suggest`, `tai web`) load the engine from a disk
cache beside the store (`src/engcache.rs`), keyed on the store's size and
mtime — a new row invalidates it within the same write. `TAI_NO_ENGINE_CACHE=1`
turns it off. **The record path is a spool, not a process per command**: the
plugins append one framed line with `print`/`printf` (measured ~0.01ms) and
ask for `tai flush` when eight records or three idle seconds have piled up —
one binary per batch, which drains, resolves repo/branch per distinct
cwd, and runs the same `is_recordable` gate `tai record` applies. `tai record`
itself is the manual write path and the plugin's fallback when the spool file
cannot be written; it hand-parses its five flags.
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
