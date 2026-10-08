# Path arguments, answered by the filesystem rather than by the history: what
# you are about to use is by definition not in the history yet.

# A path argument is answered by the filesystem, not by the history. The reported
# case: `chmod +x ` on a history holding only `chmod +x script`, where `script` is
# not a file here and the file just downloaded is the one wanted. The same limits
# and the same roots as plugins/tai.zsh and tai/fresh.py, because this is one
# rule in three places and they have to read the same.
_TAI_FILE_PER_ROOT=8
_TAI_FILE_ROOTS_MAX=8
_TAI_FILE_TOP=8

# A name from the filesystem, written so a shell passes it as one word.
#
# `cat ` answered with `My Document.pdf` and Ctrl-F wrote it into the line bare,
# so what ran was `cat My Document.pdf`: two arguments, one of them a file that
# does not exist. Readline quotes its own COMPREPLY, so Tab was already right
# and the accept key was wrong — the same line, two answers, and the wrong one
# was the one that skipped the shell's machinery.
#
# Backslash-escaping, because it is what readline writes for the same name and
# because it is legible: the user sees what they are getting. `~/` at the front
# is left alone — that is how a download root is written on purpose, and escaping
# it would stop the shell expanding it — while a `~` inside a name is escaped,
# since the shell would otherwise read `~main` as somebody's home directory.
#
# Only names need this. A candidate that is the word of a line the user ran is
# already the text they typed, so quoting it would turn `cat "my file.txt"` into
# `cat \"my`.
_tai_quote() {
  local s="$1" lead="" out=""
  if [[ "$s" == "~/"* ]]; then lead="~/"; s="${s#\~/}"; fi
  out="${s//[![:alnum:]_.\/:=@%+,^-]/\\&}"
  # Answered in a global, not on stdout: this runs on the keystroke path, where
  # a function's stdout is the terminal and a `$( )` around the call was a fork
  # per press.
  _TAI_QUOTED="$lead$out"
}

