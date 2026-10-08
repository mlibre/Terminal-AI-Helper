# Path arguments, answered by the filesystem rather than by the history: what
# you are about to use is by definition not in the history yet.

# A global rather than a return value for the same reason as _TAI_LINES: this runs
# on every keystroke and cannot afford a subshell.
_tai_file_answer() {
  local line="$1" before word key last real
  local -a kept
  _TAI_FILES=(); _TAI_FILES_Q=()
  # Only once the word being typed is not the command name. `cat` on its own is a
  # command tai is completing, not a path.
  [[ "$line" == *' '* ]] || return 1
  if [[ "$line" == *' ' ]]; then
    before="${line% *}"; word=""; _TAI_FILE_JOIN="$line"
  else
    word="${line##* }"; before="${line%"$word"}"; _TAI_FILE_JOIN="$before"
  fi
  # The key the index marks a file argument with is the line without its last
  # word, which is exactly `before` once the cursor's word is removed. Trailing
  # spaces are trimmed because a word boundary is one space however many there
  # are.
  #
  # Trimmed by expansion, not by a loop. A loop over `while [[ "$key" == *" " ]]`
  # never terminates: that pattern matches a space *anywhere*, so `chmod +x` —
  # which has one in the middle — entered it and `${key% }` shortened nothing.
  # The inner expansion peels the trailing run of spaces in one step, and does
  # nothing at all when there is none.
  key="${before%%"${before##*[![:space:]]}"}"
  # A key of nothing but spaces is no lookup at all. zsh returns an empty string
  # for that subscript rather than erroring, so this is one line of agreement
  # with the bash plugin rather than a guard zsh needs.
  [[ -n "$key" ]] || return 1
  # A wrapper runs the command behind it, so it is transparent to the file too:
  # `sudo cat ` is a path argument exactly as `cat ` is.
  if (( ${_TAI_WRAPPERS[(I)${before%% *}]} )); then key="${key#* }"; fi
  (( ${_TAI_FILE[$key]:-0} )) || return 1
  _tai_fresh_files "$word"
  # A completion has to extend the word being typed, and a file found through a
  # glob does not always: `chmod +x freeb` matches `~/Downloads/freebuff…` in
  # *Downloads*, but that name does not start with the word, so accepting it
  # would replace three typed letters with a path from somewhere else. The menu
  # filters on this too, further down; this is the half that decides the hint.
  kept=()
  for last in "${_TAI_FRESH[@]}"; do
    [[ "$last" == "$word"* ]] && kept+=( "$last" )
  done
  _TAI_FRESH=( "${kept[@]}" )
  # A learned argument that is still a file here outranks the newest one: the
  # user asked for that file before and it exists, which is what keeps this from
  # displacing `cat ~/notes/todo.txt` with whatever was touched last.
  if [[ -n "$_TAI_BEST" ]]; then
    last="${_TAI_BEST##* }"
    real="${last/#\~/$HOME}"
    if [[ "$last" == "$word"* && -e "$real" ]]; then
      _TAI_FILES=( "$last" ); _TAI_FILES_Q=( 0 )
      _TAI_FRESH=( "${(@)_TAI_FRESH#$last}" )
    fi
  fi
  _TAI_FILES+=( "${_TAI_FRESH[@]}" )
  # Everything after the learned word came off the disk and needs quoting before
  # a shell will pass it as one word — see _tai_quote. The learned word does not:
  # it is the text the user ran, already quoted if they quoted it.
  for last in "${_TAI_FRESH[@]}"; do _TAI_FILES_Q+=( 1 ); done
  return 0
}

# The files `word` could be, newest first, into _TAI_FRESH.
#
# Where they are looked for is two kinds of place and no others: the directory
# you are standing in, and the directories a download lands in. Reading the
# subdirectories too was tried and removed — it made `chmod +x ` answer
# `chmod +x tests/__pycache__/test_smoke.cpython-314.pyc` on a machine where a test had
# just run, which is a file nobody chmods and the newest thing on disk besides
# the AppImage just downloaded. `~/Downloads` is a root anyway, so the case that
# matters did not need the descent.
#
# The word is used as a glob, which is how completion has always matched: empty
# matches every file in a root, and `freeb` matches the one that means something.
# That also means a word can be a pattern the shell refuses — `foo(bar` is an
# invalid qualifier, `foo[` an unclosed class — and an error printed on every
# keystroke is worse than no answer at all, so a word with any of those in it is
# not looked up. The shell's own completion will say what it can read.
# Whether a word can be handed to the glob at all.
#
# A word is a glob here, which is how completion has always matched, and zsh
# refuses to compile some of them: `foo[bar` is an unclosed class and `foo(bar`
# an invalid qualifier, and the refusal is a `bad pattern` line on the terminal
# from inside a widget. `foo[bar` is a filename somebody is halfway through
# typing, so this has to be a quiet no-op rather than an error.
#
# It was a guard in `_tai_fresh_files` and *not* in `_tai_menu_open`, which
# globs the same word on the same keystroke — so the ghost text stayed silent
# and Tab printed `bad pattern: foo[bar*(N)`. One question, so one function, and
# both callers ask it.
_tai_globable() {
  [[ "$1" != *[\(\)\[\]\^\<\>\|]* ]]
}

# A name from the filesystem, written so a shell passes it as one word.
#
# `cat ` answered with `My Document.pdf` and the arrow wrote it into the line
# bare, so what ran was `cat My Document.pdf`: two arguments, one of them a file
# that does not exist. The name is right and the line is wrong, which is worse
# than no answer because it looks like it worked. A name with a `'`, a `$` or a
# `\` is the same bug wearing a different hat — an unbalanced quote swallows the
# rest of the line.
#
# Backslash-escaping, because it is what zsh's own completion writes and because
# it is legible in the line: the user can see what they are getting and delete a
# character without having to read a quoting rule. The safe set is deliberately
# narrow. `~` is the exception at the front of the text, because `~/Downloads/…`
# is written that way on purpose and escaping it would stop the shell expanding
# it.
#
# Only names need this. A candidate that is the word of a line the user ran is
# already the text they typed, and quoting it would turn `cat "my file.txt"` into
# `cat \"my` — so the caller says which it has, rather than this guessing.
_tai_quote() {
  local s="$1" out="" ch
  local -i i n first=1
  # `$#s` in the same `local` as `s` is expanded before `s` is assigned: zsh
  # expands every word of a `local` before it performs any of them, so `n` came
  # out 0 and this returned nothing at all — silently.
  n=${#s}
  for (( i = 1; i <= n; i++ )); do
    ch="$s[i]"
    if [[ "$ch" == [A-Za-z0-9_./:=@%+,^-] ]] || { (( first )) && [[ "$ch" == '~' ]]; }; then
      out+="$ch"
    else
      out+="\\$ch"
    fi
    first=0
  done
  _TAI_QUOTED="$out"
}
# The answer, in a global rather than on stdout, for the same reason every other
# helper here answers in a global: the caller is a widget, and `$( )` would be a
# process on the keypress path for the sake of one string.
typeset -g _TAI_QUOTED=""

_tai_fresh_files() {
  local word="$1" dir as c real glob
  local -i i n=0
  _TAI_FRESH=(); _TAI_FRESH_MTIME=()
  _tai_globable "$word" || return 0
  # Three cases, because a word with a `/` in it is naming where to look rather
  # than what to look for, and only the third one is a search across roots:
  #
  #   * `~` is expanded first, so `~/Down` is something the shell can glob at all;
  #   * an absolute word, or one that already contains a slash, is globbed once,
  #     where it points. `~/Downloads/freeb` is not `freeb` in every root — it is
  #     that one path, and spreading it across eight roots matches nothing. The
  #     answer is the word as it was typed plus whatever the glob added, so the
  #     `~` the user typed is the `~` they get back;
  #   * a bare name is the search, and that is the case this feature is for.
  glob="${word/#\~/$HOME}"
  if [[ "$glob" == /* || "$glob" == */* ]]; then
    local -i n=0
    # A word naming a directory lists what is in it. Without this, `~` globs as
    # `~*` — every *sibling* of the home directory — which matches nothing, so
    # `cat ~<Tab>` offered nothing at all in the one place a person is certain
    # there are files. The trailing slash is added, not the name, so the answer
    # is `~/Downloads/…` and stays inside what was typed.
    [[ -d "$glob" ]] && glob="${glob%/}/"
    # `(om)` orders the glob by mtime, newest first, so `_TAI_FILE_PER_ROOT`
    # counts the newest that many rather than the alphabetically first that
    # many. A plain glob is sorted by name, so a downloads folder full of
    # `aaa*.mp4` kept eight of those and dropped the AppImage that had just
    # arrived — which is the only thing this function exists to offer.
    for c in ${~glob}*(omN); do
      [[ -f "$c" ]] || continue
      _tai_keep_fresh "$word${c#$glob}" "$c"
      (( ++n >= _TAI_FILE_PER_ROOT )) && break
    done
    return 0
  fi
  _tai_file_roots
  for (( i = 1; i <= ${#_TAI_ROOT_DIR}; i++ )); do
    _tai_keep_root "${_TAI_ROOT_DIR[i]}" "${_TAI_ROOT_AS[i]}" "$glob"
  done
  return 0
}

# One root's files, newest first, into _TAI_FRESH. `$2` is how this root is
# written in the line, and each root reads at most _TAI_FILE_PER_ROOT: a
# directory of eight files already ranked by mtime must not use up the whole
# answer, or `chmod +x ` in a busy project would never reach ~/Downloads.
_tai_keep_root() {
  local dir="$1" as="$2" glob="$3" c
  local -i n=0
  # `(om)` — ordered by mtime, newest first — so the cap takes the newest
  # _TAI_FILE_PER_ROOT rather than the alphabetically first that many. zsh does
  # the sort inside the glob with no process, so this is free, and it is the
  # whole point of the function: `chmod +x ` has to name the file that just
  # landed, and a name-ordered glob in a folder of `aaa*` files never would.
  if [[ "$dir" == "." ]]; then
    for c in ${~glob}*(omN); do
      [[ -f "$c" ]] || continue
      _tai_keep_fresh "$as$c" "$c"
      (( ++n >= _TAI_FILE_PER_ROOT )) && break
    done
  else
    for c in "$dir"/${~glob}*(omN); do
      [[ -f "$c" ]] || continue
      _tai_keep_fresh "$as${c#$dir/}" "$c"
      (( ++n >= _TAI_FILE_PER_ROOT )) && break
    done
  fi
}

# Where files are looked for, most relevant first: `(directory, how to write it)`.
# A root is a bare name when it is where you already are, a relative path when it
# is next door, and `~/…` when it is under your home directory — the form that
# still works in a directory where the same file is not next to you.
_tai_file_roots() {
  local -a dirs prefixes
  local d seen=""
  dirs=( "." ); prefixes=( "" )
  # `[[ ... && ... || ... ]]` groups as `(a && b) || c`, so a directory that does
  # not exist fails the first pair and is let through by the second — which is
  # how `~/Download` came to be read as a root on a machine that has only
  # `~/Downloads`. Each test gets its own line, and each one is the whole
  # question: is it there, is it somewhere else, have I read it already.
  seen="|.|"
  for d in ${(s.:.)${TAI_FILE_ROOTS:+${TAI_FILE_ROOTS}:}}\
           "${XDG_DOWNLOAD_DIR:+$XDG_DOWNLOAD_DIR}" "$HOME/Downloads" "$HOME/Download" "$HOME/Desktop"; do
    [[ -n "$d" ]] || continue
    d="${d/#\~/$HOME}"
    [[ "$d" == /* ]] || d="$PWD/$d"
    d="${d%/}"
    [[ "$d" == "$PWD" ]] && continue
    [[ -d "$d" ]] || continue
    [[ "$seen" == *"|$d|"* ]] && continue
    seen+="$d|"
    if [[ "$d" == "$HOME"/* ]]; then
      dirs+=( "$d" ); prefixes+=( "~/${d#$HOME/}/" )
    else
      dirs+=( "$d" ); prefixes+=( "${d}/" )
    fi
    (( ${#dirs} >= _TAI_FILE_ROOTS_MAX )) && break
  done
  _TAI_ROOT_DIR=( "${dirs[@]}" ); _TAI_ROOT_AS=( "${prefixes[@]}" )
}

# The paths behind _TAI_FRESH, parallel to it. Kept apart because the answer is
# written the way a person would type it — `~/Downloads/x`, `Download/x`, `x` —
# and `-nt` needs a path the shell can resolve, which `~/…` only is after the
# tilde is expanded.
typeset -ga _TAI_FRESH_MTIME

# Keep one file among the newest few, in order.
#
# Bounded by _TAI_FILE_TOP, so the cost per file is a fixed number of `-nt` tests
# rather than a sort of everything found: this is on the keystroke path, and
# `test -nt` is a builtin, so the ranking costs no process at all (measured at
# ~0.2ms for eight files across eight roots).
_tai_keep_fresh() {
  local shown="$1" real="$2" m i
  local -a names paths
  # A name with a raw control byte in it is unpaintable for the same reason
  # an unrecording stored command is: what lands in POSTDISPLAY is not text.
  _tai_clean "$shown" || return 0
  # Insert before the first entry older than this file, so the list stays newest
  # first. The comparison is `&& break` and not `|| break`: a loop that steps
  # *past* the files that are newer and appends after them produces a list
  # ordered oldest-first, which still looks plausible and always names the file
  # you downloaded last somewhere in the middle instead of first.
  for (( m = 1; m <= ${#_TAI_FRESH}; m++ )); do
    [[ "$real" -nt "$_TAI_FRESH_MTIME[m]" ]] && break
  done
  (( m > _TAI_FILE_TOP )) && return 0
  # Rebuilt by index rather than sliced. `${A[@]:0:m-1}` is a *bash* subscript
  # form: zsh reads the `m` as a modifier and fails with "unrecognized modifier",
  # which on the keystroke path is a message on the terminal every keystroke.
  for (( i = 1; i < m; i++ )); do
    names+=( "${_TAI_FRESH[i]}" ); paths+=( "${_TAI_FRESH_MTIME[i]}" )
  done
  names+=( "$shown" ); paths+=( "$real" )
  for (( i = m; i <= ${#_TAI_FRESH}; i++ )); do
    names+=( "${_TAI_FRESH[i]}" ); paths+=( "${_TAI_FRESH_MTIME[i]}" )
  done
  _TAI_FRESH=( "${names[@]}" ); _TAI_FRESH_MTIME=( "${paths[@]}" )
}

