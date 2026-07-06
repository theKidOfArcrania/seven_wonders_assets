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
import os
import re
import sys
import base64
from io import BytesIO

import yaml

ILLUS_CACHE_DIR = 'illustration_cache'

# ---------------------------------------------------------------------------
# Canvas / layout geometry (matches the hand-tuned template)
# ---------------------------------------------------------------------------
W, H = 570, 870
CORNER_R = 24

BANNER_Y0, BANNER_Y1 = 22, 56
MEANDER_Y = 238          # centre of the fret band that caps the top panel
ILLUS_Y = 247            # illustration region starts here

BENEFIT_CX, BENEFIT_CY = 285, 140   # central benefit medallion
BENEFIT_R = 56

COST_BW = 66             # width of the left-hand cost ribbon (8px margins)
COL_GAP = 8              # horizontal margin between the cost and chain-in columns
COST_R = 23              # cost badge radius

MINI_CARD_H = 1.68
MINI_CARD_W = 1.2

ICONS_SRC = 'seven_wonders_icons.svg'
SYM_NATIVE_D = 72.0                  # emblem shapes drawn at radius ~36 in a 100 box

# ---------------------------------------------------------------------------
# Per-colour top-panel palettes: (edge, base, centre)
# ---------------------------------------------------------------------------
COLORS = {
    'brown':  ('#5c2a14', '#7d391e', '#8a4526'),
    'grey':   ('#68686a', '#909195', '#a2a9b3'),
    'blue':   ('#00608f', '#0078b8', '#0086d7'),
    'yellow': ('#bc7a05', '#d79a0a', '#e7a709'),
    'red':    ('#a61717', '#dc2c2c', '#ff1f41'),
    'green':  ('#3a4a10', '#4f6316', '#528622'),
    'purple': ('#422452', '#5a3570', '#6a4382'),
}

# Card type colour -> the tinted emblem drawn inside an effect card glyph.
CARD_TYPE_SYM = {
    'brown': 'square', 'grey': 'diamond', 'blue': 'tablet',
    'yellow': 'circle', 'red': 'cross_x', 'green': 'triangle',
    'purple': 'star',
}

BANNER = '#cdc0b0'
BANNER_DK = '#9c8f7d'
OUTLINE = '#2b2620'
CREAM = '#d8cab2'
BG = '#c1c6d4'

# ---------------------------------------------------------------------------
# Render-key -> inkscape:label in seven_wonders_icons.svg. Anything not listed
# (or absent from the sheet) renders as a black-box placeholder. Native centres
# are no longer hand-measured: IconLib.measure() renders each element alone and
# reads its alpha bounding box, so adding an icon just means adding a label here.
# ---------------------------------------------------------------------------
ICON_GROUPS = {
    'wood': 'Wood', 'clay': 'Brick', 'stone': 'Stone', 'ore': 'Ore',
    'vp': 'VP', 'slash': 'Slash',
    'loom': 'Loom', 'glass': 'Glass', 'papyrus': 'Papyrus',
    'arrow_right': 'ArrowRight', 'arrow_left': 'ArrowLeft',
    'coin': 'Coin', 'shield': 'Army',
    'sci_compass': 'Compass', 'sci_gear': 'Gear', 'sci_tablet': 'Tablet',
    'wonder': 'PartialWonder', 'wonder_full': 'FullWonder',
}
# every medallion is scaled by 2r/ICON_NATIVE_D
ICON_NATIVE_D = 216.0
# chain-symbol art fills only ~half the native box the resource medallions do,
# so scale it up to render at the same visual size as the other medallions.
CHAIN_ART_SCALE = 1.5
# per-key art-scale multiplier for icons whose art fills less of the native box
# than a resource medallion (so they'd otherwise render small at radius r).
ICON_ART_SCALE = {'wonder': 1.3, 'wonder_full': 1.3}

# Chain 'building power' symbols are extracted straight from the sheet by their
# inkscape:label (which equals the value used in cards.yaml). Any chain symbol a
# card references that is not present in the sheet renders as a placeholder disc.

# Pre-drawn 'entity group' bars: a horizontal strip of resource medallions for a
# whole category (raw materials / manufactured goods), used by the trade powers.
# Position + size are auto-measured; this set just gates has_entity().
ENTITY_GROUPS = {'RawResources', 'ManufacturedGoods'}

# short label + fill for badges we have no art for
PLACEHOLDER_BADGES = {
    'coin':    '$',
    'shield':  'SH',
}


