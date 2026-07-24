"""Split a building outline into stacked stage segments from a hand-edited SVG.

Phase 2 of the wonder outline pipeline (see trace_outline.py for phase 1):

  1. build a COMMITTED, hand-editable `<id>_segments.svg` (build_segments_svg):
     it embeds the building PNG as a reference backdrop, draws the traced
     outline for guidance, and pre-lays N-1 RED divider `<line>`s at equal
     vertical thirds/quarters (N = the wonder's stage count) inside a
     `#dividers` group. The artist drags the line endpoints to any arbitrary
     position/angle in a vector editor, then re-runs the pipeline.
  2. compute `<id>_segments.json` (parse_dividers + segment_polygon): read the
     (edited) divider lines back, rasterise the outline, assign every pixel to a
     band by counting how many dividers it lies below, then re-trace each band
     into its own polygon(s) using the phase-1 tracer.

Segments are emitted bottom-to-top (stage 1 = the bottom band) to match the
"one tier per stage, built bottom-to-top" convention in cards.yaml.

Pure numpy + PIL + xml.etree (no scipy/opencv), matching trace_outline.py.
"""
import math
import re
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw

import trace_outline

# Default cut styling / geometry for the generated SVG.
_OUTLINE_STROKE = "#33dd33"
_DIVIDER_STROKE = "#ff2020"
_SPAN_MARGIN = 0.05          # extend divider lines this fraction of W past the bbox
MIN_AREA_FRAC = 0.0015       # drop band components smaller than this fraction of
#                              the building area (cut slivers)


