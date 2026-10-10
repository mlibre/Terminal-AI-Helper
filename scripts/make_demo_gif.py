#!/usr/bin/env python3
"""Re-record the readme demo GIF: a real zsh running the real plugin on a pty.

    python3 scripts/make_demo_gif.py                  # -> assets/demo.gif
    python3 scripts/make_demo_gif.py --out /tmp/x.gif --debug

What it does, in order:

  1. builds a scratch world under /tmp/tai — a real git repo with a media
     tree, plus a hand-written index whose commands are the demo's story;
  2. drives the repo's own pty harness (tests/plugin_pty.py) through four
     beats, one keystroke at a time, snapshotting a pyte screen after each
     and remembering which key produced the frame;
  3. renders the frames GitHub-dark under window chrome, with a keycap strip
     along the bottom showing every key the "user" strokes — the key just
     pressed lit up, older ones fading — and drops QA stills of the
     well-known moments next to the recording.

Nothing on screen is drawn by hand: every frame is the plugin's own bytes
turned back into a screen by pyte. When the product's look or story moves,
this is the one command that re-tells it.

Dependencies: `pip install pyte pillow` (the rest is stdlib), a zsh on PATH
or the bundled one under tests/bin/, git, and the DejaVu fonts.
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from plugin_pty import Session, CTRL_SPACE, RIGHT, DOWN, ENTER, CTRL_U  # noqa: E402
from plugin_env import write_index  # noqa: E402

# -- the world ---------------------------------------------------------------
# A scratch HOME with a git repo the demo really runs commands in, and a media
# tree the `cd` beat really walks into. The index is the story: every line the
# GIF shows was learned here first.
DEMO_HOME = pathlib.Path("/tmp/tai/tai_demo_home")
CWD = DEMO_HOME / "Projects" / "tai"
MEDIA = CWD / "media" / "mlibre" / "B"

# Score order: earlier = higher. `git status --short` sits low on purpose —
# it is the sibling that turns the typo beat's answer from one grey line into
# a ranked list. No single quotes (the fixture writer refuses them).
DEMO_COMMANDS = [
    "git status",
    "git pull --rebase",
    "git checkout -b feature/login",
    "git push origin main",
    'git commit -m "fix the ghost"',
    "git status --short",
    "python3 -m http.server 8000",
    "python3 -m venv .venv",
    "ls -la",
    "ls -lah",
    "cd media/mlibre/B",
    "cd ..",
    "tai refresh",
    "tai doctor",
    "tai web --no-browser",
]

PS1 = "%F{green}➜%f  %F{cyan}%~%f "
COLS, ROWS = 80, 24


def build_world() -> None:
    shutil.rmtree(DEMO_HOME, ignore_errors=True)
    MEDIA.mkdir(parents=True)
    for name in ("Clip", "Movies", "Projects"):
        (MEDIA / name).mkdir()
    (CWD / "README.md").write_text("# tai demo\n")
    (CWD / ".gitignore").write_text("media/\n")
    env = dict(os.environ, HOME=str(DEMO_HOME))

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=CWD, env=env, check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    git("add", ".")
    git("-c", "user.name=tai", "-c", "user.email=tai@demo",
        "commit", "-q", "-m", "first")
    write_index(commands=DEMO_COMMANDS)


# -- the recording -----------------------------------------------------------
# A frame is (screen snapshot, display milliseconds, keys pressed for it).
# The snapshot is pyte's screen: one tuple of styled cells per row plus the
# cursor position. `keys` drives the keycap strip in the renderer.
def record(debug: bool = False) -> list:
    import pyte

    build_world()
    os.chdir(CWD)
    s = Session("zsh", env_extra={"HOME": str(DEMO_HOME), "PS1": PS1})
    frames: list[tuple] = []
    try:
        # A real `clear`, run by the shell, so ZLE's own idea of the screen and
        # pyte's start from the same place. The mark goes down BEFORE the
        # clear: pyte has to see the clear itself and the prompt ZLE redraws
        # after it, or the first seconds of the demo are fragments on an empty
        # screen.
        mark = len(s.seen)
        s.write("clear -x\n")
        s.settle()
        screen = pyte.Screen(COLS, ROWS)
        stream = pyte.ByteStream(screen)

        def feed() -> None:
            nonlocal mark
            data = s.seen[mark:]
            if data:
                stream.feed(data.encode(errors="replace"))
                mark = len(s.seen)

        def snap(screen: pyte.Screen):
            cells = []
            for y in range(ROWS):
                row = []
                for x in range(COLS):
                    c = screen.buffer[y][x]
                    row.append((c.data, c.fg, c.bg, c.bold, c.reverse))
                cells.append(tuple(row))
            return (tuple(cells), (screen.cursor.x, screen.cursor.y))

        def text_of(snap_tuple) -> str:
            return "\n".join("".join(ch for ch, *_ in row).rstrip()
                             for row in snap_tuple[0]
                             if any(ch.strip() for ch, *_ in row))

        def push(ms: int, keys: tuple = ()) -> None:
            feed()
            frames.append((snap(screen), ms, keys))

        def type(text: str, ms: int = 100) -> None:
            for ch in text:
                label = "\u2423" if ch == " " else ch
                s.write(ch)
                s.settle()
                push(ms, (label,))

        def key(seq: str, ms: int, label: str,
                quiet: float | None = None) -> None:
            s.write(seq)
            s.settle(quiet)
            push(ms, (label,))

        def seen_text() -> str:
            return text_of(snap(screen))

        def run_enter(name: str, evidence: str, ms: int = 1300) -> None:
            """Enter to run, and a second one if the first only took."""
            key(ENTER, 500, "\u23ce")
            if evidence not in seen_text():
                print(f"    (second enter needed for {name})")
                key(ENTER, ms, "\u23ce")
            assert evidence in seen_text(), f"{name}: {evidence!r} never appeared"

        def checkpoint(name: str) -> None:
            if debug:
                print(f"=== {name} ===")
                print(text_of(snap(screen)))

        # -- beat 1: the grey hint, taken with the arrow key, run ------------
        push(1000)                       # fresh prompt, hold
        type("git st")
        push(850)                        # ghost visible, hold
        checkpoint("beat1 hint")
        key(RIGHT, 500, "\u2192")        # take the hint
        run_enter("git status", "nothing to commit")
        checkpoint("beat1 ran")

        # -- beat 2: a bare first word opens the ranked list -----------------
        type("git", 110)
        push(350)
        key(CTRL_SPACE, 900, "Ctrl \u2423")   # the habits, ranked
        checkpoint("beat2 menu")
        key(DOWN, 400, "\u2193")
        key(DOWN, 400, "\u2193")
        key(ENTER, 600, "\u23ce")        # take the selected line
        assert "git checkout -b feature/login" in seen_text()
        run_enter("checkout", "Switched to a new branch")
        checkpoint("beat2 ran")

        # -- beat 3: the filesystem answers the next word --------------------
        type("cd me")
        push(850)
        checkpoint("beat3 hint")
        key(RIGHT, 400, "\u2192")        # take: cd media/mlibre/B
        type("/", 140)
        push(300)
        key(CTRL_SPACE, 900, "Ctrl \u2423")   # Clip/  Movies/  Projects/
        checkpoint("beat3 menu")
        key(DOWN, 380, "\u2193")
        key(DOWN, 380, "\u2193")
        key(ENTER, 600, "\u23ce")        # fill Projects/
        checkpoint("beat3 taken")
        run_enter("cd", "media/mlibre/B/Projects", 1500)
        checkpoint("beat3 ran")

        # -- beat 4: a typo brings the list up on its own --------------------
        # No key is pressed to open it: the glimpse is the plugin's own answer
        # to a word that matches nothing verbatim. Down turns the preview into
        # a selection, Enter takes the habit the typo was shadowing — and the
        # corrected line answers with a hint of its own.
        type("git statsu")
        push(1600)                       # the list the typo brought up, hold
        checkpoint("beat4 preview")
        preview = seen_text()
        assert "git status" in preview and "git status --short" in preview, \
            f"the typo preview never showed the list:\n{preview}"
        key(DOWN, 900, "\u2193")         # the preview becomes the selection
        checkpoint("beat4 armed")
        key(ENTER, 900, "\u23ce")        # take it: the typo becomes the habit
        assert "git statsu" not in seen_text(), \
            "the take never landed: the typo is still on the line"
        push(1900)                       # the corrected line, holding
        checkpoint("beat4 taken")
        key(CTRL_U, 500, "Ctrl U")       # a fresh line for the outro
        push(1400)
    finally:
        s.close()

    # Identical neighbours merge — but only when neither holds a keystroke.
    # A frame with keys must survive on its own, or the keycap strip would
    # never light up on a screen that did not change.
    merged: list[tuple] = []
    for snap_tuple, ms, keys in frames:
        if merged and not keys and not merged[-1][2] \
                and merged[-1][0] == snap_tuple:
            merged[-1] = (snap_tuple, merged[-1][1] + ms, ())
        else:
            merged.append((snap_tuple, ms, keys))
    return merged


# -- the rendering -----------------------------------------------------------
# Theme: GitHub-dark. Window chrome with traffic lights and a title bar, one
# shared palette across all frames so nothing flickers, and a keycap strip
# along the bottom that shows the keys being stroked.
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu",
             "/usr/share/fonts/dejavu",
             "/usr/share/fonts/TTF"]

FS = 16
CELL_W, CELL_H = 10, 23

PAGE = (1, 4, 9)
WIN_BG = (13, 17, 23)
BAR_BG = (22, 27, 34)
BORDER = (48, 54, 61)
TITLE_FG = (139, 148, 158)
DEFAULT_FG = (201, 209, 217)
CURSOR_BG = (182, 194, 207)
CURSOR_FG = (13, 17, 23)
DOTS = ((255, 95, 87), (254, 188, 46), (40, 200, 64))

NAMED = {
    "black": (72, 79, 88), "red": (255, 123, 114), "green": (63, 185, 80),
    "brown": (210, 153, 34), "yellow": (210, 153, 34), "blue": (88, 166, 255),
    "magenta": (188, 140, 255), "cyan": (97, 214, 224), "white": (201, 209, 217),
    "brightblack": (125, 133, 144), "grey": (125, 133, 144),
    "brightred": (255, 161, 152), "brightgreen": (86, 211, 100),
    "brightyellow": (227, 179, 65), "brightblue": (121, 192, 255),
    "brightmagenta": (210, 168, 255), "brightcyan": (86, 212, 221),
    "brightwhite": (240, 246, 252),
}

# The keycap strip: the key just pressed lights up in the accent blue, the
# ones before it cool off with age, the oldest fade into the page.
KEY_MAX = 14
KEY_H, KEY_PAD, KEY_GAP, KEY_R = 32, 9, 7, 6
HUD_GAP, HUD_H = 16, 44
CAP_BG, CAP_BORDER, CAP_FG = (33, 38, 45), (63, 68, 77), (166, 175, 186)
HOT_BG, HOT_BORDER, HOT_FG = (31, 111, 235), (89, 157, 255), (240, 246, 252)


def color(name: str, fallback: tuple) -> tuple:
    if name in (None, "", "default"):
        return fallback
    if name in NAMED:
        return NAMED[name]
    if name.isdigit() and int(name) < 256:
        idx = int(name)
        if idx < 16:
            order = ["black", "red", "green", "brown", "blue", "magenta",
                     "cyan", "white", "brightblack", "brightred",
                     "brightgreen", "brightyellow", "brightblue",
                     "brightmagenta", "brightcyan", "brightwhite"]
            return NAMED[order[idx]]
        return DEFAULT_FG
    return DEFAULT_FG


def mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _font_path(name: str) -> str:
    for d in FONT_DIRS:
        p = pathlib.Path(d) / name
        if p.is_file():
            return str(p)
    sys.exit(f"make_demo_gif: {name} not found under {FONT_DIRS} — "
             "install the DejaVu font package (ttf-dejavu / fonts-dejavu).")


MARGIN, TITLE_H, PAD, BORDER_W = 20, 42, 14, 1
TEXT_X = MARGIN + BORDER_W + PAD
TEXT_Y = MARGIN + BORDER_W + TITLE_H + PAD
W = MARGIN * 2 + BORDER_W * 2 + PAD * 2 + COLS * CELL_W
WIN_H = 0   # set in render_all: the height the demo's own rows need
TOTAL_H = 0  # WIN_H plus the keycap strip
STRIP_Y = 0  # top of the keycap strip

font_r = None
font_b = None
ui_r = None
hud_font = None
DOT_Y = MARGIN + TITLE_H // 2
Y_OFF = (CELL_H - FS) // 2 + 1


def _fonts() -> None:
    global font_r, font_b, ui_r, hud_font
    from PIL import ImageFont
    font_r = ImageFont.truetype(_font_path("DejaVuSansMono.ttf"), FS)
    font_b = ImageFont.truetype(_font_path("DejaVuSansMono-Bold.ttf"), FS)
    ui_r = ImageFont.truetype(_font_path("DejaVuSans.ttf"), 13)
    hud_font = ImageFont.truetype(_font_path("DejaVuSans.ttf"), 14)


def rows_used(frames) -> int:
    """Deepest row any frame draws on, plus a little breathing room."""
    deepest = 0
    for snap, _ms, _keys in frames:
        cells, _ = snap
        for y, row in enumerate(cells):
            if any(c[0].strip() for c in row):
                deepest = max(deepest, y)
    return min(ROWS, deepest + 3)


def chrome() -> "Image.Image":
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, TOTAL_H), PAGE)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([MARGIN, MARGIN, W - MARGIN - 1, WIN_H - MARGIN - 1],
                        radius=11, fill=BAR_BG, outline=BORDER, width=BORDER_W)
    d.rounded_rectangle([MARGIN, MARGIN + TITLE_H, W - MARGIN - 1,
                         WIN_H - MARGIN - 1], radius=11, fill=WIN_BG)
    d.rectangle([MARGIN + 1, MARGIN + TITLE_H - 11,
                 W - MARGIN - 2, MARGIN + TITLE_H], fill=BAR_BG)
    d.rounded_rectangle([MARGIN, MARGIN, W - MARGIN - 1, WIN_H - MARGIN - 1],
                        radius=11, outline=BORDER, width=BORDER_W)
    for i, col in enumerate(DOTS):
        x = MARGIN + 24 + i * 22
        d.ellipse([x - 6, DOT_Y - 6, x + 6, DOT_Y + 6], fill=col)
    title = "tai — zsh"
    tw = d.textlength(title, font=ui_r)
    d.text(((W - tw) // 2, MARGIN + (TITLE_H - 17) // 2 - 1), title,
           font=ui_r, fill=TITLE_FG)
    return img


def draw_hud(d, history: list, hot: tuple) -> None:
    """The keycap strip under the window: the story told in keys.

    The keys of this frame glow in the accent colour — that is the stroke
    happening now — and the keys before it cool toward the page as they age.
    """
    visible = list(history[-KEY_MAX:])
    hot_n = min(len(hot), len(visible))
    x = TEXT_X
    y = STRIP_Y + (HUD_H - KEY_H) // 2
    for i, label in enumerate(visible):
        is_hot = i >= len(visible) - hot_n
        if is_hot:
            bg, border, fg = HOT_BG, HOT_BORDER, HOT_FG
        else:
            t = max(0.30, 1.0 - 0.13 * (len(visible) - 1 - i))
            bg = mix(PAGE, CAP_BG, t)
            border = mix(PAGE, CAP_BORDER, t)
            fg = mix(PAGE, CAP_FG, t)
        w = int(d.textlength(label, font=hud_font)) + KEY_PAD * 2
        d.rounded_rectangle([x, y, x + w - 1, y + KEY_H - 1], radius=KEY_R,
                            fill=bg, outline=border, width=1)
        d.text((x + KEY_PAD, y + (KEY_H - 14) // 2 - 1), label,
               font=hud_font, fill=fg)
        x += w + KEY_GAP


def render(snap, history: list, hot: tuple) -> "Image.Image":
    from PIL import ImageDraw
    img = chrome()
    d = ImageDraw.Draw(img)
    cells, (cx, cy) = snap
    for y, row in enumerate(cells):
        py = TEXT_Y + y * CELL_H
        for x, (ch, fg, bg, bold, rev) in enumerate(row):
            px = TEXT_X + x * CELL_W
            if rev:
                cellbg = color(fg, DEFAULT_FG)
                cellfg = WIN_BG
            else:
                cellbg = (color(bg, WIN_BG)
                          if bg not in (None, "", "default") else WIN_BG)
                cellfg = color(fg, DEFAULT_FG)
            if cellbg != WIN_BG:
                d.rectangle([px, py, px + CELL_W - 1, py + CELL_H - 1],
                            fill=cellbg)
            if ch.strip():
                d.text((px, py + Y_OFF), ch,
                       font=font_b if bold else font_r, fill=cellfg)
    px, py = TEXT_X + cx * CELL_W, TEXT_Y + cy * CELL_H
    d.rounded_rectangle([px, py, px + CELL_W - 1, py + CELL_H - 1],
                        radius=2, fill=CURSOR_BG)
    ch = cells[cy][cx][0]
    if ch.strip():
        d.text((px, py + Y_OFF), ch, font=font_r, fill=CURSOR_FG)
    draw_hud(d, history, hot)
    return img


def text(snap) -> str:
    cells, _ = snap
    return "\n".join("".join(c[0] for c in row) for row in cells)


def render_all(frames, out: pathlib.Path, qa: pathlib.Path) -> None:
    from PIL import Image
    global WIN_H, TOTAL_H, STRIP_Y
    _fonts()
    WIN_H = MARGIN * 2 + BORDER_W * 2 + TITLE_H + PAD * 2 \
        + rows_used(frames) * CELL_H
    TOTAL_H = WIN_H + HUD_GAP + HUD_H
    STRIP_Y = WIN_H - MARGIN + HUD_GAP
    qa.mkdir(parents=True, exist_ok=True)

    images, durations = [], []
    history: list = []
    for snap, ms, keys in frames:
        history.extend(keys)
        images.append(render(snap, history, keys))
        durations.append(ms)

    # One shared palette from the busiest frames, so nothing shifts between
    # them — the keycap strip's blues included.
    strip = Image.new("RGB", (W, TOTAL_H * min(6, len(images))))
    for k in range(min(6, len(images))):
        strip.paste(images[k], (0, TOTAL_H * k))
    pal = strip.quantize(colors=96, method=Image.MEDIANCUT,
                         dither=Image.Dither.NONE)
    pq = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in images]
    out.parent.mkdir(parents=True, exist_ok=True)
    pq[0].save(out, save_all=True, append_images=pq[1:],
               duration=durations, loop=0, optimize=True)
    kb = out.stat().st_size / 1024
    total = sum(durations) / 1000
    print(f"{out}  {len(pq)} frames  {total:.1f}s  {kb:.0f} KB  ({W}x{TOTAL_H})")

    # QA stills at the well-known moments, so a broken beat is one image read
    # away from a diagnosis.
    def find(pred, start=0):
        hit = None
        for i in range(start, len(frames)):
            if pred(frames[i][0]):
                hit = i
        return hit

    def find_key(label: str, start: int = 0):
        for i in range(start, len(frames)):
            if label in frames[i][2]:
                return i
        return None

    marks = {}
    # The ghost moment is pinned by its keystrokes, not by text alone: bare
    # "git" also reads "git status" (its own grey hint), and after the arrow
    # takes the hint the line reads the same again. Only the history of keys
    # — ending in s, then t — witnesses the "git st" moment.
    hist: list = []
    for i, (snap_i, _ms, keys) in enumerate(frames):
        hist.extend(keys)
        if len(hist) >= 2 and hist[-2] == "s" and hist[-1] == "t" \
                and "➜  ~/Projects/tai git status" in text(snap_i) \
                and "On branch" not in text(snap_i):
            marks["ghost"] = i
            break
    for name, (pred, first) in {
        "menu1": (lambda s: "git-receive-pack" in text(s), False),
        "fs_menu": (lambda s: "Clip/" in text(s), False),
        "ran1": (lambda s: "On branch main" in text(s), True),
        "typo_preview": (lambda s: "git statsu" in text(s)
                         and "git status --short" in text(s), True),
    }.items():
        i = find(pred) if not first else next(
            (i for i, (s_, _m, _k) in enumerate(frames) if pred(s_)), None)
        if i is not None:
            marks[name] = i
    armed = None
    if "typo_preview" in marks:
        armed = find_key("\u2193", marks["typo_preview"])
        if armed is not None:
            marks["typo_armed"] = armed
            taken = find_key("\u23ce", armed)
            if taken is not None:
                marks["typo_taken"] = taken
    marks["final"] = len(frames) - 1
    for name, i in marks.items():
        images[i].save(qa / f"{name}.png")
    print("qa stills:", ", ".join(f"{k}={v}" for k, v in sorted(marks.items())))


# -- the command -------------------------------------------------------------
def main() -> None:
    for mod in ("pyte", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            sys.exit(
                f"make_demo_gif: the '{mod}' package is missing.\n"
                "  pip install pyte pillow")
    ap = argparse.ArgumentParser(
        description="Re-record the readme demo GIF from a real zsh session.")
    ap.add_argument("--out", type=pathlib.Path,
                    default=REPO / "assets" / "demo.gif",
                    help="where the GIF lands (default: assets/demo.gif)")
    ap.add_argument("--qa-dir", type=pathlib.Path,
                    default=pathlib.Path("/tmp/tai/tai_demo_qa"),
                    help="where the QA stills land")
    ap.add_argument("--debug", action="store_true",
                    help="print the screen at every beat checkpoint")
    args = ap.parse_args()

    frames = record(debug=args.debug)
    # /tmp/tai holds the QA stills; the frames themselves are not kept.
    render_all(frames, args.out.resolve(), args.qa_dir.resolve())


if __name__ == "__main__":
    main()