# ---------------------------------------------------------------------------
# Icon library: load the source once, pull out the <defs> and the groups we use
# ---------------------------------------------------------------------------
class IconLib:
    # namespaces the extracted fragments may reference (inkscape/sodipodi attrs)
    _NS = ('xmlns="http://www.w3.org/2000/svg" '
           'xmlns:xlink="http://www.w3.org/1999/xlink" '
           'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"')

    def __init__(self, path=ICONS_SRC):
        text = open(path, encoding='utf-8').read()
        self.text = text
        defs = text[text.index('<defs'):text.index('</defs>') + 7]
        self.defs_inner = defs[defs.index('>') + 1:defs.rindex('<')]
        self._cache = {}
        self._center_cache = {}

    # -- extraction -------------------------------------------------------
    def _extract_by_label(self, label):
        '''Extract the element carrying inkscape:label="<label>" - either a
        balanced <g>...</g> group or a self-closing <path .../>.'''
        text = self.text
        p = text.index('inkscape:label="%s"' % label)
        gs = max(text.rfind('<g', 0, p), text.rfind('<path', 0, p))
        tagend = text.index('>', gs)
        if text[tagend - 1] == '/' or text[gs:gs + 5] == '<path':
            return text[gs:tagend + 1]
        tag = re.compile(r'<(/?)g\b[^>]*?(/?)>')
        depth = 0
        for m in tag.finditer(text, gs):
            if m.group(2) == '/':
                continue
            depth += 1 if m.group(1) == '' else -1
            if depth == 0:
                return text[gs:m.end()]
        raise ValueError('unbalanced group for label %s' % label)

    def by_label(self, label):
        if label not in self._cache:
            self._cache[label] = self._extract_by_label(label)
        return self._cache[label]

    def has_label(self, label):
        return ('inkscape:label="%s"' % label) in self.text

    # -- geometry (auto-measured native bounding box) ---------------------
    def measure(self, label):
        '''Return (cx, cy, w, h) in native units of `label`'s rendered art,
        computed by rendering the element alone and reading its alpha bbox.
        Cached, so each icon is measured at most once per run.'''
        if label not in self._center_cache:
            import io
            import resvg_py
            from PIL import Image
            scale = 0.5
            sw, sh = int(3000 * scale), int(4000 * scale)
            doc = ('<svg %s width="%d" height="%d" viewBox="0 0 3000 4000">'
                   '<defs>%s</defs>%s</svg>'
                   % (self._NS, sw, sh, self.defs_inner, self.by_label(label)))
            png = bytes(resvg_py.svg_to_bytes(svg_string=doc, width=sw, height=sh))
            bb = Image.open(io.BytesIO(png)).convert('RGBA').getchannel('A').getbbox()
            if not bb:
                raise ValueError('empty render for label %s' % label)
            x0, y0, x1, y1 = bb
            self._center_cache[label] = ((x0 + x1) / 2 / scale, (y0 + y1) / 2 / scale,
                                         (x1 - x0) / scale, (y1 - y0) / scale)
        return self._center_cache[label]

    # -- placement --------------------------------------------------------
    def _emit(self, label, cx, cy, s):
        ncx, ncy, _, _ = self.measure(label)
        return ('<g transform="translate(%.2f,%.2f) scale(%.5f) '
                'translate(%.2f,%.2f)">%s</g>'
                % (cx, cy, s, -ncx, -ncy, self.by_label(label)))

    def has(self, key):
        return key in ICON_GROUPS and self.has_label(ICON_GROUPS[key])

    def place(self, key, cx, cy, r):
        '''Emit medallion `key` scaled to radius `r`, centred on (cx, cy).'''
        s = (2 * r) / ICON_NATIVE_D * ICON_ART_SCALE.get(key, 1.0)
        return self._emit(ICON_GROUPS[key], cx, cy, s)

    def has_chain(self, name):
        return self.has_label(name)

    def place_chain(self, name, cx, cy, r):
        '''Emit chain symbol `name` (a raw inkscape:label) at radius `r`. Chain
        art fills only ~half the native box the medallions do, so an extra
        CHAIN_ART_SCALE brings it up to medallion size.'''
        return self._emit(name, cx, cy, (2 * r) / ICON_NATIVE_D * CHAIN_ART_SCALE)

    def has_entity(self, name):
        return name in ENTITY_GROUPS and self.has_label(name)

    def entity_size(self, name, r):
        '''On-card (w, h) of entity `name` when placed at medallion radius r.'''
        _, _, nw, nh = self.measure(name)
        f = (2 * r) / ICON_NATIVE_D
        return nw * f, nh * f

    def entity_width(self, name, r):
        return self.entity_size(name, r)[0]

    def place_entity(self, name, cx, cy, r):
        '''Emit an entity-group bar (RawResources / ManufacturedGoods) at the
        same medallion scale as every other icon, centred on (cx, cy).'''
        return self._emit(name, cx, cy, (2 * r) / ICON_NATIVE_D)


