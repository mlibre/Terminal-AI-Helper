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
- A lock must record its holder and be reclaimable when that holder is gone. A
  lock that outlives its process is the one failure that stops maintenance
  *permanently and silently*, because its only symptom is that maintenance did
  not run.
- Batch bounded model questions. Never use an LLM on every keystroke.
- Treat model output as uncertain input; validate, rank, and gate it in code.
  A model may only *choose* from the candidates it was shown, and safety is
  decided by `decisions.unsafe_score` — never by the model's own `destructive`
  score, which is the model grading its own homework.
- Do not execute commands during discovery, indexing, or evaluation.
- Add capabilities only when they preserve the fast path and low memory usage.
- Avoid background services unless the user explicitly requests one.
- **A failure that cannot be seen must not destroy the last good state.** An
  unreadable store is the case to reason from: `load_rows` returned `[]` on any
  error, an empty list is indistinguishable from a fresh install, and so one
  corrupt database rebuilt the whole index out of the 60-line seed corpus,
  replaced 6.2k learned commands, and `tai refresh` printed a green
  `✓ 60 commands indexed`. Read *failures* raise and read *absence* returns
  empty, because those are different facts and only one of them is a first run.
  The build refuses to publish over a good index when the store is unreadable.
- **A lock that cannot answer "is the holder gone?" eventually must.** The
  maintenance lock used to answer *no* to both questions it could not answer — an
  unreadable pid, and a pid that is alive because the machine rebooted and the
  number was reused — and a lock that is never reclaimed stops maintenance
  permanently and silently. Every branch reaches a decision: the pid says
  whether the process is running, the boot id says whether that pid can still
  mean the same process, and the age settles what neither can. A live holder is
  still respected; the rule is that *no* branch is permanent.
- **An index the shell has read is replaced, not merged.** Re-sourcing overwrote
  the value of a key the new index still had and left a key it no longer
  mentioned exactly where it was, so `tai purge --stale` removed a suggestion
  from the store and the running shell kept offering it. The arrays are emptied
  first, in both plugins, which is what makes the file the whole truth rather
  than a growing superset of every index that shell has ever read.

## Suggestion rules

Ranking answers *what to show*; these rules answer *what is allowed to be shown*.
They exist because each one was a reported failure.

- **A suggestion must extend the line.** A candidate identical to the typed prefix
  is not an answer and must never win, in the shell plugins or in the engine. A
  candidate that does not *start with* the prefix is not an answer either, and
  the list is where that is easiest to get wrong: every `cd …` line in the history
  offers its next word, so `cd ..` offers `..` and `cd p` would be answered by
  `git status` offering `status`. Both were reported, and both were the list
  asking a weaker question than the hint asks — the hint filters on "extends the
  line" inside `_tai_best`, so the list must too. Look candidates up by the
  longest *complete* word of the line, not by the line itself: the index writes
  one key per cumulative word boundary, so the shorter key holds a superset and
  the exact key is a recall trap (`ls -l` suggests nothing queried as its own
  key).
- **A suggestion must exist.** Rank by frequency answers "how often did you run
  this", never "does this still work". A candidate whose path is gone is
  dropped from the index, and the check is conservative by design: a token that
  cannot be read statically is *unknown*, and unknown never removes anything.
  A bare name is not shaped like a path, so shape alone is not enough — `cd`'s
  argument is a directory by what `cd` means, and judging it that way is what
  keeps a `cd ter` that failed from being offered for `cd te`. Only `cd` and
  `pushd`, only the first argument, and never a leading dash, because `cd -` is
  "go back" and judging it as a path named `-` would drop a command that works.
- **The shell plugin and the Python path must agree.** `_tai_query` and
  `tai suggest` are two implementations of one rule set. When a rule changes,
  change both, and keep a case in both `tests/test_plugins_files.py` and
  `tai/paths.py` / `test_smoke.py`. There is now a third implementation:
  `plugins/zsh/files.zsh`, `plugins/bash/files.bash` and `tai/fresh.py` all read
  the same roots, in the same order, behind the same three limits, and the shell
  ones may not import the Python one. That is only maintainable because the
  limits are named in each and the roots are one variable (`TAI_FILE_ROOTS`) for
  all three — a fourth copy of the rule should be written as a test that runs
  the other three, not as a fourth copy.
