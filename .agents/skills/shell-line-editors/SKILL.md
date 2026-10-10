---
name: Shell Line Editors
description: Build and debug terminal autocomplete, inline ghost text, and key bindings in zsh (ZLE) and bash (readline). Use when a shell plugin misbehaves, when suggestions do not appear or do not render, when a key binding is wrong or hijacks a default, when adding inline hints to bash, or when testing shell plugins at all. Covers ZLE widgets and hooks, POSTDISPLAY ghost text, readline READLINE_LINE/READLINE_POINT, bind -x, COMPREPLY completion, why readline cannot render ghost text, how ble.sh and fish do it anyway, and how to test real shells through a pty.
---

# Shell Line Editors (zsh ZLE / bash readline)

Use this skill when working on shell autocomplete, inline hints, key bindings,
or any plugin that hooks the interactive line editor. The two editors have
almost nothing in common, and **most shell-autocomplete bugs come from
assuming a zsh facility exists in bash** or vice versa.

## The one fact that explains the whole design

| | zsh | bash |
| --- | --- | --- |
| line editor | ZLE, built in | GNU readline, a C library |
| ghost text after the cursor | **yes** — `POSTDISPLAY` | **no API at all** |
| "run this shell function on a key" | `zle -N` widget | `bind -x` |
| completion | `compdef` / `_arguments` | `complete -F` + `COMPREPLY` |
| before/after command hooks | `preexec` / `precmd` | `PROMPT_COMMAND` |

Readline exposes no post-display hook. Nothing can draw text *after* the cursor
without corrupting the editable buffer. So **inline ghost text is zsh-only**
unless you replace readline (see "Ghost text in bash" below). Do not fake it
by stuffing escape codes into `READLINE_LINE`: those bytes become part of the
buffer, the user edits them, and the command breaks.

## zsh / ZLE

### Widgets take function NAMES, not code strings

This is the single most common zsh plugin bug. Both of these are wrong:

```zsh
zle -N my-widget 'BUFFER+="foo"'                    # ✗ No such shell function
add-zle-hook-widget line-init 'POSTDISPLAY=""'      # ✗ function definition file not found
```

`zle -N` and `add-zle-hook-widget` both interpret their string argument as the
name of an existing shell function. A code string is treated as a function name
that cannot be found. Define a real function instead:

```zsh
_tai_line_init() { POSTDISPLAY=""; _TAI_SUGGESTION="" }
add-zle-hook-widget line-init _tai_line_init
```

Symptom of getting this wrong: `zle -N` registers without complaint and only
fails with `No such shell function` when the widget is **invoked**;
`add-zle-hook-widget` prints `azhw:zle-line-init:N: ...: function definition file
not found` on **every prompt** and the hook never runs. High confidence,
reproduced in this repo.

### Autoloadable functions are not loaded

`add-zle-hook-widget` and `add-zsh-hook` ship with zsh but are only
*autoloadable*. `$+functions[add-zle-hook-widget]` is `0` in a bare `zsh -f`
and in many minimal configs, because nothing has loaded them yet. A guard like
`if (( $+functions[add-zle-hook-widget] ))` then silently skips installation and
the feature vanishes with no error.

```zsh
autoload -Uz add-zle-hook-widget add-zsh-hook 2>/dev/null
```

Symptom: the feature works on a machine with oh-my-zsh and silently does
nothing on a clean one. High confidence, reproduced here.

### Ghost text with POSTDISPLAY

`line-pre-redraw` fires before every screen refresh, which is the correct place
to recompute a suggestion — not `zle-line-init` (once per line) and not a
per-keystroke widget (no redraw guarantee).

```zsh
_tai_update() {
  POSTDISPLAY=""; region_highlight=()
  local out; out="$(_tai_query "$BUFFER" "$_TAI_LAST")"
  if [[ -n "$out" && "$out" == "$BUFFER"* && "$out" != "$BUFFER" ]]; then
    POSTDISPLAY="${out#$BUFFER}"                 # bare text, styled below
    region_highlight+=("${#BUFFER} $((${#BUFFER} + ${#out} - ${#BUFFER})) fg=8,bold")
  fi
}
add-zle-hook-widget line-pre-redraw _tai_update
add-zle-hook-widget line-init _tai_line_init     # clear stale POSTDISPLAY
```