# ---------------------------------------------------------------------------
# Card-type emblems (the little shape on the title-scroll ends and the pale
# emblem tinted inside a {card: COLOUR} glyph). Drawn procedurally in a 100x100
# box centred on (50,50) with a native "radius" of ~36 (see SYM_NATIVE_D).
# ---------------------------------------------------------------------------
_EMBLEM_SHAPES = {
    'square':   '<rect x="18" y="18" width="64" height="64" rx="7"/>',
    'diamond':  '<polygon points="50,12 88,50 50,88 12,50"/>',
    'tablet':   '<rect x="33" y="14" width="34" height="72" rx="5"/>',
    'circle':   '<circle cx="50" cy="50" r="34"/>',
    'cross_x':  ('<rect x="40" y="10" width="20" height="80" rx="6" '
                 'transform="rotate(45 50 50)"/>'
                 '<rect x="40" y="10" width="20" height="80" rx="6" '
                 'transform="rotate(-45 50 50)"/>'),
    'triangle': '<polygon points="50,13 87,83 13,83"/>',
    'star':     ('<polygon points="50,12 59.4,37.1 86.1,38.3 65.2,54.9 72.3,80.7 '
                 '50,66 27.7,80.7 34.8,54.9 13.9,38.3 40.6,37.1"/>'),
}


def card_emblem(name, cx, cy, r, fill='#6f6558', stroke='#3f382c',
                stroke_width=3, opacity=1.0):
    '''Draw a card-type emblem `name` at radius `r`, centred on (cx, cy).
    Unknown names render nothing.'''
    shape = _EMBLEM_SHAPES.get(name)
    if shape is None:
        return ''
    s = (2 * r) / SYM_NATIVE_D
    return ('<g opacity="%g" fill="%s" stroke="%s" stroke-width="%g" '
            'stroke-linejoin="round" transform="translate(%.2f,%.2f) scale(%.5f) '
            'translate(-50,-50)">%s</g>'
            % (opacity, fill, stroke, stroke_width, cx, cy, s, shape))


_RES_ALIASES = {
    'wood': 'wood', 'stone': 'stone', 'ore': 'ore', 'clay': 'clay',
    'brick': 'clay', 'glass': 'glass', 'papyrus': 'papyrus', 'scroll': 'papyrus',
    'loom': 'loom', 'textile': 'loom', 'cloth': 'loom',
    'coin': 'coin', 'coins': 'coin',
}


def parse_cost(raw):
    '''Return a flat list of resource names (one per badge). Empty for free.

    build_cost is a {resource: count} mapping in cards.yaml (empty = free).
    '''
    if not raw:
        return []

    badges = []

    def add(name, count):
        name = _RES_ALIASES.get(str(name).strip().lower())
        if name:
            badges.extend([name] * int(count))

    if isinstance(raw, dict):
        for name, count in raw.items():
            add(name, count)
        return badges

    # legacy natural-language fallback ('1 wood, 2 ore' / '1 each of ...')
    s = str(raw).strip().lower()
    if s in ('free', 'unknown', 'none', ''):
        return []
    m = re.match(r'\d+\s+each of\s+(.*)', s)
    if m:
        for part in m.group(1).split(','):
            add(part.strip(), 1)
        return badges
    for seg in s.split(','):
        seg = seg.strip()
        m = re.match(r'(\d+)\s+([a-z]+)', seg)
        if m:
            add(m.group(2), int(m.group(1)))
    return badges


# ---------------------------------------------------------------------------
# SVG fragment builders
# ---------------------------------------------------------------------------
def meander_path(x0, x1, ycenter, period, height):
    sx = period / 16.0
    sy = height / 12.0
    top = ycenter - height / 2.0
    tile = [(0, 4), (0, 12), (12, 12), (12, 0), (4, 0), (4, 8), (8, 8), (8, 4)]
    n = int((x1 - x0) / period) + 4
    pts, x = [], x0 - period
    for _ in range(n):
        for u, v in tile:
            pts.append((x + u * sx, top + v * sy))
        x += period
    pts.append((x, top + 4 * sy))
    return 'M ' + ' L '.join('%.1f,%.1f' % p for p in pts)


def esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def placeholder_box(cx, cy, r, label, fontsize=None):
    '''A gray rounded-square placeholder with a small white label.'''
    d = 2 * r
    fs = fontsize if fontsize else max(9, int(r * 0.9))
    return (
        '<g>'
        '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" '
        'fill="#555555" stroke="#ffffff" stroke-width="1.2"/>'
        '<text x="%.1f" y="%.1f" text-anchor="middle" '
        'font-family="Arial, sans-serif" font-size="%d" font-weight="700" '
        'paint-order="stroke" stroke="#ffffff" stroke-width="2" '
        'fill="#000000">%s</text>'
        '</g>'
        % (cx - r, cy - r, d, d, r * 0.35,
           cx, cy + fs * 0.35, fs, esc(label))
    )

def hang_ribbon(x0, slots, ribbon_color, icons):
    '''Draw a tapered cost-style ribbon column hanging from the meander, its
    left edge at x0, carrying a vertical stack of badges.
    slots: list of (kind, name) where kind in {'res', 'chain'}.'''
    if not slots:
        return ''
    d = 2 * COST_R
    shade = 4                         # right-edge shadow width
    cx = x0 + (COST_BW - shade) / 2.0  # centre on the lit ribbon body
    gap = (COST_BW - shade) / 2.0 - COST_R   # vertical gap == left/right margin
    top = ILLUS_Y - 2

    ys = []                           # vertical centres, one per slot
    y = top + gap + COST_R
    for _ in slots:
        ys.append(y)
        y += d + gap
    bottom = ys[-1] + COST_R + gap
    # frilled, tapered tail hanging below the content area
    step = 7                        # how far each side tapers in at the tail
    tail_h = 18                     # tail length below the content area
    notch = 9                       # depth of the central upward notch
    tb = bottom + tail_h
    L, R = x0, x0 + COST_BW

    def ribbon_d(dx=0.0, dy=0.0):
        return ('M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f '
                'L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z') % (
            L + dx, top + dy,
            R + dx, top + dy,
            R + dx, bottom + dy,
            R - step + dx, tb + dy,
            (L + R) / 2.0 + dx, tb - notch + dy,
            L + step + dx, tb + dy,
            L + dx, bottom + dy)

    parts = []
    # soft drop shadow behind the whole ribbon
    parts.append('<path d="%s" fill="#00000055"/>' % ribbon_d(2, 3))
    # ribbon body + tail
    parts.append('<path d="%s" fill="%s"/>' % (ribbon_d(), ribbon_color))
    # right-edge shading down the straight body for a bit of relief
    parts.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" fill="#00000033"/>'
                 % (R - shade, top, shade, bottom - top))
    # subtle darker shading across the tapered tail
    parts.append('<path d="%s" fill="#00000022"/>' % (
        'M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z' % (
            L, bottom, R, bottom, R - step, tb, (L + R) / 2.0, tb - notch,
            L + step, tb, L, bottom)))
    shadows, glyphs = [], []
    for (kind, name), cy in zip(slots, ys):
        if kind == 'chain':
            # chain-in symbol: rendered larger, with no drop shadow
            glyphs.append(_chain_glyph(icons, name, cx, cy, COST_R * 1.3))
            continue
        shadows.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="#000000" '
                       'opacity="0.42" filter="url(#softshadow)"/>'
                       % (cx + 3, cy + 4, COST_R))
        if name == 'coin':
            glyphs.append(_coin_badge(icons, cx, cy, COST_R, 1))
        elif name in ICON_GROUPS:
            glyphs.append(icons.place(name, cx, cy, COST_R))
        else:
            glyphs.append(placeholder_box(cx, cy, COST_R - 1,
                                          PLACEHOLDER_BADGES.get(name, name[:2].upper())))
    parts.extend(shadows)
    parts.extend(glyphs)
    return '\n'.join(parts)


def cost_banner(icons, badges, ribbon_color):
    '''Resource build-cost badges on a ribbon hanging down the left edge.'''
    return hang_ribbon(0, [('res', b) for b in badges], ribbon_color, icons)


def chain_in_column(icons, chain_in, ribbon_color, has_costs):
    '''The chain-in symbol (free-build key) in its own separate ribbon column,
    mirroring the physical card: it sits just to the right of the resource
    cost column, at the top of the illustration.'''
    if not chain_in:
        return ''
    x0 = COST_BW + COL_GAP if has_costs else 0
    return hang_ribbon(x0, [('chain', chain_in)], ribbon_color, icons)


