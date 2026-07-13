'''Build the 7 Wonders wonder stage medallions from an entry in cards.yaml.

Usage
-----
  python gen_wonders.py <card_id> [more_ids...]   # build specific wonders
  python gen_wonders.py --all                     # build every wonder in cards.yaml
  python gen_wonders.py --list                    # list wonder ids

Outputs: out/svg/<id>.svg and out/png/<id>.png
'''
import base64
import gencore
import io
import os
import resvg_py
import sys
from PIL import Image, ImageDraw

CORNER_R = 24
BENEFIT_CX, BENEFIT_CY = 285, 80   # central benefit medallion
BENEFIT_W = BENEFIT_CX * 2
BENEFIT_R = 56

SEP_R = 40

COST_W = 180
COST_CX = BENEFIT_W + COST_W / 2

W, H = (BENEFIT_W + COST_W), BENEFIT_CY * 2

CREAM = '#d8cab2'

def build_svg(card, ctype, icons):
    color = ctype.get('color', 'grey')
    end_symbol = ctype.get('banner_end_symbol', '')
    edge, base, ctr = gencore.COLORS.get(color, gencore.COLORS['grey'])
    bnr = card.get('banner') or {}
    chain_in = bnr.get('chain_in_symbol')
    chain_out = bnr.get('chain_out_symbol') or []

    return '\n'.join(p)

def cost_area(badges, is_done, icons):
    p = []
    rows = int((len(badges) + 2) / 3)
    cols = int((len(badges) + rows - 1) / rows)

    cell_width = gencore.COST_R * 2 + gencore.COST_GAP
    inner_w = (cols - 1) * cell_width
    start_cx = COST_CX - inner_w / 2

    for c in range(cols):
        cx = start_cx + c * cell_width
        col = badges[c*rows:(c+1)*rows]
        inner_h = (len(col) - 1) * cell_width
        start_cy = BENEFIT_CY - inner_h / 2
        for r in range(len(col)):
            cy = start_cy + r * cell_width
            p.append(gencore.place_cost_glyph(col[r], cx, cy, icons))
            if is_done:
                p.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="#000000" '
                               'opacity="0.62"/>'
                               % (cx, cy, gencore.COST_R))
    return '\n'.join(p)

def build_svg(stage, is_done, icons):
    badges = gencore.parse_cost(stage.get('build_cost'))

    p = []
    p.append('<svg xmlns="http://www.w3.org/2000/svg" '
             'xmlns:xlink="http://www.w3.org/1999/xlink" '
             'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
             'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
             'width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H))
    p.append('<defs>')
    p.append('<filter id="softshadow" x="-40%" y="-40%" width="180%" height="180%">'
             '<feGaussianBlur stdDeviation="3.2"/></filter>')
    p.append(icons.defs_inner)
    p.append('</defs>')

    # compose from the structured medallion layout
    medallion = stage.get('banner', {}).get('medallion', {})
    p.append(gencore.effect_medallion(icons, medallion, BENEFIT_CX, BENEFIT_CY, BENEFIT_R))
    # line between the wonder benefits and its costs
    p.append('<rect x="%d" y="%d" width="4" height="%d" fill="#afafaf" opacity="0.55"/>'
             % (BENEFIT_W - 2, BENEFIT_CY - BENEFIT_R, BENEFIT_R * 2))

    # cost badges
    p.append(cost_area(badges, is_done, icons))

    p.append('</svg>')
    return '\n'.join(p)

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_png(svg_str):
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg_str, width=W, height=H))
    card = Image.open(io.BytesIO(png)).convert('RGBA')
    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1],
                                           radius=CORNER_R, fill=255)
    bg = Image.new('RGBA', (W, H))
    return Image.composite(card, bg, mask)

def build_one(wonder_id, index, svg_dir, png_dir, icons, do_png=True):
    if wonder_id not in index:
        print('  ! unknown wonder id: %s' % wonder_id)
        return
    wonder = index[wonder_id]
    for i, stage in enumerate(wonder['stages']):
        i = i + 1
        for is_done in [True, False]:
            is_done_str = '_done' if is_done else ''
            base = f'{wonder_id}_{i}{is_done_str}'
            svg_str = build_svg(stage, is_done, icons)
            svg_path = os.path.join(svg_dir, base + '.svg')
            with open(svg_path, 'w', encoding='utf-8') as f:
                f.write(svg_str)
            if do_png:
                img = render_png(svg_str)
                png_path = os.path.join(png_dir, base + '.png')
                img.save(png_path)
                print('  %-22s stage %d -> %s' % (wonder_id, i, png_path))
            else:
                print('  %-22s stage %d -> %s' % (wonder_id, i, svg_path))

def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return
    index = gencore.load_wonders()
    if argv[0] == '--list':
        for wid in index:
            print(wid)
        return

    icons = gencore.IconLib()
    svg_dir = os.path.join('out', 'svg')
    png_dir = os.path.join('out', 'png')
    os.makedirs(svg_dir, exist_ok=True)
    os.makedirs(png_dir, exist_ok=True)

    do_png = '--no-png' not in argv
    argv = [a for a in argv if a != '--no-png']

    ids = list(index) if argv[0] == '--all' else argv
    print('Building %d wonder(s):' % len(ids))
    for wid in ids:
        build_one(wid, index, svg_dir, png_dir, icons, do_png=do_png)

if __name__ == '__main__':
    main(sys.argv[1:])
