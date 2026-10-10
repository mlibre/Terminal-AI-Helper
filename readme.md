# TAI >_ Terminal AI Helper 💻

![tai](assets/tai.png)

**TAI learns the commands you actually run and suggests them as you type.**
Native zsh and bash — no server, no daemon, no Python on your keystrokes;
everything runs on your machine, offline, stdlib only.

Type `systemctl` and a grey hint appears: `restart nginx`. `→` takes it. Press
`Tab` and your own history answers what usually comes next. It is
autocomplete that knows *you*:

```text
systemctl <→>         restart nginx           ← the hint, taken
git <Tab>             pull      status        ← your habits, ranked
cd D<Tab>             Desktop/  Documents/  Downloads/
```

![tai in action — the hint, the ranked list, the filesystem, a typo
answered by the habit it shadows](assets/demo.gif)

[Docs](docs/architecture.md) · [Development](docs/development.md) · [Docs site](https://mlibre.github.io/Terminal-AI-Helper/) · [فارسی](readme-fa.md)

## Install Or Update

```sh
curl -fsSL https://raw.githubusercontent.com/mlibre/Terminal-AI-Helper/main/install.sh | bash
# Or using npm
npm i -g terminal-ai-helper@latest
```

## Commands

```sh
tai update      # newest version — GitHub for a checkout, npm for an npm install (alias: tai upgrade)
tai refresh     # import new history rows, then rebuild the indexes
tai discover    # learn --help from installed tools (cached, ~5-35s)
tai doctor      # what is indexed, and what is held back
tai bench       # build time, latency, memory, each shell's index read time
tai web         # a read-only localhost dashboard (alias: tai dashboard)
tai eval        # candidate coverage against your own history
tai version     # print the release this install is running
tai purge       # drop unusable rows, then rebuild
tai forget git sta   # drop every stored row for a command, exactly as typed
tai uninstall   # remove everything tai added
```

The line downloads TAI to `~/.tai` and runs the installer from there; run it
again later and it updates in place. It enables both shells, imports your
history, and builds the first index:

```text
→ cloning tai to /home/you/.tai…
→ learning from your history, building the index…
✓ zsh installed
✓ bash installed

  1. Type a few letters — tai finishes the command in grey. → takes it.
  2. Tab opens a menu of what fits. Enter picks; a second Enter runs.
  3. It learns from your history — offline, private, no account.

  Try it now: type "cd " and watch the grey.
→ exec zsh
```

That last line matters: a terminal that is already open keeps the old
version until you open a new one — same after `tai update`.

## Your first minute

1. **Type a command you run often.** A grey hint shows the rest; `→` or
   `Ctrl-F` takes it whole, `Alt-F` one word.
2. **Press `Tab`.** On a first word your history ranks the lines it knows;
   `Enter` fills one in, a second `Enter` runs it.
3. **Run `tai web`.** A localhost dashboard of everything it learned, with a
   try box that answers exactly what the prompt would.

Every command you run teaches it — secrets, multi-line pastes and stray keys
are filtered out before anything is stored, and nothing ever leaves the
machine. New commands show up within seconds; no restart, ever.

## The keys

| key                     | what it does                                                                                                                                           |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `→`                     | take the hint at the end of the line, else move right                                                                                                  |
| `Ctrl-F`                | take the hint                                                                                                                                          |
| `Alt-F` / `Ctrl-Right`  | take **one word** of the hint, leaving the rest to type (bash: `Ctrl-Right`)                                                                           |
| `Tab`                   | write what every candidate shares, then cycle files, folders and options; on a bare first word the learned lines come first, ranked like the dashboard |
| `Ctrl-Space` / `Ctrl-T` | open the list, hint or no hint; press again to move the selection (zsh)                                                                                |
| `Down`                  | peek at what usually follows — opens the list when the typed line shows no hint; history otherwise (zsh)                                               |
| `Enter`                 | take the selected entry and stop; a second `Enter` runs the line                                                                                       |
| `Alt-G`                 | the command that followed the last one (bash)                                                                                                          |

A paste is not a query — pasted text never opens a list, and one typed
character brings it back. `Enter` fills a selection in and stops; running it
is always a second, deliberate `Enter`.

**bash** ranks identically and takes the hint keys, but readline cannot draw
a list below the line and has no menu key: `Tab` completes directly — after
`tai` by default, after every command with `TAI_COMPLETE_ALL=1`.

## What it suggests, and what it will not

One loop makes all of it go. The shell appends each command to a local spool
file and it lands in a local SQLite database seconds later — secrets,
multi-line pastes and one-key accidents never become vocabulary. The first
time you run an unknown tool, its real `--help` is read in the background, so
`tool <Tab>` knows its subcommands and flags from then on. `tai refresh`
turns the database into two index files ranked your way — how often, how
recently, in this directory, after what — and the shell answers from its own
memory, an array hit in microseconds.

- **It has to extend your line.** `ls -l` suggests `ls -la`, never itself; a
  whole command suggests nothing.
- **It has to exist.** A command whose path is gone is not offered, however
  often you typed it — `tai doctor` reports how much is held back, and
  `tai purge --stale` drops it.
- **A wrapper is not a wall.** `sudo`, `doas`, `nohup`, `time`, `nice` and
  friends complete as if the wrapper were not there.
- **A path is answered by the filesystem** — what is actually there, newest
  first. A `cd` is answered by directories that exist here, judged from where
  you stand, not offered two directories away.
- **`systemctl` is answered by the machine's own unit list.**
  `systemctl restart her` cycles `nginx.service` and friends — read once,
  held in the shell.
- **A typo is answered by what it is a typo of.** When nothing extends the
  line, the learned lines that hold every word you typed appear; the one-off
  typo ranks below the habit it shadows, in the index itself.
- **A word the shell refused is not a suggestion** — exit 127 is the shell
  refusing a word, not a command failing — and an installed tool outranks a
  word that resolves to nothing: `opencode` beats `opencoe` because it
  exists, here, on your PATH.
- **A tool you have never run still gets an answer:** `tool --help`. Run it
  once and the real `--help` is learned in the background.

- **If another plugin also draws hints, both work.** `→`, `Ctrl-F` and
  `Alt-F` take whatever is on screen, and tai never overwrites a hint it did
  not draw. The installer pauses zsh-autosuggestions when found and
  `tai uninstall` gives it back; `TAI_KEEP_AUTOSUGGEST=1 ./install.sh` keeps
  both.

## A dashboard of what it learned

```sh
tai web              # or: tai dashboard  →  http://127.0.0.1:8247/
```

![the tai web dashboard — the try box answering `git`, the state of the
store, and the ranked panels](assets/web.png)

One server, read-only: it binds `127.0.0.1`, answers GETs only, and writes
nothing. Clicking a suggestion copies it; `--port N` moves it; `--no-browser`
skips the auto-open.

## Configuration

All have working defaults; they are for when yours is wrong.

| variable                | default                         | what it changes                                         |
| ----------------------- | ------------------------------- | ------------------------------------------------------- |
| `TAI_NO_AUTO_RECORD`    | off                             | stop writing history; predictions keep working          |
| `TAI_NO_LEARN`          | off                             | keep recording, skip reading a new tool's `--help`      |
| `TAI_NO_MENU`           | off                             | `Tab` goes back to "take the hint, else complete"       |
| `TAI_SPOOL_MAX`         | `8`                             | pending commands that force a batched flush             |
| `TAI_SPOOL_SECONDS`     | `3`                             | idle seconds that force one                             |
| `TAI_SPOOL`             | beside the database             | the spool file the plugins append to                    |
| `TAI_REBUILD_EVERY`     | `100`                           | recorded commands between full index rebuilds           |
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

`~/Downloads` and `~/Desktop` are always file roots. `TAI_HISTORY_FILES` is
the fix when a cron `tai refresh` learns nothing: it must be told where the
history is.

## Releases, update and uninstall

A ready-to-install `tai_<version>_all.deb` is attached to every GitHub
release; it puts `tai` on `PATH` and prints the two `source` lines for your
rc file.

```sh
tai update      # newest version — GitHub for a checkout, npm for an npm install; alias: tai upgrade
```

It stops rather than overwrite a copy you have edited yourself.

```sh
tai uninstall
```

Removes the shell integration, the wrapper, the indexes, the SQLite history
and the cached knowledge. The `~/.tai` folder stays — on an npm install,
`npm rm -g terminal-ai-helper` removes the package itself. Restart the
terminal afterwards.

## Why it stays fast

The keystroke path is shell builtins only — a hint is an array lookup
(~0.2ms on a real history), recording is a builtin append and one `tai flush`
per batch, ranking happens once at build time, and the answer is a file the
shell reads at startup. Run `tai bench` and `tai doctor` yourself — printed
numbers go stale the moment your history changes.

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
