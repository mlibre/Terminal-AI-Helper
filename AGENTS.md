# AGENTS.md

The rules below are what the product must do. Code style, the test harness and
what to verify for a change: [docs/development.md](docs/development.md).

## Product principles

Build **simple, fast, smart, delightful software** that works immediately.

- Prefer embedded/local operation over daemons or servers.
- No setup, manual configuration, or unnecessary dependencies.
- Optimize for the common path: it should just work.
- Keep the fast path fast and the smart path optional.
- Prefer clear architecture over clever complexity.
- Ship safe defaults and complete functionality out of the box.
- Make failure recoverable and preserve the last good state.
- Write clean, small, understandable code.
- Omit information that does not help users decide, act, or recover. Implementation
  details, disclaimers and negative claims belong only where they change
  expectations or prevent misuse.
- **No cosmetic git commands.** A commit, branch, or push must change behavior
  or documentation, not formatting, ordering, or appearances.

## Project direction

`tai` is an embedded terminal autocomplete system for zsh and bash.

- Native shell lookup on the keystroke path.
- No daemon, socket, server, or per-keystroke Python process.
- Learn from command history and local CLI `--help`/`man` data.
- Use bounded Jev-like decisions for optional semantic reranking and safety.
- Candidate generation may propose commands; code owns execution and safety.
- Keep the local experience fast, private, and useful without an account.

## Architecture rules

- Keep the normal autocomplete path embedded and process-free.
- Use SQLite for durable local data and generated native shell indexes for lookup.
- Rebuild indexes atomically; never leave the shell with a partial index.
- A lock must record its holder and be reclaimable when that holder is gone.
  The pid says whether the process is running, the boot id says whether that
  pid can still mean the same process, and the age settles what neither can.
- Batch bounded model questions. Never use an LLM on every keystroke.
- Treat model output as uncertain input; validate, rank, and gate it in code.
  Safety is decided by `decisions.unsafe_score`, never by the model's own
  `destructive` score, which is the model grading its own homework.
- Do not execute commands during discovery, indexing, or evaluation.
- Add capabilities only when they preserve the fast path and low memory usage.
- Avoid background services unless the user explicitly requests one.
- **Read failures raise; read absence returns empty.** An unreadable store is
  indistinguishable from a fresh install when `load_rows` returns `[]` on any
  error, so let errors raise and refuse to publish over a good index when the
  store is unreadable.
- **An index the shell has read is replaced, not merged.** The arrays are
  emptied first, in both plugins, which is what makes the file the whole truth
  rather than a growing superset of every index that shell has ever read.

## Suggestion rules

Ranking answers *what to show*; these rules answer *what is allowed to be shown*.
Each one exists because of a reported failure.

- **A suggestion must extend the line.** A candidate identical to the typed
  prefix is not an answer and must never win, in the shell plugins or in the
  engine. Look candidates up by the longest *complete* word of the line, not by
  the line itself: the shorter key holds a superset, and the exact key is a
  recall trap (`ls -l` suggests nothing queried as its own key).
- **A suggestion must exist.** A candidate whose path is gone is dropped from
  the index, and the check is conservative by design: a token that cannot be
  read statically is *unknown*, and unknown never removes anything. Only `cd`
  and `pushd`, only the first argument, and never a leading dash are judged as
  paths — `cd -` is "go back".
- **The shell plugin and the Python path must agree.** `_tai_query`,
  `tai suggest`, and the file-root rule in `plugins/zsh/files.zsh`,
  `plugins/bash/files.bash`, and `tai/fresh.py` are three implementations of
  one rule set: change them together, and keep a case in both test suites.
  A fourth copy of the rule is a test that runs the other three, not a fourth
  copy.
- **One decision, one function — and a shared environment variable means one
  name, not one per consumer.** `TAI_INDEX` is one variable with one meaning,
  and each consumer reading it as its own file is a silent split.
- **Generated vocabulary ranks below observed usage.** A convention's score is
  *only* its rank in the corpus, in a band below anything the history saw —
  never a bonus added on top of a real score, which is not a tiebreak but a
  promotion. The band cannot grow into the observed range, and a test asserts it
  on real built scores.
- **A relative move is not a destination.** `..`, `.`, `-` rank below every
  real destination and are never removed; with no destination in the history
  the rule stands aside, because ranking a candidate below nothing would invent
  an order rather than express one.
- **An installed command always has an answer.** `tool --help` for a tool the
  history has never seen is honest; inventing a flag is not. Unused tools are
  not indexed — the plugin already answers them, and an index copy would cost
  startup time for an answer the shell produces for free.
- **A wrapper must not hide what is behind it.** A closed list — `sudo`,
  `doas`, `nohup`, `time`, `nice`, `ionice`, `stdbuf`, `command` — is
  transparent: rank the line behind the wrapper, put the wrapper back. Keep
  `env`, `xargs`, and `sudo FOO=1 cmd` out, and try the wrapper *before* the
  installed-name `--help` fallback.
