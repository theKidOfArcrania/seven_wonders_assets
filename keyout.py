"""Key an isolated monument out of a "background + building" composite.

The wonder-building pipeline paints a monument into the reserved foreground of an
otherwise empty background panorama (see gen_all_illustrations.py). This module
recovers a clean transparent cutout of just that monument by diffing the
composite against the original empty background:

  1. per-pixel max-channel absolute difference -> where paint changed.
  2. threshold -> binary mask; morphological close to seal thin gaps.
  3. keep the LARGEST 4-connected blob (drops scattered speckle) via a
     downsampled BFS (no scipy dependency).
  4. fill interior holes and feather the edge for clean compositing.
  5. apply the mask as alpha and return the FULL-canvas cutout at native size -
     the monument keyed out IN PLACE (no crop, no resize), so its position and
     scale match the generated composite exactly.

For a Night building the SAME day-derived mask is applied to the Night composite
(which is an edit of the Day composite, so the monument sits on identical
pixels) - guaranteeing the Day and Night cutouts register exactly.
"""
import collections

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Tuned on the rhodes/halikarnassos references; kept fixed so day/night stay
# consistent. A LOW threshold keeps more of the monument's edges (columns,
# pediment, thin figures) that resemble the background; the trade-off is more
# scattered interior/background speckle, which is acceptable (the largest-blob
# bbox drops far-flung specks and the rest is easy to retouch out by hand).
THRESH = 16          # max-channel abs-diff threshold (0-255)
DS = 4               # downsample factor for connected-component labeling
PAD = 12             # padding (px) added around the detected blob bbox
FEATHER = 1.2        # gaussian blur radius applied to the alpha edge


def largest_blob_bbox(mask_ds):
    """BFS over a small boolean array; return (bbox, area) of the biggest
    4-connected component in downsampled coordinates, or (None, 0)."""
    h, w = mask_ds.shape
    seen = np.zeros_like(mask_ds, dtype=bool)
    best = None
    best_area = 0
    for sy in range(h):
        for sx in range(w):
            if not mask_ds[sy, sx] or seen[sy, sx]:
                continue
            q = collections.deque([(sy, sx)])
            seen[sy, sx] = True
            minx = maxx = sx
            miny = maxy = sy
            area = 0
            while q:
                y, x = q.popleft()
                area += 1
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask_ds[ny, nx] \
                            and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            if area > best_area:
                best_area = area
                best = (minx, miny, maxx, maxy)
    return best, best_area


def fill_holes(mask_img):
    """Fill interior holes of a binary ('L') mask.

    Flood the outside background inward on the inverse; whatever is NOT reached
    is an interior hole, which is OR'd back into the foreground.
    """
    inv = mask_img.point(lambda p: 255 - p)
    flood = inv.copy()
    ImageDraw.floodfill(flood, (0, 0), 0, thresh=10)
    holes = flood  # 255 only where an interior hole remains
    return Image.fromarray(
        np.maximum(np.asarray(mask_img), np.asarray(holes)).astype("uint8"))


def compute_alpha_and_bbox(empty, comp, thresh=THRESH, ds=DS, pad=PAD):
    """Diff `empty` vs `comp` -> (feathered full-canvas alpha 'L', bbox).

    bbox is the padded, largest-blob bounding box in full-resolution
    coordinates. Returns (None, None) if nothing changed.
    """
    if empty.size != comp.size:
        raise ValueError("size mismatch: %s vs %s" % (empty.size, comp.size))
    W, H = comp.size
    a = np.asarray(empty.convert("RGB"), np.int16)
    b = np.asarray(comp.convert("RGB"), np.int16)
    diff = np.abs(a - b).max(axis=2).astype(np.uint8)
    mask = diff > thresh

    # morphological close (dilate then erode) to seal thin gaps
    m = Image.fromarray((mask * 255).astype(np.uint8))
    m = m.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MinFilter(7))
    mask = np.asarray(m) > 127

    dsmask = mask[::ds, ::ds]
    bbox_ds, _ = largest_blob_bbox(dsmask)
    if bbox_ds is None:
        return None, None
    x0, y0 = bbox_ds[0] * ds, bbox_ds[1] * ds
    x1, y1 = (bbox_ds[2] + 1) * ds, (bbox_ds[3] + 1) * ds
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(W, x1 + pad); y1 = min(H, y1 + pad)

    keep = np.zeros_like(mask)
    keep[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    mimg = fill_holes(Image.fromarray((keep * 255).astype(np.uint8)))
    alpha = mimg.filter(ImageFilter.GaussianBlur(FEATHER))
    return alpha, (x0, y0, x1, y1)


def apply_alpha(comp, alpha):
    """Apply `alpha` to `comp` and return the FULL-canvas RGBA (no crop, no
    resize): the monument stays at its native position and scale, everything
    else transparent."""
    rgba = comp.convert("RGBA")
    rgba.putalpha(alpha)
    return rgba


def key_out(empty, mask_comp, apply_comp, thresh=THRESH):
    """Produce the final transparent building cutout at NATIVE dimensions.

    * `empty`      - the original empty background panorama.
    * `mask_comp`  - the composite used to DERIVE the mask (always the DAY
                     composite, so Day and Night share one silhouette).
    * `apply_comp` - the composite the mask is APPLIED to (DAY composite for the
                     day building, NIGHT composite for the night building).
    Returns a full-canvas RGBA the SAME size as the composite - the monument
    keyed out in place, NOT cropped and NOT resized - or None if no monument was
    detected.
    """
    alpha, _bbox = compute_alpha_and_bbox(empty, mask_comp, thresh=thresh)
    if alpha is None:
        return None
    if apply_comp.size != mask_comp.size:
        raise ValueError("apply/mask size mismatch: %s vs %s"
                         % (apply_comp.size, mask_comp.size))
    return apply_alpha(apply_comp, alpha)
