#!/usr/bin/env python3
"""Re-record the readme demo GIF: a real zsh running the real plugin on a pty.

    python3 scripts/make_demo_gif.py                  # -> assets/demo.gif, 2x
    python3 scripts/make_demo_gif.py --out /tmp/x.gif --scale 1 --debug

What it does, in order:

  1. builds a scratch world under /tmp/tai — a real git repo with a media
     tree, plus a hand-written index whose commands are the demo's story;
  2. drives the repo's own pty harness (tests/plugin_pty.py) through four
     beats, one keystroke at a time, snapshotting a pyte screen after each
     and remembering which key produced the frame;
  3. renders the frames GitHub-dark under window chrome, with one big keycap
     centered under the window — the TAI key of the moment, and nothing
     else. No typed characters: they are what the key produces, and the
     line above shows them. No history either: one cap, big enough to
     read, lit for exactly as long as its frame — and every TAI key holds
     a beat longer than the typing around it, so the stroke is something
     a viewer catches instead of misses. QA stills of the well-known
     moments land next to the recording.

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

from plugin_pty import Session, RIGHT, DOWN, ENTER, TAB  # noqa: E402
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
# Every TAI key's frame holds this much longer than the beat asked for: the
# single keycap is on screen exactly as long as its frame, and a stroke the
# viewer can barely see teaches nothing.
KEY_HOLD = 1.6


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
def record(debug: bool = False) -> tuple:
    import pyte

    build_world()
    os.chdir(CWD)
    s = Session("zsh", env_extra={"HOME": str(DEMO_HOME), "PS1": PS1})
    frames: list[tuple] = []
    qa: dict = {}   # beat -> frame index, pinned where the beat happens
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
            st = snap(screen)
            # Identical neighbours merge — but only when neither holds a
            # keystroke. A frame with keys must survive on its own, or the
            # keycap strip would never light up on a screen that did not
            # change.
            if frames and not keys and not frames[-1][2] \
                    and frames[-1][0] == st:
                frames[-1] = (st, frames[-1][1] + ms, ())
            else:
                frames.append((st, ms, keys))

        def type(text: str, ms: int = 100) -> None:
            # Characters appear one by one, but a character is not a key of
            # the plugin: the one keycap under the window is reserved for
            # TAI's own keys — the arrow, the chords, the Enters. What is
            # typed is what those keys produce, and the line above shows it.
            for ch in text:
                s.write(ch)
                s.settle()
                push(ms)

        def key(seq: str, ms: int, label: str,
                quiet: float | None = None) -> None:
            s.write(seq)
            s.settle(quiet)
            push(int(ms * KEY_HOLD), (label,))

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
        qa["ghost"] = len(frames) - 1
        checkpoint("beat1 hint")
        key(RIGHT, 500, "\u2192")        # take the hint
        run_enter("git status", "nothing to commit")
        qa["ran1"] = len(frames) - 1
        checkpoint("beat1 ran")

        # -- beat 2: a bare first word opens the ranked list -----------------
        type("git", 110)
        push(350)
        key(TAB, 900, "Tab")             # the habits, ranked: Tab's menu
        qa["menu1"] = len(frames) - 1
        checkpoint("beat2 menu")
        key(TAB, 400, "Tab")             # every Tab moves the selection
        key(TAB, 400, "Tab")
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
        key(TAB, 900, "Tab")             # Clip/  Movies/  Projects/
        qa["fs_menu"] = len(frames) - 1
        checkpoint("beat3 menu")
        key(TAB, 380, "Tab")             # every Tab moves the selection
        key(TAB, 380, "Tab")
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
        qa["typo_preview"] = len(frames) - 1
        checkpoint("beat4 preview")
        preview = seen_text()
        assert "git status" in preview and "git status --short" in preview, \
            f"the typo preview never showed the list:\n{preview}"
        key(DOWN, 900, "\u2193")         # the preview becomes the selection
        qa["typo_armed"] = len(frames) - 1
        checkpoint("beat4 armed")
        key(ENTER, 900, "\u23ce")        # take it: the typo becomes the habit
        qa["typo_taken"] = len(frames) - 1
        assert "git statsu" not in seen_text(), \
            "the take never landed: the typo is still on the line"
        # The closing shot is the payoff itself: the typo corrected, its
        # habit on the line, the hint answering. No Ctrl-U wipe before it —
        # clearing a line is zsh's own kill-whole-line, not the plugin's,
        # and a demo has no business teaching what every shell already does.
        push(2300)                       # the corrected line, holding
        checkpoint("beat4 taken")
    finally:
        s.close()

    return frames, qa


# -- the rendering -----------------------------------------------------------
# Theme: GitHub-dark. Window chrome with traffic lights and a title bar, one
# shared palette across all frames so nothing flickers, and a keycap strip
# along the bottom that shows the keys being stroked.
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu",
             "/usr/share/fonts/dejavu",
             "/usr/share/fonts/TTF"]

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

# The keycap: one cap centered under the window, the key of the moment,
# always lit — there is no history to cool off, so there is no cooled-cap
# palette either.
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


def _font_path(name: str) -> str:
    for d in FONT_DIRS:
        p = pathlib.Path(d) / name
        if p.is_file():
            return str(p)
    sys.exit(f"make_demo_gif: {name} not found under {FONT_DIRS} — "
             "install the DejaVu font package (ttf-dejavu / fonts-dejavu).")


def set_scale(k: float) -> None:
    """Derive every pixel constant from the scale factor.

    The recording is always 80x24 characters; the scale is how many pixels
    each character is worth. 2 is the default: GitHub shows the readme at
    the container width on 1x screens and uses every pixel of a 2x take on
    the retina ones, so the text stays sharp where a 1x render goes soft.
    """
    global SCALE, FS, CELL_W, CELL_H, MARGIN, TITLE_H, PAD, BORDER_W
    global TEXT_X, TEXT_Y, W, WIN_R, DOT_Y, DOT_OFF, DOT_GAP, DOT_R
    global KEY_H, KEY_PAD, KEY_GAP, KEY_R, HUD_GAP, HUD_H, Y_OFF, CURSOR_R
    SCALE = k
    g = lambda v: max(1, int(round(v * k)))
    FS = g(16)
    CELL_W, CELL_H = g(10), g(23)
    MARGIN, TITLE_H, PAD, BORDER_W = g(20), g(42), g(14), g(1)
    TEXT_X = MARGIN + BORDER_W + PAD
    TEXT_Y = MARGIN + BORDER_W + TITLE_H + PAD
    W = MARGIN * 2 + BORDER_W * 2 + PAD * 2 + COLS * CELL_W
    WIN_R = g(11)
    DOT_Y = MARGIN + TITLE_H // 2
    DOT_OFF, DOT_GAP, DOT_R = g(24), g(22), g(6)
    KEY_H, KEY_PAD, KEY_GAP, KEY_R = g(44), g(12), g(10), g(8)
    HUD_GAP, HUD_H = g(16), g(60)
    Y_OFF = (CELL_H - FS) // 2 + 1
    CURSOR_R = g(2)


set_scale(2)  # the default; --scale overrides before rendering

WIN_H = 0   # set in render_all: the height the demo's own rows need
TOTAL_H = 0  # WIN_H plus the keycap strip
STRIP_Y = 0  # top of the keycap strip

font_r = None
font_b = None
ui_r = None
hud_font = None


def _fonts() -> None:
    global font_r, font_b, ui_r, hud_font
    from PIL import ImageFont
    font_r = ImageFont.truetype(_font_path("DejaVuSansMono.ttf"), FS)
    font_b = ImageFont.truetype(_font_path("DejaVuSansMono-Bold.ttf"), FS)
    ui_r = ImageFont.truetype(_font_path("DejaVuSans.ttf"),
                              max(10, round(13 * SCALE)))
    hud_font = ImageFont.truetype(_font_path("DejaVuSans.ttf"),
                                  max(12, round(20 * SCALE)))


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
                        radius=WIN_R, fill=BAR_BG, outline=BORDER,
                        width=BORDER_W)
    d.rounded_rectangle([MARGIN, MARGIN + TITLE_H, W - MARGIN - 1,
                         WIN_H - MARGIN - 1], radius=WIN_R, fill=WIN_BG)
    d.rectangle([MARGIN + 1, MARGIN + TITLE_H - 11,
                 W - MARGIN - 2, MARGIN + TITLE_H], fill=BAR_BG)
    d.rounded_rectangle([MARGIN, MARGIN, W - MARGIN - 1, WIN_H - MARGIN - 1],
                        radius=WIN_R, outline=BORDER, width=BORDER_W)
    for i, col in enumerate(DOTS):
        x = MARGIN + DOT_OFF + i * DOT_GAP
        d.ellipse([x - DOT_R, DOT_Y - DOT_R, x + DOT_R, DOT_Y + DOT_R],
                  fill=col)
    title = "TAI — zsh"
    tw = d.textlength(title, font=ui_r)
    d.text(((W - tw) // 2, MARGIN + (TITLE_H - ui_r.size - 4) // 2), title,
           font=ui_r, fill=TITLE_FG)
    return img


def draw_hud(d, keys: tuple) -> None:
    """The keycap centered under the window: the key of the moment, only that.

    Not the typed characters — they are what the key produces, and the line
    above shows them. Not the keys before it either — one cap, big enough
    to read, lit for exactly as long as its frame holds: the stroke
    happening now, mid-frame where the eye already is.
    """
    if not keys:
        return
    widths = [int(d.textlength(label, font=hud_font)) + KEY_PAD * 2
              for label in keys]
    x = (W - (sum(widths) + KEY_GAP * (len(keys) - 1))) // 2
    y = STRIP_Y + (HUD_H - KEY_H) // 2
    for label, w in zip(keys, widths):
        d.rounded_rectangle([x, y, x + w - 1, y + KEY_H - 1], radius=KEY_R,
                            fill=HOT_BG, outline=HOT_BORDER, width=1)
        d.text((x + KEY_PAD, y + (KEY_H - hud_font.size) // 2 - 1), label,
               font=hud_font, fill=HOT_FG)
        x += w + KEY_GAP


def render(snap, keys: tuple) -> "Image.Image":
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
                        radius=CURSOR_R, fill=CURSOR_BG)
    ch = cells[cy][cx][0]
    if ch.strip():
        d.text((px, py + Y_OFF), ch, font=font_r, fill=CURSOR_FG)
    draw_hud(d, keys)
    return img


def render_all(frames, beats: dict, out: pathlib.Path,
               qa_dir: pathlib.Path) -> None:
    from PIL import Image
    global WIN_H, TOTAL_H, STRIP_Y
    _fonts()
    WIN_H = MARGIN * 2 + BORDER_W * 2 + TITLE_H + PAD * 2 \
        + rows_used(frames) * CELL_H
    TOTAL_H = WIN_H + HUD_GAP + HUD_H
    STRIP_Y = WIN_H - MARGIN + HUD_GAP
    qa_dir.mkdir(parents=True, exist_ok=True)

    images, durations = [], []
    for snap, ms, keys in frames:
        images.append(render(snap, keys))
        durations.append(ms)

    # One shared palette, sampled from frames spread across the whole take —
    # the ending keycaps and menus included, not just how it opens — so
    # nothing shifts between them.
    k = min(8, len(images))
    picks = sorted({round(j * (len(images) - 1) / (k - 1))
                    for j in range(k)}) if k > 1 else [0]
    strip = Image.new("RGB", (W, TOTAL_H * len(picks)))
    for slot, j in enumerate(picks):
        strip.paste(images[j], (0, TOTAL_H * slot))
    pal = strip.quantize(colors=128, method=Image.MEDIANCUT,
                         dither=Image.Dither.NONE)
    pq = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in images]
    out.parent.mkdir(parents=True, exist_ok=True)
    pq[0].save(out, save_all=True, append_images=pq[1:],
               duration=durations, loop=0, optimize=True)
    kb = out.stat().st_size / 1024
    total = sum(durations) / 1000
    print(f"{out}  {len(pq)} frames  {total:.1f}s  {kb:.0f} KB  ({W}x{TOTAL_H})")

    # QA stills at the well-known moments, so a broken beat is one image read
    # away from a diagnosis. The recorder pins each beat where it happens —
    # it knows, the pixels do not.
    marks = dict(beats)
    marks["final"] = len(frames) - 1
    for name, i in sorted(marks.items()):
        images[i].save(qa_dir / f"{name}.png")
    print("qa stills:",
          ", ".join(f"{name}={i}" for name, i in sorted(marks.items())))


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
    ap.add_argument("--scale", type=float, default=2.0,
                    help="pixel density multiplier, 1 to 4 (default 2: the "
                         "readme shows it at container width, so the extra "
                         "pixels are what keep the text sharp on retina)")
    ap.add_argument("--debug", action="store_true",
                    help="print the screen at every beat checkpoint")
    args = ap.parse_args()

    set_scale(min(max(args.scale, 1.0), 4.0))
    frames, beats = record(debug=args.debug)
    # /tmp/tai holds the QA stills; the frames themselves are not kept.
    render_all(frames, beats, args.out.resolve(), args.qa_dir.resolve())


if __name__ == "__main__":
    main()

