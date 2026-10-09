# Architecture

How tai is put together, and why. For what the keys do, read
[the readme](../readme.md); this is what happens underneath them.

## The one-line shape

```text
command → shell spool (builtin append)  →  tai flush  →  SQLite  →  tai refresh  →  zsh + bash associative arrays  →  ZLE / readline
```

`tai refresh` writes two ordinary shell-sourceable snapshots —
`$XDG_DATA_HOME/tai/zsh-index.zsh` and `bash-index.bash` beside it; the
plugins load them into associative arrays at startup and do everything else
with shell builtins. There is no server component on the autocomplete path —
and no Python process on the *recording* path either: a command is one
`print`/`printf` append to a spool file, and `tai flush` ingests batches of
them into the store when the batch is worth a process (eight records or three
idle seconds, whichever comes first). The store's `is_recordable` gate —
secrets, harness wrappers, multiline and one-key commands — is the only gate
between what was typed and what is learned, whichever transport carried it.

## Why embedded rather than a service

A completion has a budget measured in microseconds and a main thread that is
already holding the user's keystroke. A socket round-trip cannot be made
reliable inside that, and a daemon that has to be installed, started, supervised
and version-matched with the shell plugin is a great deal of surface. So the
ranking happens at build time, the answer is a file, and the keystroke path is
an associative-array lookup. The same arithmetic retired the last per-command
process: a backgrounded `tai record` after every line cost ~30ms of
interpreter per command for the life of an install, and the spool records the
same rows for ~0.01ms of shell and one interpreter per batch.

The consequence worth knowing: **learning is not instant, and does not pretend
to be.** The index is a file, and a shell that read it keeps the copy it read.
So every rebuild replaces it atomically and each prompt compares mtimes, and
the shell you are sitting in picks up a rebuild at the next prompt with nothing
to restart.

## The index

Five maps, written as text. `tai/index.py` owns the format.

| map                  | key                              | value                                        |
| -------------------- | -------------------------------- | -------------------------------------------- |
| `_TAI_SCORE`         | a whole command                  | integer milli-score                           |
| `_TAI_FIRST`         | the first word of a command      | newline-separated commands, best first         |
| `_TAI_WORD`          | the first *N* words of a command | newline-separated commands, best first         |
| `_TAI_SEQ`           | a whole command                  | what was run after it, best first             |
| `_TAI_FILE`          | a line without its last word     | `1`, when that next word is a file            |

Three things about it are load-bearing, and all three are about size, because
the index is read once at every shell startup.

**A key per word boundary, and no more.** `_TAI_WORD` holds `docker`,
`docker compose`, `docker compose logs` — never `docker ` with a trailing
space, and never a single-word key (that list is `_TAI_FIRST`'s job, and
writing it twice was once the largest thing in the file). Keys stop at eight
words: after that the answer is the file you are typing, and one pasted
90-word command was writing 90 keys each holding the whole line.

**A pair append per entry.** The zsh snapshot is `_TAI_X+=('k' 'v')`, one
builtin call and no parsing. Measured on a real index, the same data was an
order of magnitude faster as pair appends than as one shell function call per
entry — and a literal subscript assignment is additionally *broken* for a key
containing a quote, because zsh expands inside a subscript. bash cannot
pair-append, so it writes subscript assignments, which is the same content in
the same order. Compound `(k v …)` chunks were measured on both shells and
kept only the smaller file, not a faster one — a change here should re-measure.

**Caps on every list.** Twenty candidates per `_TAI_WORD` key, fifteen per
`_TAI_SEQ` key, and the seed corpus and `--help` vocabulary are capped
separately at the generator. A downloads folder has a season of videos and a
`Tab` that stats all of them is a `Tab` you notice.

The ranking signal, as an integer, is what makes the shell compare natively.
`tai/engine.py` writes it; `tai tune` searches the weights and writes them back
into `tai/engine.py`, so what the plugins read is what was tuned.