def chain_out_badges(icons, chain_out):
    '''The chain-out symbols (up to two) this card grants, rendered large and
    unframed at the top-right of the coloured top panel: two symbols together
    span most of its height, mirroring the physical card.'''
    if not chain_out:
        return ''
    syms = chain_out[:2]
    cx = 512.0
    cy = 102.0
    r = 42.0
    out = []
    for sym in syms:
        out.append(_chain_glyph(icons, sym, cx, cy, r))
        cy += r * 2 - 4
    return '\n'.join(out)

def _res_shadow(cx, cy, r):
    '''Flat drop-shadow that sits directly under a single medallion.'''
    return ('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#160a03" opacity="0.42" '
            'filter="url(#softshadow)"/>' % (cx + 5, cy + 6, r))


def _rect_shadow(cx, cy, w, h, rx):
    '''Rounded-rectangle drop-shadow matching a card glyph.'''
    return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" '
            'fill="#160a03" opacity="0.42" filter="url(#softshadow)"/>'
            % (cx - w / 2 + 5, cy - h / 2 + 6, w, h, rx))

def _wonder_shadow(cx, cy, r):
    '''Drop-shadow matching the wonder/pyramid glyph outline.'''
    s = (2 * r) / SYM_NATIVE_D
    dx, dy = cx + 5, cy + 6
    return ('<g fill="#160a03" opacity="0.42" filter="url(#softshadow)" '
            'transform="translate(%.2f,%.2f) scale(%.5f) translate(-50,-50)">'
            '<polygon points="20,80 34,34 66,34 80,80"/>'
            '<rect x="40" y="18" width="20" height="18" rx="3"/></g>'
            % (dx, dy, s))

def _single_resource(icons, res, cx, cy, r):
    if res in ICON_GROUPS:
        return icons.place(res, cx, cy, r)
    return placeholder_box(cx, cy, r, PLACEHOLDER_BADGES.get(res, res.upper()),
                           fontsize=16)


def _icon_or_box(icons, key, cx, cy, r, label):
    '''Place icon-group `key` if its art exists in the sheet, else a box.'''
    if icons is not None and icons.has(key):
        return icons.place(key, cx, cy, r)
    return placeholder_box(cx, cy, r, label, fontsize=max(9, int(r * 0.7)))

def _badge_number(cx, cy, n):
    return ('<text x="%.1f" y="%.1f" text-anchor="middle" '
            'font-family="\'Times New Roman\', Times, serif" font-size="65" '
            'font-weight="700" paint-order="stroke" fill="#000000" '
            'stroke="#ffffff" stroke-width="3.5">%s</text>'
            % (cx + 2, cy + 22, n))

def _glyph_number(cx, cy, n, rr):
    '''A small number overlaid on a glyph (coin/vp/shield), scaled to fit.'''
    fs = rr * 1.5
    return ('<text x="%.1f" y="%.1f" text-anchor="middle" '
            'font-family="\'Times New Roman\', Times, serif" font-size="%.1f" '
            'font-weight="700" paint-order="stroke" fill="#000000" '
            'stroke="#ffffff" stroke-width="%.1f">%s</text>'
            % (cx, cy + fs * 0.35, fs, max(1.6, rr * 0.075), n))

def _science_icon(icons, name, cx, cy, r):
    name = 'sci_' + str(name)
    return _icon_or_box(icons, name, cx, cy, r, str(name)[:3].upper())

def _vp_badge(icons, cx, cy, r, n):
    '''A small VP laurel graphic carrying a number (the per-card VP value).'''
    return (_res_shadow(cx, cy, r) + icons.place('vp', cx, cy, r) +
            _glyph_number(cx, cy, n, r))


def _coin_badge(icons, cx, cy, r, n):
    '''A small coin graphic carrying a number (the per-card coin value).'''
    return (_res_shadow(cx, cy, r) + _icon_or_box(icons, 'coin', cx, cy, r, '$') +
            _glyph_number(cx, cy, n, r))

def _card_glyph(cx, cy, rr, color, icons=None):
    '''A stylised game card in the given type colour: a rounded rectangle with a
    white border and a pale tinted card-type emblem'''
    edge, base, ctr = COLORS.get(color, COLORS['grey'])
    w, h = rr * MINI_CARD_W, rr * MINI_CARD_H
    s = _rect_shadow(cx, cy, w, h, rr * 0.2)
    s += ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" '
          'fill="%s" stroke="#f4ecd8" stroke-width="%.1f"/>'
          % (cx - w / 2, cy - h / 2, w, h, rr * 0.2, ctr, max(2.2, rr * 0.16)))
    sym = CARD_TYPE_SYM.get(color)
    if sym is not None:
        s += card_emblem(sym, cx, cy, w / 5.0, fill='#f4ecd8',
                         stroke='none', stroke_width=0, opacity=0.65)
    return s

