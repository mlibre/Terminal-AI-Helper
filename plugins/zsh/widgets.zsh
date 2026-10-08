# The redraw, the keys, and what is bound to them.

# Style the hint with region_highlight, not with escape bytes inside
# POSTDISPLAY. ZLE writes POSTDISPLAY verbatim, so an embedded \e[2;3;90m is
# echoed as the literal characters "^[[2;3;90m" instead of dimming anything.
# region_highlight is ZLE's own mechanism for colouring a region of the line;
# zsh-autosuggestions styles its hint the same way.
#
# The one thing this must not do is *clear* a hint it did not draw. POSTDISPLAY is
# a single slot with no namespacing, and zsh-autosuggestions writes it — from the
# history file, for lines tai has no candidate for at all, because `_TAI_FIRST` is
# keyed by whole first words and a half-typed command name is not a key. Writing
# an empty string there therefore erased a visible hint on every keystroke, and
# the key that takes hints then found nothing to take: `nan` hinting `o .zshrc`
# and Tab opening a list over it. So POSTDISPLAY is only ever written when tai
# has something to say, and cleared when what tai said is gone — never to silence
# someone else.
# A paste is not typing, and the glimpse must not treat it as one. The signal
# is the terminal's own: a terminal that speaks bracketed paste wraps what it
# sends in the paste envelope, and the widget below marks whatever arrives in
# one. That is a paste however short it is, and one typed or removed character
# afterwards is a keystroke again, which re-arms the glimpse. A jump of many
# characters in one redraw — a paste in a terminal that does not send the
# envelope, an insertion nobody wrapped — is marked too, at a bound high
# enough that the fastest coalesced burst of ordinary typing never reaches it.
# The ghost hint is untouched by any of this: a prefix answer to the line as it
# now stands is one line, not a screen.
typeset -g _TAI_PASTE_PREV=""
typeset -gi _TAI_PASTE_QUIET=0
typeset -gi _TAI_PASTE_JUMP=25

# Is the line *typed*? A history line is not: Up fills the buffer with a
# command the user ran once, and on that buffer Down means "the next history
# entry" — it did not mean "open the list" until something was typed. The flag
# is set by the widgets that mean editing (insert, delete, taking a hint) and
# cleared when a whole line arrives some other way: the arrow widgets below
# clear it as they hand the key to history, and a new line starts without it.
# Without the flag, Up-then-Down through the history opened the loose list on
# a line nobody typed, with the line itself as its only row.
typeset -gi _TAI_TYPED=0