Do **not** put SGR escapes in `POSTDISPLAY`; see the next section.

Then accept by mutating `BUFFER`/`CURSOR` and clearing `POSTDISPLAY`.

`POSTDISPLAY` is rendered by ZLE, so the exact bytes on the wire are
terminal-dependent. **Do not assert on the painted escape sequence**; read
`$POSTDISPLAY` or your own suggestion variable from inside a widget instead.
On a pty with no real terminal behind it, ZLE may not paint the post-display
region at all — capture the variable, never the bytes.

### A file-backed index is stale in every open shell

A generated snapshot sourced at startup is a *copy*. Anything that rewrites the
file — a rebuild triggered by learning, a package upgrade, another terminal
window — is invisible to a shell that already sourced it, so the plugin appears
to have learned nothing until the next terminal. The user's read of that is "it
doesn't work", not "restart your shell".

Fix it with a builtin comparison at the prompt, no process:

```zsh
_TAI_INDEX_STAMP="${_TAI_INDEX_FILE}.stamp"
_tai_load_index() { [[ -r "$_TAI_INDEX_FILE" ]] && source "$_TAI_INDEX_FILE"
                     : >| "$_TAI_INDEX_STAMP" 2>/dev/null || _TAI_INDEX_WATCH=0; }
_tai_index_changed() { (( _TAI_INDEX_WATCH )) && [[ "$_TAI_INDEX_FILE" -nt "$_TAI_INDEX_STAMP" ]]; }
# in precmd / PROMPT_COMMAND:
_tai_index_changed && _tai_load_index
```

Four things make or break this:

- The stamp is *touched* on every sync, so the check is "the file moved on", not
  "the file is newer than startup".
- A read-only index directory cannot hold the stamp. Watching it anyway re-sources
  the whole index on **every** prompt, so drop the watch when the touch fails.
- `-nt` compares whole seconds, so a rebuild in the same second the shell started
  is missed until the next one. Sleep past a second boundary in the test.
- Declare the arrays in the plugin, not only in the generated file. An unset
  variable is an *indexed* array in bash, and bash then evaluates the subscript
  arithmetically — so `arr[$key]` with `key=9router` prints
  `value too great for base` on every keystroke instead of looking anything up.
  A shell started before the first build has to be quiet, not broken.

### Never put escape bytes inside POSTDISPLAY

The snippet above is subtly wrong, and the failure is very visible:

```zsh
POSTDISPLAY=$'\e[2;3;90m'"$hint"$'\e[0m'    # ✗
```

ZLE writes `POSTDISPLAY` **verbatim**. It is a plain string, not a prompt, so
it is not prompt-expanded and the escape bytes are not interpreted — the user
sees the literal characters `^[[2;3;90m~/projects/myapp^[[0m` at their prompt.
This is not a terminal bug and not a "my terminal shows `^[`" problem; it is
the plugin printing its own markup.

Use `region_highlight`, ZLE's own mechanism for styling a region of the line.
This is exactly what zsh-autosuggestions does
(`/usr/share/zsh/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh`, in
`_zsh_autosuggest_highlight_apply`):

```zsh
_tai_update() {
  POSTDISPLAY=""; region_highlight=()
  ...
    POSTDISPLAY="$_TAI_SUGGESTION"                      # bare text
    region_highlight+=("${#BUFFER} $((${#BUFFER} + ${#_TAI_SUGGESTION})) $_TAI_HIGHLIGHT_STYLE")
}
```

Two details that matter:

- **Region offsets span `BUFFER` *and* `POSTDISPLAY`.** The start is
  `${#BUFFER}`, not the cursor, because the hint is drawn after the line. The
  end offset is exclusive.
- **Clear `region_highlight` on every pass** and on `line-init`, alongside
  `POSTDISPLAY`, or the highlight outlives the text it was styling.