def _wonder_inset(icons, tok, cx, cy, r):
    icon = 'wonder_full' if tok.get('wonder') == 'full' else 'wonder'
    parts = [
        _wonder_shadow(cx, cy, r),
        _icon_or_box(icons, icon, cx, cy, r, 'WON'),
    ]
    br = r * 0.66
    anchor = r * 1.3
    coy = cy + r * .8
    if 'coin' in tok:
        parts.append(_coin_badge(icons, cx - anchor, coy, br, tok['coin']))
    if 'vp' in tok:
        parts.append(_vp_badge(icons, cx + anchor, coy, br, tok['vp']))
    return '\n'.join(parts)

def _card_inset(icons, tok, card, cx, cy, rr):
    '''A normal-size Age III commercial glyph: a card (or wonder) with the coin
    inset in its lower-left corner and the VP laurel in its lower-right corner.
    `tok` is a single mapping carrying card/wonder plus coin and vp values.'''
    w, h = rr * MINI_CARD_W, rr * MINI_CARD_H
    parts = [_card_glyph(cx, cy, rr, card, icons)]

    anchor = w * 0.7
    br = rr * 0.6
    coy = cy + h / 2 - br * 0.2
    if 'coin' in tok:
        parts.append(_coin_badge(icons, cx - anchor, coy, br, tok['coin']))
    if 'vp' in tok:
        parts.append(_vp_badge(icons, cx + anchor, coy, br, tok['vp']))
    return '\n'.join(parts)

def _effect_item(icons, tok, cx, cy, rr):
    '''Render one structured composition token (with its own drop shadow).

    Token is a mapping, one of:
      {res: RESOURCE}
      {coin: N} {vp: N} {shield: N}
      {card: COLOR} {card: COLOR, vp: N} {card: COLOR, coin: N}
      {wonder: true} {wonder: true, vp: N} {wonder: full, vp: N}
      {sci: compass|gear|tablet}
    '''
    if tok == 'shield':
        return _res_shadow(cx, cy, rr) + _icon_or_box(icons, 'shield', cx, cy, rr, 'SH')
    elif tok == 'slash':
        raise ValueError
    if 'res' in tok:
        prod = _RES_ALIASES.get(tok['res'], tok['res']) 
        return _res_shadow(cx, cy, rr) + _single_resource(icons, prod, cx, cy, rr)
    if 'card' in tok:
        return _card_inset(icons, tok, tok['card'], cx, cy, rr)
    if 'wonder' in tok:
        return _wonder_inset(icons, tok, cx, cy, rr)
    if 'coin' in tok:
        return _coin_badge(icons, cx, cy, rr, tok['coin'])
    if 'vp' in tok:
        return _vp_badge(icons, cx, cy, rr, tok['vp'])
    if 'sci' in tok:
        return _science_icon(icons, tok['sci'], cx, cy, rr)
    shadow = _res_shadow(cx, cy, rr)
    return shadow + placeholder_box(cx, cy, rr, '?')

def _trade_medallion(icons, layout, cx, cy):
    '''Trade power (marketplace / trading posts): a resource entity-group bar
    with a coin stacked above it, flanked by neighbour arrows on the sides.'''
    arrows = layout.get('arrows', 'both')
    row = layout.get('tokens', []) or []
    entity = next((t['entity'] for t in row if 'entity' in t), None)
    coin_n = next((t['coin'] for t in row if 'coin' in t), 1)

    er = 54.0                       # medallion radius of the bar's inner icons
    ecy = cy + 20
    parts = []
    # coin sits above the bar
    cr = 27.0
    ccy = cy - 40
    parts.append(_coin_badge(icons, cx, ccy, cr, coin_n))
    # the entity-group bar itself
    if entity and icons.has_entity(entity):
        ew, eh = icons.entity_size(entity, er)
        parts.append(_rect_shadow(cx, ecy, ew, eh, eh / 2.0))
        parts.append(icons.place_entity(entity, cx, ecy, er))
    else:
        ew = 150.0
        parts.append(placeholder_box(cx, ecy, ew / 2, str(entity or '?')))
    # neighbour arrows on the two sides of the bar, at the same size as the
    # arrows on every other effect card (rr*1.44 with the standard rr=42)
    ar = 42.0 * 1.44
    half = ew / 2 + ar - 20
    if arrows in ('all', 'both', 'left'):
        parts.append(icons.place('arrow_left', cx - half, ecy, ar))
    if arrows in ('all', 'both', 'right'):
        parts.append(icons.place('arrow_right', cx + half, ecy, ar))
    return '\n'.join(parts)

