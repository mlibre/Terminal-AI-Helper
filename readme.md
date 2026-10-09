# TAI >_ Terminal AI Helper 💻

![tai](assets/tai.png)

`tai` learns the commands you actually run and suggests them as you type. zsh
and bash suggest natively, in their own processes — no Python, socket, or
background service on the keystroke path. Offline, stdlib only.

Type `docker` → grey hint `compose up -d`; `→` or `Ctrl-F` takes it, `Alt-F`
takes one word. An empty prompt predicts the command after your last. `Tab`
cycles files, folders and options; `Ctrl-Space` opens a list to look at first:

```text
git <→>            git pull --rebase        ← the hint, taken
git <Ctrl-Space>   pull      status          ← the list, below the line
git <Ctrl-Space> <Enter>   git pull         ← one entry chosen, line is there
```

[Docs](docs/architecture.md) · [Development](docs/development.md) · [فارسی](readme-fa.md)

## Install

```sh
git clone https://github.com/mlibre/terminal-ai-helper
cd terminal-ai-helper
./install.sh
```

The installer enables the plugin for both shells, imports your history, and
builds the index — one progress line and one command to copy:

```text
→ learning from your history, building the index…
✓ zsh installed
✓ bash installed

→ exec zsh
```

The last line is not optional: a running shell keeps its old plugin until the
file is sourced again — same after `tai update`.

## The keys

| key                     | what it does                                                      |
| ----------------------- | ----------------------------------------------------------------- |
| `Tab`                   | cycle files, folders and options; never the hint                   |
| `Ctrl-Space` / `Ctrl-T` | open the list, hint or no hint; press again to move the selection |
| `Enter`                 | take the selected entry and stop; a second `Enter` runs the line  |
| `→`                     | take the hint at the end of the line, else move right             |
| `Ctrl-F`                | take the hint                                                     |
| `Alt-F` / `Ctrl-Right`  | take **one word** of the hint, leaving the rest to type           |
| `Alt-G`                 | the command that followed the last one (bash)                     |

One question per key: `Tab` cycles completions, `Ctrl-Space` (also on
`Ctrl-T`, which every terminal can send) opens the list to look at; the hint
is `→`'s, never `Tab`'s. Pasted text never opens the list on its own — a
paste is not a query — and one typed or removed character brings it back.
Taking a directory from a list is not a request to go there, so `Enter` takes
and stops; a second runs it. The selected entry is highlighted, a directory
name takes your terminal's directory colour, and shared text renders once,
above the list:

```text
cd media/mlibre/B/<Ctrl-Space>   Clip/   Movies/   Projects/   Teb/
```

Each entry shows only what it *adds* — and only what the line already shows is
ever cut: `cd tm` in a home where everything lives under `tmp/` still lists
`tmp/vllm`, because a row that reads `vllm` under `cd tm` is a name neither you
nor the shell typed. In **bash** the ranking is identical, but readline cannot
draw a list below the line — `→` and `Ctrl-F` write plain characters and `Tab`
completes directly, by default only after `tai`, or after every command with
`TAI_COMPLETE_ALL=1`.

## What it suggests, and what it will not

### It has to extend your line

`ls -l` suggests `ls -la`, never itself; a whole command suggests nothing. A
learned name and a same-named path in the current directory are one completion.
A name on `PATH` that cannot run — no execute bit, or a directory — is never
offered.

### A wrapper is not a wall

`sudo`, `doas`, `nohup`, `time`, `nice`, `ionice`, `stdbuf` and `command` run
the command after them, so the line behind one completes as if the wrapper
were not there — `sudo git` suggests `sudo git pull --rebase`. `env` and
`xargs` are excluded: they change what follows enough that putting the wrapper
back would be a lie.

### It has to exist

A command whose path is gone is not offered, however often you typed it —
`cd ~/projects/myapp` stops being the top suggestion for `cd` the moment its
directory is gone. Unreadable tokens are *unknown*, and unknown removes
nothing: `~main` is git's "previous commit on `main`" at least as often as a
home directory.

```sh
tai doctor        # reports "stale paths: N of M commands are not suggested"
tai purge --stale # drops those rows, then rebuilds
```

### A convention is not evidence

tai ships a small universal corpus so a fresh shell still answers `ls -` with
`ls -la` — all of it guesses, ranked below anything your history has seen.
`..`, `.` and `-` are not destinations — true everywhere, so their frequency
says how often you walk back up, not where you are going. They stay offered
for `cd .` etc.; they just lose a ranking they cannot win on evidence.