- **One decision, one function — and a shared environment variable means one
  name, not one per consumer.** Two copies of the same rule drift silently
  because each one is correct on its own: the zsh index writer capped a word
  key's candidates at 20 and the bash writer, three lines below it, did not, so
  bash held 60 where zsh held 20 — 96KB of index for keys the menu shows ten of,
  and the two plugins reading a different number of answers for the same prefix.
  Both now call `_word_value`. The same shape bit `TAI_INDEX`, which Python read
  as the zsh file and the bash plugin read as *its* file: with it set, bash
  sourced the zsh snapshot and `bash-index.bash` was written twice a day and read
  by nobody. It is now one variable with one meaning, and the pty suite could
  never have caught it because it sets one `TAI_INDEX` for both shells.
- **Generated vocabulary ranks below observed usage.** A seed or a `--help`
  candidate is a convention, not evidence. Order a convention by its own rank
  step, large enough that ordinary recency noise cannot flip it, and never give
  it recency credit for a timestamp it never had. A convention's score is
  *only* its rank in the corpus, in a band below anything the history saw — never
  a bonus added on top of a real score, which is not a tiebreak but a promotion.
  That was the `SEED_BONUS` of 1500, and it inverted the rule it existed to
  express: `cd ..` (4 runs, plus ~2.04 of corpus bonus) outranked a project
  directory the user had visited 10 times, 6226 to 4963, with 15 seeds above the
  best real command in the whole index. The band is `len(corpus) * step`, so it
  cannot grow into the observed range however long the corpus gets — that gap
  *is* the invariant, and a test asserts it on real built scores.
- **A relative move is not a destination.** `..`, `.` and `-` are true in every
  directory that has ever existed, so how often they were typed is evidence about
  how often the user walks back up, not about where they want to be. They rank
  below every real destination and are never removed — `cd ..` is a command that
  works, and `cd .` still completes to it, because the rule is about *ranking*,
  not about hiding. The reported case was `cd` answering `cd ..` on a history
  that said where the user actually goes. With no destination in the history the
  rule stands aside, and `cd ..` is the whole answer: ranking a candidate below
  nothing would invent an order rather than express one.
- **An installed command always has an answer.** `tool --help` for a tool the
  history has never seen is honest; inventing a flag is not. The check is a
  shell builtin hash lookup, so it costs nothing and forks nothing. That is also
  why unused tools are *not* indexed: the plugin already answers them, and an
  index copy would cost two entries and four word keys per tool at every shell
  startup for an answer the shell can produce for free.
- **A wrapper must not hide what is behind it.** The index is keyed on the whole
  line, so `sudo git` finds nothing on a history full of `git ...`. A closed
  list of wrappers — `sudo`, `doas`, `nohup`, `time`, `nice`, `ionice`,
  `stdbuf`, `command` — is transparent: rank the line behind it, put the wrapper
  back. Keep the list closed, and keep out the wrappers that change what follows
  them (`env`, `xargs`) and the forms whose remainder is not the wrapped command
  (`sudo FOO=1 cmd`). Try the wrapper *before* the installed-name `--help`
  fallback, or the fallback answers first and the wrapper is never examined.
- **Learning has to show up where the user is.** Reading a tool's help and
  rewriting the index is only half the feature; the shell that was already open
  holds the old arrays, so the plugin re-reads the index when the file is newer
  than a stamp it touches. Assert it through a real prompt, not by re-sourcing.