def _effect_medallion(icons, layout, cx, cy, r):
    '''Compose a commercial / guild power from a structured `medallion` layout:
      { arrows: both|left|right|none|all, tokens: [ {...}, ... ] }
    optionally flanked by flat neighbour arrows and/or a down arrow.'''
    arrows = layout.get('arrows', 'none')
    row = layout.get('tokens', []) or []

    if any('entity' in t for t in row):
        return _trade_medallion(icons, layout, cx, cy)

    # a down arrow (vineyard / bazar) pushes the glyph set up to make room below
    dy = -24.0 if arrows == 'all' else 0.0
    gy = cy + dy

    slashes = sum(1 for tok in row if tok == 'slash')
    n = len(row) - slashes
    has_arrows = arrows in ('all', 'both', 'left', 'right')
    gap = 14

    # Every effect glyph renders at the same target size for visual consistency
    # across cards; only shrink if a crowded row would otherwise overflow.
    avail = 300.0 if has_arrows else 400.0
    rr = min(r, (avail - (n - 1) * gap) / (2.0 * n))
    sep = 2 * rr + gap
    x0 = cx - sep * (n - 1) / 2.0

    parts = []
    if has_arrows:
        ar = rr * 1.44
        half = sep * (n - 1) / 2.0 + rr + ar + 8
        if arrows in ('all', 'both', 'left'):
            parts.append(icons.place('arrow_left', cx - half, gy, ar))
        if arrows in ('all', 'both', 'right'):
            parts.append(icons.place('arrow_right', cx + half, gy, ar))

    if arrows == 'all':
        parts.append(_chain_glyph(icons, 'ArrowDown', cx, gy + rr * 1.6, rr))

    i = 0
    for tok in row:
        if tok == 'slash':
            parts.append(icons.place('slash', x0 + (i - 1) * sep + sep / 2, gy, rr * 0.85))
        else:
            parts.append(_effect_item(icons, tok, x0 + i * sep, gy, rr))
            i += 1
    return '\n'.join(parts)


def _chain_glyph(icons, name, cx, cy, r):
    '''Render one chain symbol: the real art if we have it, else a small
    placeholder disc bearing an abbreviation (symbol not yet drawn).'''
    if icons is not None and icons.has_chain(name):
        return icons.place_chain(name, cx, cy, r)
    lbl = ''.join(w[0] for w in name.split('_'))[:3].upper()
    return ('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#e7dcc4" '
            'stroke="#4a4130" stroke-width="1.6"/>'
            '<text x="%.1f" y="%.1f" text-anchor="middle" '
            'font-family="Arial, sans-serif" font-size="%.1f" font-weight="700" '
            'fill="#4a4130">%s</text>'
            % (cx, cy, r, cx, cy + r * 0.34, r * 0.8, esc(lbl)))

def banner(name, end_symbol):
    s = []
    by0, by1 = BANNER_Y0, BANNER_Y1
    cy = (by0 + by1) / 2.0
    BX0, BX1 = 44, 526            # where the full-height ribbon body ends
    xl, xr = 20, 550             # swallowtail prong tips
    step, notch = 7, 11          # vertical taper of prongs / depth of notch

    def path(dx=0.0, dy=0.0):
        pts = [
            (xl, by0 + step), (BX0, by0), (BX1, by0), (xr, by0 + step),
            (xr - notch, cy), (xr, by1 - step), (BX1, by1), (BX0, by1),
            (xl, by1 - step), (xl + notch, cy),
        ]
        return 'M ' + ' L '.join('%.1f,%.1f' % (x + dx, y + dy)
                                 for x, y in pts) + ' Z'

    # soft drop shadow behind the whole ribbon
    s.append('<path d="%s" fill="#00000044"/>' % path(1.5, 2.5))
    # ribbon body + swallowtail ends
    s.append('<path d="%s" fill="%s"/>' % (path(), BANNER))
    # darker underside on each forked tail for a folded-ribbon look
    s.append('<path d="M %d,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %d,%.1f Z" '
             'fill="#00000018"/>'
             % (BX0, by0, xl, by0 + step, xl + notch, cy,
                xl, by1 - step, BX0, by1))
    s.append('<path d="M %d,%.1f L %.1f,%.1f L %.1f,%.1f L %.1f,%.1f L %d,%.1f Z" '
             'fill="#00000018"/>'
             % (BX1, by0, xr, by0 + step, xr - notch, cy,
                xr, by1 - step, BX1, by1))
    # bottom shading along the body
    s.append('<rect x="%d" y="%d" width="%d" height="3" fill="%s" opacity="0.55"/>'
             % (BX0, by1 - 1, BX1 - BX0, BANNER_DK))
    # procedural card-type end-symbol near each scroll end
    for cx in (62, 508):
        s.append(card_emblem(end_symbol, cx, cy, 9))
    s.append('<text x="285" y="%.1f" text-anchor="middle" '
             'font-family="Georgia, \'Times New Roman\', serif" font-size="20" '
             'font-weight="600" letter-spacing="1" fill="%s">%s</text>'
             % (cy + 7, OUTLINE, esc(name.upper())))
    return '\n'.join(s)

