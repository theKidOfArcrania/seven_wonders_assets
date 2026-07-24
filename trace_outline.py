"""Trace the silhouette of a keyed building into a simplified outline polygon.

The wonder-building pipeline (see gen_all_illustrations.py / keyout.py) produces
`<id>_building.png`: the monument on a TRANSPARENT canvas at its native position
and scale. This module turns that alpha silhouette into a single closed polygon
outlining the WHOLE building, so the UI can draw the built/unbuilt stage outline.

Pipeline (pure numpy + PIL, no scipy/opencv - matching keyout.py):

  1. alpha -> binary mask (threshold), morphological close to seal thin gaps.
  2. keep only the LARGEST 4-connected component (drops retouch speckle): a
     downsampled BFS locates the biggest blob + a seed, then a full-res flood
     recovers exactly that component.
  3. fill interior holes (reuses keyout.fill_holes).
  4. Moore-neighbor boundary trace -> ordered outer contour (pixels).
  5. Douglas-Peucker simplify -> a handful of vertices.
  6. normalize to 0-1 (x/W, y/H) so the polygon is resolution-independent.

The returned dict is JSON-serialisable:
  {"width": W, "height": H, "points": [[nx, ny], ...]}   # closed implicitly
"""
import collections
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import keyout

# Tuned on the rhodes/gizah references. THRESH is low so faint anti-aliased
# edges (columns, spires) are still captured; the largest-component keep drops
# the resulting scattered speckle so only the monument survives.
THRESH = 8            # alpha threshold (0-255) above which a pixel is building
CLOSE = 7             # morphological close kernel (odd); seals thin gaps
DS = 4               # downsample factor for the largest-component search
SIMPLIFY_FRAC = 0.004  # Douglas-Peucker epsilon as a fraction of the diagonal

# 4-connectivity used for both the downsampled and full-res floods.
_N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))

# 8 neighbours in clockwise order starting due west; used by the Moore tracer.
_CW = ((0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1))


def _flood(mask, seed):
    """Full-resolution 4-connected flood fill; return the component bool mask."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    q = collections.deque([seed])
    out[seed] = True
    while q:
        y, x = q.popleft()
        for dy, dx in _N4:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not out[ny, nx]:
                out[ny, nx] = True
                q.append((ny, nx))
    return out


def _largest_component(mask, ds=DS):
    """Return a bool mask of the largest 4-connected component of `mask`.

    The biggest blob is located on a `ds`-downsampled copy (cheap), then a
    full-resolution flood from a mapped seed recovers it at native detail.
    """
    dm = mask[::ds, ::ds]
    h, w = dm.shape
    seen = np.zeros_like(dm)
    best_area = 0
    best_seed = None
    for sy in range(h):
        for sx in range(w):
            if not dm[sy, sx] or seen[sy, sx]:
                continue
            q = collections.deque([(sy, sx)])
            seen[sy, sx] = True
            area = 0
            while q:
                y, x = q.popleft()
                area += 1
                for dy, dx in _N4:
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and dm[ny, nx] \
                            and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            if area > best_area:
                best_area = area
                best_seed = (sy, sx)
    if best_seed is None:
        return None

    H, W = mask.shape
    fy, fx = best_seed[0] * ds, best_seed[1] * ds
    if not (fy < H and fx < W and mask[fy, fx]):
        # the downsampled seed can land on a hole/edge; snap to the nearest
        # full-res foreground pixel within one downsample cell.
        fy = fx = None
        for r in range(ds + 1):
            cy, cx = best_seed[0] * ds, best_seed[1] * ds
            for yy in range(max(0, cy - r), min(H, cy + r + 1)):
                for xx in range(max(0, cx - r), min(W, cx + r + 1)):
                    if mask[yy, xx]:
                        fy, fx = yy, xx
                        break
                if fy is not None:
                    break
            if fy is not None:
                break
        if fy is None:
            return None
    return _flood(mask, (fy, fx))


def _moore_trace(mask):
    """Moore-neighbour boundary trace of a single component -> [(x, y), ...].

    Returns the outer contour as ordered pixel coordinates, walked clockwise
    starting from the top-most (then left-most) foreground pixel. The polygon is
    open: the closing edge from the last vertex back to the first is implicit.
    """
    # pad by 1 so neighbour lookups never leave the array.
    padded = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), bool)
    padded[1:-1, 1:-1] = mask
    ys, xs = np.where(padded)
    if len(xs) == 0:
        return []
    y0 = int(ys.min())
    x0 = int(xs[ys == y0].min())
    start = (y0, x0)

    def idx_of(c, b):
        for i, (dy, dx) in enumerate(_CW):
            if (c[0] + dy, c[1] + dx) == b:
                return i
        return 0

    boundary = [start]
    c = start
    b = (y0, x0 - 1)          # we arrived at `start` from the west (background)
    guard = padded.size * 8
    steps = 0
    while steps < guard:
        steps += 1
        start_i = idx_of(c, b)
        found = None
        for k in range(1, 9):
            i = (start_i + k) % 8
            n = (c[0] + _CW[i][0], c[1] + _CW[i][1])
            if padded[n[0], n[1]]:
                found = (i, n)
                break
        if found is None:
            break                # isolated pixel
        i, n = found
        b = (c[0] + _CW[(i - 1) % 8][0], c[1] + _CW[(i - 1) % 8][1])
        c = n
        if c == start:
            break
        boundary.append(c)
    # strip the 1px pad back off.
    return [(x - 1, y - 1) for (y, x) in boundary]


def _douglas_peucker(pts, eps):
    """Simplify an open polyline with the Douglas-Peucker algorithm."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        s, e = stack.pop()
        x1, y1 = pts[s]
        x2, y2 = pts[e]
        dx, dy = x2 - x1, y2 - y1
        d2 = dx * dx + dy * dy
        maxd = -1.0
        idx = -1
        for i in range(s + 1, e):
            x, y = pts[i]
            if d2 == 0:
                dist = math.hypot(x - x1, y - y1)
            else:
                # distance from point to the infinite line through (s, e).
                dist = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / math.sqrt(d2)
            if dist > maxd:
                maxd = dist
                idx = i
        if maxd > eps and idx != -1:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [pts[i] for i in range(n) if keep[i]]