- **Do not rank what you cannot measure.** Generated vocabulary keeps the order
  the help lists it in, capped, and a real command outranks it. A flag you have
  used is known to be useful; a flag you have not is not, and inventing a
  usefulness order for a large tool's options is a guess wearing a number.
  **What the parser makes of a help text is a suggestion, so the parser is the
  place to be careful.** Two ways it offered commands that do not exist, both in
  the same function: a guard written with a doubled backslash where a word
  character was meant — inside a raw string that is a literal backslash, the
  letter `w` and a dash, so it blocked a token only after `w` or a dash, and
  `[--help]` yielded the subcommand `elp` while `[-abc]` yielded `bc`; and a
  list reordered from the value it had just been reassigned, so every long meta
  option fell out of both lists and `--version` was never offered at all. The
  first is invisible in review and the second looks like a reordering, so: **a
  regular expression is code — check the class you wrote is the class you meant,
  and check what a transformation reads from the value it just replaced.**
- **How the list is drawn.** Four rules, one topic, and all four are things the
  arrays look correct without: a menu that cycles must not rewrite the line; a
  multi-line `POSTDISPLAY` must start on its own line or ZLE erases in the wrong
  place; a row is what an entry *adds* while the whole entry is what it *writes*;
  and the selection is drawn by ZLE through `region_highlight`, never by a
  character in the text. Each one, and the reported failure behind it, is in
  "How the menu is drawn" in [docs/development.md](docs/development.md).

  **Assert all four on an emulated screen that keeps attributes, not on the
  plugin's arrays.** The text is right whether or not the colour is, and every
  other check passes straight through a menu that draws nothing.