def illustration_layer(card):
    '''Bottom illustration: embed the cached generated art if we have it,
    otherwise fall back to a gray placeholder box.'''
    clip = (
        '<clipPath id="illusclip"><rect x="0.5" y="%d" width="%d" height="%d"/></clipPath>'
        % (ILLUS_Y, W - 1, H - ILLUS_Y)
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

# ---------------------------------------------------------------------------
# Whole-card assembly
# ---------------------------------------------------------------------------
def build_svg(card, ctype, icons):
    color = ctype.get('color', 'grey')
    end_symbol = ctype.get('banner_end_symbol', '')
    edge, base, ctr = COLORS.get(color, COLORS['grey'])
    badges = parse_cost(card.get('build_cost'))
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

    p.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, BG))
    p.append('<rect x="0" y="0" width="%d" height="%d" rx="%d" ry="%d" '
             'fill="url(#panelg)"/>' % (W, H, CORNER_R, CORNER_R))
    p.append('<path d="M2,%d L2,30 M%d,30 L%d,%d" stroke="#00000055" '
             'stroke-width="2.4" fill="none"/>' % (ILLUS_Y, W - 2, W - 2, ILLUS_Y))

    p.append(illustration_layer(card))

    # cost ribbon hangs from the meander down the left edge (behind the fret)
    p.append(cost_banner(icons, badges, base))
    # chain-in symbol gets its own column, just right of the cost ribbon
    p.append(chain_in_column(icons, chain_in, base, bool(badges)))

    # fret band capping the top panel (bleeds off both edges)
    p.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.4" '
             'stroke-linejoin="miter"/>'
             % (meander_path(0, W, MEANDER_Y, 30, 13), CREAM))

    # compose from the structured medallion layout
    medallion = card.get('banner', {}).get('medallion', {})
    p.append(_effect_medallion(icons, medallion, BENEFIT_CX, BENEFIT_CY, BENEFIT_R))

    # chain-out badges: framed, at the top-right of the coloured panel
    p.append(chain_out_badges(icons, chain_out))

    p.append(banner(card.get('name', card['id']), end_symbol))
    p.append('</svg>')
    return '\n'.join(p)


# ---------------------------------------------------------------------------
# cards.yaml loading / flattening
# ---------------------------------------------------------------------------
def load_cards(path='cards.yaml'):
    data = yaml.safe_load(open(path, encoding='utf-8'))
    types = data['card_types']
    index = {}
    for c in data['cards']:
        index[c['id']] = (c, types[c['type']])
    return index


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_png(svg_str):
    import resvg_py
    from PIL import Image, ImageDraw
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg_str, width=W, height=H))
    card = Image.open(BytesIO(png)).convert('RGB')
    mask = Image.new('L', (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1],
                                           radius=CORNER_R, fill=255)
    bg = Image.new('RGB', (W, H), (193, 198, 212))
    return Image.composite(card, bg, mask)


def build_one(card_id, index, svg_dir, png_dir, do_png=True):
    if card_id not in index:
        print('  ! unknown card id: %s' % card_id)
        return
    card, ctype = index[card_id]
    svg_str = build_svg(card, ctype, ICONS)
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


ICONS = None


def main(argv):
    global ICONS
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return
    index = load_cards()
    if argv[0] == '--list':
        for cid in index:
            print(cid)
        return

    ICONS = IconLib()
    svg_dir = os.path.join('out', 'svg')
    png_dir = os.path.join('out', 'png')
    os.makedirs(svg_dir, exist_ok=True)
    os.makedirs(png_dir, exist_ok=True)

    do_png = '--no-png' not in argv
    argv = [a for a in argv if a != '--no-png']

    ids = list(index) if argv[0] == '--all' else argv
    print('Building %d card(s):' % len(ids))
    for cid in ids:
        build_one(cid, index, svg_dir, png_dir, do_png=do_png)


if __name__ == '__main__':
    main(sys.argv[1:])