Style syntax is zle region-highlight attributes (`fg=8`, `fg=244`, `bg=…`,
`bold`, `standout`, `underline`) — **not SGR numbers, and not `reverse`**, which
is not a type ZLE knows. One unknown attribute drops the *whole* region, not
just itself: measured on zsh 5.9.2, `reverse,fg=green` paints nothing at all,
not green. So the failure mode is "the text is drawn and no colour is", with no
warning — which looks exactly like a region that is out of range. Verify by
reading `$region_highlight` from a widget, and verify that it *worked* by
looking at the bytes ZLE paints; never assert on painted bytes alone.

A user reporting "the ghost text shows my escape codes" is reporting this bug
exactly — take it at face value instead of suspecting their terminal.

### One array element per region, or nothing is painted

```zsh
region_highlight+=("6 10 fg=8,bold")      # ✓ one element, three fields
region_highlight+=(6 10 fg=8,bold)        # ✗ three elements
```

The documented shape is **one array element per region, holding
`start end attrs` in a single string**. Three elements are read as three regions
of one character each, so ZLE emits no colour at all — no error, no warning, and
the post-display is painted in the terminal's default colours. Symptom: the text
is drawn perfectly and nothing is highlighted, which looks exactly like "this
cannot be highlighted". It cannot be that — see below. Measured here, zsh 5.9.2.

### A multi-row POSTDISPLAY can absolutely be highlighted

A menu drawn in a multi-line `POSTDISPLAY` — rows joined by newlines, the whole
thing preceded by one so it starts at column 0 — is coloured by
`region_highlight` exactly like a one-line hint. The claims that it cannot be
because "a widget wrote it" or "it spans more than one line" are both false, and
both cost a hand-drawn `▸` marker that nobody could theme. Offset arithmetic for
a layout of cells:

- characters count over `BUFFER` and the post-display together, so a menu whose
  post-display starts with a newline puts its first row at `${#BUFFER} + 1`;
- a row `r` starts one character past the end of row `r-1` (its own newline), and
  the gap between two cells belongs to the cell that follows it;
- measure each cell's start while its row is being laid out. Counting afterwards
  puts every cell after the first a couple of characters left of where it is
  drawn — and the menu still *looks* right, because the text is where the text is.

Two regions over the same characters **combine** rather than replacing each
other, despite what the manual's "the last specification wins" says: `fg=blue` and
`standout` over one cell give blue *and* reverse video. So do not overlap a
selection with a colour you do not want inside it — skip the selected cell.

Draw a selected menu cell by highlighting it, not by drawing a marker character
in front of it. `standout` is reverse video on any terminal that has it.

### `POSTDISPLAY` is also the answer to "what has the user been shown?"