_tai_paste_state() {
  # The unwrapped case: how far the buffer moved since the last redraw. One
  # character in either direction is a keystroke — typed or removed — and ends
  # the quiet; more than _TAI_PASTE_JUMP at once is an arrival and starts it.
  # Everything between leaves the flag as it is.
  local -i d=$(( ${#BUFFER} - ${#_TAI_PASTE_PREV} ))
  (( d < 0 )) && d=$(( -d ))
  if (( d == 1 )); then
    _TAI_PASTE_QUIET=0
  elif (( d > _TAI_PASTE_JUMP )); then
    _TAI_PASTE_QUIET=1
  fi
  _TAI_PASTE_PREV="$BUFFER"
}

# The bracketed-paste envelope is the terminal saying "this much arrived at
# once". Wrapped, not replaced: the builtin still reads to the closing marker
# and inserts the text, and the flag it sets comes down at the next keystroke —
# any edit of one character — because the rule is about what arrived, not a
# permanent mode.
tai-bracketed-paste() {
  _TAI_PASTE_QUIET=1
  zle .bracketed-paste
}
zle -N tai-bracketed-paste
if (( $+widgets[bracketed-paste] )) && \
   [[ "${widgets[bracketed-paste]:-}" != *tai-bracketed-paste* ]]; then
  zle -N bracketed-paste tai-bracketed-paste
fi

_tai_update() {
  local -i ours_was=$_TAI_DREW
  # What the last widget was decides whether this buffer is typed. The hook
  # runs after every widget, and the names here are the ones that mean an
  # edit; anything else leaves the flag as it was, because "unknown" is not
  # evidence either way and the arrows speak for themselves.
  case "${LASTWIDGET:-}" in
    self-insert*|backward-delete-char*|delete-char*|tai-accept|tai-accept-word|tai-accept-or-complete|tai-tab|tai-enter)
      _TAI_TYPED=1 ;;
  esac
  _TAI_SUGGESTION=""
  region_highlight=()
  # The menu stands in for the ghost while it is open, and POSTDISPLAY holds one
  # of them at a time — the two cannot both be drawn.
  if _tai_menu_live; then
    _tai_menu_paint
    return
  fi
  if (( _TAI_MENU_IDX )); then
    _tai_menu_close
    # A menu closing must take its own rows with it, or the pieces of it stay on
    # screen. Only ever ours: `ours_was` says whether we put it there.
    (( ours_was )) && POSTDISPLAY=""
  fi
  _tai_paste_state
  _tai_query "$BUFFER" "$_TAI_LAST"
  if [[ -n "$_TAI_BEST" && "$_TAI_BEST" == "$BUFFER"* && "$_TAI_BEST" != "$BUFFER" ]]; then
    _TAI_SUGGESTION="${_TAI_BEST#$BUFFER}"
    POSTDISPLAY="$_TAI_SUGGESTION"
    _TAI_DREW=1
    region_highlight+=("${#BUFFER} $((${#BUFFER} + ${#_TAI_SUGGESTION})) $_TAI_HIGHLIGHT_STYLE")
  elif (( ours_was )); then
    # What tai drew last redraw is gone, so the slot is ours to clear — and only
    # ours. This is the branch that has to know what the *last* redraw put there,
    # which is why `ours_was` is read before anything is written: clearing on
    # `_TAI_DREW` after resetting it is clearing on a value that is never true,
    # and tai's own hint then stayed on screen after it stopped being true.
    POSTDISPLAY=""
    _TAI_DREW=0
  fi
  # Otherwise POSTDISPLAY holds another plugin's hint, it is theirs to take down,
  # and the keys that take hints read it — see _tai_shown_hint.
  #
  # Nothing on paper for this line — nothing matched, no path answer, no use of
  # an installed tool — but there is something that mentions the words typed:
  # draw it. This is the interactive counterpart to the --help fallback: not a
  # guess, but a show of the mostly-through-the-glass completions the history
  # doesn't offers straight.
  #
  # Not while the line is a paste, though: it arrived at once and nobody has
  # read it, so three rows of guesses under it is noise on top of noise — the
  # report that asked for this rule called it exactly that. One typed or
  # removed character re-arms the glimpse, and Down asks for it directly at any
  # time.
  if (( ! _TAI_MENU_IDX )) && [[ -z "$_TAI_BEST" ]] && \
     (( _TAI_TYPED )) && (( ! _TAI_PASTE_QUIET )) && \
     (( ${#BUFFER} >= _TAI_LOOSE_MIN_PREFIX )) && \
     (( ${#_TAI_LOOSE_LINES} )) && \
     [[ -z "${_TAI_SCORE[$BUFFER]:-}" ]] && \
     [[ "$BUFFER" != "$_TAI_MENU_DISMISS" ]] && \
     [[ "${TAI_NO_MENU:-0}" != "1" ]]; then
    _tai_menu_open_loose && _tai_menu_paint
  fi
}
# 1 when POSTDISPLAY holds something tai drew: a hint or a menu. The one bit of
# state that decides whether clearing the slot is cleaning up after ourselves or
# deleting a stranger's work.
typeset -gi _TAI_DREW=0
typeset -g _TAI_MENU_DISMISS=""
# 1 when the loose selection has been closed by Up on its first entry: while the
# buffer keeps the same text, the list does not spring back. Any keystroke that
# changes the prefix past the dismiss case re-arms it.

# The hint that is on screen, into _TAI_HINT, and true when there is one.
#
# Not `_TAI_SUGGESTION`, and that is the whole point of this function. tai and
# zsh-autosuggestions share one POSTDISPLAY, autosuggestions fetches
# *asynchronously* and therefore writes the slot after tai's redraw, and tai
# loading last does not change that — load order decides who writes first, not
# who writes last. So for every line the history file can answer, tai's own
# variable is empty while a hint is plainly visible, and a key that asked tai's
# variable opened the list *over* it: `nan` hinting `o .zshrc`, and Tab listing
# instead of completing. The user pressed the key that takes hints while looking
# at a hint; that is the promise, whoever drew it.
#
# A menu is a multi-line POSTDISPLAY that opens with a newline, so "non-empty and
# holding no newline" is a hint and nothing else — which is the same test the
# one-entry menu rule uses, for the same reason.
_tai_shown_hint() {
  if [[ -n "$_TAI_SUGGESTION" ]]; then
    _TAI_HINT="$_TAI_SUGGESTION"
  elif [[ -n "$POSTDISPLAY" && "$POSTDISPLAY" != *$'\n'* ]]; then
    _TAI_HINT="$POSTDISPLAY"
  else
    _TAI_HINT=""
  fi
  [[ -n "$_TAI_HINT" ]]
}
typeset -g _TAI_HINT=""

# Accept widgets.
tai-accept() {
  _tai_shown_hint || return
  # A name off the disk is quoted on the way in, or the line says `cat My
  # Document.pdf` and what runs is `cat My Document.pdf` with two arguments. The
  # hint on screen stays the bare name: it is a preview of the file, and the user
  # should not have to read a quoting rule to recognise it.
  #
  # Two branches rather than one `$( … || print … )`, so the common answer costs
  # no process: this is a key press, but it is one keystroke from the path that
  # has none at all.
  if (( _TAI_BEST_Q )); then
    _tai_quote "$_TAI_HINT"
    BUFFER+="$_TAI_QUOTED"
  else
    BUFFER+="$_TAI_HINT"
  fi
  CURSOR=${#BUFFER}
  _TAI_SUGGESTION=""
  _TAI_HINT=""
  POSTDISPLAY=""
  region_highlight=()
}
zle -N tai-accept

# One key, one question — and Tab's question is the word it is on. `→` is the
# key that takes the hint; Tab cycles the completions through the menu this
# plugin draws, so files, folders and options are what it moves through.
#
# With no menu to cycle, Tab is zsh's own completion — the word it is on, the
# files around it, the options of it. Either way it never accepts the ghost:
# that hint is for `→`, whichever plugin drew it (see _tai_shown_hint, which is
# why the arrow reads the screen rather than tai's own variable).
#
# The exception is the `--help` hint for a command the history has never seen,
# and it is about tai's *own* answer only. `docker ` is not asking which file, and
# `docker --help` is not the answer to anything the user typed — it is tai
# guessing at what you meant, from no evidence at all. It stays a hint — `→`
# takes it — and Tab still lists the honest completion (`--help`) and the files.
tai-tab() {
  (( CURSOR == ${#BUFFER} )) || { zle expand-or-complete; return }
  tai-menu
}
zle -N tai-tab

# Tab opens the menu, and every Tab after that moves the selection one entry
# along. The line is not touched: a menu is a way of looking, not of choosing.
#
# When there is nothing to offer, the key goes back to zsh's own completion.
# `docker` has nothing to complete *to*, because everything tai knows about it
# starts with `docker`, so the honest answer there is the file listing — not a
# menu of one entry that is the word already typed.
#
# One entry is not a list — nothing to look at, nothing to choose between — so
# a single candidate is committed, which is what `cd t<Tab>` does when the
# only thing starting with `t` is one directory.
tai-menu() {
  # The index is keyed on whole lines, so there is no history behind a word in
  # the middle of one, and replacing a word the cursor is not on is not a
  # completion. That case is zsh's, and it is better at it than this is.
  (( CURSOR == ${#BUFFER} )) || { zle expand-or-complete; return }
  # The menu belongs to one word. If the line or the cursor has moved since it
  # was built, that word is gone.
  _tai_menu_live || _tai_menu_close
  if (( _TAI_MENU_IDX )) && (( _TAI_MENU_ARMED )); then
    _TAI_MENU_IDX=$(( _TAI_MENU_IDX % $#_TAI_MENU + 1 ))
    _tai_menu_paint
    return
  fi
  if (( _TAI_MENU_IDX )); then
    # The user pressed for the list, not the guess-and-check preview; the list
    # is rebuilt from what the line actually completes to.
    _tai_menu_close
  fi
  if _tai_menu_open; then
    :
  elif _tai_menu_open_loose; then
    # Asked for with the key: unlike the preview that appears on its own, there
    # is no reading phase.
    _TAI_MENU_ARMED=1
  else
    zle expand-or-complete
    return
  fi
  # One entry is not a list — nothing to look at, nothing to choose between — so
  # the key takes it, which is what every shell does with an unambiguous
  # completion. It also reaches the loose list: a glimpse of exactly one learned
  # line is answered, not dangled.
  if (( ${#_TAI_MENU} == 1 )); then
    _tai_menu_commit
    return
  fi
  _tai_menu_paint
}
zle -N tai-menu

# Enter takes the selected entry, and stops there. It does not also run the line,
# because filling in a directory you picked out of a menu is not a request to go
# there: you may have wanted the rest of the path, or another argument, or to see
# what you are about to run. A second Enter runs it, and between the two the line
# says exactly what it will do. With no menu open, Enter is Enter.
tai-enter() {
  if (( _TAI_MENU_IDX )) && (( _TAI_MENU_ARMED )); then
    _tai_menu_commit
    return
  fi
  # A glimpsed menu still swallows the selection; Enter at a disarmed menu is
  # the line's Enter.
  if (( _TAI_MENU_IDX )); then _tai_menu_close; POSTDISPLAY=""; region_highlight=(); fi
  zle accept-line
}
zle -N tai-enter

# Right arrow accepts the suggestion at the end of the line, and moves the
# cursor one character anywhere else. Bound straight to tai-accept it took
# forward-char away entirely: with a ghost on screen there was then no key left
# that could step right, which is what the user reaches for when they want to
# keep typing inside the hint instead of taking all of it.
#
# It replaces the `forward-char` *widget* rather than binding the key, and that
# is the whole trick. The arrow does not reach a plugin as bytes: ZLE resolves
# it to the widget, in both cursor-key modes. A terminal in application mode —
# which is where a terminal is once ZLE has emitted terminfo's `smkx` at
# line-init, i.e. for the whole time the prompt is waiting, on konsole and on
# every xterm-compatible terminal — sends `ESC O C` and not `ESC [ C`, and then
# *neither* byte string is ever looked up: measured, `bindkey '^[[C'` and
# `bindkey '^[[OC'` are both dead there, and `bindkey kcuf1` is not a thing.
# The `→` did nothing at all, and a hint on screen with a key that does nothing
# to take it is the report. Wrapping the widget catches both forms, and a user
# who has bound the arrow to something of their own never reaches this at all.
tai-forward-char() {
  # The arrow keys are the ghost's keys. With a menu open there is no ghost to
  # take, so the arrow puts the menu away and the next redraw brings it back.
  (( _TAI_MENU_IDX )) && _tai_menu_close
  # _tai_shown_hint and not `_TAI_SUGGESTION`, and this is load-bearing rather
  # than tidy: replacing the `forward-char` *widget* is what lets the arrow work
  # in both cursor-key modes, and it also replaces the wrapping
  # zsh-autosuggestions put around that same widget. So this widget is the only
  # thing standing between the arrow and a hint that plugin drew — reading tai's
  # own variable here made `→` stop accepting the hint the user could see.
  if _tai_shown_hint && [[ $CURSOR == ${#BUFFER} ]]; then
    zle tai-accept
  else
    zle .forward-char                 # the builtin, not this one
  fi
}
zle -N forward-char tai-forward-char

# Up/Down over a loose menu. The loose menu is previewed while typing — each
# line of it holds a whole command, and Enter on it is the typed line's Enter.
# It only becomes the *selection* on Down: that marks the first entry, and from
# then on Down steps, Up steps, and Up from the first entry leaves the list and
# returns to the line typed.
#
# The arrows are captured by their *key sequences*, never by replacing the
# widget names a guess might bind them to: zsh's stock keymap sends the arrow
# to down-line-or-history, but manjaro-zsh-config and zsh-history-substring-
# search steal it for history-substring-search-down, and a plugin that only
# replaced down-line-or-history never sees that key. So the sequences go to
# these widgets, and what the key used to run becomes the fallback for when
# this plugin has no menu to show: history search keeps searching, a stock
# shell keeps scrolling the history.
#
# The fallback is the name of the *old* handler. For a stock keymap that is
# plain down-line-or-history — this plugin never replaces widgets, so the
# name still reaches the original. For a function widget (the
# history-substring-search pair is defined in shell code), its body is stashed
# under a fresh name before the key is re-bound, or the original is lost —
# zle would have it only while the keymap still points at it, which is the
# binding we are replacing.
#
# (The fallback also cannot be a `.down-line-or-history`: the `.`-prefixed
# form is looked up by name, and a widget registration hijacks it — called
# from a widget that replaced it, the "fallback" recursed until zsh's own
# function limit killed the keypress.)
_tai_arrow_fallback() {
  # The widget the given key ran before this plugin, or the stock name when
  # the key is free. bindkey prints one line, `"^[[B" widget`: the widget is
  # the last word; `undefined-key` means the key has no binding of its own.
  # The answer is written into _TAI_REPLY *without* a subshell, so the two
  # side effects inside the function-widget branch — a copy of the original
  # body and a new zle widget for it — survive the call. Asking for the
  # answer via $(...) would drop both again, which is how the keymap came
  # to name `_tai_orig_up' and ZLE said "No such widget".
  local line="$(bindkey "$1")"
  case "$line" in
    *undefined-key*|"") _TAI_REPLY="$2" ;;   # free: the plain zsh widget
    *)
      local w="${line##* }"
      if (( $+functions[$w] )); then
        functions[$3]="${functions[$w]}"   # stash the body
        zle -N "$3"
        _TAI_REPLY="$3"
      else
        _TAI_REPLY="$w"
      fi
      ;;
  esac
}

# Only on the first load does the keymap still know the *previous* handler.
# On a re-source the arrow already runs our widgets, and overwriting the
# stash then would save that as the fallback — the fallback recursing into
# the very widget it was called by. The previous answer has to live.
if [[ -z "${_TAI_DOWN_FALLBACK:-}" || -z "${_TAI_UP_FALLBACK:-}" ]]; then
(( $+terminfo )) || zmodload zsh/terminfo 2>/dev/null
# The arrow travels as `^[[B` until ZLE has emitted a prompt, and as the
# terminfo answer (`^[OB` on every terminal that has one) for the whole life
# of a line thereafter. Both spellings name one widget in the usual case;
# the application-mode answer is the fallback, which is also what a
# userless default keeps working for.
_tai_apps_d="^[OB"; _tai_apps_u="^[OA"
(( $+terminfo )) && [[ -n "${terminfo[kcud1]}" ]] && _tai_apps_d="${terminfo[kcud1]}"
(( $+terminfo )) && [[ -n "${terminfo[kcuu1]}" ]] && _tai_apps_u="${terminfo[kcuu1]}"
_tai_arrow_fallback "$_tai_apps_d" down-line-or-history _tai_orig_down
typeset -g _TAI_DOWN_FALLBACK="$_TAI_REPLY"
_tai_arrow_fallback "$_tai_apps_u" up-line-or-history _tai_orig_up
typeset -g _TAI_UP_FALLBACK="$_TAI_REPLY"
unset _tai_apps_d _tai_apps_u _TAI_REPLY
fi

tai-arrow-down() {
  if (( _TAI_MENU_IDX )); then
    if (( _TAI_MENU_ARMED )); then
      _TAI_MENU_IDX=$(( _TAI_MENU_IDX % ${#_TAI_MENU} + 1 ))
    else
      _TAI_MENU_ARMED=1
    fi
    _tai_menu_paint
    return
  fi
  if [[ "${TAI_NO_MENU:-0}" != "1" && -z "$_TAI_BEST" && \
        $_TAI_TYPED -eq 1 && \
        -z "${_TAI_SCORE[$BUFFER]:-}" && "$BUFFER" != "$_TAI_MENU_DISMISS" && \
        ${#BUFFER} -ge $_TAI_LOOSE_MIN_PREFIX && ${#_TAI_LOOSE_LINES} -gt 0 ]]; then
    # Asked for by arrow, not by a redraw: the list arrives armed. Asked for
    # by arrow on a *typed* line, that is — a buffer that arrived from the
    # history is a line the user ran once, and Down on it means history.
    _tai_menu_open_loose && _TAI_MENU_ARMED=1 && _tai_menu_paint
  else
    _TAI_TYPED=0
    zle "$_TAI_DOWN_FALLBACK"
  fi
}
zle -N tai-arrow-down
tai-arrow-up() {
  if (( _TAI_MENU_IDX )); then
    if (( _TAI_MENU_ARMED )) && (( _TAI_MENU_IDX > 1 )); then
      _TAI_MENU_IDX=$(( _TAI_MENU_IDX - 1 ))
      _tai_menu_paint
    else
      # First item; and if the menu was not even armed yet, the glimpse goes
      # back to the typed line.
      _TAI_MENU_DISMISS="$BUFFER"
      _tai_menu_close
      POSTDISPLAY=""
      region_highlight=()
    fi
    return
  fi
  _TAI_TYPED=0
  zle "$_TAI_UP_FALLBACK"
}
zle -N tai-arrow-up
# The keys move to tai. What the key used to run keeps working through the
# saved fallback: history search keeps searching, stock zsh keeps scrolling.
bindkey '^[[B' tai-arrow-down
bindkey '^[OB' tai-arrow-down
bindkey '^[[A' tai-arrow-up
bindkey '^[OA' tai-arrow-up


tai-accept-or-complete() {
  if [[ -n "$_TAI_SUGGESTION" ]]; then zle tai-accept; else zle expand-or-complete; fi
}
zle -N tai-accept-or-complete

tai-accept-word() {
  (( _TAI_MENU_IDX )) && _tai_menu_close
  # Whatever drew the hint, for the same reason the arrow does:
  # _tai_shown_hint.
  if _tai_shown_hint; then
    local rest="$_TAI_HINT"
    # A file name is one word however it is written, so `cat My` takes the whole
    # of `My Document.pdf` rather than `My\` — a trailing backslash continues the
    # line, which is the one thing a completion must never leave behind.
    if (( _TAI_BEST_Q )); then
      _tai_quote "$rest"
      BUFFER+="$_TAI_QUOTED"; CURSOR=${#BUFFER}; _TAI_HINT=""; _tai_update
    elif [[ "$rest" =~ '^([[:space:]]*[^[:space:]]+)' ]]; then
      BUFFER+="$match[1]"; CURSOR=${#BUFFER}; _TAI_HINT=""; _tai_update
    else zle tai-accept; fi
  else zle forward-word 2>/dev/null; fi
}
zle -N tai-accept-word

_tai_line_init() {
  POSTDISPLAY=""
  _TAI_SUGGESTION=""
  _TAI_HINT=""
  _TAI_DREW=0
  _TAI_MENU_DISMISS=""
  # A new line is a new story: no paste has arrived on it yet, and nothing
  # on it is typed.
  _TAI_PASTE_PREV=""
  _TAI_PASTE_QUIET=0
  _TAI_TYPED=0
  # And no directory snapshot is worth keeping either: the file answer reads
  # its snapshots for a second after taking them, and a line that starts now
  # should not be answered by what was on disk while the last one was edited.
  _TAI_SNAP_AT=()
  region_highlight=()
  _tai_menu_close
}

# Both hook helpers ship with zsh but are only autoloadable, so a minimal
# config (or `zsh -f`) leaves them unloaded. Without this the line-pre-redraw
# hook is never installed and the ghost text silently disappears.
autoload -Uz add-zle-hook-widget add-zsh-hook 2>/dev/null

# add-zle-hook-widget takes a function name, not a code string, so the
# line-init reset needs a real function of its own.
#
# The registration is *not* silenced, and that is the asymmetry with every other
# error path in this file. Suppressing output is right on the keystroke path,
# where a message is noise on every character; this runs once at shell startup,
# where one line is free — and the failure it hides is the one this very comment
# is about. If `line-pre-redraw` does not install, the ghost text silently
# disappears and the user is left with a plugin that appears to do nothing. That
# is worth one line at startup; it is not worth one per keystroke.
if (( $+functions[add-zle-hook-widget] )); then
  add-zle-hook-widget line-pre-redraw _tai_update || print -u2 \
    "tai: zsh could not install its redraw hook — suggestions will not appear"
  add-zle-hook-widget line-init _tai_line_init
else
  print -u2 "tai: zsh is missing add-zle-hook-widget — suggestions will not appear"
fi

_tai_preexec() {
  # The command only. Resolving the repo and branch costs four forks, and doing
  # it here put them on the critical path of every command the user runs — about
  # 5ms, measured. They are rank context for a background write, so they are
  # resolved in the background write instead, which also means the repo, branch
  # and cwd all describe the same moment. bash has always done it this way.
  _TAI_CMD="$1"
  # The directory the command was *run from*. A prompt-time $PWD comes after the
  # command, so for `cd src` the recorded origin was the destination — and the
  # path-liveness check judged the cd's argument against the place it had just
  # arrived at, condemning the most-used destination in the index.
  _TAI_CWD="$PWD"
}
_tai_precmd() {
  local code=$?
  # Learn something, and the shell you are sitting in gets it now. A rebuild
  # replaces the file atomically, so this sees either the old index or the new
  # one and never a half-written file.
  _tai_index_changed && _tai_load_index
  if [[ -n "$_TAI_CMD" ]]; then
    _TAI_LAST="$_TAI_CMD"
    # The unit cache pre-loads itself in the background after a command that
    # mentions systemctl has run — the next Tab on a systemctl line then
    # answers from an array instead of forking. Off the critical path, silent.
    if [[ "$_TAI_CMD" == *systemctl* ]]; then
      ( _tai_units_load 0 >/dev/null 2>&1; _tai_units_load 1 >/dev/null 2>&1 ) &
    fi
    # TAI_NO_AUTO_RECORD=1 keeps the in-memory last command for predictions
    # but skips the durable write. Read per prompt so it can be set per command.
    [[ "${TAI_NO_AUTO_RECORD:-0}" == "1" ]] || \
      ( ${(z)_TAI_BIN} record "$_TAI_CMD" --cwd "${_TAI_CWD:-$PWD}" --git "$(_tai_repo)" --branch "$(_tai_branch)" --exit $code >/dev/null 2>&1 & )
    _TAI_CMD=""
  fi
}
if (( $+functions[add-zsh-hook] )); then
  add-zsh-hook preexec _tai_preexec
  add-zsh-hook precmd _tai_precmd
fi
bindkey '^F' tai-accept
# TAI_NO_MENU=1 puts Tab back to "take the ghost text, or complete". Read once,
# here, because a key binding is not a place to read the environment on every
# press, and because the opt-out is about how the shell is set up rather than
# about one command.
if [[ "${TAI_NO_MENU:-0}" == "1" ]]; then
  bindkey '^I' tai-accept-or-complete
else
  bindkey '^I' tai-tab
  # The list, asked for directly. Two keys for one widget, and both are here for
  # the same reason: `Ctrl-Space` is what every IDE means by "show completions"
  # and is easy to reach, but terminals disagree about what to send for it —
  # xterm sends NUL, and a setup that has rebound it sends nothing at all. So the
  # same widget is also on `Ctrl-T`, which every terminal sends. Verified to
  # reach a widget in a real pty, both.
  #
  # Neither is a prefix key: a prefix pays KEYTIMEOUT on every press, measured at
  # 401ms for `^X` on a widget that otherwise costs 2ms.
  bindkey '^@' tai-menu
  bindkey '^T' tai-menu
  # Enter is CR on a terminal, and C-j is the same request from a keyboard that
  # sends LF. Both fill the word in; neither runs the line while a menu is open.
  bindkey '^M' tai-enter
  bindkey '^J' tai-enter
fi
# The right arrow is handled by replacing the forward-char widget, above: the
# key does not reach a plugin as bytes, so there is nothing here to bind.
#
# One word of the hint is on two keys. Alt-F is the chord this plugin documents;
# Ctrl-Right is the one the hand already makes for "next word", and
# `ESC [ 1 ; 5 C` is the xterm/kitty/konsole form of it — the *modified* arrows do
# not change with the cursor-key mode, only the bare ones do. Both verified to
# reach a widget in a real pty. With no hint on screen both fall through to
# readline's own `forward-word`, so the key keeps meaning "move on" when there is
# nothing to accept.
bindkey '\ef' tai-accept-word 2>/dev/null
bindkey '\e[1;5C' tai-accept-word 2>/dev/null