- **Learning has to show up where the user is.** A plugin re-reads the index
  when the file is newer than its stamp, and that is asserted through a real
  prompt, not by re-sourcing.
- **Do not rank what you cannot measure.** Generated vocabulary keeps the
  order the help lists it in, capped, and a real command outranks it. **A
  regular expression is code** — check the class you wrote is the class you
  meant, and check what a transformation reads from the value it just replaced.
- **How the list is drawn.** Four rules, each a reported failure, all in "How
  the menu is drawn" in [docs/development.md](docs/development.md). Assert all
  four on an emulated screen that keeps attributes, not on the plugin's
  arrays — the text is right whether or not the colour is.
- **A candidate from the filesystem is raw text; a candidate from the history
  is shell text, and they are written by the same key.** The quoting flag
  travels with the candidate (`_TAI_FILES_Q`, `_TAI_MENU_Q`, bash's one
  `_tai_quote` at the accept site), and the hint on screen stays unquoted —
  a preview should look like the file, not like a quoting rule. A learned line
  is already shell text; quoting it again corrupts it.
- **A guard placed after the thing it guards cannot fire.** Ask the question
  of the state *before* the build-up: `from_history - stale`.
- **An answer a shell prints with no newline is printed onto the prompt.**
  Suggestions end with a newline; a command substitution strips it anyway, so
  the omission only ever breaks the terminal it printed to.
- **A file the shell sources is code, so check whose it is before running it.**
  Read the writer's first line plus the shape of the first data line and refuse
  on mismatch, with one line naming the file and `tai refresh`. The writer
  `os.replace`s a temporary file, so a torn index cannot exist. **A guard is
  only as good as the state it inspects.**
- **A safety gate has to cover the spellings of what it covers, not one of
  them.** `rm -rf`, `rm -fr`, `rm --recursive --force` are one intent; so are
  `kill 1234` and `docker system prune -af`. The rules stay blunt: a positive
  costs a suggestion, a negative offers `rm -rf /`, so `echo 'rm -rf /'`
  scoring 1.0 is correct and must not be "fixed".
- **A diagnostic must not invent the failure it exists to report.** A first
  run, an empty store, and a broken store are different facts — `sqlite3.connect`
  creating the file it inspects is how they get conflated.
- **A plugin loaded from an rc file must not change the shell's errexit.**
  Suspend it across the sources, and restoring is the part that matters — a
  guard that leaves `set -e` off is worse than the bug it prevents. Bash's
  statements were measured to succeed with `set -e` on and get no guard.
  **A sourced file is a guest: anything it does to the shell's options outlives
  the load, and anything it lets fail outlives the file.**
- **The command a user runs when something is broken has to be able to say
  what.** The error path is the one a person reads at their worst moment, so it
  is the path written for a person.
- **A suggestion has to be reachable by the key, in the mode the terminal is
  in — and a key nobody can find is a feature nobody has.** ZLE emits terminfo's
  `smkx`, so `→` sends `ESC O C` in an active prompt; the plugin replaces the
  `forward-char` widget (both byte modes), with `zle .forward-char` as the
  fallback. The test that catches it sends `ESC O C`. Ask which keys the user
  actually presses, keep a second binding for keys terminals disagree about
  (`Ctrl-Space` is NUL everywhere, so also `Ctrl-T`), and drive every candidate
  key through a real pty before offering it — `bindkey` wants `'^T'` or
  `$'\e[Z'`, not a raw control byte.
- **The key that fills in a completion must not also run it.** Taking a
  completion and executing it are two decisions. `Enter` takes; a second
  `Enter` runs. It was asked for after it shipped the other way — do not
  "fix" it back without being asked.
- **One completion is not a list — unless nobody has been shown it.** A single
  entry is taken only when it is already in front of the user: a name in the
  current directory, or the current line already reaching it. Otherwise draw
  the list and let `Enter` be the second key. Read the hint out of
  `POSTDISPLAY`, not out of `_TAI_BEST`: the promise being read may be another
  plugin's. A learned name and a path of the same name are one entry —
  de-duplicate on the name without its trailing slash, learned form winning.
- **A `Tab` press is not the keystroke path, but it is not free either.** A
  glob and a hash expansion are fine; testing every name on `PATH` to display
  ten was measured at tens of milliseconds. `$commands` is what a `PATH`
  directory *offers*, not what can run — ask about the path, not the name.
- **One key, one question.** `→` takes the hint, `Ctrl-Space` opens the list,
  `Tab` cycles files/folders/options and never touches the hint. The one
  exception is the `--help` fallback for a command the history has never seen:
  the hint is drawn for `→`, and `Tab` answers the typed word with the honest
  menu. The test that matters is the pair — `docker ` lists, `git <Tab>` lists
  without writing a hint.
