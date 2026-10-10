# Usage

Everything TAI can do on a first meeting, and the six keys that reach it. For
what happens underneath, read [architecture.md](architecture.md).

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/mlibre/Terminal-AI-Helper/main/install.sh | bash
```

The line downloads TAI to `~/.tai` and runs the installer from there; run it
again later and it updates TAI in place. It enables both shells, imports
your history, and builds the first index — progress, a short welcome, and
one command to copy:

```text
→ cloning tai to /home/you/.tai…
→ learning from your history, building the index…
✓ zsh installed
✓ bash installed
   _
  | |_  __ _ ___     the terminal that knows your next command
  | ' \/ _` (_-<
  |_||_\__,_/__/

  1. Type a few letters — tai finishes the command in grey. → takes it.
  2. Tab opens a menu of what fits. Enter picks; a second Enter runs.
  3. It learns from your history — offline, private, no account.

  More keys: Ctrl-F takes the whole hint · Ctrl-Space opens the list
             Down peeks at what usually follows

  Try it now: type "cd " and watch the grey.
→ exec zsh
```

That last line matters: a terminal that is already open keeps the old
version until you open a new one — same after `tai update`. A ready-to-install
`.deb` is attached to every GitHub release.

## Your first minute

1. **Type a command you run often.** After a few letters a grey hint shows
   the rest. `→` or `Ctrl-F` takes it whole, `Alt-F` takes one word.
2. **Press `Tab`.** Files and folders cycle; on a first word, the lines your
   history ranks highest come first — pick one, `Enter` fills it in, a
   second `Enter` runs it.
3. **Run `tai web`.** A localhost dashboard of everything tai learned, with a
   try box that answers exactly what the prompt would.

Every command you run teaches it. New commands show up in the running shell
within seconds — no restart, ever.

## The keys

| key                     | what it does                                                      |
| ----------------------- | ----------------------------------------------------------------- |
| `→`                     | take the hint at the end of the line, else move right             |
| `Ctrl-F`                | take the hint                                                     |
| `Alt-F` / `Ctrl-Right`  | take **one word** of the hint, leaving the rest to type (bash: `Ctrl-Right`) |
| `Tab`                   | cycle files, folders and options; on a bare first word the learned lines come first, ranked like the dashboard |
| `Ctrl-Space` / `Ctrl-T` | open the list, hint or no hint; press again to move the selection |
| `Enter`                 | take the selected entry and stop; a second `Enter` runs the line  |
| `Alt-G`                 | the command that followed the last one (bash)                     |

Two things worth knowing. A paste is not a query — pasted text never opens a
list on its own, and one typed character brings it back. And taking a
directory from a list is not a request to go there: `Enter` fills it in and
stops, so the line always says exactly what it will do before it runs.

**bash** ranks identically, accepts the same keys, and readline cannot draw a
list below the line: `→` and `Ctrl-F` accept the hint, `Ctrl-Right` takes one
word of it, and `Tab` completes directly — by default only after `tai`, or
after every command with `TAI_COMPLETE_ALL=1`.

If another plugin also draws hints, both work: `→`, `Ctrl-F` and `Alt-F` take
whatever is on screen, and tai never overwrites a hint it did not draw.

## What it suggests, and what it will not

- **It has to extend your line.** `ls -l` suggests `ls -la`, never itself.
- **It has to exist.** A command whose path is gone is not offered, however
  often you typed it — `tai doctor` reports how much is held back,
  `tai purge --stale` drops it.
- **A wrapper is not a wall.** `sudo`, `nohup`, `time`, `nice` and friends
  run what follows them, so the line behind one completes as if the wrapper
  were not there.
- **A typo is answered by what it is a typo of.** When nothing extends the
  line, the learned lines that hold every word you typed appear; a one-off
  typo ranks below the habit it shadows, in the index itself.
- **An installed command outranks an uninstalled word**, all else equal — and
  a name the shell never found (exit 127) is not a suggestion at all.
- **A tool you have never run still gets an answer:** `tool --help` — the
  only honest thing to say about a command the history has never seen. Run it
  once and the real `--help` is learned in the background.

## A dashboard of what it learned

```sh
tai web              # or: tai dashboard  →  http://127.0.0.1:8247/
```

![the tai web dashboard — the try box answering `git `, the ranked panel, and
the `why` factors behind a score](web.png)

The one server in the product: you start it, it binds `127.0.0.1`, it answers
GETs only, and it can write nothing.

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

No restart is needed after any of them: the plugin re-reads the index at the
next prompt.
