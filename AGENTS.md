# AGENTS.md

The rules below are what the product must do. Code style, the test harness and
what to verify for a change: [docs/development.md](docs/development.md).

## Product principles

Build **simple, fast, smart, delightful software** that works immediately.

- Embedded/local over daemons or servers; no setup, config, or dependencies.
- Optimize the common path; keep the fast path fast and the smart path optional.
- Clear architecture over clever complexity; safe defaults; complete out of the box.
- Make failure recoverable and preserve the last good state; write clean, small,
  understandable code; omit information that does not help users decide, act,
  or recover.
- **No cosmetic git commands.**

## Project direction

`tai` is an embedded terminal autocomplete system for zsh and bash.

- Native shell lookup on the keystroke path.
- Learn from command history and local CLI `--help`/`man` data; use bounded
  Jev-like decisions for optional semantic reranking and safety; candidate
  generation may propose commands, code owns execution and safety.
- Keep the local experience fast, private, and useful without an account.

## Architecture rules

- Keep the normal autocomplete path embedded and process-free. No daemon,
  socket, server, or per-keystroke Python process. The one server is `tai
  web`: launched by the user, bound to 127.0.0.1, GET-only, read-only,
  answering through the same engine as the prompt — never on the keystroke
  path, never started by the plugins.
- SQLite for durable local data; generated native shell indexes for lookup.
- Rebuild indexes atomically; never leave the shell with a partial index.
- A lock must record its holder and be reclaimable when that holder is gone:
  the pid says whether the process is running, the boot id says whether that
  pid can still mean the same process, the age settles the rest.
- Batch bounded model questions; never an LLM on every keystroke. Treat model
  output as uncertain input — validate, rank, and gate it in code. Safety is
  decided by `decisions.unsafe_score`, never by the model's own `destructive`
  score, which is the model grading its own homework.
- Do not execute commands during discovery, indexing, or evaluation.
- Add capabilities only when they preserve the fast path and low memory.
- Avoid background services unless the user explicitly requests one.
- **Read failures raise; read absence returns empty.** An unreadable store
  looks like a fresh install when `load_rows` returns `[]` on any error, so
  let errors raise and refuse to publish over a good index.
- **An index the shell has read is replaced, not merged.** The arrays are
  emptied first, in both plugins — that is what makes the file the whole truth.

## Suggestion rules

Ranking answers *what to show*; these answer *what is allowed to be shown*.
Each exists because of a reported failure.

- **A suggestion must extend the line.** A candidate identical to the typed
  prefix is never an answer, in the plugins or the engine. Look candidates up
  by the longest *complete* word of the line, not the line: the shorter key
  holds a superset; the exact key is a recall trap.
- **A suggestion must exist.** A candidate whose path is gone is dropped from
  the index, conservatively: a token that cannot be read statically is
  *unknown*, and unknown never removes anything. Only `cd`/`pushd`, only the
  first argument, never a leading dash — `cd -` is "go back".
- **A directory argument is answered by directories that exist here.** After
  `cd`/`pushd` no file is ever offered, and a relative destination learned
  elsewhere is tested from the directory the user stands in — one builtin
  stat, the same rule in the ghost, the menu, the loose glimpse, the
  completion.
- **A buffer that arrived from the history is not typed.** Up-then-Down moves
  history, and the arrows clear the typed flag on their way to the fallback.
  Opening the list on a recalled line armed it with itself as its only row —
  the reported "stuck in a list".
- **The shell plugin and the Python path must agree.** `_tai_query`,
  `tai suggest`, and the file-root rule in `plugins/zsh/files.zsh`,
  `plugins/bash/files.bash`, `tai/fresh.py` are three implementations of one
  rule set: change them together, keep a case in both test suites. A fourth
  copy of a rule is a test that runs the other three.
- **One decision, one function — and a shared environment variable means one
  name.** `TAI_INDEX` is one variable with one meaning; each consumer reading
  it as its own file is a silent split.
- **Generated vocabulary ranks below observed usage.** A convention's score is
  *only* its rank in the corpus, in a band below anything the history saw —
  never a bonus on top, which is a promotion, not a tiebreak. A test asserts
  the band on real built scores.
- **A relative move is not a destination.** `..`, `.`, `-` rank below every
  real destination and are never removed; with no destination in the history
  the rule stands aside.
- **An installed command always has an answer.** `tool --help` for a tool the
  history never saw is honest; inventing a flag is not. Unused tools are not
  indexed — the plugin answers them for free.
- **A wrapper must not hide what is behind it.** A closed list — `sudo`,
  `doas`, `nohup`, `time`, `nice`, `ionice`, `stdbuf`, `command` — is
  transparent: rank the line behind it, put the wrapper back. Keep `env`,
  `xargs`, and `sudo FOO=1 cmd` out; try the wrapper before the installed-name
  `--help` fallback.
