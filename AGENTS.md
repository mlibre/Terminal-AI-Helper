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
- Do not execute a learned command during discovery, indexing, or evaluation;
  asking an installed tool for `--help` or `__complete` is not running the
  user's line.
- Add capabilities only when they preserve the fast path and low memory.
- Avoid background services unless the user explicitly requests one.
- **Read failures raise; read absence returns empty.** An unreadable store
  looks like a fresh install when `load_rows` returns `[]` on any error, so
  let errors raise and refuse to publish over a good index.
- **An index the shell has read is replaced, not merged.** The arrays are
  emptied first, in both plugins — that is what makes the file the whole truth.

## The behavior lives in the suites

Every suggestion rule this file used to spell out was a bug a user reported
first, and each one is now an executable check: the suites under `tests/` are
the letter of the law, this file keeps the spirit. A rule broken is a red
build. When a check fails, fix the product, never the check — and write the
check before the fix lands, not after.

- `tests/test_smoke*.py`, `tests/test_typo.py`, `tests/test_engcache.py`,
  `tests/test_spool.py`, `tests/test_cli.py` — the engine, the store, the typo
  tolerance, the engine cache, the spool and the CLI: what the history and the
  filesystem promise, what a rebuild may publish, and what a store that cannot
  be read is allowed to look like.
- `tests/test_plugins*.py` — both shells, driven through a real pty and read
  off an emulated screen, so a rule that works in one shell and not the other
  is caught where it would be caught: on the screen.
- `tests/test_rules.py` — the rules themselves, one by one, where this file
  used to carry them as prose.
- `tests/test_web.py` — the one server, and the page it serves.

## Product promises

The line here is the promise; a test somewhere under `tests/` is the proof.

- A suggestion must extend the line, and must exist: an answer equal to what
  was typed is not an answer, and a candidate whose path is gone is not a
  candidate.
- A directory argument is answered by directories that exist here — in the
  ghost, the menu, the loose glimpse, and the completion, in both shells.
- A buffer that arrived from the history is not typed.
- A wrapper must not hide what is behind it.
- An installed command always has an answer: `tool --help` for a tool the
  history never saw is honest; inventing a flag is not.
- A line the shell never ran is not a command: a line whose every recorded
  run was exit 127 — command not found — is hidden from suggestions in both
  rankers, its rows kept in the store for the day the tool exists.
- An installed command outranks an uninstalled word, all evidence equal. The
  engine never asks the filesystem itself: the caller answers `on_path`, once
  per word, and the index resolves the whole vocabulary at rebuild time.
- Generated vocabulary ranks in a band below observed usage, keeps the help's
  own order, and is capped; unused tools are not indexed at all.
- A path is answered by the filesystem, because the history cannot know it;
  `~word` is unknown before it is a path, and unknown removes nothing.
- Learning has to show up where the user is: a record reaches an open shell
  within one debounce, not a hundred records.
- A command the user ran is stored by a builtin append and a batched flush —
  no interpreter per command on the recording path, any more than on the
  keystroke one. The store's `is_recordable` gate is the only gate between
  what was typed and what is learned, whichever transport carried the record.
- The key that fills in a completion must not also run it: `Enter` takes, a
  second `Enter` runs. It was asked for after it shipped the other way — do
  not "fix" it back without being asked.
- One key, one question; one completion is not a list.
- A suggestion has to be reachable by the key, in the mode the terminal is in
  — a key nobody can find is a feature nobody has.

## Craft the suites cannot assert

- The shell plugin and the Python path are three implementations of one rule
  set: change them together, keep a case in both suites. A fourth copy of a
  rule is a test that runs the other three.
- One decision, one function — and a shared environment variable means one
  name. `TAI_INDEX` is one variable with one meaning.
- Do not rank what you cannot measure. A regular expression is code: check the
  class you wrote is the class you meant, and what a transformation reads from
  the value it just replaced.
- A guard placed after the thing it guards cannot fire — ask the question of
  the state *before* the build-up. A guard is only as good as the state it
  inspects.
- A diagnostic must not invent the failure it exists to report: a first run,
  an empty store, and a broken store are different facts.
- The command a user runs when something is broken has to be able to say what;
  the error path is written for a person.
- A sourced file is a guest: its effect on the shell's options outlives the
  load. A shell parameter is not an environment variable; the plugin is the
  only bridge.
- Coerce at the boundary. Measure the index's own load time — it is a startup
  cost, and what is written down is the measurement.
- Keep every regex hot. Run the cheapest vetoes first. Rank first, then cap.
- A quoted subscript range on an array is one element, not N — the offset form
  `"${arr[@]:0:$cap}"`. And `[[ x == *"*" ]]` is a different question in bash
  and zsh.
- Never write to a shared slot to silence yourself, and never read your own
  copy of what the user can see.
- Drive every key through a real pty first — `bindkey` wants `'^T'` or
  `$'\e[Z'`, not a raw control byte — and remember ZLE emits terminfo's
  `smkx`, so `→` sends `ESC O C` in an active prompt.
- A fresh listing is a snapshot with a TTL, not a per-keystroke glob.
- A paste is not a query. Answers travel in globals, never in `$( )`, on the
  keystroke path. What the plugin paints is terminal text, never the stored
  line.

A change is ready when it is useful, understandable, safe, tested, documented,
and does not make the default experience slower or harder.
