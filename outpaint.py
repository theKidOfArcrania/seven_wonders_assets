"""Seamless rightward outpaint to widen a wonder-background panel into a wide
display panorama.

The immersive city band in the UI is very wide and short, but a single gpt-image
panel is 1536x1024 (1.5:1). To get a wide scene we extend the panel rightward
into a wider seamless panorama. The stitched panorama is kept at its NATIVE
unscaled size (no crop, no resize) as the committed display background.

Mechanism (narrow overlap):
  * `build_seed` seeds a fresh 1536x1024 canvas whose left `OVERLAP` px are the
    panel's rightmost `OVERLAP` px; the rest is a blurred horizontal stretch of
    that seam edge (gives the model tone to continue). The model paints a
    full-width seamless continuation.
  * `blend_wide` composites the model's extension OVER the original panel (the
    ORIGINAL panel stays the base layer) with an alpha ramp across just the
    `OVERLAP` band, opaque afterwards, after tonally matching the extension to
    the panel over the overlap. A narrow overlap => stitched width
    W + (W - OVERLAP), so more NEW width is gained.

Prompts are scene-agnostic (they lean on the seeded left edge for content), so
the same two prompts widen every wonder's day / night panel.
"""
from PIL import Image, ImageFilter
import numpy as np

W, H = 1536, 1024
PANEL = "%dx%d" % (W, H)
OVERLAP = 100                 # px of the panel fed as context AND crossfaded

EXTEND_RIGHT = (
    "The LEFT edge strip of this image is the right-hand border of a wide "
    "painterly semi-realistic classical-antiquity panorama. Paint the WHOLE "
    "image as a SEAMLESS continuation of that scene flowing to the RIGHT: the "
    "SAME horizon line, ground/sea level, terrain and city silhouette, sky "
    "gradient, colour palette, daylight direction, atmospheric depth and "
    "painterly style, with no visible seam at the left edge. Continue the "
    "landscape and settlement gently receding into the distance to the right. "
    "No text, no borders, no frame."
)

EXTEND_RIGHT_NIGHT = (
    "The LEFT edge strip of this image is the right-hand border of a wide "
    "painterly semi-realistic classical-antiquity NIGHT panorama. Paint the "
    "WHOLE image as a SEAMLESS continuation of that scene flowing to the RIGHT "
    "at the SAME time of night: the SAME deep starlit sky, moonlight level, "
    "horizon, ground/sea level, terrain and city silhouette, cool moonlit tones "
    "with warm torch and lantern glow, colour palette, atmospheric depth and "
    "painterly style, with no visible seam at the left edge. Continue the "
    "landscape and settlement gently receding into the distance to the right. "
    "No text, no borders, no frame."
)


def build_seed(panel, overlap=OVERLAP):
    """Seed a 1536x1024 canvas for a rightward extension of `panel`.

    Left `overlap` px = panel's rightmost `overlap` px (real context); the rest
    is a blurred horizontal stretch of the seam edge so the model has tone to
    continue rather than empty canvas.
    """
    if panel.size != (W, H):
        panel = panel.resize((W, H))
    seed = Image.new("RGB", (W, H))
    seed.paste(panel.crop((W - overlap, 0, W, H)), (0, 0))
    edge = panel.crop((W - 8, 0, W, H)).resize((W - overlap, H))
    seed.paste(edge.filter(ImageFilter.GaussianBlur(24)), (overlap, 0))
    return seed


def blend_wide(panel, ext, overlap=OVERLAP):
    """Stitch `ext` onto the right of `panel`; return a (W + (W-overlap)) x H image.

    The ORIGINAL panel is the base layer; `ext` is composited OVER it with an
    alpha ramp 0->1 across the `overlap` band (opaque afterwards). `ext` is
    tonally matched to the panel over the overlap strip so the seam is invisible.
    """
    if panel.size != (W, H):
        panel = panel.resize((W, H))
    if ext.size != (W, H):
        ext = ext.resize((W, H))
    pa = np.asarray(panel.convert("RGB"), np.float32)
    ex = np.asarray(ext.convert("RGB"), np.float32)
    shift = (pa[:, W - overlap:W, :].reshape(-1, 3).mean(0)
             - ex[:, 0:overlap, :].reshape(-1, 3).mean(0))
    ex = np.clip(ex + shift, 0, 255)

    out_w = W + (W - overlap)
    wide = np.zeros((H, out_w, 3), np.float32)
    wide[:, 0:W, :] = pa
    ox = W - overlap
    alpha = np.ones((H, W, 1), np.float32)
    alpha[:, 0:overlap, 0] = np.linspace(0, 1, overlap, dtype=np.float32)[None, :]
    region = wide[:, ox:ox + W, :]
    wide[:, ox:ox + W, :] = region * (1 - alpha) + ex * alpha
    return Image.fromarray(np.clip(wide, 0, 255).astype(np.uint8))