### A path is answered by the filesystem

The history can tell you which file you used last time — never the one
downloaded a minute ago. So for a line that ends in a file, the files actually
there are the candidates, newest first, and a learned one keeps its place only
while it *is* a file:

```text
# history: `chmod +x script`, and nothing else
chmod +x        hint: ~/Downloads/Freebuff-0.0.154-linux-x86_64.AppImage
```

The roots are the current directory and the download directories —
`$XDG_DOWNLOAD_DIR`, `~/Downloads`, `~/Download`, `~/Desktop` — never their
subdirectories: descending made `chmod +x` answer a `.pyc`. More via
`TAI_FILE_ROOTS=~/tmp:~/scratch`. A word naming a directory is completed by
what is inside it — `cat ~/tmp` reads `~/tmp/x`, not `~tmpx`.

### cd is answered by directories that exist here

`cd ` lists destinations — directories of the current directory, and the ones
the history holds — never a file. A destination learned somewhere else is
judged from where you stand: a bare `vllm` recorded in another directory is
not offered two directories away from vllm — the difference between a memory
and an error message.

### History navigation is history; systemctl completes its units

Up-then-Down through the history moves through the history — the loose list
opens for a line you are *typing*, and a line that arrived from the history is
never answered with a list of itself. `sudo systemctl restart her<Tab>` cycles
the real unit files — from a list read once a day, then held in the shell:
no process on the keystroke path. `--user` completes user units; a verb still
being typed (`resta`) completes nothing, because a unit after it would be a
guess.

### A typo is answered by what it is a typo of

When nothing extends the line, the glimpse shows the learned lines that hold
every word you typed. A word one letter from a word the history holds verbatim
(`forestt` → the `beauty-forest` lines) is answered *as that word*, and a
one-off that shadows a habit — `tai sintall` typed once beside `tai install`
typed a dozen times — is ranked below the habit in the index itself. Even when
the evidence ties outright — `tai un` faced with `tai unsintall` and
`tai unisntall` recorded beside `tai uninstall`, each once, every row exit 0 —
the strongest spelling stands and the typos rank under it, in the prompt and
in `tai suggest` alike.

### A tool you have never run still gets an answer

Type a command on your `PATH` and tai offers `tool --help` — the only honest
thing it can say about a command your history has never seen. Run it once and
tai reads the real `--help` in the background, so `tool <Tab>` knows its
subcommands and flags:

```text
9router --p   →  9router --port          (hint: ort)
9router --n   →  9router --no-browser
```

Your own commands always outrank ones read from `--help`, and the answer is a
shortlist: 32 candidates per prefix, 64 verbs and 96 flags per tool.

### If another plugin also draws hints