**The file is shell code, so the shell is told whose it is.** The plugin
*sources* the snapshot rather than parsing it — that is what makes startup an
array assignment instead of a parse — which means a file that is not tai's
would be executed rather than rejected, and in bash an unterminated `(` leaves
the parser mid-construct, running the plugin's *next* line as a command. Each
loader therefore reads the writer's first line and the shape of the first data
line before sourcing, and on a mismatch prints one line naming the file and
`tai refresh`, then continues with empty arrays. It does not try to detect a
torn file: the generator `os.replace`s a temporary one, so one cannot exist.

## Ranking

`tai/engine.py`. Eight signals, all read from SQLite at build time:

```text
freq · recency · cwd · repo · branch · hour-of-day · exit code · what ran before
```

plus a token bigram/trigram for flags (`logs -f --tail 50`) and typo tolerance
on the first word (`tai/typo.py`). The same module owns the shadow rule
(`shadow_map`): a rare line that nearly duplicates a stronger one is ranked
just below it, in the engine and the index alike, however the rows'
frequencies, timestamps and exit codes tie. The full weight list lives at the
top of
`tai/engine.py`, and the built engine is pickled beside the store for the
one-shot paths, keyed on the store file itself — a new row invalidates it
(`tai/engcache.py`); the keystroke path never touches it.

The length penalty is the one term the index bakes. Its difference between
two candidates is the same whatever prefix the engine is asked with, so
subtracting it at build time makes the scores the plugins read order exactly
as the engine would, for every one-word question — without it, a three-run
long line out-ranked a two-run short one in the prompt while the dashboard
said the opposite, and the hint named a line the user's own panel did not.
The per-query terms — the directory you stand in, the hour of day, what ran
before — stay per-query: a snapshot cannot carry them, and the dashboard's
`why` panel names them when a rank surprises.

Three rules are about what may be *ranked* rather than how, and they live in
`tai/paths.py` because they are about whether a candidate still exists. Each
is a promise in [AGENTS.md](../AGENTS.md) and a check in the suites: path
liveness (a command whose path is gone is not suggested; unknown never removes
anything), `cd`
destinations (`..`, `.`, `-` rank below every real destination, never removed),
and file arguments (a line ending in a *file* is answered from the filesystem,
learned from what the command means and written into `_TAI_FILE`).

## Three implementations of one rule

`plugins/zsh/files.zsh`, `plugins/bash/files.bash` and `tai/fresh.py` all
answer the same question — where do we look for a file argument — and all
three have to agree. The shell ones may not import the Python one, because no
process may touch the keystroke path. The limits are named in each and the
roots are one variable (`TAI_FILE_ROOTS`) for all three; a fourth copy of a
rule should be a test that runs the other three.

## Learning from installed tools

`tai/knowledge.py` reads `--help`, `man`, and the `__complete` protocol from
tools the history has not seen, in a throwaway directory, in parallel, and
caches the result in the `cli_tools` table. Nothing suggested is ever executed.

The `__complete` protocol is the only *machine-readable* statement a CLI makes
about its own vocabulary — `tool __complete <args> <word>` prints
`candidate<TAB>description` per match. Very few tools speak it, but where it
exists it is worth a lot: `gh --help` yields no verbs to the parser and
`gh __complete ""` yields all of them, each with a description.

Anything read this way is a *convention*, and conventions rank below anything
the history actually saw — a promise in [AGENTS.md](../AGENTS.md), asserted on
real built scores in the suites.

## The optional model layer

`tai/jev.py` and `tai/decisions.py`. Off by default, and deliberately: the
embedded path is local, fast and private, and stays that way.

When `--jev` is used, the model is a *judge*, never a generator: it is shown
candidates code already produced and may only choose among them. Safety is
decided in code by `decisions.unsafe_score`, never by the model's own
`destructive` score. Output is Jev-Choice-shaped — `{choice, probabilities,
confidence}` — so a local SystemOne alter can drop in later behind a confidence
gate without touching the shell plugins.

## Diagnostics

`tai bench` (build time, latency, RSS, index read time), `tai doctor` (what is
indexed and what is held back), `tai eval` (candidate coverage), `tai tune`
(coordinate search over the weights). `doctor` reports the stale-path count
deliberately: an index that quietly omits half the history looks exactly like a
working one until you notice the suggestion you expected is not there.