# The files `word` could be, newest first, one per line.
#
# `stat -c %Y` is the only way to rank by mtime in bash, so it is one process per
# root rather than per keystroke — bash has no ghost text, so this runs on a
# completion press, which the shell is already spending a process on. zsh ranks
# the same files with builtin `test -nt` and no process at all.
# Keep one file among the newest few, in order.
#
# Insert before the first entry older than this file, so the list stays newest
# first. `&& break` and not `|| break`: stepping *past* the files that are newer
# and appending after them gives a list ordered oldest-first, which still looks
# plausible and always buries the file you downloaded last somewhere in the
# middle.
#
# `$3` is the mtime as a whole number of seconds, because bash cannot compare
# mtimes without a process: `stat -c %Y` is the one fork, and it is per file
# rather than per keystroke — bash has no ghost text, so this only runs when
# completion is asked for. zsh ranks the same files with builtin `test -nt` and
# forks nothing.
_tai_keep_fresh() {
  local shown="$1" real="$2" mtime
  mtime="$(stat -c %Y -- "$real" 2>/dev/null)" || mtime=0
  [[ "$mtime" == "" ]] && mtime=0
  # A name with a control byte in it is unpaintable text for the same reason
  # an unrecordable stored command is: what is written to the terminal is not
  # text.
  [[ "$shown" =~ [[:cntrl:]] ]] && return 0
  local -i m
  for (( m = 0; m < ${#_TAI_FRESH[@]}; m++ )); do
    (( mtime > _TAI_FRESH_MTIME[m] )) && break
  done
  (( m >= _TAI_FILE_TOP )) && return 0
  _TAI_FRESH=( "${_TAI_FRESH[@]:0:m}" "$shown" "${_TAI_FRESH[@]:m}" )
  _TAI_FRESH_MTIME=( "${_TAI_FRESH_MTIME[@]:0:m}" "$mtime" "${_TAI_FRESH_MTIME[@]:m}" )
}

_tai_fresh_files() {
  local word="$1" dir as c real line skip glob
  local -a dirs prefixes
  local -i i n=0
  _TAI_FRESH=(); _TAI_FRESH_MTIME=()
  # Three cases, because a word with a `/` in it names where to look rather than
  # what to look for, and only the third is a search across roots:
  #
  #   * `~` is expanded first, so `~/Down` is something the shell can glob at all;
  #   * an absolute word, or one that already holds a slash, is globbed once,
  #     where it points. `~/Downloads/freeb` is not `freeb` in every root — it is
  #     that one path. The answer is the word as typed plus what the glob added,
  #     so the `~` the user typed is the `~` they get back;
  #   * a bare name is the search, and that is the case this feature is for.
  glob="${word/#\~/$HOME}"
  # A word naming a directory lists what is in it. Without this, `~` globs as
  # `~*` — every *sibling* of the home directory — which matches nothing.
  [[ -d "$glob" ]] && glob="${glob%/}/"
  if [[ "$glob" == /* || "$glob" == */* ]]; then
    # `ls -t` for the ordering, for the same reason as _tai_keep_root below: a
    # name-ordered glob caps on the alphabet and drops the newest file.
    while IFS= read -r -d '' c; do
      [[ -f "$c" ]] || continue
      real="$c"
      _tai_keep_fresh "$word${c#$glob}" "$real"
      (( ++n >= _TAI_FILE_PER_ROOT )) && break
    done < <(ls -t -1 --zero -N -- ${glob}* 2>/dev/null)
    return 0
  fi
  dirs=( "." ); prefixes=( "" )
  # No subdirectories, and that is a decision rather than an omission: reading
  # them made `chmod +x ` answer `chmod +x tests/__pycache__/test_smoke.cpython-314.pyc`
  # on a machine where a test had just run — a file nobody chmods, and the newest
  # thing on disk besides the AppImage just downloaded. `~/Downloads` is a root
  # anyway, so the case that mattered did not need the descent.
  # Guarded, not bare: this is reachable under `set -u` from a caller that has
  # never set the variable, and an unset substitution erroring out is a message
  # on a completion press where "no extra roots" was the whole answer.
  local extra_roots="${TAI_FILE_ROOTS:-}"
  for dir in ${extra_roots//:/ } ${XDG_DOWNLOAD_DIR:+"$XDG_DOWNLOAD_DIR"} \
             "$HOME/Downloads" "$HOME/Download" "$HOME/Desktop"; do
    [[ -n "$dir" ]] || continue
    [[ "${dir/#\~/$HOME}" == /* ]] || dir="$PWD/$dir"
    dir="${dir%/}"
    [[ -d "$dir" && "$dir" != "$PWD" ]] || continue
    # Already read, as a subdirectory of the current one or as a root that came
    # earlier in this list: standing in your home directory must not offer
    # `~/Downloads/x` twice, once as itself and once as `Downloads/x`.
    skip=0
    for c in "${dirs[@]}"; do [[ "$c" == "$dir" ]] && skip=1; done
    (( skip )) && continue
    dirs+=( "$dir" )
    if [[ "$dir" == "$HOME"/* ]]; then
      prefixes+=( "~/${dir#"$HOME"/}/" )
    else
      prefixes+=( "${dir%/}/" )
    fi
    (( ${#dirs} >= _TAI_FILE_ROOTS_MAX )) && break
  done
  for (( i = 0; i < ${#dirs[@]}; i++ )); do
    _tai_keep_root "${dirs[i]}" "${prefixes[i]}" "$glob"
  done
  return 0
}

# One root's files, newest first, into _TAI_FRESH. Each root reads at most
# _TAI_FILE_PER_ROOT: a directory of eight files already ranked by mtime must not
# use up the whole answer, or `chmod +x ` in a busy project would never reach
# ~/Downloads.
_tai_keep_root() {
  local dir="$1" as="$2" glob="$3" c
  local -i n=0
  # The current directory is globbed without a `./` prefix, because a completion
  # is a word: `./notes.txt` in a menu beside `~/Downloads/x` would be the same
  # file written two ways, and readline would insert the `./` too.
  #
  # bash has no mtime-ordered glob — zsh's is `(om)`, and this is the same rule
  # asked for again. A plain glob is sorted by name, so capping *it* took the
  # alphabetically first eight files and dropped the AppImage that had just been
  # downloaded from a folder of `aaa*.mp4` — the one case this whole path exists
  # for. So the order comes from `ls -t`, which is one fork for the whole root
  # and replaces the per-file `stat` below for the purpose of *ordering*.
  # `--zero` and `-N` are what make it safe: NUL-separated, so a newline in a
  # filename is not a row break, and literal, so quoting is never applied to a
  # name. Both are coreutils, the same dependency `stat -c` below already is.
  local pattern="$glob"
  [[ "$dir" != "." ]] && pattern="$dir/$glob"
  while IFS= read -r -d '' c; do
    [[ -f "$c" ]] || continue
    if [[ "$dir" == "." ]]; then
      _tai_keep_fresh "$as$c" "$c"
    else
      _tai_keep_fresh "$as${c#"$dir"/}" "$c"
    fi
    (( ++n >= _TAI_FILE_PER_ROOT )) && break
  done < <(ls -t -1 --zero -N -- ${pattern}* 2>/dev/null)
}

# The files a path argument could be, and whether what tai learned for this line
# is still one of them.
#
# Answers in the globals _TAI_FILES and _TAI_FILES_Q — one flag per entry, 1
# when it is a name off the disk that has to be quoted before a shell passes it
# as one word, 0 when it is the word of a line the user ran and is already
# shell text. Nothing is printed: every caller runs under `bind -x`, where
# stdout is the terminal, and the old print-then-capture round trip was a fork
# per answer on a keypress.
_tai_file_answer() {
  local line="$1" before word key last
  local -a kept
  _TAI_FILES=(); _TAI_FILES_Q=()
  # `*" "*` — a space *anywhere* — and not `*' '`, which is a space at the end.
  # That second one reads like the test above it and is the opposite question:
  # `chmod +x freeb` has a space in it and no trailing one, so it would have been
  # turned away before it was ever looked up.
  [[ "$line" == *" "* ]] || return 1
  if [[ "$line" == *" " ]]; then before="${line% *}"; word=""; else word="${line##* }"; before="${line%"$word"}"; fi
  # Trimmed by expansion, not by a loop. A loop over `while [[ "$key" == *" " ]]`
  # never terminates: that pattern matches a space *anywhere*, so `chmod +x` —
  # which has one in the middle — entered it and `${key% }` shortened nothing.
  key="$before"
  key="${key%"${key##*[![:space:]]}"}"
  # A key of nothing but spaces — a line typed as `    ` — is no lookup at all,
  # and bash treats an empty associative-array subscript as a hard error rather
  # than a miss. zsh has no such distinction, which is why only bash needs this.
  [[ -n "$key" ]] || return 1
  if [[ " ${_TAI_WRAPPERS} " == *" ${before%% *} "* ]]; then key="${key#* }"; fi
  [[ -n "${_TAI_FILE[$key]:-}" ]] || return 1
  _tai_fresh_files "$word"
  # A completion has to extend the word being typed, and a file found through a
  # glob does not always: `chmod +x freeb` matches `~/Downloads/freebuff…` in
  # *Downloads*, but that name does not start with the word, so accepting it
  # would replace three typed letters with a path from somewhere else.
  kept=()
  for last in "${_TAI_FRESH[@]}"; do
    [[ "$last" == "$word"* ]] && kept+=("$last")
  done
  _TAI_FRESH=( "${kept[@]}" )
  # A learned argument that is still a file here outranks the newest one: it was
  # asked for before and it exists, which is what keeps this from displacing
  # `cat ~/notes/todo.txt` with whatever was touched last. It is already the
  # text the user ran, so its flag is 0; everything that came off the disk is
  # raw text and travels with a 1.
  _tai_best "${_TAI_WORD[$key]:-}" "$line"
  last="${_TAI_BEST_LINE##* }"
  if [[ -n "$last" && "$last" == "$word"* && -e "${last/#\~/$HOME}" ]]; then
    _TAI_FILES+=( "$last" ); _TAI_FILES_Q+=( 0 )
  fi
  _TAI_FILES+=( "${_TAI_FRESH[@]}" )
  for last in "${_TAI_FRESH[@]}"; do _TAI_FILES_Q+=( 1 ); done
}

# Words that run the command after them rather than being the command. The
# history is full of `git ...` and the shell is asked for `sudo git ...`, so
# without treating the wrapper as transparent the whole vocabulary behind it is
# invisible. A closed list, and deliberately without `env` or `xargs`: both change
# what follows them enough that putting the wrapper back would be a lie.
