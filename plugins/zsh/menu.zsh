# The Tab menu: the completions for the word under the cursor, below the line,
# with one of them selected. The line is not touched until Enter takes a
# choice.

# The selection is drawn by ZLE rather than by a character in the text, which
# means region_highlight, because that is the only thing that can colour what
# ZLE paints: escape bytes inside POSTDISPLAY are written out verbatim and
# reach the terminal as the literal characters "^[[7m".
#
# Three things about region_highlight decide whether a region appears at all,
# and all three were measured here rather than read off a manual — the first two
# are the reason this used to be drawn with a `▸` instead:
#
#   * One array element per region, holding "start end attrs" as one string.
#     Three elements — offsets and style as separate words — are read as three
#     regions of one character each, so nothing is painted at all. The ghost
#     text above has always had it right, which is why it is coloured and a
#     first attempt at colouring the menu was not.
#   * A region reaches the post-display, the menu's newline and all. The limit
#     is the one above, not "a widget wrote it" and not "it is several lines".
#   * Offsets count characters over the line and the post-display together, and
#     the end offset is exclusive. The post-display opens with the newline that
#     puts the menu on a line of its own, so the first row starts one character
#     after the end of the line.
typeset -ga _TAI_MENU
# Beside _TAI_MENU: 1 when that entry is a name off the disk or off `PATH` and has
# to be quoted before a shell will pass it as one word, 0 when it is the word of a
# line the user ran and is already the text they typed. _tai_menu_commit writes
# through _tai_quote when the flag says so — see the note on `outq` in
# _tai_menu_open for why the flag travels with the entry.
typeset -ga _TAI_MENU_Q
typeset -gi _TAI_MENU_IDX=0 _TAI_MENU_FROM=0 _TAI_MENU_TO=0 _TAI_MENU_LOOSE=0
# How deep one first-word key is read, and how many learned whole lines the
# first-word menu carries ahead of the installed names. Three per key is what
# keeps a two-line `go mod …` pair beside each other — one per key is the
# ghost's contract, and a menu that showed only the keys' heads would hide
# exactly the lines the web panel ranks beside the winner.
typeset -gi _TAI_MENU_FIRST_DEPTH=3
typeset -gi _TAI_MENU_FIRST_CAP=16
typeset -gi _TAI_MENU_COLS=1 _TAI_MENU_WIDTH=3
# Whether the selection in the menu is armed: 1 means a key in the list is
# highlighted and Enter commits it. 0 (the default) means the menu was opened on
# its own, so Enter still submits the line as typed until the user steps into the
# list with the down key.
typeset -gi _TAI_MENU_ARMED=0
# The part every entry shares, dropped from what the menu draws and kept in what it
# inserts. The line above the menu already shows it.
typeset -g _TAI_MENU_STEM=""
# The line the menu was built for. A keystroke that changes the line has left
# the menu describing a word that is no longer there.
typeset -g _TAI_MENU_LINE=""

# While the menu still describes the line on screen.
_tai_menu_live() {
  (( _TAI_MENU_IDX )) || return 1
  [[ "$BUFFER" == "$_TAI_MENU_LINE" ]] || return 1
  (( CURSOR == _TAI_MENU_TO ))
}

_tai_menu_close() {
  _TAI_MENU=(); _TAI_MENU_Q=()
  _TAI_MENU_IDX=0
  _TAI_MENU_ARMED=0
  _TAI_MENU_LOOSE=0
  _TAI_MENU_STEM=""
}

