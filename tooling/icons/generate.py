#!/usr/bin/env python3
"""Regenerate this add-in's command-button icons from the Lucide SVG sources
in ./svg. Each raster is rendered directly from the vector at its exact target
pixel size with rsvg-convert (no upscaling), producing the standard and HiDPI
(@2x) files a Fusion resources folder ships: 16x16, 16x16@2x, 32x32, 32x32@2x,
64x64, 64x64@2x (16/32/32/64/64/128 px). Requires rsvg-convert and Pillow.

    python3 generate.py            # write every icon into ../../addin/*/resources
    python3 generate.py mtxMark    # just one

Colour language: green = add/set, red = remove/clear, orange = highlight/accent,
white = neutral.
"""
import os, re, subprocess, sys
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "svg")
RES_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "addin", "BatchKit", "resources"))

WHITE, ORANGE, GREEN, RED = "#FFFFFF", "#F3731D", "#06A84A", "#E84038"
FILES = {"16x16.png": 16, "16x16@2x.png": 32, "32x32.png": 32,
         "32x32@2x.png": 64, "64x64.png": 64, "64x64@2x.png": 128}
SIZES = sorted(set(FILES.values()))

SPEC = {
    'bkRename': ('comp', 'square-pen', '#FFFFFF', 'hash', '#F3731D', 0.5),
    'bkMaterial': ('comp', 'gauge-simple', '#FFFFFF', 'copy', '#F3731D', 0.5),
    'bkSelectByMaterial': ('simple', 'wand-sparkles', '#FFFFFF', (1, 2, 3, 4, 5, 6, 7)),
}


def _svg(name):
    with open(os.path.join(SVG_DIR, name + ".svg")) as f:
        return f.read()


def _recolour(svg, base, accent_paths, accent):
    svg = svg.replace('stroke="currentColor"', 'stroke="%s"' % base)
    if accent_paths:
        idx = [0]
        def repl(m):
            i = idx[0]; idx[0] += 1
            tag = m.group(0)
            if i in accent_paths:
                tag = tag[:5] + ' stroke="%s"' % accent + tag[5:]
            return tag
        svg = re.sub(r'<path', repl, svg)
    return svg


def _thicken(svg, px):
    if px >= 32:
        return svg
    w = 2.6 if px == 16 else 2.3
    return svg.replace('stroke-width="2"', 'stroke-width="%s"' % w)


def _render(svg, px):
    td = os.path.join(HERE, ".tmp"); os.makedirs(td, exist_ok=True)
    ts = os.path.join(td, "_i%d.svg" % os.getpid())
    tp = os.path.join(td, "_i%d.png" % os.getpid())
    with open(ts, "w") as f:
        f.write(svg)
    subprocess.run(["rsvg-convert", "-w", str(px), "-h", str(px), ts, "-o", tp],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    im = Image.open(tp).convert("RGBA"); os.remove(ts); os.remove(tp)
    return im


def render_simple(glyph, px, base=WHITE, accent_paths=(), accent=ORANGE):
    return _render(_thicken(_recolour(_svg(glyph), base, set(accent_paths), accent), px), px)


def render_composite(bg, badge, px, base=WHITE, badge_col=ORANGE, scale=0.5):
    base_im = render_simple(bg, px, base=base)
    b = max(8, int(round(px * scale)))
    badge_im = render_simple(badge, b, base=badge_col)
    out = base_im.copy(); out.alpha_composite(badge_im, (px - b, px - b))
    return out


def render_filled_badge(px, disc, mark, mark_col, scale=0.5, mark_frac=0.56):
    from PIL import ImageDraw
    b = max(10, int(round(px * scale)))
    ss = 4; big = b * ss
    disc_im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    rgb = tuple(int(disc[i:i + 2], 16) for i in (1, 3, 5))
    ImageDraw.Draw(disc_im).ellipse([0, 0, big - 1, big - 1], fill=rgb + (255,))
    disc_im = disc_im.resize((b, b), Image.LANCZOS)
    mb = max(6, int(round(b * mark_frac)))
    disc_im.alpha_composite(render_simple(mark, mb, base=mark_col),
                            ((b - mb) // 2, (b - mb) // 2))
    return disc_im, b


def render_comp_filled(bg, px, base, disc, mark, mark_col, scale=0.5):
    base_im = render_simple(bg, px, base=base)
    badge, b = render_filled_badge(px, disc, mark, mark_col, scale=scale)
    out = base_im.copy(); out.alpha_composite(badge, (px - b, px - b))
    return out


def build_one(cid, px):
    s = SPEC[cid]
    if s[0] == "simple":
        return render_simple(s[1], px, base=s[2], accent_paths=s[3])
    if s[0] == "compfill":
        return render_comp_filled(s[1], px, s[2], s[3], s[4], s[5], scale=s[6])
    return render_composite(s[1], s[3], px, base=s[2], badge_col=s[4], scale=s[5])


def main():
    ids = sys.argv[1:] or list(SPEC)
    for cid in ids:
        d = os.path.join(RES_DIR, cid); os.makedirs(d, exist_ok=True)
        cache = {px: build_one(cid, px) for px in SIZES}
        for fn, px in FILES.items():
            cache[px].save(os.path.join(d, fn))
        print("wrote", cid)


if __name__ == "__main__":
    main()