- **A candidate from the filesystem is raw text; a candidate from the history is
  shell text, and they are written by the same key.** `cat` answered
  `My Document.pdf` and every take path wrote it into the line bare, so what ran
  was `cat My Document.pdf` — two arguments, one of them a file that does not
  exist. It looks like it worked: there is a suggestion and the suggestion is
  the right file. So the flag travels *with* the candidate (`_TAI_FILES_Q`,
  `_TAI_MENU_Q`, and bash's one `_tai_quote` at the accept site) rather than
  being guessed when the line is written, and the hint on screen stays
  unquoted — a preview should look like the file, not like a quoting rule.
  Quoting everything is the other half of the same bug: a learned
  `cat "my file.txt"` is already shell text and would become `cat \"my`.
- **A guard placed after the thing it guards cannot fire.** `build()` refused to
  publish an index when every stored command was stale — and asked whether
  `eng.cmds` was empty, three lines after the seed corpus and the generated tool
  vocabulary had been put *into* `eng.cmds`. The state it inspected was the one
  it had just filled, so the check was unreachable, and the case it exists for
  published 69 conventions over the user's history with a green ✓. Ask the
  question of the state *before* the build-up: `from_history - stale`.
- **An answer a shell prints with no newline is printed onto the prompt.**
  `tai suggest god` wrote `godot .` and left the cursor there, so the next prompt
  appeared as `godot .user@host %`. Nothing documents that, and a command
  substitution strips the newline anyway, so the omission only ever broke the
  terminal it printed to.
- **A file the shell *sources* is code, so check whose it is before running it.**
  The index is shell text by design — that is what makes startup an array
  assignment instead of a parse — and a file that is not an index was executed:
  in bash an unterminated `(` does not stop at the file, it leaves the parser
  mid-construct, so the plugin's *own* next line ran as a command name and the
  shell printed `No such file or directory` on every start, from a file the user
  never touched. One builtin read of the writer's first line, plus the shape of
  the first data line, refuses it. The check deliberately does not try to catch a
  torn file: the writer `os.replace`s a temporary file, so a torn index cannot
  exist. **A guard is only as good as the state it inspects** — and the first
  version of this one passed every file, because a second `< "$file"` opens the
  file again and reads the *first* line twice.
- **A safety gate has to cover the spellings of what it covers, not one of them.**
  `unsafe_score` matched `rm -rf` and not `rm -fr` — the same command with the
  flags the other way round, which is how it is written when the force is what the
  person had in mind — nor `rm --recursive --force`, nor `kill 1234`, nor
  `docker system prune -af`. The general form is that a pattern is written once,
  against the spelling in the report, and every other spelling of the same intent
  walks past it. The other direction is the deliberate trade: a false positive
  here costs a suggestion, a false negative offers `rm -rf /` as a grey hint, so
  the rules stay blunt and `echo 'rm -rf /'` scoring 1.0 is correct and must not
  be "fixed".
- **A diagnostic must not invent the failure it exists to report.**
  `tai doctor` told a machine that had never run tai that its database schema was
  out of date — because `sqlite3.connect` *creates* the file it was inspecting,
  found no `commands` table in the database it had just made, and called that an
  old schema. A first run, an empty store and a broken store are three different
  facts and the diagnostic exists to tell them apart.
- **A plugin loaded from an rc file must not change the shell's errexit.**
  `set -e` belongs to the user, and one of our statements returning non-zero
  takes their *session* down at startup — on the last line of a file they never
  edited. It was not even one of ours: the load calls zsh's own
  `add-zle-hook-widget`, whose internal `(( del ))` returns false on a shell that
  has no such hook yet, and `ERR_EXIT` ended the shell. `plugins/tai.zsh`
  suspends errexit for the five `source` calls and restores it as its last act —
  restoring is the part that matters, because a guard that quietly leaves `set -e`
  off is a worse bug than the one it prevents. bash needs none of it and gets
  none: every top-level statement in `plugins/bash/` was measured to succeed with
  `set -e` on, so a guard there would be a claim nothing checks. The general form:
  **a sourced file is a guest — anything it does to the shell's options outlives
  the load, and anything it lets fail outlives the file.**
- **The command a user runs when something is broken has to be able to say
  what.** `~/.local/bin/tai` is two lines and execs the checkout's `cli.py`, so
  moving or deleting that folder — the one copy, since nothing is installed
  outside it — answered `python3: can't open file …: [Errno 2] No such file or
  directory`, which names no cause and offers no next step. One `[ -r … ]` test
  now says which path is gone and what to do instead. The general form: **the
  error path is the one a person reads at their worst moment, so it is the path
  that has to be written for a person** — and it is the path nothing tests until
  something has already gone wrong.
- **A suggestion has to be reachable by the key, in the mode the terminal is in
  — and a key nobody can find is a feature nobody has.** `→` did nothing for a
  hint on screen, and the plugin looked correct: it bound `^[[C`. Once ZLE has
  emitted terminfo's `smkx` at line-init — i.e. for the whole time the prompt is
  waiting — the terminal is in application-cursor mode and sends `ESC O C`, and
  there **neither** `^[[C` **nor** `^[[OC` is ever looked up (`bindkey kcuf1` is
  `undefined-key`). So the plugin replaces the `forward-char` *widget*, with
  `zle .forward-char` as the fallback, and catches both.
  - **The test that catches it sends `ESC O C`.** A suite that only ever writes
    normal-mode bytes passes a plugin no user can use. When a key "does nothing",
    the first question is which *widget* it resolves to; the second is what the
    *harness* sends rather than what the terminal sends.
  - **The list key was `Ctrl-T` and the user asked which key it was.** Ask which
    keys they actually press before choosing one, and keep a second binding for
    the keys terminals disagree about: `Ctrl-Space` is the easy one and every
    xterm sends NUL for it, so the same widget is also on `Ctrl-T`.
  - **Then drive every candidate key through a real pty before offering it.**
    Two of the first three probes said a key "did nothing" when the plugin was
    fine and the probe's own quoting had bound `\${^T}` instead of `^T`.
    `bindkey` wants `'^T'` or `$'\e[Z'`; a raw control byte typed into a command
    line is a different string.
- **The key that fills in a completion must not also run it.** `Enter` on a marked
  directory stopped at `cd Movies/` with the shell where it was, because taking a
  completion and executing it are two decisions and binding them to one key makes
  the first one irreversible: you may have wanted the rest of the path, or another
  argument, or to read what you were about to run. The second `Enter` runs it.
  This was asked for after it shipped the other way round, so it is a choice and
  not an obvious one — do not "fix" it back without being asked.
- **One completion is not a list — unless nobody has been shown it.** A list of one
  has nothing to look at and nothing to choose between, so the key takes it, which
  is what every shell does when a completion is unambiguous. The exception is the
  one thing the user has *not* seen: `exe` in a shell whose only `exe*` was a
  script nobody had ever run got written into the line by one `Tab`, with the hint
  saying nothing — the hint and `Tab` answering two different questions about the
  same three letters. So a single entry is taken only when it is already in front
  of them: `_tai_menu_takeable` asks whether the entry is a name in the current
  directory, which `ls` would show, or whether `$BUFFER$POSTDISPLAY` — the line as
  the last redraw left it — already reaches that word. Otherwise the list of one is
  drawn and `Enter` is the second key. Read the hint out of `POSTDISPLAY`, not out
  of `_TAI_BEST`: the hint on screen is the promise being read, and it may be
  another plugin's. And a name tai has learned and a path in the current directory
  are one completion said twice — de-duplicate on the name without its trailing
  slash, and let the learned form win so the list agrees with the hint.
- **A `Tab` press is not the keystroke path, but it is not free either.** A glob
  and a hash expansion are both fine; testing every name on `PATH` to display ten
  of them was measured at tens of milliseconds. And `$commands` is a list of names
  a `PATH` directory *offers*, not of commands that can be run: a file with no
  execute bit and a directory are both in it, and `whence` calls neither a
  command. Ask about the path, not the name.
- **One key, one question.** `Tab` took the hint *and* opened the list, so what
  it did depended on what happened to be on screen, and a readable hint with a
  menu opened over it was the system asking a question it had already answered.
  The question a key asks must not depend on the screen: `→` takes the hint,
  `Ctrl-Space` opens the list, and `Tab` cycles — files, folders, options — and
  never touches the hint. The one exception is the `--help` fallback for a
  command the history has never seen: that is tai guessing at what you meant from
  no evidence, so the hint is drawn (for `→`) and `Tab` answers the *typed* word
  with the honest menu. The test that matters is the pair — `docker ` lists, and
  `git <Tab>` lists `pull` and `status` without writing a hint — because either
  half alone passes a plugin that made the wrong rule.
- **The list and the hint are the same lookup.** A word the hint would suggest is
  a word the list can offer, so both read `_tai_lines`. Duplicating the index
  lookup in a second place is how a wrapper rule or an `--help` fallback ends up
  working in one and not the other.
- **A test fixture is a stand-in until it is the generator, and a stand-in
  measures itself.** Every plugin test reads an index `tests/plugin_env.py`
  builds. That fixture wrote one `_TAI_WORD` key per prefix *starting at one
  word*, plus the same key with a trailing space, under a comment saying it
  mirrored `tai/index.py` — which writes neither, because the command-name list
  lives once in `_TAI_FIRST` and every lookup is `${prefix% *}`. The two extra
  families manufactured exactly the key wrapper transparency reads, so
  `sudo git` was green in CI and answered **nothing** on a real install: `git`,
  `git st`, `sudo ls -l`, `doas docker` all dead in zsh and bash while
  `tai suggest` was right. The general form: **a test that passes proves only
  that its inputs match its expectations, so the inputs have to be produced by
  the code under test** — the fixture now imports `WORD_KEY_MAX_DEPTH` and
  `WORD_CANDIDATE_CAP` from `tai.index` instead of restating them, and
  `test_fixture_matches_generator` asserts the two writers agree on the key set
  and the candidate lists, so a drifting fixture fails by name.
- **A test nobody calls is not a test.** `test_zsh_ghost_styling` asserted a
  highlight span of `0 3` and had been failing since the plugin began counting
  the hint from the end of the buffer, because no `main()` ever called it.
  Splitting the suite gave every test a `main()` that does, and that one failed
  on its first run: the runner is part of the test.
- **The harness's own shared state is a fourth implementation of every rule.**
  Every shell wrote the line it had read back to one `/tmp` file, so a shell left
  over from a killed run wrote there once more and the next wait returned on *its*
  answer: a menu test read `sudo git ` for a line that only ever held `tzz_a`, and
  `docker<Tab>` read `--help` for `docker ps` — failures that name a plugin and
  prove nothing. Each session now names its own dump file (`$TAI_TEST_DUMP`), and
  the pty is given the size `Screen` emulates, because a zero winsize had zsh
  reporting `COLUMNS=83` for a terminal of 80. **A read from shared state is a
  claim about every writer, and only a path no other shell was handed is a claim
  about one.**
- **A model that cannot see a bug will report the plugin as broken instead.**
  `Screen` gave every character one column, so a redraw of a line holding a
  Japanese name interleaved with itself and a menu whose selection was exactly
  right measured as three columns out — the harness, not the plugin, and the
  first draft of the test "failed" against correct code. A wide character now
  takes two columns and the second is a continuation: occupied, so a region that
  reaches it is seen, and invisible in `lines()`, because the character to its
  left is the whole story. The general form: **before believing a screen
  assertion, ask what the screen would have to be wrong about for the plugin to
  be innocent.**
- **A cap applied to the wrong ordering is the feature failing, not a
  shortlist.** "The files that are here, **newest first**" was capped with
  `sorted(glob(...))[:PER_ROOT]` — alphabetical, applied *before* mtime was
  considered — so a Downloads folder holding `aaa1.mp4 … aaa12.mp4` and
  `zzz-just-downloaded.AppImage` answered with eight `aaa*` files and never the
  download. That is the one case the feature exists for, and all three
  implementations did it, because all three globbed. The same reasoning applies
  to any "best N": **rank first, then cap.** zsh gets it free with `(om)`
  (ordered by mtime, newest first) — note `*(-.)` looks like the same thing and
  is not stable — and bash pays one `ls -t -1 --zero -N` per root, where
  `--zero` is what makes a newline in a filename not a row break and `-N` is
  what stops a name being quoted. The cap is still a shortlist: the answer is
  `PER_ROOT` files of the *newest* files.
- **A quoted subscript range on an array is one element, not N.**
  `ranked=( "${ranked[1,$cap]}" )` trimmed the loose list to a single word
  holding every entry joined by spaces — 22 lines went in, one joined cell
  came out, and "the loose list is a glance" drew as one row. The unquoted
  `${arr[a,b]}` would split nothing in zsh but can glob-expand, so the safe
  spelling is the offset form: `"${arr[@]:0:$cap}"`. The older trim passed
  tests because a prefix rarely matched more than a capful of lines; the user
  whose index matched 256 saw the one-cell row.
- **The remembered line the user means goes above the remembered line that
  nearly is.** `_tai_loose` matched a typed word as "every letter in order",
  so `forest` surfaced every aria2c URL with an f…o…r…e…s…t in it while
  `cd tmp/fun-game/beauty-forest/` ranked by raw score below the noise. A
  line holding every typed word *verbatim* now ranks first, and the fuzzy
  in-order match — the typo tolerance — is the tail it falls into when no
  exact line exists. Same for the ghost list's reasoning generally: a weaker
  matcher widens recall only when the strong one returns nothing.
- **`~word` is git's revision syntax as often as it is a home directory.**
  `expanduser("~main")` hands the token back unchanged because no account called
  `main` exists, `exists()` then says no, and MISSING is the only verdict that
  removes a command — so `tai purge --stale` destroyed `git diff ~main`,
  `git log ~HEAD`, `git rebase ~origin/main` and `make ~build`, four real
  commands with no missing path in them. A token is judged a path only when
  `~` is alone or is followed by `/`, or when the name is a real account; every
  other `~word` is UNKNOWN, which removes nothing. Ask of any resolver: **what
  did the *shell* do with this token?** It expanded nothing here, so there was
  nothing to resolve.
- **Filter what a rule removes from every place it is read, not the one you
  found.** A command whose paths were gone was dropped from `cmds`, and therefore
  from `_TAI_SCORE`, `_TAI_FIRST` and `_TAI_WORD` — and left in `eng.seq`, which
  is written verbatim, so it came back on an **empty** prompt, where the hint is
  whatever followed the last command. Two directions, both needed: a stale
  command must not *follow* a live one, and must not be a *key*, because
  `_TAI_SEQ['cd /nowhere/gone']` is read right after the user ran that command.
  Filtering also emptied some keys, and `_TAI_SEQ[k]=''` is read as one empty
  candidate, so a key with no answer left is not written at all.
- **A `Path` bound into SQLite raises, and the `except` reports "not stored".**
  `append_and_count` took `cwd` as given and bound it; a `Path` is not a type
  sqlite3 will bind, so it raised `ProgrammingError` inside the handler that
  returns `(False, count)` — a whole history silently dropped, with a green
  `✓ N commands indexed`. Coerce at the boundary rather than trusting the
  annotation the caller did not read.
- **Measure the index's own load time; it is a startup cost.** A 10k-line history
  is 6.2k distinct commands, and the index shipped took **572ms** to `source` —
  half a second on every zsh start, found by measuring rather than by reasoning.
  Three things cut it by most of that, none of them a cache or a lazy load:
  - **A pair append, not a call.** `_TAI_X+=('key' 'value')` takes the key as an
    ordinary word, never re-parsed as code, and costs one builtin call. Passing
    the key to a helper as `$2` is also safe but costs a shell *function* call per
    entry; a literal subscript assignment (`_TAI_WORD['k']='v'`) is not merely
    slower, it is a **syntax error** for a key containing a quote, because zsh
    expands inside a subscript. The old comment blamed quoting for a problem
    that was expansion, and reached for the expensive fix.
  - **Stop writing keys nothing can ask for.** Every key in both plugins is
    `${prefix% *}`, so the copy of each key with a trailing space was dead, and
    the single-word key was a second copy of `_TAI_FIRST`.
  - **Bound the keys by depth.** One pasted 90-word command wrote 90 keys.
    Sourcing is linear in the file, so *bytes are startup time* — and a format
    change is worth more than a cache once the cost is understood.

  **The figure goes stale, so what is written down is the measurement.** This
  bullet claimed 83ms; the same 2.4MB index takes **222ms** in zsh and 226ms in
  bash on the machine it was last checked, measured as seven runs of
  `zsh -f -c 'source …'` less the 2ms of an empty `zsh -f -c true`. `tai bench`
  prints it as `index source` per shell with the file's size, because a claim
  about a cost a rebuild can double is a claim with an expiry date. Two savings
  were measured and *not* taken, so they are not counted twice: the key repeated
  inside each `_TAI_WORD` candidate is 14% of the file, and the self-referential
  `_TAI_SEQ` entries are 0.1%. Neither is worth a format change in the part of
  the system with the least margin in it.
- **A shell parameter is not an environment variable, and a plugin is the only
  thing that can bridge that.** zsh and bash both set `HISTFILE` without exporting
  it, so `tai refresh` — a Python child — saw nothing and imported only
  `~/.zsh_history` and `~/.bash_history` by name. On the machine this was reported
  from: 441 recorded commands, every one from the *bash* file, the zsh history
  never read at all, and a Manjaro `~/.zhistory` invisible because it is not a
  default anybody could guess. The plugins now export it as `TAI_HISTORY_FILES` at
  load time. The general form: a tool that needs to know something only the
  interactive shell knows must be *told* at startup, because by the time the user
  runs a command it is already too late.
- **Never write to a shared slot to silence yourself, and never read your own
  copy of what the user can see.** `POSTDISPLAY` is one slot with no namespacing,
  and zsh-autosuggestions fetches *asynchronously*, so it writes the slot after
  tai's redraw no matter where tai was sourced. tai setting `POSTDISPLAY=""`
  when it had no answer therefore erased a visible hint on every keystroke, and
  the keys that take hints read tai's own variable rather than the screen — so
  `nan` hinting `o .zshrc` had `Tab` open a list over it. Both halves have to go:
  the writer stops clearing what it did not draw (`_TAI_DREW` is the one bit that
  says whose it is), and the readers read what is on screen (`_tai_shown_hint`).
  Assert it by writing `POSTDISPLAY` from a widget, which is what the other plugin
  does — loading the other plugin makes the test depend on a distro package.
  Making tai's hints the visible ones is a **configuration** change, not an
  autocomplete feature, so it belongs in `~/.zshrc` and to the installer and
  `tai uninstall`, which has to give back whatever it took. The line is
  `add-zsh-hook -d precmd _zsh_autosuggest_start`, and it is only effective in
  the rc file: the hook is what installs the plugin's widget wrappers, so removing
  it *after* the first prompt leaves `_zsh_autosuggest_bound_1_forward-char` bound
  and nothing changes. Measured both ways in a pty, reading
  `${widgets[forward-char]}` — the check worth keeping, since the hint itself is
  not observable without a history to match against. There is no line to comment
  out on Manjaro: the plugin is sourced by `/usr/share/zsh/manjaro-zsh-prompt`, a
  system file, so "is it loaded" has to ask zsh
  (`zsh -ic '(( $+functions[_zsh_autosuggest_start] ))'`) rather than look for a
  path that is not there.
- **A command name is a word being written, so the lookup answers it unfinished.**
  `_TAI_FIRST` is keyed by the *whole* first word, so `godot` is a key and `god` is
  not — and a half-typed command is the ordinary state of a line, not an edge case.
  This was written up here as a design choice, on the grounds that "the engine is
  the same way, deliberately". **It was not: the engine walks a prefix range over
  its sorted command names, and `tai suggest god` answered `godot .` the whole
  time the shell said nothing.** A claim about two implementations agreeing, made
  without running the other one. The plugins now take the exact name first and, on
  a miss, the names that begin with it — zsh with `(I)` (0.07ms over 754 keys),
  bash with a loop over the keys (1.4ms), both bounded by `_TAI_PREFIX_KEYS`.
  Two things must not come with it: a word *nothing* begins with stays silent
  rather than reaching for the nearest name, and a glob character is not a prefix,
  because `c*t` reaching `cat` is the index answering a question nobody asked — and
  `(I)` honours no backslash escape, so the guard cannot be quoted in.
  The general form: **a prefix the index does not have may still be a prefix the
  user is typing.** Check the other implementation before writing down why two
  things differ.
- **A path is answered by the filesystem, because the history cannot know it.**
  The reported case: `chmod +x` on a history holding only `chmod +x script`
  suggested `chmod +x script` — a file that is not there — while the AppImage just
  downloaded went unmentioned. What you are about to use is by definition not in
  the history yet, so frequency cannot answer this at any length. Three rules keep
  it from becoming a net loss, each a reported or measured failure: a learned
  argument that **is still a file here** keeps the top place (`cat
  ~/notes/todo.txt` must not be displaced by whatever was touched last); the roots
  are the current directory and the download directories and **not the
  subdirectories**, because descending made `chmod +x` answer
  `chmod +x tests/__pycache__/test_smoke.cpython-314.pyc`; and a file that does
  not start with the word being typed is not an answer to it, so `chmod +x Down`
  is not completed by a file in `~/Downloads`. Whether a line ends in a file is
  decided by what the command means — `chmod`'s last word is a path whatever is in
  front of it — because a bare name like `script` is unreadable by shape and a
  history-imported row carries no directory to check it against.
- **`[[ x == *"*" ]]` is a different question in bash and zsh.** zsh treats
  `*' '` as "ends with a space"; so does bash — and a line written as
  `[[ "$line" == *' ' ]]` next to `[[ "$line" == *' '* ]]` looks like a
  whitespace difference and is not: the first turned `chmod +x freeb` away before
  it was ever looked up, because that line has a space in it and no trailing one.
  Two tests that read as "the same thing" are the ones to suspect.

## Completion standard

A change is ready when it is useful, understandable, safe, tested, documented,
and does not make the default experience slower or harder.
