'''Bake the Seven Wonders UI icons into PNG files.

This mirrors the card / wonder pipelines (`gen_card.py`, `gen_wonders.py`): each
icon is written to `out/svg/<id>.svg` and rasterized to `out/png/<id>.png`. Real
icons are pulled from the shared sheet (`seven_wonders_icons.svg`) by their
`inkscape:label`; icons the sheet has no art for yet bake as labelled placeholder
boxes. The icon set + sizes are defined by `design/seven-wonders/icon-manifest.md`.

Usage
-----
  python bake_icons.py <id> [more_ids...]   # build specific icons
  python bake_icons.py --all                # build every icon in the manifest
  python bake_icons.py --list               # list icon ids
  python bake_icons.py --all --no-png       # write only the SVGs

Outputs: out/svg/<id>.svg and out/png/<id>.png
'''
import gencore
import io
import os
import resvg_py
import sys
from PIL import Image

# ---------------------------------------------------------------------------
# The icon set (see design/seven-wonders/icon-manifest.md)
# ---------------------------------------------------------------------------
# Real icons: manifest id -> inkscape:label on the shared sheet.
SHEET = {
    'wood': 'Wood', 'stone': 'Stone', 'clay': 'Brick', 'ore': 'Ore',
    'glass': 'Glass', 'papyrus': 'Papyrus', 'loom': 'Loom',
    'coin': 'Coin', 'vp': 'VP', 'shield': 'Army',
    'gear': 'Gear', 'tablet': 'Tablet', 'compass': 'Compass',
}

# Placeholder icons: manifest id -> short label drawn in the placeholder box.
PLACEHOLDERS = {
    'military_victory': 'V+',
    'military_defeat': 'V-',
    'discard': 'DISC',
    'pass': 'PASS',
}

# Baked PNG size (W, H) per id. Defaults to DEFAULT_SIZE; override here to match
# the manifest whenever an icon needs a non-square / non-default box.
DEFAULT_SIZE = (64, 64)
SIZES = {}

# Manifest order (drives --all and --list).
ICON_IDS = list(SHEET) + list(PLACEHOLDERS)

# Fraction of the box the art's measured bounding box is scaled to fill, leaving
# a thin transparent margin so glyphs don't touch the edges.
FIT_PAD = 0.90


def size_of(iid):
    return SIZES.get(iid, DEFAULT_SIZE)


# ---------------------------------------------------------------------------
# SVG assembly
# ---------------------------------------------------------------------------
def _svg(w, h, defs, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd" '
            'width="%d" height="%d" viewBox="0 0 %d %d">'
            '<defs>%s</defs>%s</svg>' % (w, h, w, h, defs, body))


def icon_svg(icons, iid, w, h):
    '''Center the sheet art for `iid` in a w×h box, scaled to fit its measured
    native bounding box (aspect ratio preserved).'''
    label = SHEET[iid]
    ncx, ncy, nw, nh = icons.measure(label)
    s = min(w * FIT_PAD / nw, h * FIT_PAD / nh)
    body = icons._emit(label, w / 2.0, h / 2.0, s)
    return _svg(w, h, icons.defs_inner, body)


def placeholder_svg(iid, w, h):
    r = min(w, h) / 2.0 * FIT_PAD
    body = gencore.placeholder_box(w / 2.0, h / 2.0, r, PLACEHOLDERS[iid])
    return _svg(w, h, '', body)


def build_svg(icons, iid, w, h):
    if iid in SHEET:
        return icon_svg(icons, iid, w, h)
    if iid in PLACEHOLDERS:
        return placeholder_svg(iid, w, h)
    raise KeyError(iid)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_png(svg_str, w, h):
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg_str, width=w, height=h))
    return Image.open(io.BytesIO(png)).convert('RGBA')


def build_one(iid, svg_dir, png_dir, icons, do_png=True):
    if iid not in SHEET and iid not in PLACEHOLDERS:
        print('  ! unknown icon id: %s' % iid)
        return
    w, h = size_of(iid)
    svg_str = build_svg(icons, iid, w, h)
    svg_path = os.path.join(svg_dir, iid + '.svg')
    with open(svg_path, 'w', encoding='utf-8') as f:
        f.write(svg_str)
    if do_png:
        png_path = os.path.join(png_dir, iid + '.png')
        render_png(svg_str, w, h).save(png_path)
        print('  %-18s %d×%d -> %s' % (iid, w, h, png_path))
    else:
        print('  %-18s %d×%d -> %s' % (iid, w, h, svg_path))


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return
    if argv[0] == '--list':
        for iid in ICON_IDS:
            kind = 'sheet' if iid in SHEET else 'placeholder'
            print('%-18s %s' % (iid, kind))
        return

    do_png = '--no-png' not in argv
    argv = [a for a in argv if a != '--no-png']

    icons = gencore.IconLib()
    svg_dir = os.path.join('out', 'svg', 'icons')
    png_dir = os.path.join('out', 'png', 'icons')
    os.makedirs(svg_dir, exist_ok=True)
    os.makedirs(png_dir, exist_ok=True)

    ids = ICON_IDS if argv and argv[0] == '--all' else argv
    print('Building %d icon(s):' % len(ids))
    for iid in ids:
        build_one(iid, svg_dir, png_dir, icons, do_png=do_png)


if __name__ == '__main__':
    main(sys.argv[1:])