# What one entry looks like when it is drawn: the entry without the stem every
# entry shares. Sliced by length rather than with `${e#$stem}`, because a stem that
# came out of a filename can hold `*`, `?` or `[` and a pattern built from it would
# strip the wrong number of characters.
_tai_menu_cell() {
  local e=$1
  if [[ -n "$_TAI_MENU_STEM" ]]; then
    REPLY=$e[$(( ${#_TAI_MENU_STEM} + 1 )),-1]
  else
    REPLY=$e
  fi
  # An entry that is equal to the stem would draw one blank row: `.config/`
  # and `.config/opencode` share the stem `.config/`, and cutting it off the
  # first one leaves an empty line where the choice is. Paint the whole
  # entry for that row instead: a row nobody can read is not a row.
  [[ -z "$REPLY" ]] && REPLY=$e
  # A loose list is made of whole history lines, and a command line can be a
  # curl request — characters that have no business being in one cell of one
  # column. Without a cap the widest entry picks `_TAI_MENU_WIDTH` to the full
  # length of that line, the row laid out exceeds the terminal, and ZLE erases
  # the wrong cells on redraw. Paint cells never wider than it can take.
  local -i cap
  if (( _TAI_MENU_LOOSE )); then
    # A learned line is not picked out letter by letter from the list, so the
    # cell is not a width the terminal sets: enough characters to recognise
    # the command, and a longer cell is a screen of curl.
    cap=$_TAI_MENU_LOOSE_CELL
  else
    cap=$(( ${COLUMNS:-80} - 4 ))
    (( cap < 15 )) && cap=15
  fi
  if (( ${#REPLY} > cap )); then
    REPLY="${REPLY[1,$(( cap - 1 ))]}…"
  fi
}

# The longest prefix every entry starts with, cut back to a word boundary so what
# is left in front of a row is something a person would have typed. `git ch` is not
# a word: cutting there would leave `d foo`, `t bar` and `rl x` where there was a
# choice to make. Ending on `/` or a space is what makes the remainder readable, and
# `cd ` before `/run` and `/home` is the case the boundary is for.
#
# Computed on the whole menu, not on what the columns end up holding, so the stem
# does not change when the terminal is resized or the cap trims the list.
#
# And it is bounded by the word being completed, which is the half that keeps the
# stem honest: a stem may only cut what the line already shows. The report that
# found this: `cd tm` in a home directory where every learned destination sits
# under `tmp/` — every entry shared that prefix, the boundary cut handed back
# `tmp/`, and the menu drew `vllm`, `fun-game/`, `fabric` under a line reading
# `cd tm`. Nothing on the line says `tmp/`, so the rows read as completions of
# `tmvllm`, `tmfun-game` — names nobody typed and nothing offers. A stem is a
# reminder of text that is already on screen; anything else is information taken
# away.
_tai_menu_stem() {
  local word="$1"
  local -a ent
  local c first rest tail
  local -i k len ok at=0
  ent=( "${_TAI_MENU[@]}" )
  first=${ent[1]}
  len=${#first}
  # The longest prefix every entry starts with, found by shrinking until they all
  # still match. Bounded by the first entry's length and by the number of entries,
  # so it is a handful of pattern tests and not a loop over the screen.
  for (( k = len - 1; k >= 1; k-- )); do
    rest=$first[1,$k]
    ok=1
    for c in "${ent[@]}"; do
      [[ "$c" == "$rest"* ]] || { ok=0; break }
    done
    (( ok )) || continue
    at=$k
    break
  done
  (( at )) || { _TAI_MENU_STEM=""; return }
  rest=$first[1,$at]
  # Clamp to what was typed, before any boundary cut: a stem the word does not
  # start with is text the line does not show, and no boundary rule downstream
  # can put it back. `tmp/` for a menu opened on `tm` shrinks here to nothing;
  # `tmp/` for a menu opened on `tmp/` — the stem the rule below exists for —
  # passes untouched.
  while [[ -n "$rest" && "$word" != "$rest"* ]]; do
    rest="${rest%/}"; rest="${rest% }"
    local -i blen2=0
    if [[ "$rest" == */* ]]; then
      tail=${rest##*/}
      (( ( ${#rest} - ${#tail} ) > blen2 )) && blen2=$(( ${#rest} - ${#tail} ))
    fi
    if [[ "$rest" == *' ' ]]; then
      tail=${rest##* }
      (( ( ${#rest} - ${#tail} ) > blen2 )) && blen2=$(( ${#rest} - ${#tail} ))
    fi
    (( blen2 )) || { rest=""; break; }
    rest=$rest[1,$blen2]
  done
  [[ -n "$rest" ]] || { _TAI_MENU_STEM=""; return }
  # Already a whole word — the common case for a path, which ends in a slash.
  [[ "$rest" == */ || "$rest" == *' ' ]] && { _TAI_MENU_STEM=$rest; return }
  # Otherwise cut back to the last `/` or space in it, whichever is later, and
  # *include* that delimiter. Cutting to the last slash rather than the last
  # component matters: `media/x/y1` and `media/x/y2` share `media/x/y`, and a stem
  # of `media/x/y` would leave `1` and `2` as the whole of the choice.
  #
  # `${rest##*/}` drops the delimiter along with everything before it, so the
  # prefix that ends *on* it is what is left — `len - len(tail)`, with no `+1` for
  # a slash already removed. Adding one there is what turns `stemroot/` into
  # `stemroot/s` and every row into a name with a letter bitten off the front.
  local -i blen=0
  if [[ "$rest" == */* ]]; then
    tail=${rest##*/}
    blen=$(( ${#rest} - ${#tail} ))
  fi
  if [[ "$rest" == *' ' ]]; then
    tail=${rest##* }
    (( ${#rest} - ${#tail} > blen )) && blen=$(( ${#rest} - ${#tail} ))
  fi
  # No boundary anywhere in it: nothing is a stem, and cutting inside a word is
  # worse than repeating it — `cat foo` and `curl bar` share `c`, and drawing
  # `d foo` next to `rl bar` makes the menu a different question.
  (( blen )) || { _TAI_MENU_STEM=""; return }
  (( blen < ${#rest} )) && rest=$rest[1,$blen]
  _TAI_MENU_STEM=$rest
}

# The learned whole lines whose first word extends the typed word, best first,
# into _TAI_MENU_LEARNED — the menu's answer for a line that is still one word.
#
# Source 1 below looks the index up by the text *in front of* the word, and a
# first word has none: `g` looked up nothing and the menu answered with the
# installed command names alone, while the ghost — which asks the word itself
# through _tai_first_values — had `go mod vendor` and friends all along. The
# report: typing `g` hinted one line, Tab replaced it with a screen of
# binaries, and the first suggestion was `godot .` where the web panel
# said `go mod vendor`. The lines are whole lines, because for a first word a
# completion and the line it came from are the same span: committing
# `go mod vendor` onto `g` writes the line, which is exactly what the panel
# promises.
#
# One head per key is the ghost's contract and it is not enough here — the
# panel's second row was `go mod download`, the `go` key's *second* line — so
# each matching key lends its first _TAI_MENU_FIRST_DEPTH lines and the list is
# merged by score. The merge is one sort over at most _TAI_PREFIX_KEYS × depth
# zero-padded `score line` strings, a Tab-press cost, not a keystroke cost.
# No fork, the same rule as the rest of the menu.
_tai_menu_first_lines() {
  # `d` is declared here and nowhere else, and the reason is the report that
  # found this function: typing `g` and pressing Tab printed one `d=N` line on
  # the terminal per matching key before the menu drew. The declaration used
  # to sit inside the loop below, and zsh's `local` — `typeset` in local
  # clothing — applied to a parameter that already exists prints its value:
  # silent on the first key, which creates it, then one echo per later key
  # carrying whatever the previous key's inner loop had left in it. An
  # assignment keeps it quiet; a re-declaration does not, and `typeset_silent`
  # is the user's option to set, not this plugin's to assume. Any variable a
  # loop re-declares has to live in the function's one `local` line.
  local word="$1" k values line s d
  local -a merged
  _TAI_MENU_LEARNED=()
  # A glob character in the word is refused for the same reason
  # _tai_first_values refuses it: the match below would mean something other
  # than "begins with the word".
  [[ "$word" != *[\*\?\[]* ]] || return 1
  merged=()
  for k in "${(@k)_TAI_FIRST[(I)${word}*]}"; do
    values="${_TAI_FIRST[$k]}"
    # Peel, don't split: a key's whole list is split nowhere else on the
    # keystroke path, and `git` holds thousands of lines when the first three
    # are all a menu can show.
    for (( d = 0; d < _TAI_MENU_FIRST_DEPTH; d++ )); do
      line="${values%%$'\n'*}"
      [[ -n "$line" ]] || break
      s="${_TAI_SCORE[$line]:-0}"
      merged+=( "${(l:8::0:)s} $line" )
      [[ "$values" == *$'\n'* ]] || break
      values="${values#*$'\n'}"
    done
    (( ${#merged[@]} >= _TAI_PREFIX_KEYS * _TAI_MENU_FIRST_DEPTH )) && break
  done
  (( ${#merged[@]} )) || return 1
  # Zero-padded scores sort as themselves; (O) is the ranking. The strip is a
  # fixed nine characters — eight of pad and the space — because the score is
  # always the prefix.
  local -a sorted
  sorted=( "${(O)merged[@]}" )
  local m
  for m in "${(@)sorted[1,_TAI_MENU_FIRST_CAP]}"; do
    _TAI_MENU_LEARNED+=( "${m[10,-1]}" )
  done
  (( ${#_TAI_MENU_LEARNED[@]} ))
}
typeset -ga _TAI_MENU_LEARNED=()

# Build the menu for the word under the cursor, or fail when there is nothing to
# show. Failing is not an error: the caller hands the key to the shell's own
# completion, which is better at quoting and suffixes than this is.
_tai_menu_open() {
  # `out` holds a candidate and, beside it in `outq`, 1 when the candidate is a
  # name that has to be quoted before a shell will pass it as one word and 0 when
  # it is the word of a line the user actually ran. Three of the four sources
  # below are names — the file answer, `$commands`, and a glob of this directory —
  # and none of them is shell text yet; the fourth is a learned line's word, which
  # already is. So the flag is recorded where each entry is added, and
  # _tai_menu_commit writes the entry through _tai_quote when it says so. Guessing
  # at the moment of writing is how a `cat "my file.txt"` in the history becomes
  # `cat \"my`.
  local -a out outq entries dirs kept keptq
  local -A isdir dup
  local word before c rest p real
  local -i len=${#BUFFER} i skip width term limit added=0 cap=0

  # The width of the terminal, which the layout below is built to. Read once
  # here, because the command list is capped by it before the menu knows how
  # many columns it will end up with.
  term=${COLUMNS:-0}
  (( term > 0 )) || term=80

  # The word the cursor is on, and the line in front of it. The index is keyed
  # on whole lines, so a lookup needs both.
  if [[ "$BUFFER" == *' ' ]]; then
    _TAI_MENU_FROM=$(( len + 1 )); _TAI_MENU_TO=$len
    word=""; before=$BUFFER
  else
    word=${BUFFER##*' '}; before=${BUFFER%"$word"}
    _TAI_MENU_TO=$len; _TAI_MENU_FROM=$(( len - ${#word} + 1 ))
  fi

  # A directory argument changes what the word can be. After `cd` and `pushd`
  # a file is an error the user has to notice and retype, so every source
  # below is filtered for it: no file answer, no --help, a dirs-only glob,
  # and a learned destination has to be a directory *from here*. Read after
  # `before` exists — a directory argument is a fact about the line, and the
  # line has to be split before its head can be asked about.
  local -i dir_arg=0
  _tai_dirarg "$before" && dir_arg=1

  # 0a. Units, when the line is a systemctl unit argument: the cached unit list
  #    IS the vocabulary there, and files, command names and the directory
  #    listing around it would be noise. Loaded on first use — the one fork a
  #    Tab pays, once — and read from the array ever after.
  if _tai_units_menu; then
    if (( ${#_TAI_UNITS_MENU} )); then
      for c in "${_TAI_UNITS_MENU[@]}"; do
        [[ -n "$c" && "$c" != "$word" && "$c" == "$word"* ]] || continue
        _tai_clean "$c" || continue
        [[ -z "${dup[${c%/}]}" ]] || continue
        dup[${c%/}]=1
        _TAI_MENU+=( "$c" ); _TAI_MENU_Q+=( 0 )
      done
      if (( ${#_TAI_MENU} )); then
        _TAI_MENU_ARMED=1
        _tai_menu_layout "$word"
        return 0
      fi
    fi
  else

  # 0b. Files, when the line is known to end in one: the filesystem is the
  #    vocabulary for a path, so what is on disk comes before what tai learned.
  #    The same lookup the ghost text makes — including the ranking that decides
  #    whether a learned argument is still a file here — so the two cannot
  #    disagree about which file `chmod +x ` means. That lookup is _TAI_BEST from
  #    the last redraw, which described this line: a keystroke that changed the
  #    line has already run _tai_update and put it back in step.
  #    Never on a directory argument: the file answer answers with files, and
  #    after `cd` a file is the one thing the shell refuses.
  if (( ! dir_arg )) && _tai_file_answer "$BUFFER"; then
    out+=( "${_TAI_FILES[@]}" )
    outq+=( "${_TAI_FILES_Q[@]}" )
  fi

  # 1. What tai has learned, best first, mapped from "a line I have run" to
  #    "the word I am typing": a candidate is a whole command, and only the word
  #    the cursor is on is a completion.
  _tai_lines "$before"
  skip=$(( _TAI_MENU_FROM - 1 ))
  for c in "${_TAI_LINES[@]}"; do
    (( ${#c} > skip )) || continue
    rest="$_TAI_LINES_LEAD${c}"
    out+=( "${${rest[skip + 1, -1]}%% *}" ); outq+=( 0 )
  done

  # 1b. The first word has no `before` for the lookup above, and without this
  #    source the menu answered a one-word line with the installed command
  #    names alone — the learned lines the ghost hints were never offered.
  #    Here they come first, whole and ranked the way the engine ranks them;
  #    the installed names keep their place below, and committing a learned
  #    row onto a one-word line writes the whole line, because for a first
  #    word the completion and the line it came from are one span.
  local -i learned_whole=0
  if [[ -z "$before" ]] && _tai_menu_first_lines "$word"; then
    out+=( "${_TAI_MENU_LEARNED[@]}" )
    repeat ${#_TAI_MENU_LEARNED[@]}; do outq+=( 0 ); done
    learned_whole=1
  fi
  # An installed command the history has never seen. Worth offering only once
  # the command name is complete, which is the same rule _tai_query keeps so
  # `9router --p` is never answered with `--help`. When the command name *is* the
  # word being typed the completion is that name again, and the filter below
  # throws it away like any other candidate that says nothing new.
  if (( ! dir_arg )) && (( ! ${#_TAI_LINES} )) && [[ "$before" == *' ' ]]; then
    c="${before%% *}"
    _tai_installed "$c" && { out+=( --help ); outq+=( 0 ) }
  fi

  # 2. Commands, but only where the word *is* the command and is not empty:
  #    anywhere else a command name is not what the argument is, and on an empty
  #    line zsh's own listing is the better answer — a menu can hold ten entries
  #    out of four thousand, and which ten it holds would be arbitrary. A word at
  #    the head of a line is usually going to be run, so these come before the
  #    directory below: `ta` is more often `tail` than the `tabdir` next to you.
  #
  #    A line that ends in a file is skipped outright: `cat git` is asking for a
  #    file, and two hundred command names starting with `git` is a directory
  #    listing with extra steps.
  #
  #    A wrapper is not a word: `sudo git ` is a menu about `git`, so the line to
  #    test is the one behind the wrapper. Without that, wrapping a command used
  #    to be enough to lose its command completions.
  #    `$commands` is a hash of every name a PATH directory offers, and both the
  #    pattern match and the sort are done by the shell.
  #
  #    "Offers" is not "can be run". A file without the execute bit is in that
  #    hash, and a directory is in it too, and `whence` calls neither a command —
  #    so a list built from it without asking is a list of names that would fail
  #    with "permission denied" when Enter ran them. The value half of the entry
  #    is the full path, so two tests on it answer the only question there is.
  # `${#_TAI_LINES_LEAD}` and not `${_TAI_LINES_LEAD}`: the parameter is empty on
  # every unwrapped line, and `(( && x > 1 ))` with an empty expansion is
  # "bad math expression: operand expected" — an error on stderr from inside a
  # widget, on every menu, for every user. A length is 0 rather than nothing.
  local -i head_from=$_TAI_MENU_FROM file_arg=${#_TAI_FILES}
  if (( ${#_TAI_LINES_LEAD} && _TAI_MENU_FROM > 1 )); then
    head_from=$(( _TAI_MENU_FROM - ${#_TAI_LINES_LEAD} ))
  fi
  # Both globs below put the typed word into a pattern, so both need the same
  # answer as the ghost text gets. Skipping them costs the menu its PATH and
  # directory entries for a word the shell cannot compile anyway; what it does
  # not cost is an error message on the terminal on every press.
  local -i globable=0
  _tai_globable "$word" && globable=1
  if (( globable && head_from == 1 && ${#word} && ! file_arg )); then
    entries=( ${(o)${(Mk)commands:#${word}*}} )
    # Stop at the most entries the menu could ever hold, which with the narrowest
    # cell a one-character name gives — three columns of name, two of gutter — is
    # a column every five. Reading past that cannot add an entry that fits, and
    # testing every name on PATH to display ten of them is a Tab press measured in
    # tens of milliseconds.
    cap=$(( (term - 1) * _TAI_MENU_ROWS / 5 ))
    for c in "${entries[@]}"; do
      (( added >= cap )) && break
      p=${commands[$c]}
      [[ -x "$p" && ! -d "$p" ]] || continue
      out+=( "$c" ); outq+=( 1 )
      added=$(( added + 1 ))
    done
  fi

  # 3. Files and directories, in the order the shell's own glob returns them —
  #    one builtin expansion, no process, and no sort because a glob is already
  #    sorted. Directories are marked, so the list reads as paths. `globable` is
  #    the same question as above: both of these are patterns built from the
  #    typed word, so neither is safe to compile when it will not parse.
  #    On a directory argument the glob answers with directories only — the
  #    file half of this listing is what put `AGENTS.md` after `cd `. The five
  #    relative moves skip the glob entirely: `cd .` listing the dot-directory
  #    cache is a menu about nothing, and the learned word completes those
  #    moves as it always has.
  if (( globable )); then
    if (( dir_arg )) ; then
      if [[ "$word" != "." && "$word" != ".." && "$word" != "-" &&
            "$word" != "./" && "$word" != "../" ]]; then
        # Unquoted, exactly like the assignment below: quoted, the pattern
        # never compiles and the loop iterates once over the literal text.
        # And the slash is appended here, because zsh's `(/)` qualifier
        # SELECTS directories without marking them — the returned names are
        # bare, and a cd menu reads `stemroot` where every other shell
        # writes `stemroot/`.
        for c in ${~word}*(N/); do out+=( "$c/" ); outq+=( 1 ); done
      fi
    else
      entries=( ${~word}*(N) )
      dirs=( ${~word}*(N/) )
      for c in "${dirs[@]}"; do isdir[${c%/}]=1; done
      for c in "${entries[@]}"; do
        if (( ${+isdir[$c]} )); then out+=( "$c/" ); else out+=( "$c" ); fi
        outq+=( 1 )
      done
    fi
  fi
  fi

  # Keep what extends the line, once each, in the order above.
  #
  # A candidate with a raw control byte in it is not a candidate at all:
  # it would be written into POSTDISPLAY, and there it is an escape
  # sequence, not text. The learned lists already filter these away, but
  # the filesystem answers do not — a directory and a pasted command dump
  # the bytes it carries straight through the glob — so the menu checks
  # before it speaks.
  #
  # "Extends" is the whole test, and "extends" means adds something: a candidate
  # equal to the word is a copy of what is typed, and a candidate that does not
  # start with it is a different word entirely. Every `cd …` line in the history
  # offers its next word, and `cd ..` offers `..` while `cd p` is answered by
  # `git status` offering `status` — neither extends the word being typed, so
  # neither is a completion. The hint has always made this test, inside
  # _tai_best; the menu has to make it too, or the two disagree about what the
  # word can be.
  #
  # The key ignores a trailing slash, because a learned `cd tzz_dir` and the
  # directory `tzz_dir` are one completion said twice: a menu holding both offers
  # the user a choice between a name and the same name, and whichever wins,
  # typing on gives the same result. The learned form wins, so the menu says what
  # the hint said.
  _TAI_MENU=(); _TAI_MENU_Q=()
  for (( i = 1; i <= ${#out}; i++ )); do
    c="$out[i]"
    [[ -n "$c" && "$c" != "$word" && "$c" == "$word"* ]] || continue
    _tai_clean "$c" || continue
    [[ -z "${dup[${c%/}]}" ]] || continue
    if (( dir_arg )) && [[ "$c" != "-" ]]; then
      # A learned destination was judged for liveness where it was recorded;
      # from here it may be gone, and bare `vllm` two directories away from
      # vllm is the reported case. One builtin stat per surviving candidate,
      # and `-` is the shell's own slot, which works everywhere.
      real="$c"
      [[ "$real" == "~"* ]] && real="${real/#\~/$HOME}"
      [[ -d "$real" ]] || continue
    fi
    dup[${c%/}]=1
    _TAI_MENU+=( "$c" ); _TAI_MENU_Q+=( "${outq[i]}" )
  done
  (( ${#_TAI_MENU} )) || return 1

  _TAI_MENU_ARMED=1
  # A menu holding whole learned lines lays out with no stem, the loose list's
  # rule: the line above shows only the typed `g`, and a stem cut from the
  # rows' common prefix (`go `) would leave rows reading `mod vendor` under a
  # line that never said `go`. An empty word bounds the stem to nothing, the
  # same way the loose open passes one.
  if (( learned_whole )); then
    _tai_menu_layout ""
  else
    _tai_menu_layout "$word"
  fi
}

# Layout and cap: shared by the normal and the loose menus. `$1` is the word the
# menu was opened for, which bounds the stem — a stem may only cut what the line
# already shows.
_tai_menu_layout() {
  _TAI_MENU_STEM=""
  # No stem on a loose list: its entries are whole command lines, and the stem
  # between them is the very command they share — the typed `gst` was answered
  # by two lines beginning `git `, and cutting that off left the rows reading
  # `pull --rebase    status`, which says neither what they are nor what they
  # are for. On a per-word menu the stem is what the line above already shows;
  # here the entries replace the line, so each one has to read whole.
  if (( ${#_TAI_MENU} > 1 )) && (( ! _TAI_MENU_LOOSE )); then
    _tai_menu_stem "$1"
  fi

  # Lay the list out once, at the width the terminal has now, and keep only the
  # entries that fit: the selection has to stay inside the menu it moves through.
  # Two columns of gutter, two of gap, one the terminal would wrap on.
  local width=2 c
  for c in "${_TAI_MENU[@]}"; do
    _tai_menu_cell "$c"; (( ${#REPLY} > width )) && width=${#REPLY}
  done
  _TAI_MENU_WIDTH=$(( width + 2 ))
  local term=${COLUMNS:-0}
  (( term > 0 )) || term=80
  _TAI_MENU_COLS=$(( (term - 1) / (_TAI_MENU_WIDTH + 2) ))
  (( _TAI_MENU_COLS > 0 )) || _TAI_MENU_COLS=1
  # The loose list is a glance at whole learned lines, not a choice among
  # words, so it reads down at most three rows rather than spread across the
  # terminal: one line per entry, and _TAI_MENU_LOOSE_ROWS of them.
  if (( _TAI_MENU_LOOSE )); then
    _TAI_MENU_COLS=1
  fi
  local -i rows=$_TAI_MENU_ROWS
  (( _TAI_MENU_LOOSE )) && rows=$_TAI_MENU_LOOSE_ROWS
  local limit=$(( _TAI_MENU_COLS * rows ))
  if (( ${#_TAI_MENU} > limit )); then
    kept=(); keptq=()
    for (( i = 1; i <= limit; i++ )); do kept+=( "${_TAI_MENU[i]}" ); keptq+=( "${_TAI_MENU_Q[i]}" ); done
    _TAI_MENU=( "${kept[@]}" ); _TAI_MENU_Q=( "${keptq[@]}" )
  fi
  _TAI_MENU_IDX=1
  _TAI_MENU_LINE=$BUFFER
  return 0
}

# The fallback when no candidate extends the line: the remembered commands
# whose text holds every word typed, verbatim as a whole line. Where the normal
# menu is per-word, a pick here replaces the whole typed line, so the from/to
# range spans $BUFFER. First open is disarmed: the list appears for reading,
# and Down marks an entry before Enter takes it.
_tai_menu_open_loose() {
  (( ${#_TAI_LOOSE_LINES} )) || return 1
  _TAI_MENU=( "${_TAI_LOOSE_LINES[@]}" )
  local c _
  _TAI_MENU_Q=()
  for c in "${_TAI_MENU[@]}"; do _TAI_MENU_Q+=( 0 ); done
  _TAI_MENU_FROM=1
  _TAI_MENU_TO=${#BUFFER}
  _TAI_MENU_ARMED=0
  _TAI_MENU_LOOSE=1
  _tai_menu_layout ""
  _TAI_MENU_ARMED=0
}

# Draw the menu below the line: entries in columns, the selected one in reverse
# video, a directory's name in blue with its slash left in the terminal's own
# colour, which is what makes a row read as paths rather than as a word list.
#
# Every cell's span is measured while the row is laid out rather than counted
# afterwards, because the offsets region_highlight wants are characters across
# the line and the whole post-display, and a column's start is only known once
# the rows before it have been built.
_tai_menu_paint() {
  POSTDISPLAY=""
  region_highlight=()
  (( _TAI_MENU_IDX )) || return
  local -a rows
  local -a lo hi                     # lo[i], hi[i]: entry i's span, end exclusive
  local row="" cell name
  local -i n=$#_TAI_MENU i col=0
  # The post-display opens with a newline, so the first row starts one character
  # past the end of the line, and every later row one past the end of the one
  # before it — hence the +1 on the newline that joins them.
  local -i row_at=$(( ${#BUFFER} + 1 ))
  for (( i = 1; i <= n; i++ )); do
    # The gap between two cells is drawn before the cell, so it is joined to the
    # row first: measuring the cell against a row that does not have the gap yet
    # puts every cell after the first one two characters to the left of where it
    # is drawn.
    (( col )) && row+="  "
    _tai_menu_cell "${_TAI_MENU[i]}"; name=$REPLY
    cell="  $name"
    lo[i]=$(( row_at + ${#row} ))
    # Pad every cell but the last in its row: a padded cell at the end of a row
    # is a row that ends in spaces, and ZLE repaints the whole postdisplay on
    # every keystroke, trailing blanks and all.
    (( col == _TAI_MENU_COLS - 1 || i == n )) || printf -v cell "%-${_TAI_MENU_WIDTH}s" "$cell"
    hi[i]=$(( lo[i] + ${#cell} ))
    row+="$cell"
    col=$(( (col + 1) % _TAI_MENU_COLS ))
    (( col )) || { rows+=( "$row" ); row_at=$(( row_at + ${#row} + 1 )); row=""; }
  done
  [[ -n "$row" ]] && rows+=( "$row" )
  # On a line of its own, not glued to the end of the input line. A row laid out
  # to the terminal width and started after the prompt runs off the right edge,
  # the terminal wraps it, and ZLE — which believes the whole post-display is one
  # line — then erases in the wrong place and leaves the pieces of the previous
  # menu on screen. Starting at column 0 gives every row the full width, and no
  # prompt can be long enough to crowd it out.
  POSTDISPLAY=$'\n'"${(F)rows}"
  # Ours now, so closing it may take it away again — and so a foreign hint that
  # was on screen is not the one that gets erased on the way out.
  _TAI_DREW=1
  # A directory is the one entry the menu marks with a slash, so the slash is the
  # test: colour the name and stop before the slash, which is then left in the
  # terminal's own colour — a blue name with a plain slash reads as a path, and
  # a wholly blue cell reads as a word.
  for (( i = 1; i <= n; i++ )); do
    [[ "${_TAI_MENU[i]}" == */ ]] || continue
    # Not the armed selection, which is drawn as one block. Two regions over the
    # same characters do not replace each other, they combine: a selected
    # directory came out bold blue *and* reversed, which is a colour nothing has
    # a name for.
    (( _TAI_MENU_ARMED && i == _TAI_MENU_IDX )) && continue
    # A cell is drawn with two columns of gutter in front of the name, which is
    # what the selected entry's block covers and what the name's colour skips.
    _tai_menu_cell "${_TAI_MENU[i]}"; name=$REPLY
    region_highlight+=("$(( lo[i] + 2 )) $(( lo[i] + 1 + ${#name} )) $_TAI_DIR_STYLE")
  done
  # The selection cell: only once the user has armed it with the down key. A
  # menu that appears on its own and shows a highlight makes the selection look
  # already taken, and pressing Down would move past it without ever showing it.
  if (( _TAI_MENU_ARMED )); then
    region_highlight+=("$lo[_TAI_MENU_IDX] $hi[_TAI_MENU_IDX] $_TAI_MENU_STYLE")
  fi
}

# Put the selected entry where the word was, and close the menu. Nothing runs: the
# line is now `cd Movies/` with the cursor after it, which is the point — a
# completion is not a decision to execute.
_tai_menu_commit() {
  local entry="${_TAI_MENU[_TAI_MENU_IDX]}"
  # The whole entry, quoted when it is a name — and the entry, not the row that
  # was drawn for it, which is the rule that keeps a shared stem or a directory
  # slash from truncating what gets written.
  (( ${_TAI_MENU_Q[_TAI_MENU_IDX]} )) && { _tai_quote "$entry"; entry="$_TAI_QUOTED" }
  BUFFER="${BUFFER[1,$(( _TAI_MENU_FROM - 1 ))]}$entry${BUFFER[_TAI_MENU_TO + 1,-1]}"
  CURSOR=${#BUFFER}
  _tai_menu_close
}