- **Learning has to show up where the user is.** A plugin re-reads the index
  when the file is newer than its stamp, asserted through a real prompt.
- **Do not rank what you cannot measure.** Generated vocabulary keeps the
  help's own order, capped. **A regular expression is code**: check the class
  you wrote is the class you meant, and what a transformation reads from the
  value it just replaced.
- **How the list is drawn.** Four rules, each a reported failure, in "How the
  menu is drawn" in [docs/development.md](docs/development.md). Assert on an
  emulated screen that keeps attributes, not on the plugin's arrays.
- **A candidate from the filesystem is raw text; one from the history is shell
  text, and they are written by the same key.** The quoting flag travels with
  the candidate (`_TAI_FILES_Q`, `_TAI_MENU_Q`, bash's one `_tai_quote`), and
  the hint on screen stays unquoted. A learned line is already shell text;
  quoting it again corrupts it.
- **A guard placed after the thing it guards cannot fire.** Ask the question of
  the state *before* the build-up: `from_history - stale`.
- **An answer a shell prints with no newline lands on the prompt.** Suggestions
  end with a newline; a command substitution strips it anyway.
- **A file the shell sources is code, so check whose it is before running it.**
  Read the writer's first line plus the shape of the first data line and refuse
  on mismatch, naming the file and `tai refresh`. The writer `os.replace`s a
  temporary file, so a torn index cannot exist. **A guard is only as good as
  the state it inspects.**
- **A safety gate must cover the spellings of what it covers.** `rm -rf`,
  `rm -fr`, `rm --recursive --force` are one intent; so are `kill 1234` and
  `docker system prune -af`. A positive costs a suggestion, a negative offers
  `rm -rf /`; `echo 'rm -rf /'` scoring 1.0 is correct and must not be "fixed".
- **A diagnostic must not invent the failure it exists to report.** A first
  run, an empty store, and a broken store are different facts;
  `sqlite3.connect` creating the file it inspects is how they get conflated.
- **A plugin loaded from an rc file must not change the shell's errexit.**
  Suspend it across the sources and restore after — a guard that leaves
  `set -e` off is worse than the bug it prevents. **A sourced file is a
  guest: its effect on the shell's options outlives the load.**
- **The command a user runs when something is broken has to be able to say
  what.** The error path is written for a person.
- **A suggestion has to be reachable by the key, in the mode the terminal is
  in — and a key nobody can find is a feature nobody has.** ZLE emits
  terminfo's `smkx`, so `→` sends `ESC O C` in an active prompt; the plugin
  replaces the `forward-char` widget (both byte modes) with `zle .forward-char`
  as fallback, and keeps a second binding for keys terminals disagree about
  (`Ctrl-Space` is NUL, so also `Ctrl-T`). Drive every key through a real pty
  first — `bindkey` wants `'^T'` or `$'\e[Z'`, not a raw control byte.
- **The key that fills in a completion must not also run it.** `Enter` takes; a
  second `Enter` runs. It was asked for after it shipped the other way — do
  not "fix" it back without being asked.
- **One completion is not a list.** A single entry is taken, not drawn — the
  previous rule drew a one-entry menu for a PATH match the history never saw,
  where every shell simply completes. Read the hint out of `POSTDISPLAY`, not
  `_TAI_BEST`: the promise may be another plugin's. A learned name and a path
  of the same name are one entry — de-duplicate on the name without its
  trailing slash, learned form winning.
- **A `Tab` press is not the keystroke path, but it is not free either.** A
  glob and a hash expansion are fine; testing every name on `PATH` to display
  ten was tens of milliseconds. `$commands` is what a `PATH` directory
  *offers* — ask about the path, not the name.
- **One key, one question.** `→` takes the hint, `Ctrl-Space` opens the list,
  `Tab` cycles and never touches the hint. The one exception is the `--help`
  fallback for an unseen command: the hint is drawn for `→`, and `Tab` answers
  the typed word with the honest menu.
- **The list and the hint are the same lookup.** Both read `_tai_lines`;
  duplicating the lookup is how a rule ends up working in one shell and not
  the other.
- **Rank first, then cap.** "Newest first" capped with `sorted(glob(...))[:N]`
  was capped alphabetically before mtime was considered. zsh gets it free with
  `(om)`; bash pays one `ls -t -1 --zero -N` per root.
- **A quoted subscript range on an array is one element, not N.** The safe
  spelling is the offset form `"${arr[@]:0:$cap}"`.
- **The remembered line the user means goes above the remembered line that
  nearly is.** A line holding every typed word verbatim ranks first; the
  in-order fuzzy match is the tail it falls into when no exact line exists.
- **`~word` is git's revision syntax as often as it is a home directory.**
  Judge a token a path only when `~` is alone, followed by `/`, or names a real
  account; every other `~word` is UNKNOWN, which removes nothing. Ask of any
  resolver: what did the *shell* do with this token?
- **Filter what a rule removes from every place it is read, not the one you
  found.** A stale command must not follow a live one, and must not be a key;
  a key with no answer left is not written at all.
- **Coerce at the boundary.** A `Path` bound into SQLite raises, and the
  `except` reports "not stored" — so `cwd` is coerced before binding.
- **Measure the index's own load time; it is a startup cost.** A 10k-line
  history's index took 572ms to source; pair appends, no dead keys, and
  bounded key depth cut it to ~222ms. `tai bench` prints `index source` per
  shell with the file's size — what is written down is the measurement.
- **A shell parameter is not an environment variable; a plugin is the only
  bridge.** `HISTFILE` is not exported, so the plugins export
  `TAI_HISTORY_FILES` at load time.
- **Never write to a shared slot to silence yourself, and never read your own
  copy of what the user can see.** tai does not clear `POSTDISPLAY` it did not
  draw (`_TAI_DREW` is whose), and the hint keys read `_tai_shown_hint`.
  Making tai's hints the visible ones is a configuration change — `~/.zshrc`,
  the installer, and `tai uninstall`, which gives back whatever it took; on
  Manjaro "is it loaded" has to ask zsh.
- **A command name is a word being written, so the lookup answers it
  unfinished.** The plugins take the exact name first, then the names that
  begin with it (zsh `(I)`, bash a loop), bounded by `_TAI_PREFIX_KEYS`. A
  word nothing begins with stays silent rather than reaching for the nearest
  name, and a glob character is not a prefix. Check the other implementation
  before writing down why two things differ.
- **A path is answered by the filesystem, because the history cannot know it.**
  A learned argument that is still a file here keeps the top place; the roots
  are the current directory and the download directories, not their
  subdirectories; a file that does not start with the word being typed is not
  an answer to it; a line ends in a file when the command means one.
- **`[[ x == *"*" ]]` is a different question in bash and zsh.** `*' '` does
  not mean "ends with a space" the way it reads; two tests that look like the
  same thing are the ones to suspect.
- **The stem a menu cuts may only be text the line already shows.** A stem is
  a reminder, not an abbreviation — `cd tm` with every entry under `tmp/` drew
  `vllm` under a line reading `cd tm`. A stem that runs past the typed word is
  clamped away, even when a boundary makes it look cuttable (`wombat ` under
  `womb`).
- **A fresh listing is a snapshot with a TTL, not a per-keystroke glob.** Each
  root's newest files are held for one second, with one direct listing as the
  fallback — a fresh glob of a 5,000-entry home measured 6.3ms per keystroke.
- **Keep every regex hot.** Alternating two `=~` patterns per line recompiles
  both on every line that fails the first: 6ms → 73ms on a 3,300-line no-match
  query. One tier per pass, one pattern per pass.
- **A record may not wait a hundred records to be worth suggesting.** The
  background rebuild also fires when the index on disk is older than the
  newest stored row, past a short debounce — otherwise a command typed now
  takes the next ninety-nine to reach the hint, which reads as "it is not
  learning".

## Completion standard

- **Unbounded per-line cost is a freeze on the keystroke path.** The loose
  glimpse's fuzzy match ran one letters-as-a-glob pattern per typed word
  against every stored line, and on real learned lines — long `aria2c` URLs —
  that was minutes, per keystroke. Run the cheapest vetoes first (presence
  without a pattern is a C strstr; words with no pattern at all first of all),
  and every literal of the word must be present before any pattern is
  compiled.
- **A paste is not a query.** Text that arrives at once — the bracketed-paste
  envelope, or a paste-sized jump in one redraw — has not been read by anyone,
  so the loose glimpse stays quiet until one typed or removed character
  re-arms the list. `Down` still asks for it directly; the ghost hint is not
  touched.
- **Answers travel in globals, never in `$( )`, on the keystroke path.** In
  zsh a fork is the one cost a redraw cannot pay; in bash `bind -x` makes a
  function's stdout the terminal, so a `$( )` around a lookup was both a fork
  per keypress and the only thing keeping the answer off the screen. The zsh
  question is also remembered beside its answer, so a redraw that did not
  change the line restores it instead of recomputing it.
- **What the plugin paints is terminal text, never the stored line.** A stored
  command can carry raw control bytes, and POSTDISPLAY goes to the terminal
  literally, so an ESC inside a glimpse was an escape sequence on the wire.
  The store refuses them at record time, the engine drops them at build, and
  both plugins filter at the candidate lists — a candidate is only allowed to
  land on screen if it is still text there.

A change is ready when it is useful, understandable, safe, tested, documented,
and does not make the default experience slower or harder.