def outline(rgba, thresh=THRESH, close=CLOSE, ds=DS, simplify_frac=SIMPLIFY_FRAC):
    """Trace `rgba`'s alpha silhouette into a normalized outline polygon.

    Returns {"width", "height", "points": [[nx, ny], ...]} with points in 0-1
    space, or None if no building silhouette is found.
    """
    rgba = rgba.convert("RGBA")
    W, H = rgba.size
    alpha = np.asarray(rgba)[:, :, 3]
    mask = alpha > thresh
    if not mask.any():
        return None

    if close and close > 1:
        m = Image.fromarray((mask * 255).astype(np.uint8))
        m = m.filter(ImageFilter.MaxFilter(close)).filter(ImageFilter.MinFilter(close))
        mask = np.asarray(m) > 127

    comp = _largest_component(mask, ds=ds)
    if comp is None:
        return None
    comp = np.asarray(
        keyout.fill_holes(Image.fromarray((comp * 255).astype(np.uint8)))) > 127

    boundary = _moore_trace(comp)
    if len(boundary) < 3:
        return None

    eps = simplify_frac * math.hypot(W, H)
    simple = _douglas_peucker(boundary, eps)
    points = [[round(x / W, 5), round(y / H, 5)] for (x, y) in simple]
    return {"width": W, "height": H, "points": points}


def render_preview(rgba, data, backdrop=(45, 45, 45), line=(220, 20, 20),
                   dots=(0, 220, 0), width=None):
    """Render a QA preview: the building over a neutral backdrop with its outline
    polygon drawn as a closed red line (and green vertex dots). Returns an RGB
    image the same size as `rgba`.
    """
    rgba = rgba.convert("RGBA")
    W, H = rgba.size
    canvas = Image.new("RGBA", (W, H), backdrop + (255,))
    canvas.alpha_composite(rgba)
    draw = ImageDraw.Draw(canvas)
    pts = [(p[0] * W, p[1] * H) for p in data.get("points", [])]
    if len(pts) >= 2:
        lw = width or max(2, round(max(W, H) / 400))
        draw.line(pts + [pts[0]], fill=line + (255,), width=lw)
        if dots:
            r = lw + 1
            for x, y in pts:
                draw.ellipse([x - r, y - r, x + r, y + r], fill=dots + (255,))
    return canvas.convert("RGB")