`POSTDISPLAY` is one slot, shared with plugins like
[zsh-autosuggestions](https://github.com/zsh-users/zsh-autosuggestions).
**Both hints work** — `→`, `Ctrl-F` and `Alt-F` take whatever is on screen,
and tai never overwrites a hint it did not draw. Only one is visible at a
time; the installer pauses autosuggestions when found, `tai uninstall` gives
it back, and `TAI_KEEP_AUTOSUGGEST=1 ./install.sh` keeps both.

## A dashboard of what it learned

```sh
tai web              # or: tai dashboard  →  http://127.0.0.1:8247/
```

One page, served by the tool itself: a box where typing a prefix answers
exactly what the prompt would answer — through the same engine, not a copy —
plus the most-run commands, the latest recorded rows, and the state of the
store and both indexes. It binds `127.0.0.1` only, answers GETs only, and can
write nothing: the database is your shell history. A light/dark toggle sits in
the corner and the choice is remembered; clicking a suggestion copies it; and
multi-kilobyte history rows arrive bounded, so the page stays quick on stores
that hold pasted JSON. `--port N` moves it; `--no-browser` skips the
auto-open.

## Commands

```sh
tai refresh     # import new history rows, then rebuild the indexes
tai discover    # learn --help from installed tools (cached, ~5-35s)
tai doctor      # what is indexed, and what is held back
tai bench       # build time, latency, memory, each shell's index read time
tai web         # a read-only localhost dashboard (alias: tai dashboard)
tai eval        # candidate coverage against your own history
tai update      # newest version from GitHub, then reinstall (alias: tai upgrade)
tai version     # print the release this install is running
tai purge       # drop unusable rows, then rebuild
tai uninstall   # remove everything tai added
```

No restart needed: the plugin re-reads the index at the next prompt.
Automatic maintenance is enabled by the plugins:

```text
every accepted command → SQLite
every 100 recorded commands → index maintenance is due
index older than the newest row → a rebuild fires in the background
first use of an unseen tool → its --help is read, once, in the background
```

History is filtered on the way in: secret-looking tokens, session-harness
markers, and commands spanning more than one line are rejected.

## Knobs

All have working defaults; they are for when yours is wrong.

| variable                | default                         | what it changes                                         |
| ----------------------- | ------------------------------- | ------------------------------------------------------- |
| `TAI_NO_AUTO_RECORD`    | off                             | stop writing history; predictions keep working          |
| `TAI_NO_LEARN`          | off                             | keep recording, skip reading a new tool's `--help`      |
| `TAI_NO_MENU`           | off                             | `Tab` goes back to "take the hint, else complete"       |
| `TAI_FILE_ROOTS`        | —                               | extra places to look for a file argument, `:`-separated |
| `TAI_SKIP_PATH_CHECK`   | off                             | suggest commands whose paths no longer exist            |
| `TAI_COMPLETE_ALL`      | off                             | bash `Tab` completes every command                      |
| `TAI_HIGHLIGHT_STYLE`   | `fg=8,bold`                     | zsh hint style, set before sourcing                     |
| `_TAI_MENU_ROWS`        | `10`                            | rows the zsh list may take                              |
| `_TAI_MENU_STYLE`       | `standout`                      | zsh selection style, set before sourcing                |
| `_TAI_DIR_STYLE`        | `fg=blue,bold`                  | zsh colour for a directory's name                       |
| `TAI_DB`                | `$XDG_DATA_HOME/tai/history.db` | the history database                                    |
| `TAI_HISTORY_FILES`     | the shell's `HISTFILE`          | extra history files to import, `:`-separated            |
| `TAI_HELP_TIMEOUT`      | `1.5`                           | budget for one tool's `--help`                          |
| `TAI_HELP_TIMEOUT_SLOW` | `6.0`                           | budget for a tool that printed nothing first            |
| `TAI_COMPLETE_TIMEOUT`  | `2.0`                           | budget for a tool's `__complete` answer                 |
| `TAI_DISCOVER_WORKERS`  | `8`                             | parallel `--help` probes                                |

`$XDG_DOWNLOAD_DIR`, `~/Downloads`, `~/Download` and `~/Desktop` are always
file roots; `TAI_INDEX` names the zsh index, `bash-index.bash` the bash one.
`TAI_HISTORY_FILES` is the fix when `tai refresh` learns nothing: the plugins
export your shell's `HISTFILE`, so a cron `tai refresh`, or one from a shell
without the plugin, must be told where the history is.

## Releases, update and uninstall

Every push that passes the tests is released on GitHub as `v<VERSION>` (from
the `VERSION` file at the repo root — `v1.0.0`, not a date with a hash), with a
ready-to-install `tai_<version>_all.deb` attached to the release. The .deb
puts `tai` on `PATH` and the plugins under `/usr/lib/tai`; its postinst prints
the two `source` lines to add to your rc file.

```sh
tai update      # git pull --ff-only from GitHub, then reinstall; alias: tai upgrade
```

No merge commit, stops with git's own message on local edits; nothing lives
outside the checkout, so there is no second copy to sync.

```sh
tai uninstall
```

Removes the shell integration, the `tai` wrapper, the generated indexes, the
SQLite history and the cached CLI knowledge. Your checkout stays; restart the
terminal afterwards.

## Why it stays fast

Run `tai bench` and `tai doctor` yourself — printed numbers go stale the
moment your history changes. The design: ranking happens once, at build time;
the answer is a file the shell reads at startup; no process sits on the
keystroke path — a hint is an array lookup in the shell's own memory. The
one-shot paths answer from a disk cache of the built engine. Its *size* is
the real cost, so keys stop at eight words with twenty candidates each. See
[docs/architecture.md](docs/architecture.md).

The rules in [AGENTS.md](AGENTS.md) are each a reported failure — the most
useful thing in the repo if you are about to change how a suggestion is
chosen.

## 🇵🇸 In Support of Palestine

This project supports Palestine. If you can, donate to [UNRWA](https://www.unrwa.org).

The people of Palestine endure daily suffering and loss, too often unseen and
unhelped. They are asking for support — and we can give our voice, our care,
and our help. Even a small act can matter. Please do what you can. 🤲

And if you ever doubt what is right and what is wrong, remember: when someone
kills a baby to loot baby’s belongings, he is the devil — and he stands on the
wrong side. ✊