Inside a widget, before painting anything, `POSTDISPLAY` still holds what the
last redraw drew — the text after the cursor that the user is looking at. That
makes it the honest thing to compare a completion against before writing to the
line: if the candidate is not what `$BUFFER$POSTDISPLAY` already says, the user
has not seen it, and one key should not commit it. Read it rather than your own
suggestion variable, because a plugin loaded earlier may be the one drawing the
hint (Manjaro's prompt loads `zsh-autosuggestions`). Symptom of getting this
wrong: a completion the user's own hint contradicts is filled in anyway, and the
"suggestion" they saw is coming from somewhere else entirely.

### POSTDISPLAY is one global slot: you are racing other plugins

There is exactly one `POSTDISPLAY`, and every plugin that draws a hint writes
it. `zsh-autosuggestions` and `zsh-syntax-highlighting` both do. This is not a
hypothetical: Manjaro's `manjaro-zsh-prompt` sources
`zsh-autosuggestions` **from the prompt file, not the config file**, so it is
easy to miss when auditing a `.zshrc`, and distro configs load it *before* any
user-installed plugin.

Rules that follow:

- **Be the last writer.** Recompute on `line-pre-redraw` (fires on every
  refresh) and never cache a `precmd`-time value. Verified: with autosuggestions
  loaded *after* tai, the ghost is wiped entirely (`POSTDISPLAY` ends up empty);
  loaded before tai, tai wins.
- **Never source order by accident.** Assert this in a test — load the
  competing plugins, then the plugin under test, then check the hint survives.
- **Never bind a key another plugin owns.** autosuggestions and friends also
  wrap widgets and rewrite `precmd`; adding to those lists by hand breaks them.
- To disable a conflicting plugin cleanly, remove its own hook rather than
  neutering the function:
  `add-zsh-hook -d precmd _zsh_autosuggest_start`
- On Manjaro also `unsetopt correct` — its autocorrect rewrites the buffer as
  you type, which fights any inline hint.

### Pick the lookup key from the last *complete* word, not from the line

A generated completion index that stores one key per cumulative word boundary —
`_TAI_WORD["ls"]`, `_TAI_WORD["ls "]`, `_TAI_WORD["ls -la"]` — is a trap if the
plugin queries it with the raw line. `"ls -l"` is itself a key, holding only
`ls -l`, so the lookup succeeds, returns a candidate, and the user sees no
suggestion at all. The key that also holds `ls -la` is `ls`.

```zsh
word="${prefix% *}"                       # last complete word
values="${_TAI_WORD[$word]:-}"             # a superset of _TAI_WORD[$prefix]
```

The exact-prefix lookup is not just redundant, it is strictly worse: the
generator emits every cumulative prefix, so the shorter key always contains
everything the longer one does.

### A candidate equal to what is typed is not a suggestion

The highest-scoring candidate is often the line you already have — that is what
a frequency model produces when you have typed `ls -l` a hundred times. It can
never be rendered as a hint, and letting it win hides the candidate that *could*
have extended the line. Filter it out inside the candidate loop, not in the
display step, or `ls -l` shadows `ls -la` forever.

```zsh
[[ -z "$c" || "$c" == "$prefix" || "$c" != "$prefix"* ]] && continue
```

The same rule has to hold on the non-ZLE side: a `tai suggest "ls -la"` that
answers `ls -la` is a suggestion system telling you what you already typed. When
there is genuinely nothing to add, return empty — not the input echoed back.

- `BUFFER` is the whole line, `CURSOR` is a character index into it.
- `zle -N name` then `bindkey '^F' name`. A widget that should also fall back
  to a builtin does `zle other-widget 2>/dev/null` inside itself.
- **An arrow key is a widget, not a byte sequence.** To change what `→` does,
  replace the widget it resolves to: `zle -N forward-char my-forward`, with
  `zle .forward-char` (leading dot = the builtin) as the fallback. Measured on
  zsh 5.9.2: once ZLE has emitted terminfo's `smkx` at line-init — which is for
  the whole time the prompt is waiting — the terminal is in application-cursor
  mode and sends `ESC O C`, and in that mode **neither** `bindkey '^[[C'` **nor**
  `bindkey '^[[OC'` is ever reached; `bindkey kcuf1` is not a thing either
  (`undefined-key`). So binding both spellings, which is the usual advice, is
  worse than useless: it looks right, every test written with `ESC [ C` passes,
  and the key does nothing for every user on a real terminal. Wrapping the widget
  catches both forms, and a user who rebound the arrow never reaches your widget
  at all. Symptom: "the key that takes my suggestion does nothing" on konsole or
  anything xterm-compatible, while a scripted test with the normal-mode bytes
  passes. `zsh-autosuggestions` does the same thing, for the same reason.
- `add-zle-hook-widget` is the supported way to stack hooks; assigning to
  `precmd_functions` by hand breaks other plugins.
- `${(z)var}` splits a string into shell words respecting quotes. Needed when a
  variable holds `"python3 /path/cli.py"`.

## bash / readline

### bind -x runs a shell command, so name a function

```bash
bind -x '"\C-f": _tai_accept'      # ✓ names a function
bind -x '"\C-f": BUFFER+="foo"'    # ✗ runs that text as a command
```

`bind -x` does **not** define a widget. It stores a string that readline runs
in the shell, with `READLINE_LINE` and `READLINE_POINT` set, so you must give
it a function *name*. Passing shell code is not rejected: `bind` accepts it and
nothing useful happens, with no error to warn you. That silence is the trap.
Note the asymmetry with zsh, where `zle -N` and `add-zle-hook-widget` *do*
complain (when invoked), so the same mistake fails loudly in zsh and quietly in
bash.

`READLINE_LINE` and `READLINE_POINT` are the only writable state. Assigning
them changes the line; `READLINE_POINT` is a character index, clamped by
readline but worth clamping yourself.

```bash
_tai_accept() {
  local out; out="$(_tai_query "$READLINE_LINE" "$_TAI_LAST")"
  if [[ -n "$out" && "$out" == "$READLINE_LINE"* ]]; then
    READLINE_LINE="$out"; READLINE_POINT=${#READLINE_LINE}
  fi
}
```

To do "accept, else normal movement", implement the movement yourself:

```bash
elif (( READLINE_POINT < ${#READLINE_LINE} )); then
  READLINE_POINT=$((READLINE_POINT + 1))
fi
```

You **cannot** invoke readline's own completion from inside `bind -x`. So
`Tab` is either your completion or a real `complete`-driven completion, never
both. Do not bind `Tab` unless you are willing to break filename completion.

### Completion is fill `COMPREPLY`, nothing else

```bash
_tai_complete() {
  local line="${COMP_LINE:0:$COMP_POINT}" values
  COMPREPLY=()
  while IFS= read -r c; do COMPREPLY+=("$c"); done <<< "$values"
}
complete -o nospace -F _tai_complete tai     # only after the command `tai`
complete -D -o nospace -F _tai_complete     # for every command
```

`COMP_LINE` is the line, `COMP_POINT` the cursor index — unlike zsh there is no
`$BUFFER`/`$CURSOR` in completion functions.

### readline/bash traps

- **Empty associative-array subscripts are a hard error.** `${_TAI_FIRST[$first]}`
  with `first=""` prints `bad array subscript` on bash 5.x and aborts. zsh
  returns empty. Always guard `[[ -n "$key" ]]` before indexing. Symptom is an
  error on an empty prompt, which is the *most* common place to press a key.
- **Never quote a multi-word command.** `( "$_TAI_BIN" record ... )` breaks when
  `_TAI_BIN` is `python3 /path/cli.py`; bash looks for one file with a space in
  the name. Use an argv array: `read -r -a _TAI_ARGV <<< "$_TAI_BIN"` then
  `"${_TAI_ARGV[@]}"`. This only bites when the binary is not on `PATH`, so it
  hides from anyone testing only an installed copy. zsh's `${(z)...}` splits
  correctly and does not have this bug — a useful asymmetry to remember when
  one shell records and the other does not.
- **`bind -x` replaces the key entirely.** Overriding `Alt-F`/`Meta-F` silently
  removes readline's `forward-word`. Prefer keys whose loss users will not
  notice, and never repurpose a common editing key without saying so.
- `PROMPT_COMMAND` must be appended idempotently:
  `[[ "$PROMPT_COMMAND" != *"_tai_prompt"* ]] && PROMPT_COMMAND="_tai_prompt;$PROMPT_COMMAND"`.
- Capture `$?` **first**: `local code=$?` on the hook's first line, before any
  other command clobbers it.
- Background your writes: `( ... & )` with output to `/dev/null` so a slow
  start never delays the prompt.

## Ghost text in bash: what actually exists

Nobody does this in stock readline. The real options:

- **ble.sh** — a line editor written in pure bash that *replaces* readline.
  Its `auto-complete` (default on bash 4.0+) suggests as fish and
  zsh-autosuggestions do, styled `ble-face -s auto_complete fg=238,bg=254`.
  Accept keys: `S-RET`, and at end of line `right` / `C-f` / `end`; one word
  with `M-right` / `M-f`; accept-and-run with `C-RET`. High confidence, from
  ble.sh's own README.
- **fish** — own editor, so inline suggestions are native.
- **atuin** — no ghost text; it is a history *search* UI, and optionally a
  daemon.
- Hand-rolled `bind -x` + escape codes — works until multi-line input, wide
  characters, or a colourful prompt, then corrupts the display. Avoid.

Note ble.sh picks `right` and `C-f` to accept. That is a good sanity check on
any key choice.

## Testing shell plugins

**Calling the functions from a non-interactive shell tests almost nothing.** It
skips key bindings, the `READLINE_LINE`/`READLINE_POINT` contract, `ZLE`
redraw, and prompt-time recording. Drive a real interactive shell on a pty
instead. Stdlib only:

```python
pid, fd = pty.fork()
if pid == 0:
    os.execve("/usr/bin/bash", ["bash", "--norc", "-i"], env)
os.write(fd, b"git status")       # type
os.write(fd, b"\x1b[C")          # right arrow
os.write(fd, b"\x1b")            # ESC to abandon the line
```

### A pty harness must wait for evidence, never for a stopwatch

`os.write(fd, data); time.sleep(0.4)` is a guess with a 400ms price on every
interaction, and it is *still* wrong on a loaded machine. A pty is an
asynchronous conversation, and there is almost always something observable to wait
for instead:

| what you need to know | wait for |
| --- | --- |
| a command finished | a marker the shell printed: `cmd; echo __done_N__` |
| a keypress was processed | output to stop arriving |
| a widget or `$EDITOR` ran | the file it writes to exist, with content |
| a detached process finished | the row/state it was going to write |

Read with `select` and a short backstop; `select` returns the moment anything
arrives, so the poll interval is never a delay before the first read. Then let
the timeout be a *backstop that raises with the terminal tail attached*, so a hang
is a failure with a diagnosis rather than a stall.

One suite here went from **245s to 16s** with no change to what it asserts: the
whole cost was `time.sleep`. Reusable specifics that mattered:

- **Do not wait for the echo.** Matching the typed text on screen looks like real
  evidence instead of silence, and is far *slower*: back-to-back writes arrive as
  one `read`, the line editor redraws once at the end rather than per character,
  and the wait times out. Measured 245s against 16s for the same suite. Wait for
  the output to stop.
- **Do not bind a test widget to `^X` in zsh.** `^X` is a *prefix* in the emacs
  keymap, so the widget does not dispatch until zsh gives up waiting for a longer
  sequence: `KEYTIMEOUT`, 0.4s by default. The same widget measured **2.1ms bound
  to `^Y` and 400.7ms bound to `^X`**, fixed cost per press, 31 presses in the
  suite. Pick a key that is not a prefix. This is invisible in a unit test and
  impossible to guess at.
- **One write, one wait per session.** Sourcing the plugin, defining the dump
  widget and binding it are three statements; sending them as one chunk with one
  marker at the end turns four round trips into one.
- **Reuse a session when the read does not consume it.** A zsh dump widget
  *copies* the buffer, so one session can answer twenty ghost-text assertions. A
  bash `C-x C-e` aborts the line, so that one does need a fresh session.
- **Spend seconds in timestamps, not sleeps.** `-nt` compares whole seconds, so
  a test that needs "the file changed" should set an mtime with `os.utime`, not
  `sleep(1.1)`.
- **When an assertion fails, print the terminal tail.** `command not found: xit`
  is not a diagnosis; the four lines above it are.

### Reading the buffer back

The only reliable way to see what a key sequence left in the line:

- **bash**: `C-x C-e` (`edit-and-execute-command`) hands the line to `$EDITOR`.
  Set `EDITOR` to a script that copies `$1` somewhere and `exit 1` — the
  non-zero exit stops the command from running.
- **zsh**: `C-x C-e` is **not** bound by default. Bind a widget, remembering
  it needs a real function:

  ```zsh
  tai_test_dump() { print -r -- "$BUFFER" > /tmp/buf; print -r -- "SUG=[$_TAI_SUGGESTION]" >> /tmp/buf }
  zle -N tai_test_dump; bindkey '^X' tai_test_dump
  ```

  Verified: with the tai plugin loaded, typing `docker` then `C-x` writes
  `docker` and `SUG=[ ps]`.

Use `--norc -i` (bash) or `-f -i` (zsh) so the developer's own config cannot
change the result.

### "It only worked after I sourced the rc file"

Expected, and the cause is worth being precise about rather than papering over:

- **A new line in an rc file is invisible to the running shell.** That shell
  read the file at startup, before the line existed. An installer that appends
  to `~/.zshrc` cannot fix the shell that spawned it — separate process,
  separate memory. It can only print the command.
- **An edited plugin file is also inert until re-sourced.** Sourcing defines
  shell functions into memory; editing the file on disk changes nothing for the
  current session. So "I fixed the bug but nothing changed" usually means the
  fix was never loaded.
- `source ~/.zshrc` works but re-runs every other plugin in it a second time.
  `exec $SHELL` re-reads the rc once and cleanly, and is the better suggestion.

An installer should detect the calling shell and print a copy-pasteable
command rather than a generic "restart your terminal":

```sh
_parent="$(basename "$(ps -o comm= -p "$PPID" 2>/dev/null || echo unknown)")"
case "$_parent" in
  zsh)  reload="exec zsh" ;;
  bash) reload="exec bash" ;;
  *)    reload="# open a new terminal, or: exec \$SHELL" ;;
esac
```

Also make repeated `source plugin.zsh` a no-op. `add-zsh-hook` already
deduplicates, but `bindkey` and widget re-registration do not accumulate
silently, so guard anything non-idempotent.

### Isolate every input, or the harness rewrites your data

A pty harness inherits the developer's whole environment, and two of those
variables are destructive:

- **`HISTFILE`** — bash and zsh both append every line they read to it and
  write it out on exit, *including lines the test typed and then abandoned*.
  A harness that forgets this appends its own keystrokes to the real history,
  which then flows into `import-history`, the ranking model, and the built
  index. It is silent, cumulative, and it makes later runs non-reproducible.
  Set `HISTFILE` to a scratch path.
- **`TAI_INDEX`** (or whatever points at the generated snapshot) — otherwise
  every assertion silently depends on the developer's real commands. A test
  asserting `git status` completes to `git status` passes or fails based on
  whether that command happens to be in their history.

Check with a before/after copy of the real file around a full run:

```sh
cp ~/.bash_history /tmp/hist.before
python3 test_plugins.py
diff /tmp/hist.before ~/.bash_history && echo "history untouched"
```

A non-empty diff means the harness is leaking. This check found 160 polluted
lines in one repo's real history in a single session.

**A background writer is worse than a leak, because it is a race.** Recording a
stored command can trigger a detached rebuild that writes the index the test is
asserting on. It fires mid-run, so the test that breaks is whichever one happens
to read the fixture at that moment — and it reads as a ranking bug in a test that
has nothing to do with it. Two rules, and both are load-bearing:

- The default harness session is **read-only**: recording off, learning off, and
  a throwaway index path for the one test that genuinely has to record.
- **Assert the fixture is still yours** at the end of the run, by its header
  line. A late check turns "some unrelated test is wrong" into "something
  replaced my input", which is the only actionable form of the message.

This one cost real time: a background rebuild swapped a hand-written index
fixture for a generated one, `sudo git ` then resolved to a different candidate,
and the failure pointed at the wrapper feature instead of at the harness.

### Assert on a fixed fixture, not on built output

If the shell plugin reads a generated index, hand-write a tiny fixture for the
tests instead of building one with the real generator. Ranking is a separate
concern with its own tests; mixing them makes every assertion depend on
scoring internals *and* on whatever the developer happens to type.

Keep fixture keys **never empty**. `_TAI_WORD['']` is fine in zsh and a hard
`bad array subscript` error in bash, so a generator bug shows up as a bash-only
stderr failure that looks like a plugin bug.

### Traps that produce fake failures

- **The command must be submitted.** `send("ls")` types it; `send("ls\n")` runs
  it. Without the newline, later sends concatenate and you test garbage. This
  bit a `source` line in setup code: nothing was loaded, no error appeared, and
  the assertion failed for a reason that had nothing to do with the plugin.
- **`C-x C-e` aborts the line** (bash, non-zero editor exit). One assertion per
  session, or the next check runs against an empty buffer and every result
  looks broken.
- **Command substitution hides mutations.** `out="$(_tai_accept_or_right)"`
  runs in a subshell, so `READLINE_LINE` changes are lost. Call the function
  directly and redirect stderr to a file to check it.
- **Test against a scratch database.** Export `TAI_DB=/tmp/...` or a
  verification run will happily append its own keystrokes to your real command
  history and pollute the ranking. Always assert on the scratch DB.
- **A leaked fixture value looks like a plugin bug.** When an assertion returns
  a string from a *previous* test, suspect cross-test contamination in the
  index or history before suspecting the editor logic. Grep for the string in
  the real history file; if it is there, the harness caused it.
- Assert the terminal is **clean**: scan output for `bad array subscript`,
  `unbound variable`, `No such file`, and `function definition file not found`.
  Several bugs here are invisible except as one line of stderr per prompt.

A working harness lives in `test_plugins.py` in this repository; copy it rather
than rewriting. It sets `HISTFILE`, writes a fixed index fixture, and covers the
competing-`POSTDISPLAY`-plugin load order.

## Reading a variable out of a live shell to settle a rendering question

When a user reports what looks like a rendering bug, confirm the bytes before
changing any code. Write the value to a file from inside the shell and inspect
it as bytes — terminal output and chat transcripts both *display* an ESC as the
two characters `^[`, which is indistinguishable from a literal `^[` in the
buffer:

```zsh
print -r -- "PD=<$POSTDISPLAY>" >> /tmp/out
```

```python
for line in open("/tmp/out", "rb").read().split(b"\n"):
    if line.startswith(b"PD="):
        print(line, line.hex(" "))    # 1b = a real ESC
```

`^[[2;3;90m` in a paste is *not* evidence that the shell emitted literal
`^[`. Check `.hex()` first: a real `1b 5b 32 3b ...` is correct output, and the
bug is elsewhere — in the terminal, the multiplexer, or a plugin rewriting the
parameter. Verified here: the bytes were a correct ESC and the reported
"literal escape" was an artifact of how the value was being displayed.

Same technique for config questions: `zsh -i -c 'source ~/.zshrc; print -r -- ...'`
settles "is this option actually on" without a pty.

## Portability checklist

- [ ] Guard every associative-array index against an empty key (bash errors),
      including keys written by a generator — and guard it for whitespace-only
      input, which is where `"${prefix% *}"` becomes `""`.
- [ ] Candidates looked up by the last complete word, not by the raw line.
- [ ] A candidate identical to the typed text cannot win the ranking.
- [ ] `autoload -Uz` any zsh helper function before testing `$+functions`.
- [ ] `zle -N` / `add-zle-hook-widget` / `bind -x` receive function names only.
- [ ] Multi-word commands expanded as argv arrays, never a quoted string.
- [ ] `$?` captured on the first line of a prompt hook.
- [ ] `PROMPT_COMMAND` appended idempotently.
- [ ] No key binding silently steals a common editing key.
- [ ] Recording has an off switch read at prompt time, so a test or a
      one-off experiment can skip the write.
- [ ] Anything the plugin learns from use (a tool's `--help`) has its own
      switch, separate from recording: a harness that asserts recording works
      also needs the generated index to stay exactly as it wrote it, and a
      learn-triggered rebuild lands on the test's own inputs.
- [ ] No SGR escape bytes inside `POSTDISPLAY`; styling via `region_highlight`,
      and `region_highlight` cleared wherever `POSTDISPLAY` is cleared.
- [ ] Every `region_highlight` entry is ONE array element holding
      `start end attrs`, with a valid style (`standout`, not `reverse`).
- [ ] Colours asserted on a screen that keeps per-cell attributes, not on the
      plugin's arrays: correct text with no colour passes every other check.
- [ ] Recompute `POSTDISPLAY` on every `line-pre-redraw`, and tested against
      `zsh-autosuggestions` / `zsh-syntax-highlighting` loaded first.
- [ ] A rebuilt index is picked up by a shell that is already running, and a
      shell with no index at all is quiet rather than throwing on every keypress.
- [ ] No `time.sleep` standing in for a synchronisation point: every wait ends on
      a marker the shell printed, a file that appeared, or output that stopped —
      with a timeout that raises and shows the terminal tail.
- [ ] Any zsh test widget is bound to a key that is **not** a prefix (`^X` is),
      so it does not pay `KEYTIMEOUT` on every press.
- [ ] Verified on both shells, in a real terminal, with a scratch database,
      scratch `HISTFILE`, and a fixed index fixture — and a before/after diff
      proving the real history file was not touched.