# ---------------------------------------------------------------------------
# SVG generation
# ---------------------------------------------------------------------------
def build_segments_svg(href, outline_data, n_segments):
    """Return an editable segments-SVG string for one building.

    * `href`         - relative path to the building PNG (same directory).
    * `outline_data` - the phase-1 outline dict {width, height, points}.
    * `n_segments`   - number of stacked stages (>=1); N-1 divider lines.
    """
    W, H = outline_data["width"], outline_data["height"]
    pts = outline_data["points"]
    xs = [p[0] * W for p in pts]
    ys = [p[1] * H for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    margin = _SPAN_MARGIN * W
    lx0 = max(0.0, x0 - margin)
    lx1 = min(float(W), x1 + margin)

    n_segments = max(1, int(n_segments))
    poly = " ".join("%.2f,%.2f" % (p[0] * W, p[1] * H) for p in pts)

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append("<!--")
    out.append("  7 Wonders stage-segment definition (EDIT ME).")
    out.append("  The %d red line(s) in #dividers split the building into %d stacked"
               % (n_segments - 1, n_segments))
    out.append("  stages, bottom-to-top. Drag the line endpoints to any position or")
    out.append("  angle; keep them as <line> elements inside the #dividers group.")
    out.append("  Then recompute the JSON:  python gen_all_illustrations.py <id>")
    out.append("  (the edited SVG is newer than the JSON, which re-triggers the")
    out.append("  segment recompute automatically - no force flag needed).")
    out.append("-->")
    out.append('<svg xmlns="http://www.w3.org/2000/svg" '
               'xmlns:xlink="http://www.w3.org/1999/xlink" '
               'width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H))
    out.append('  <image xlink:href="%s" x="0" y="0" width="%d" height="%d"/>'
               % (href, W, H))
    out.append('  <polygon id="outline-ref" points="%s" fill="none" stroke="%s" '
               'stroke-width="2" stroke-opacity="0.7"/>' % (poly, _OUTLINE_STROKE))
    out.append('  <g id="dividers" fill="none" stroke="%s" stroke-width="4" '
               'stroke-linecap="round">' % _DIVIDER_STROKE)
    for k in range(1, n_segments):
        y = y0 + (y1 - y0) * k / n_segments
        out.append('    <line id="divider-%d" x1="%.2f" y1="%.2f" x2="%.2f" '
                   'y2="%.2f"/>' % (k, lx0, y, lx1, y))
    out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# SVG parsing (divider lines, with affine transform support)
# ---------------------------------------------------------------------------
def _matmul(m, n):
    """Compose two affine transforms (a,b,c,d,e,f); result applies n then m."""
    a, b, c, d, e, f = m
    na, nb, nc, nd, ne, nf = n
    return (a * na + c * nb, b * na + d * nb,
            a * nc + c * nd, b * nc + d * nd,
            a * ne + c * nf + e, b * ne + d * nf + f)


def _parse_transform(s):
    """Parse an SVG `transform` attribute into an affine (a,b,c,d,e,f)."""
    m = (1, 0, 0, 1, 0, 0)
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", s or ""):
        nums = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v != ""]
        if name == "matrix" and len(nums) == 6:
            t = tuple(nums)
        elif name == "translate" and nums:
            t = (1, 0, 0, 1, nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale" and nums:
            sx = nums[0]
            t = (sx, 0, 0, nums[1] if len(nums) > 1 else sx, 0, 0)
        elif name == "rotate" and nums:
            a = math.radians(nums[0])
            r = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                t = _matmul(_matmul((1, 0, 0, 1, cx, cy), r),
                            (1, 0, 0, 1, -cx, -cy))
            else:
                t = r
        else:
            continue
        m = _matmul(m, t)
    return m


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_dividers(svg_text):
    """Extract divider lines from a segments SVG as [((x1,y1),(x2,y2)), ...].

    Lines inside the `#dividers` group are used (falling back to any `<line>`
    whose id starts with "divider"). Ancestor + element `transform`s are applied
    so lines moved in a vector editor still resolve to the right coordinates.
    """
    root = ET.fromstring(svg_text)
    parents = {c: p for p in root.iter() for c in p}

    group = None
    for el in root.iter():
        if el.get("id") == "dividers":
            group = el
            break

    if group is not None:
        lines = [el for el in group.iter() if _local(el.tag) == "line"]
    else:
        lines = [el for el in root.iter()
                 if _local(el.tag) == "line"
                 and (el.get("id") or "").startswith("divider")]

    dividers = []
    for ln in lines:
        chain = []
        e = ln
        while e is not None:
            chain.append(e)
            e = parents.get(e)
        m = (1, 0, 0, 1, 0, 0)
        for el in reversed(chain):          # root -> line
            t = el.get("transform")
            if t:
                m = _matmul(m, _parse_transform(t))
        try:
            x1 = float(ln.get("x1", 0)); y1 = float(ln.get("y1", 0))
            x2 = float(ln.get("x2", 0)); y2 = float(ln.get("y2", 0))
        except (TypeError, ValueError):
            continue
        dividers.append((_apply(m, x1, y1), _apply(m, x2, y2)))
    return dividers


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
def _components(mask, min_area):
    """Yield connected-component bool masks of `mask` with area >= min_area."""
    work = mask.copy()
    comps = []
    while True:
        ys, xs = np.where(work)
        if len(xs) == 0:
            break
        comp = trace_outline._flood(work, (int(ys[0]), int(xs[0])))
        if int(comp.sum()) >= min_area:
            comps.append(comp)
        work &= ~comp
    return comps


def segment_polygon(outline_data, dividers, min_area_frac=MIN_AREA_FRAC,
                    simplify_frac=trace_outline.SIMPLIFY_FRAC):
    """Split the outline into stage bands cut by `dividers`.

    Returns {"width", "height", "outline", "segments": [{"stage", "polygons"}]}
    with all coordinates normalized to 0-1 and stages ordered bottom-to-top, or
    None if the outline is empty.
    """
    W, H = outline_data["width"], outline_data["height"]
    pts = [(p[0] * W, p[1] * H) for p in outline_data["points"]]
    if len(pts) < 3:
        return None

    mimg = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mimg).polygon(pts, fill=255)
    mask = np.asarray(mimg) > 127
    area = int(mask.sum())
    if area == 0:
        return None

    # normalise each divider to x-ascending, then sort top-to-bottom by mean y.
    dvs = []
    for (a, b) in dividers:
        (x1, y1), (x2, y2) = a, b
        if x1 > x2:
            x1, y1, x2, y2 = x2, y2, x1, y1
        dvs.append((x1, y1, x2, y2))
    dvs.sort(key=lambda d: (d[1] + d[3]) / 2.0)

    yy, xx = np.mgrid[0:H, 0:W]
    seg = np.zeros((H, W), dtype=np.int32)
    for (x1, y1, x2, y2) in dvs:
        # signed side of the (x-ascending) line: >0 means below it.
        cross = (x2 - x1) * (yy - y1) - (y2 - y1) * (xx - x1)
        seg += (cross > 0).astype(np.int32)

    n = len(dvs) + 1
    min_area = max(1, int(min_area_frac * area))
    eps = simplify_frac * math.hypot(W, H)

    bands = []                                   # index 0 = top band
    for s in range(n):
        band = mask & (seg == s)
        polys = []
        for comp in _components(band, min_area):
            boundary = trace_outline._moore_trace(comp)
            if len(boundary) < 3:
                continue
            simple = trace_outline._douglas_peucker(boundary, eps)
            polys.append([[round(x / W, 5), round(y / H, 5)]
                          for (x, y) in simple])
        bands.append(polys)

    segments = [{"stage": i + 1, "polygons": polys}
                for i, polys in enumerate(reversed(bands))]   # bottom -> top
    return {"width": W, "height": H, "outline": outline_data["points"],
            "segments": segments}


# ---------------------------------------------------------------------------
# Preview render
# ---------------------------------------------------------------------------
# Distinct per-stage colours (cycled); index 0 = stage 1 (bottom).
SEGMENT_COLORS = [
    (230, 60, 60),      # red
    (60, 140, 235),     # blue
    (70, 200, 110),     # green
    (235, 190, 50),     # amber
    (180, 90, 220),     # purple
    (60, 200, 210),     # teal
]


def render_preview(rgba, seg_data, backdrop=(45, 45, 45), fill_alpha=90):
    """Render a QA preview: the building over a neutral backdrop with each stage
    segment filled and outlined in its own colour (stage 1 = bottom). Returns an
    RGB image the same size as `rgba`.
    """
    rgba = rgba.convert("RGBA")
    W, H = rgba.size
    canvas = Image.new("RGBA", (W, H), backdrop + (255,))
    canvas.alpha_composite(rgba)
    lw = max(2, round(max(W, H) / 400))
    segs = seg_data.get("segments") or []

    # translucent fills first (so overlaps read clearly)...
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fill = ImageDraw.Draw(overlay)
    for seg in segs:
        c = SEGMENT_COLORS[(seg.get("stage", 1) - 1) % len(SEGMENT_COLORS)]
        for poly in seg.get("polygons") or []:
            pts = [(p[0] * W, p[1] * H) for p in poly]
            if len(pts) >= 3:
                fill.polygon(pts, fill=c + (fill_alpha,))
    canvas.alpha_composite(overlay)

    # ...then crisp coloured outlines on top.
    line = ImageDraw.Draw(canvas)
    for seg in segs:
        c = SEGMENT_COLORS[(seg.get("stage", 1) - 1) % len(SEGMENT_COLORS)]
        for poly in seg.get("polygons") or []:
            pts = [(p[0] * W, p[1] * H) for p in poly]
            if len(pts) >= 2:
                line.line(pts + [pts[0]], fill=c + (255,), width=lw)
    return canvas.convert("RGB")
