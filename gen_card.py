'''Build a 7 Wonders card SVG (and PNG) from an entry in cards.yaml.

Design goals
------------
* One generic template drives every card. The card's `type` selects the top-
  panel colour and banner end-symbol (via the `card_types` table in cards.yaml).
* Real hand-drawn icons from seven_wonders_icons.svg are used wherever we have
  them (resources, victory-point, coin, shield, science, wonders, trade bars,
  chain symbols). Native positions/sizes are auto-measured, not hand-coded.
  The card-type emblems (scroll ends + {card} glyphs) are drawn procedurally.
* Everything we do NOT have vector art for is drawn as a labelled placeholder
  box: undrawn chain symbols and any benefit we lack a layout for, plus the
  bottom illustration. This keeps the layout correct while flagging gaps.

Usage
-----
  python gen_card.py <card_id> [more_ids...]   # build specific cards
  python gen_card.py --all                     # build every card in cards.yaml
  python gen_card.py --list                    # list card ids

Outputs: out/svg/<id>.svg and out/png/<id>.png
'''
import base64
import gencore
import io
import os
import resvg_py
import sys
from PIL import Image, ImageDraw

W, H = 570, 870
CORNER_R = 24
ILLUS_Y = 247           # height of the banner
MEANDER_Y = 240          # centre of the fret band that caps the top panel
BENEFIT_CX, BENEFIT_CY = 285, 140   # central benefit medallion
BENEFIT_R = 56

CREAM = '#d8cab2'

ILLUS_CACHE_DIR = 'illustration_cache'

# ---------------------------------------------------------------------------
# Card building
# ---------------------------------------------------------------------------
def illustration_layer(card):
    '''Bottom illustration: embed the cached generated art if we have it,
    otherwise fall back to a gray placeholder box.'''
    clip = (
        '<clipPath id="illusclip"><rect x="0" y="%d" width="%d" height="%d"/></clipPath>'
        % (ILLUS_Y, W, H - ILLUS_Y)
    )
    cid = card['id']
    cache_png = os.path.join(ILLUS_CACHE_DIR, cid + '.png')
    if os.path.exists(cache_png) and os.path.getsize(cache_png) > 0:
        with open(cache_png, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('ascii')
        return (
            clip +
            '<g clip-path="url(#illusclip)">'
            '<image x="0" y="%d" width="%d" height="%d" '
            'preserveAspectRatio="xMidYMid slice" '
            'xlink:href="data:image/png;base64,%s"/>'
            '</g>'
            % (ILLUS_Y, W, H - ILLUS_Y, b64)
        )
    name = card.get('name', cid)
    return (
        clip +
        '<g clip-path="url(#illusclip)">'
        '<rect x="0" y="%d" width="%d" height="%d" fill="#555555"/>'
        '<text x="%d" y="%d" text-anchor="middle" '
        'font-family="Arial, sans-serif" font-size="16" fill="#cfcfcf">'
        'illustration: %s</text>'
        '</g>'
        % (ILLUS_Y, W, H - ILLUS_Y,
           W // 2, (ILLUS_Y + H) // 2, esc(name))
    )

def build_svg(card, ctype, icons):
    color = ctype.get('color', 'grey')
    end_symbol = ctype.get('banner_end_symbol', '')
    edge, base, ctr = gencore.COLORS.get(color, gencore.COLORS['grey'])
    badges = gencore.parse_cost(card.get('build_cost'))
    bnr = card.get('banner') or {}
    chain_in = bnr.get('chain_in_symbol')
    chain_out = bnr.get('chain_out_symbol') or []

    p = []
    p.append('<svg xmlns="http://www.w3.org/2000/svg" '
             'xmlns:xlink="http://www.w3.org/1999/xlink" '
             'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
             'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
             'width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H))
    p.append('<defs>')
    p.append('<linearGradient id="panelg" x1="0%%" y1="0%%" x2="100%%" y2="0%%">'
             '<stop offset="0%%" stop-color="%s"/>'
             '<stop offset="9%%" stop-color="%s"/>'
             '<stop offset="50%%" stop-color="%s"/>'
             '<stop offset="91%%" stop-color="%s"/>'
             '<stop offset="100%%" stop-color="%s"/></linearGradient>'
             % (edge, base, ctr, base, edge))
    p.append('<filter id="softshadow" x="-40%" y="-40%" width="180%" height="180%">'
             '<feGaussianBlur stdDeviation="3.2"/></filter>')
    p.append(icons.defs_inner)
    p.append('</defs>')

    p.append('<rect x="0" y="0" width="%d" height="%d" rx="%d" ry="%d" '
             'fill="url(#panelg)"/>' % (W, H, CORNER_R, CORNER_R))

    p.append(illustration_layer(card))

    # Draw top banner stuff
    p.append(gencore.banner(card.get('name', card['id']), end_symbol))

    # cost ribbon hangs from the meander down the left edge (behind the fret)
    p.append(gencore.cost_banner(ILLUS_Y, icons, badges, base))
    # chain-in symbol gets its own column, just right of the cost ribbon
    p.append(gencore.chain_in_column(ILLUS_Y, icons, chain_in, base, bool(badges)))

    # drop shadow under whole banner
    p.append('<rect x="0" y="%d" width="%d" height="6" fill="#00000055"/>'
             % (ILLUS_Y, W))

    # fret band capping the top panel (bleeds off both edges)
    p.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.4" '
             'stroke-linejoin="miter"/>'
             % (gencore.meander_path(0, W, MEANDER_Y, 30, 13), CREAM))

    # compose from the structured medallion layout
    medallion = card.get('banner', {}).get('medallion', {})
    p.append(gencore.effect_medallion(icons, medallion, BENEFIT_CX, BENEFIT_CY, BENEFIT_R))

    # chain-out badges: framed, at the top-right of the coloured panel
    p.append(gencore.chain_out_badges(icons, chain_out))

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


def build_one(card_id, index, svg_dir, png_dir, icons, do_png=True):
    if card_id not in index:
        print('  ! unknown card id: %s' % card_id)
        return
    card, ctype = index[card_id]
    svg_str = build_svg(card, ctype, icons)
    svg_path = os.path.join(svg_dir, card_id + '.svg')
    with open(svg_path, 'w', encoding='utf-8') as f:
        f.write(svg_str)
    if do_png:
        img = render_png(svg_str)
        png_path = os.path.join(png_dir, card_id + '.png')
        img.save(png_path)
        print('  %-22s -> %s' % (card_id, png_path))
    else:
        print('  %-22s -> %s' % (card_id, svg_path))

def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return
    index = gencore.load_cards()
    if argv[0] == '--list':
        for cid in index:
            print(cid)
        return

    icons = gencore.IconLib()
    svg_dir = os.path.join('out', 'svg')
    png_dir = os.path.join('out', 'png')
    os.makedirs(svg_dir, exist_ok=True)
    os.makedirs(png_dir, exist_ok=True)

    do_png = '--no-png' not in argv
    argv = [a for a in argv if a != '--no-png']

    ids = list(index) if argv[0] == '--all' else argv
    print('Building %d card(s):' % len(ids))
    for cid in ids:
        build_one(cid, index, svg_dir, png_dir, icons, do_png=do_png)

if __name__ == '__main__':
    main(sys.argv[1:])
