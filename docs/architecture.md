# Architecture

How tai is put together, and why. For what the keys do, read
[the readme](../readme.md); this is what happens underneath them.

## The one-line shape

```text
history / SQLite  →  tai refresh  →  zsh + bash associative arrays  →  ZLE / readline
```

`tai refresh` writes two ordinary shell-sourceable snapshots. The plugins load
them into associative arrays at startup and do everything else with shell
builtins. There is no server component on the autocomplete path.

```text
$XDG_DATA_HOME/tai/zsh-index.zsh
$XDG_DATA_HOME/tai/bash-index.bash
```

`XDG_DATA_HOME` when it is set, `~/.local/share` when it is not — the plugins,
the store and the index builder all read the same rule.

## Why embedded rather than a service

A completion has a budget measured in microseconds and a main thread that is
already holding the user's keystroke. A socket round-trip cannot be made
reliable inside that, and a daemon that has to be installed, started, supervised
and version-matched with the shell plugin is a great deal of surface for one
that would still have to be fast. So the ranking happens at build time, the
answer is a file, and the keystroke path is an associative-array lookup.

The consequence worth knowing: **learning is not instant, and does not pretend
to be.** The index is a file, and a shell that read it keeps the copy it read.
So every rebuild replaces it atomically and each prompt compares mtimes, and the
shell you are sitting in picks up a rebuild at the next prompt with nothing to
restart.

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
`docker compose`, `docker compose logs` — never `docker ` with a trailing space,
and never a single-word key (that list is `_TAI_FIRST`'s job, and writing it
twice was once the largest thing in the file). Keys stop at eight words: after
that the answer is the file you are typing, and one pasted 90-word command was
writing 90 keys each holding the whole line.

**A pair append per entry.** The zsh snapshot is `_TAI_X+=('k' 'v')`, one
builtin call and no parsing, rather than a function call or a literal subscript
assignment. Measured on a real index, the same data was an order of magnitude
faster as pair appends than as one shell function call per entry — and a literal
subscript assignment is additionally *broken* for a key containing a quote,
because zsh expands inside a subscript. bash cannot pair-append, so it writes
subscript assignments, which is the same content in the same order.

**Caps on every list.** Twenty candidates per `_TAI_WORD` key, fifteen per
`_TAI_SEQ` key, and the seed corpus and `--help` vocabulary are capped
separately at the generator. A downloads folder has a season of videos and a
`Tab` that stats all of them is a `Tab` you notice.

The ranking signal, as an integer, is what makes the shell compare natively.
`tai/engine.py` writes it; `tai tune` searches the weights and writes them back
into `tai/engine.py`, so what the plugins read is what was tuned.

**The file is shell code, so the shell is told whose it is.** The plugin
*sources* the snapshot rather than parsing it — that is what makes startup an
array assignment instead of a parse — which means a file that is not tai's would
be executed rather than rejected. In bash that damage does not stay inside the
file: an unterminated `(` leaves the parser mid-construct, so the plugin's *next*
line is read as part of it and run as a command. Each loader therefore reads the
writer's first line and the shape of the first data line before sourcing, and on
a mismatch prints one line naming the file and `tai refresh` and continues with
empty arrays, where the installed-command fallback still answers. It does not try
to detect a torn file: the generator `os.replace`s a temporary one, so a torn
index cannot exist.

## Ranking

`tai/engine.py`. Eight signals, all read from SQLite at build time:

```text
freq · recency · cwd · repo · branch · hour-of-day · exit code · what ran before
```

plus a token bigram/trigram for flags (`logs -f --tail 50`) and typo tolerance
on the first word (`tai/typo.py`). The full weight list lives at the top of
`tai/engine.py`.

Three of the rules are about what may be *ranked* rather than how, and they live
in `tai/paths.py` because they are about whether a candidate still exists:

- **Path liveness.** A command whose path is gone is not suggested, however
  often it was run. The check is deliberately conservative: a token it cannot
  read statically is *unknown*, and unknown never removes anything. `~main` is
  the clearest case — it is a git revision at least as often as it is a home
  directory, and it is only a home directory when that account exists.
- **`cd` destinations.** `..`, `.` and `-` are true in every directory that has
  ever existed, so their frequency is evidence about walking back up, not about
  where to be. They rank below every real destination and are never removed.
- **File arguments.** A line that ends in a *file* is answered from the
  filesystem, not from the history, because the file you are about to use is by
  definition not in the history yet. Which lines those are is learned from what
  the command means (`tai/paths.py:takes_file`) and written into `_TAI_FILE`,
  so the shell can act on it without a process and without guessing.

## Three implementations of one rule

`plugins/zsh/files.zsh`, `plugins/bash/files.bash` and `tai/fresh.py` all
answer the same question — where do we look for a file
argument — and all three have to agree. The shell ones may not import the Python
one, because no process may touch the keystroke path.

That is maintainable because the limits are named in each and the roots are one
variable (`TAI_FILE_ROOTS`) for all three. **A fourth copy of a rule should be a
test that runs the other three, not a fourth copy.**

## Learning from installed tools

`tai/knowledge.py` reads `--help`, `man`, and the `__complete` protocol from
tools the history has not seen, in a throwaway directory, in parallel, and caches
the result in the `cli_tools` table. Nothing suggested is ever executed.

The `__complete` protocol is the only *machine-readable* statement a CLI makes
about its own vocabulary — `tool __complete <args> <word>` prints
`candidate<TAB>description` per match. Very few tools speak it: five of the 4106
executables in `/usr/bin` and `/usr/local/bin` on the machine this was measured on
(`docker`, `dockerd`, `gh`, `git-lfs`, `sbctl`). But where it exists it is worth a
lot: `gh --help` yields no verbs to the parser and `gh __complete ""` yields all
of them, each with a description.

Anything read this way is a *convention*, and conventions rank below anything
the history actually saw — see "Generated vocabulary" in
[AGENTS.md](../AGENTS.md) for why that band exists and what it cost when it was
a bonus instead.

## The optional model layer

`tai/jev.py` and `tai/decisions.py`. Off by default, and deliberately: the
embedded path is local, fast and private, and stays that way.

When `--jev` is used, the model is a *judge*, never a generator. It is shown the
candidates code already produced and may only choose among them; anything else it
returns is discarded. Safety is decided in code by `decisions.unsafe_score` and
never by the model's own `destructive` score, which is the model grading its own
homework. Output is Jev-Choice-shaped — `{choice, probabilities, confidence}` —
so a local SystemOne alter can be dropped in later behind a confidence gate
without touching the shell plugins.

## Diagnostics

| command     | what it is for                                                       |
| ----------- | -------------------------------------------------------------------- |
| `tai bench` | build time, suggest latency, RSS, and each shell's index read time    |
| `tai doctor`| what the store holds and what is held back, and why                 |
| `tai eval`  | labeled candidate coverage against your own history                  |
| `tai tune`  | coordinate search over the weights, writing them back                |

`doctor` reports the stale-path count deliberately: an index that quietly omits
half the history looks exactly like a working one until you notice the
suggestion you expected is not there.