- **The list and the hint are the same lookup.** Both read `_tai_lines`;
  duplicating the index lookup is how a rule ends up working in one and not
  the other.
- **A fixture that passes proves only that its inputs match its expectations.**
  The plugin fixture imports `WORD_KEY_MAX_DEPTH` and `WORD_CANDIDATE_CAP`
  from `tai.index` instead of restating them, and
  `test_fixture_matches_generator` asserts the two writers agree — the fixture
  is the generator's own output, not a stand-in.
- **A test nobody calls is not a test.** Splitting the pty suite gave every
  test a `main()`, and the one that had never had one failed on its first run.
  The runner is part of the test.
- **Shared harness state is a fourth implementation of every rule.** Each pty
  session names its own dump file (`$TAI_TEST_DUMP`) so a shell left over from
  a killed run cannot answer for the current one, and the pty is given the
  size `Screen` emulates. A read from shared state is a claim about every
  writer, and only a path no other shell was handed is a claim about one.
- **Before believing a screen assertion, ask what the screen would have to be
  wrong about for the plugin to be innocent.** A double-width character takes
  two columns, the second a continuation: occupied, so a region reaching it is
  seen, and invisible in `lines()`, because the character to its left is the
  whole story.
- **Rank first, then cap.** "Newest first" capped with `sorted(glob(...))[:N]`
  was capped alphabetically before mtime was considered, and answered without
  the newest file. zsh gets it free with `(om)`; bash pays one
  `ls -t -1 --zero -N` per root.
- **A quoted subscript range on an array is one element, not N.**
  `ranked=( "${ranked[1,$cap]}" )` joins the list into one cell; the safe
  spelling is the offset form `"${arr[@]:0:$cap}"`.
- **The remembered line the user means goes above the remembered line that
  nearly is.** A line holding every typed word verbatim ranks first; the
  in-order fuzzy match — the typo tolerance — is the tail it falls into when
  no exact line exists.
- **`~word` is git's revision syntax as often as it is a home directory.**
  Judge a token a path only when `~` is alone, followed by `/`, or names a
  real account; every other `~word` is UNKNOWN, which removes nothing. Ask of
  any resolver: what did the *shell* do with this token?
- **Filter what a rule removes from every place it is read, not the one you
  found.** A stale command must not follow a live one, and must not be a key;
  a key with no answer left is not written at all.
- **Coerce at the boundary.** A `Path` bound into SQLite raises, and the
  `except` reports "not stored" — so `cwd` is coerced before binding, not
  trusted to the annotation.
- **Measure the index's own load time; it is a startup cost.** A 10k-line
  history's index took 572ms to source; pair appends, no dead keys, and bounded
  key depth cut it to ~222ms. `tai bench` prints `index source` per shell with
  the file's size — what is written down is the measurement, because the
  number rebuilds stale. Two savings were measured and deliberately not taken;
  neither is worth a format change in the part of the system with the least
  margin.
- **A shell parameter is not an environment variable, and a plugin is the only
  thing that can bridge that.** `HISTFILE` is not exported, so `tai refresh`
  saw nothing; the plugins export `TAI_HISTORY_FILES` at load time. A tool that
  needs to know something only the interactive shell knows must be told at
  startup.
- **Never write to a shared slot to silence yourself, and never read your own
  copy of what the user can see.** tai does not clear `POSTDISPLAY` it did not
  draw (`_TAI_DREW` is whose), and the keys that take hints read
  `_tai_shown_hint`. Making tai's hints the visible ones is a configuration
  change — `~/.zshrc`, the installer, and `tai uninstall`, which gives back
  whatever it took. On Manjaro there is no line to comment out: the plugin is
  sourced by a system file, so "is it loaded" has to ask zsh.
- **A command name is a word being written, so the lookup answers it
  unfinished.** `_TAI_FIRST` is keyed by the whole first word, so an unfinished
  name is a miss; the plugins take the exact name first, then the names that
  begin with it (zsh `(I)`, bash a loop), bounded by `_TAI_PREFIX_KEYS`. A
  word nothing begins with stays silent rather than reaching for the nearest
  name, and a glob character is not a prefix. Check the other implementation
  before writing down why two things differ.
- **A path is answered by the filesystem, because the history cannot know it.**
  A learned argument that is still a file here keeps the top place; the roots
  are the current directory and the download directories and not their
  subdirectories; a file that does not start with the word being typed is not
  an answer to it; a line ends in a file when the command means one — and a
  bare name is unreadable by shape, while a history row carries no directory.
- **`[[ x == *"*" ]]` is a different question in bash and zsh.** `*' '` does
  not mean "ends with a space" the way it reads, and two tests that look like
  the same thing are the ones to suspect.

## Completion standard

A change is ready when it is useful, understandable, safe, tested, documented,
and does not make the default experience slower or harder.
