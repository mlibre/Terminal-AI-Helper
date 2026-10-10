# TAI >_ Terminal AI Helper 💻

![tai](assets/tai.png)

**TAI learns the commands you actually run and suggests them as you type.**
Native zsh and bash — no server, no daemon, no Python on your keystrokes.
Everything runs on your machine, offline, stdlib only.

Type `docker` and a grey hint appears: `compose up -d`. `→` takes it. Press
`Tab` and your own history answers what usually comes next. It is
autocomplete that knows *you*:

```text
docker <→>            docker compose up -d    ← the hint, taken
git <Tab>             pull      status        ← your habits, ranked
cd media/mlibre/B/<Tab>          Clip/  Movies/  Projects/
```

![tai in action — the hint, the ranked list, the filesystem, a typo
answered by the habit it shadows](assets/demo.gif)

The demo is a recording of the real plugin on a real pty, not an animation —
re-record it whenever the product moves: `python3 scripts/make_demo_gif.py`
(its only dependencies are `pip install pyte pillow`).

[Docs](docs/architecture.md) · [Development](docs/development.md) · [Docs site](https://mlibre.github.io/Terminal-AI-Helper/) · [فارسی](readme-fa.md)

## Install

One line — no manual clone. It fetches the checkout to `~/.tai` and runs the
same installer from it; run it again later and it updates that checkout in
place:

```sh
curl -fsSL https://raw.githubusercontent.com/mlibre/Terminal-AI-Helper/main/install.sh | bash
```

Prefer to see the code first? The identical installer runs from any clone:

```sh
git clone https://github.com/mlibre/terminal-ai-helper
cd terminal-ai-helper
./install.sh
```

The installer enables both shells, imports your history, and builds the first
index — progress, a short welcome, and one command to copy:

```text
→ cloning tai to /home/you/.tai…
✓ zsh installed
✓ bash installed
   _
  | |_  __ _ ___     the terminal that knows your next command
  | ' \/ _` (_-<
  |_||_\__,_/__/
  … the three things worth knowing, and the keys

→ exec zsh
```

That last line matters: a running shell keeps its old plugin until it is
sourced again — same after `tai update`.

## Your first minute

1. **Type a command you run often.** After a few letters a grey hint shows
   the rest. `→` or `Ctrl-F` takes it whole, `Alt-F` takes one word.
2. **Press `Tab`.** Files and folders cycle; on a first word, the lines your
   history ranks highest come first — pick one, `Enter` fills it in, a
   second `Enter` runs it.
3. **Run `tai web`.** A localhost dashboard of everything tai learned, with a
   try box that answers exactly what the prompt would — and every answer
   carries a `why` that opens its own arithmetic.

Every command you run teaches it. Secrets, multi-line pastes and stray keys
are filtered out before anything is stored, and what you run never leaves the
machine. New commands show up in the running shell within seconds — no
restart, ever.

## How it works

Four words: watch, learn, rank, answer.

- **Watch.** Each command is appended to a local spool file by the shell
  itself — one builtin write, no process — and a few seconds later a batch
  lands in a local SQLite database. The store's filter is the only gate:
  secret-looking tokens, session-harness markers, multi-line commands and
  one-key accidents never become vocabulary.
- **Learn.** The first time you run an unknown tool, `TAI` reads its real
  `--help` in the background, so `tool <Tab>` knows its subcommands and
  flags from then on.
- **Rank.** `tai refresh` turns the database into two ordinary
  shell-sourceable index files. The ranking is yours: how often, how
  recently, in this directory, after what.
- **Answer.** The shell looks the answer up in its own memory — an
  associative-array hit, microseconds. Nothing sits between you and the key.

The one server is `tai web`: you start it, it binds `127.0.0.1`, it answers
GETs only, and it can write nothing.

## The keys

| key                     | what it does                                                                                                   |
| ----------------------- | -------------------------------------------------------------------------------------------------------------- |
| `→`                     | take the hint at the end of the line, else move right                                                          |
| `Ctrl-F`                | take the hint                                                                                                  |
| `Alt-F` / `Ctrl-Right`  | take **one word** of the hint, leaving the rest to type (bash: `Ctrl-Right`)                                   |
| `Tab`                   | cycle files, folders and options; on a bare first word the learned lines come first, ranked like the dashboard |
| `Ctrl-Space` / `Ctrl-T` | open the list, hint or no hint; press again to move the selection                                              |
| `Enter`                 | take the selected entry and stop; a second `Enter` runs the line                                               |
| `Alt-G`                 | the command that followed the last one (bash)                                                                  |

Two things worth knowing. A paste is not a query — pasted text never opens a
list on its own, and one typed character brings it back. And taking a
directory from a list is not a request to go there: `Enter` fills it in and
stops, so the line always says exactly what it will do before it runs.

**bash** ranks identically, accepts the same keys, and readline cannot draw a
list below the line: `→` and `Ctrl-F` accept the hint, `Ctrl-Right` takes one
word of it, and `Tab` completes directly — by default only after `tai`, or
after every command with `TAI_COMPLETE_ALL=1`.

## What it suggests, and what it will not

These are not edge cases; they are the product.

- **It has to extend your line.** `ls -l` suggests `ls -la`, never itself; a
  whole command suggests nothing.
- **It has to exist.** A command whose path is gone is not offered, however
  often you typed it — `cd ~/projects/myapp` stops being the top suggestion
  the moment its directory is gone. `tai doctor` reports how much is held
  back; `tai purge --stale` drops it.
- **A wrapper is not a wall.** `sudo`, `doas`, `nohup`, `time`, `nice` and
  friends run what follows them, so the line behind one completes as if the
  wrapper were not there.
- **A path is answered by the filesystem.** The history can say which file
  you used last time — never the one downloaded a minute ago. A line ending
  in a file is answered by what is actually there, newest first.
- **`cd` is answered by directories that exist here.** A destination learned
  elsewhere is judged from where you stand — a bare `vllm` recorded in
  another project is not offered two directories away from vllm.
- **A typo is answered by what it is a typo of.** When nothing extends the
  line, the learned lines that hold every word you typed appear; a one-off
  typo ranks below the habit it shadows, in the index itself.
- **A name the shell never found is not a suggestion.** Exit 127 is the shell
  refusing a word, not a command failing — so a name you mistyped and never
  once ran does not sit above the tool it was a typo of. And a tool you have
  installed outranks a word you do not have, all else equal: `opencode` beats
  `opencoe` because it exists, here, on your PATH.
- **A tool you have never run still gets an answer.** `tool --help` — the
  only honest thing to say about a command the history has never seen. Run
  it once and the real `--help` is learned in the background:

  ```text
  9router --p   →  9router --port          (hint: ort)
  9router --n   →  9router --no-browser
  ```

- **If another plugin also draws hints, both work.** `→`, `Ctrl-F` and
  `Alt-F` take whatever is on screen, and tai never overwrites a hint it did
  not draw. The installer pauses zsh-autosuggestions when found and
  `tai uninstall` gives it back; `TAI_KEEP_AUTOSUGGEST=1 ./install.sh` keeps
  both.

## A dashboard of what it learned

```sh
tai web              # or: tai dashboard  →  http://127.0.0.1:8247/
```

One page: a try box that answers exactly what the prompt would — through the
same engine, not a copy — the most-run commands, the latest recorded rows,
the state of the store and both indexes, and a `why` on every suggestion
that opens the factors and evidence behind its score. A light/dark toggle
sits in the corner and the choice is remembered; clicking a suggestion
copies it. `--port N` moves it; `--no-browser` skips the auto-open.

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
tai forget git sta   # drop every stored row for a command, exactly as typed
tai uninstall   # remove everything tai added
```

`flush` is the one the plugins call for you — it ingests their batched
records — and every other command drains whatever is pending first, so no
answer is ever a few records behind what you just did. No restart is needed:
the plugin re-reads the index at the next prompt.

## Knobs

All have working defaults; they are for when yours is wrong.

| variable                | default                         | what it changes                                         |
| ----------------------- | ------------------------------- | ------------------------------------------------------- |
| `TAI_NO_AUTO_RECORD`    | off                             | stop writing history; predictions keep working          |
| `TAI_NO_LEARN`          | off                             | keep recording, skip reading a new tool's `--help`      |
| `TAI_NO_MENU`           | off                             | `Tab` goes back to "take the hint, else complete"       |
| `TAI_SPOOL_MAX`         | `8`                             | pending commands that force a batched flush             |
| `TAI_SPOOL_SECONDS`     | `3`                             | idle seconds that force one                             |
| `TAI_SPOOL`             | beside the database             | the spool file the plugins append to                    |
| `TAI_FILE_ROOTS`        | —                               | extra places to look for a file argument, `:`-separated |
| `TAI_SKIP_PATH_CHECK`   | off                             | suggest commands whose paths no longer exist            |
| `TAI_COMPLETE_ALL`      | off                             | bash `Tab` completes every command                      |
| `_TAI_HIGHLIGHT_STYLE`  | `fg=8,bold`                     | zsh hint style, set before sourcing                     |
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
the `VERSION` file at the repo root — `v1.0.0`, not a date with a hash), with
a ready-to-install `tai_<version>_all.deb` attached. The .deb puts `tai` on
`PATH` and the plugins under `/usr/lib/tai`; its postinst prints the two
`source` lines to add to your rc file.

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
moment your history changes. The shape of it:

- the keystroke path is shell builtins only — a hint is an array lookup in
  the shell's own memory, ~0.2ms on a real history;
- recording is a builtin append too (~0.01ms), and one `tai flush` per batch
  of commands, where it used to be a Python process after every single line;
- ranking happens once, at build time, and the answer is a file the shell
  reads at startup;
- the one-shot paths (`tai suggest`, the dashboard) answer from a disk cache
  of the built engine, invalidated the moment a new row lands.

See [docs/architecture.md](docs/architecture.md) for the whole story, and
[AGENTS.md](AGENTS.md) for the rules — each one a reported failure, the most
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
