"""Batch-generate illustrations for every card AND every wonder in cards.yaml.

For each **card** it builds  <illustration_prompt>  +  the shared
meta.illustration_style  block and caches a portrait PNG in
 illustration_cache/<id>.png .

For each **wonder** it produces, cached as illustration_cache/<id>_<kind>.png
(final assets) with uncommitted intermediaries under illustration_cache/.src/:
  * <id>_illustration.png  portrait hero art (meta.illustration_style appended).
  * <id>_background.png     WIDE seamless city panorama (native unscaled size,
    the full stitched panel + rightward extension, NO crop / NO resize) with a
    reserved empty monument zone. Committed final.
  * <id>_building.png       the monument on a TRANSPARENT background at its NATIVE
    position and scale (the composite keyed out in place, NO crop / NO resize),
    produced by keying it out of a "panel + building" composite (see below).
    Committed final. Because it is committed and skipped when present, MANUAL
    retouches to this file survive re-runs; only --force overwrites it.
  * <id>_outline.json      the building silhouette traced into a normalized
    (0-1) outline polygon (see trace_outline.py), for the UI's built/unbuilt
    stage outline. Committed final; derived from the (possibly retouched)
    <id>_building.png, so re-run it after retouching the building.
  * .preview/<id>_segments_preview.png   gitignored QA render: the building over
    a neutral backdrop with each stage segment filled/outlined in its own
    colour (outline-only red line if the segments JSON is missing). Regenerable.
  * <id>_segments.svg      committed, HAND-EDITABLE stage-cut definition (see
    segment_outline.py): embeds the building + outline and pre-lays (stages - 1)
    red divider lines at equal heights. Skipped when present so edits survive;
    only --force restores the default lines.
  * <id>_segments.json     committed derived asset: the outline split into stage
    bands (bottom-to-top) by the SVG's divider lines. Recomputed automatically
    when the SVG is edited (input newer than output), no --force needed.

Wide display background (outpaint -> stitch)
--------------------------------------------
A gpt-image panel is 1536x1024 (1.5:1); to get a wide immersive scene each side's
empty PANEL is extended rightward into a wider seamless panorama, kept at its
native unscaled size (no crop, no resize):
  1. panel      - a single 1536x1024 empty panorama with a reserved open
     foreground (.src intermediary; Day text2img, Night relight of the Day panel).
  2. extension  - edit a seed made from the panel's right edge into a seamless
     rightward continuation (outpaint.py; .src intermediary). Night extends the
     NIGHT panel directly (its own prompt) so it attaches seamlessly.
  3. background - stitch panel + extension (extension composited OVER the panel
     with a crossfade + tonal match) -> committed display PNG at native size.

Composite -> keyout building pipeline
-------------------------------------
Rather than rendering the monument in isolation (which floats with inconsistent
scale/lighting), the monument is painted INTO the empty PANEL's reserved zone,
then keyed back out at native resolution IN PLACE (the building is a SEPARATE UI
overlay, so the wide display background does not carry it):
  1. composite   - edit the empty panel to add the monument in the reserved
     zone, keeping everything else identical. This is an UNCOMMITTED intermediary
     saved only under .src/<base>_<side>_composite.png.
  2. building    - diff the composite against the empty panel to key the monument
     out onto transparency at the composite's native size (no crop, no resize).

Day/Night consistency ("Day is the anchor, Night is derived")
-------------------------------------------------------------
Wonders come in `<base>_day` / `<base>_night` pairs. To keep a wonder's Day and
Night sides aligned:
  * illustration - Day and Night are BOTH generated independently from their own
    prompts (they may differ slightly, which is fine for the hero art).
  * panel        - the DAY panel is text-to-image; the NIGHT panel is an edit
    (relight) of the cached Day panel, so both share the same reserved zone.
  * background   - each side stitches its own panel + extension; the far-right
    outpaint may differ slightly between Day and Night (accepted).
  * composite    - the DAY composite is an edit of the Day panel; the NIGHT
    composite is an edit (relight) of the DAY composite, so the monument keeps
    identical geometry between Day and Night.
  * building     - the NIGHT building is keyed out with the SAME Day-derived mask
    (applied to the Night composite), so the Day and Night cutouts register.
The Day sides cache their raw panels under illustration_cache/.src/<...>.png so
the Night edits and the keyout have a pixel-aligned source to work from.

Backend: all images use gpt-image-1, by default through the Azure AI Foundry
resource's OpenAI-compatible v1 endpoint (services.ai.azure.com/openai/v1/),
falling back to public OpenAI when only OPENAI_API_KEY is set. See
gen_illustration.py. Buildings request a transparent background natively.

Design goals (per request):
  * Cache folder, one PNG per work item (card id, or "<wonder>_<kind>").
  * Skip an item if its PNG already exists (unless --force).
  * Retry transient failures; never stop the whole run on a single failure.
  * Log everything (skips, successes, failures) to illustration_cache/generation.log
    and print a summary of any items that failed at the end.

The procedural offline fallback is intentionally NOT used here: it always
draws the same forest scene, which is wrong for every subject. An item that
cannot be generated is logged as failed so it can be retried on a later run
(existing PNGs are skipped, so re-running only retries failures).

Usage:
  python gen_all_illustrations.py               # all missing cards + wonders
  python gen_all_illustrations.py --force        # regenerate everything
  python gen_all_illustrations.py --cards-only   # skip wonders
  python gen_all_illustrations.py --wonders-only # skip cards
  python gen_all_illustrations.py a b c          # only these card / wonder ids
  python gen_all_illustrations.py --dry-run      # list work, call no backend
  python gen_all_illustrations.py --retries 5
"""
import json
import os
import sys
import time
import datetime

import yaml
from PIL import Image

import gen_illustration as gi
import keyout
import outpaint
import segment_outline
import trace_outline

CARDS_YAML = "cards.yaml"
CACHE_DIR = "illustration_cache"
SRC_DIR = os.path.join(CACHE_DIR, ".src")   # pre-crop Day sources for Night edits
LOG_PATH = os.path.join(CACHE_DIR, "generation.log")

CARD_SIZE = "1024x1024"                       # gpt-image-1 request size for cards
CARD_OUT = (gi.W, gi.H)                        # final cached card size (570x623)

# Per-wonder image sizes. Request sizes must be gpt-image-1 values
# (1024x1024 / 1024x1536 / 1536x1024); `*_OUT` are the final cached sizes.
ILLUSTRATION_SIZE = "1024x1536"
ILLUSTRATION_OUT = (570, 623)
# The empty background is generated as a single 1536x1024 panel, then extended
# rightward (outpaint.py) into a WIDE seamless panorama. The committed display
# background and the keyed building are both kept at their NATIVE unscaled size:
# the background is the full stitched panorama (no crop, no resize) and the
# building is keyed out in place at the composite's dimensions (no crop, no
# resize) - so their position and scale match the generated art exactly.
BACKGROUND_SIZE = "1536x1024"
WONDER_ITEMS_PER = 10         # illustration, panel, extension, background,
#                               composite, building, outline, preview,
#                               segments-svg, segments-json (per side)

# Wrap a wonder's building_prompt so the monument is painted INTO the empty
# background's reserved zone (not onto a blank field). The rest of the scene is
# preserved exactly so the composite can be diffed against the empty background
# to key the monument out. building_prompt is written for an isolated render, so
# the template explicitly overrides any "blank/neutral/isolated background"
# wording it may contain.
PLACE_TEMPLATE = (
    "Keep this entire panorama EXACTLY as it is: the same sky, sea, terrain, "
    "city buildings, walls, ships, colours, perspective, atmosphere and "
    "lighting. Do NOT restyle, recolour, shift or remove anything already "
    "present. Make ONE change only: in the open, empty reserved area in the "
    "LEFT-CENTER foreground (roughly 20% to 36% of the image width from the "
    "left edge), add the monument described below, standing rooted on that spot "
    "as ONE single, continuous, physically connected structure, scaled to tower "
    "over the scene yet fit fully within the frame, lit to match the existing "
    "light direction and atmosphere, in the same painterly semi-realistic "
    "classical style so it looks natively painted into this scene. Ignore any "
    "wording below about a blank, plain, neutral, flat or isolated background - "
    "integrate the monument into THIS scene instead.\n\nMonument: {monument}"
)

# Relight instructions for deriving a Night asset from its cached Day source.
# Geometry comes entirely from the Day image; these change lighting only.
NIGHT_EDIT_PROMPTS = {
    "background": (
        "Repaint this exact scene at night. Keep the composition, buildings, "
        "coastline, horizon and the open reserved foreground area EXACTLY as "
        "they are - do not add, remove or move anything. Change ONLY the "
        "lighting to night: a deep indigo starlit sky with a moon, cool "
        "moonlit tones on the stone and water, and warm torch and lantern "
        "glow. Keep it slightly desaturated so foreground UI stays readable."
    ),
    "composite": (
        "Convert this daytime scene into a NIGHT version. Keep the EXACT same "
        "composition, geometry, framing, buildings, ships, coastline and the "
        "monument's pose, materials and position - do not move, add or remove "
        "anything; change ONLY the lighting and time of day. Night lighting: a "
        "deep indigo starlit sky with a moon, dark water with cool moonlit "
        "reflections, warm lamplight glowing in distant windows, and any torch "
        "or brazier on the monument lit with a warm flame casting a soft glow. "
        "Render stone and metal in cool moonlight with warm accent highlights. "
        "Keep the same painterly semi-realistic classical style."
    ),
}


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _strip_preamble(text, marker):
    """Drop the human-facing preamble, keeping the guidance from `marker` on."""
    text = text or ""
    i = text.find(marker)
    return text[i:].strip() if i != -1 else text.strip()


def load_doc():
    with open(CARDS_YAML, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    meta = doc.get("meta", {}) or {}
    styles = {
        "illustration": _strip_preamble(meta.get("illustration_style", ""), "Style:"),
        "background": _strip_preamble(meta.get("background_style", ""), "Framing:"),
        "building": _strip_preamble(meta.get("building_style", ""), "Isolation:"),
    }
    cards = list(doc.get("cards", []) or [])
    wonders = list(doc.get("wonders", []) or [])
    return cards, wonders, styles


def build_prompt(scene, style):
    scene = (scene or "").strip()
    if style and scene:
        return scene + "\n\n" + style
    return scene


def _exists(path):
    return bool(path and os.path.exists(path) and os.path.getsize(path) > 0)


def _item(key, out, phase, mode, **kw):
    """Build a work item with all fields defaulted."""
    it = {
        "key": key, "out": out, "phase": phase, "mode": mode,
        "out_size": None, "size": None, "transparent": False,
        "prompt": None, "src_path": None, "save_src": None,
        "empty_path": None, "mask_path": None, "apply_path": None,
        "panel_path": None, "ext_path": None, "in_path": None,
        "json_path": None, "svg_path": None, "outline_path": None,
        "n_segments": None, "deps": [],
        "intermediary": False,
    }
    it.update(kw)
    return it


def _wonder_items(wonder, styles):
    """Expand one wonder into its per-kind work items.

    Phases order the pipeline within a run so every item's source exists first:
      0 text2img (illustration, DAY empty panel)
      1 first-order edits (NIGHT empty panel, DAY extension, DAY composite)
      2 second-order (NIGHT extension, DAY display background, NIGHT composite,
        DAY building keyout)
      3 third-order (NIGHT display background, NIGHT building keyout)

    The empty background is a single 1536x1024 PANEL (cached in .src). It feeds
    three things: the rightward EXTENSION, the wide DISPLAY background (panel +
    extension stitched then cropped to 3.5:1), and the COMPOSITE the monument is
    painted into. The composite -> keyout runs on the narrow panel at native
    resolution (a clean diff); only the committed display background is widened.
    """
    wid = wonder["id"]
    base, _, side = wid.rpartition("_")            # "<base>", "_", "day"|"night"
    is_day = side == "day"
    def C(name):
        return os.path.join(CACHE_DIR, name)
    def S(name):
        return os.path.join(SRC_DIR, name)
    day_panel_src = S("%s_day_background.png" % base)      # DAY empty panel
    night_panel_src = S("%s_night_background.png" % base)  # NIGHT empty panel
    day_comp_src = S("%s_day_composite.png" % base)
    panel_src = day_panel_src if is_day else night_panel_src
    ext_src = S("%s_%s_extension.png" % (base, side))
    items = []

    # 1. illustration - text2img, independent Day/Night
    items.append(_item(
        "%s_illustration" % wid, C("%s_illustration.png" % wid), 0, "text2img",
        out_size=ILLUSTRATION_OUT, size=ILLUSTRATION_SIZE,
        prompt=build_prompt(
            wonder.get("illustration_prompt"), styles["illustration"])))

    # 2. empty panel (.src intermediary) - Day text2img; Night relight of Day
    if is_day:
        items.append(_item(
            "%s_panel" % wid, day_panel_src, 0, "text2img",
            size=BACKGROUND_SIZE,
            intermediary=True,
            prompt=build_prompt(
                wonder.get("background_prompt"), styles["background"])))
    else:
        items.append(_item(
            "%s_panel" % wid, night_panel_src, 1, "edit",
            deps=['%s_day_panel' % base],
            intermediary=True,
            size=BACKGROUND_SIZE, prompt=NIGHT_EDIT_PROMPTS["background"],
            src_path=day_panel_src))

    # 3. rightward extension (.src intermediary) - seeded from this side's panel
    items.append(_item(
        "%s_extension" % wid, ext_src, 1 if is_day else 2, "extend",
        size=outpaint.PANEL, src_path=panel_src,
        deps=['%s_panel' % wid],
        intermediary=True,
        prompt=outpaint.EXTEND_RIGHT if is_day else outpaint.EXTEND_RIGHT_NIGHT))

    # 4. display background - COMMITTED. panel + extension stitched into the full
    #    native wide panorama (no crop, no resize).
    items.append(_item(
        "%s_background" % wid, C("%s_background.png" % wid),
        2 if is_day else 3, "wideblend",
        deps=['%s_panel' % wid, '%s_extension' % wid],
        panel_path=panel_src, ext_path=ext_src))

    # 5. composite - UNCOMMITTED intermediary (.src only, narrow panel, no crop)
    if is_day:
        place = PLACE_TEMPLATE.format(
            monument=(wonder.get("building_prompt") or "").strip())
        items.append(_item(
            "%s_composite" % wid, day_comp_src, 1, "composite",
            deps=['%s_panel' % wid],
            intermediary=True,
            size=BACKGROUND_SIZE, prompt=place, src_path=day_panel_src))
        comp_src = day_comp_src
    else:
        night_comp_src = S("%s_night_composite.png" % base)
        items.append(_item(
            "%s_composite" % wid, night_comp_src, 2, "composite",
            intermediary=True,
            deps=['%s_day_composite' % base],
            size=BACKGROUND_SIZE, prompt=NIGHT_EDIT_PROMPTS["composite"],
            src_path=day_comp_src))
        comp_src = night_comp_src

    # 6. building - FINAL committed asset, keyed out of the narrow composite at
    #    NATIVE size (no crop, no resize; monument stays in place).
    items.append(_item(
        "%s_building" % wid, C("%s_building.png" % wid),
        2 if is_day else 3, "keyout",
        deps=['%s_composite' % wid, '%s_panel' % wid],
        transparent=True,
        empty_path=panel_src, mask_path=comp_src, apply_path=comp_src))

    # 7. outline - FINAL committed asset: the building silhouette traced into a
    #    normalized polygon (JSON), for the UI's built/unbuilt stage outline.
    #    Depends on the building: phase ordering runs it after the building, and
    #    the building's mtime drives recompute if it is retouched. Because the
    #    building is committed (not intermediary), a missing outline never forces
    #    the (hand-retouched) building to re-key.
    items.append(_item(
        "%s_outline" % wid, C("%s_outline.json" % wid),
        3 if is_day else 4, "outline",
        in_path=C("%s_building.png" % wid),
        deps=['%s_building' % wid]))

    # 8. preview - gitignored QA render: the building over a neutral backdrop
    #    with each stage segment filled/outlined in its own colour. Runs after
    #    the segments JSON; depends on the building + segments JSON so it
    #    re-renders when either changes. Both deps are committed, so a missing
    #    preview never forces anything upstream to rebuild. Falls back to an
    #    outline-only render if the segments JSON is absent.
    items.append(_item(
        "%s_preview" % wid,
        os.path.join(CACHE_DIR, ".preview", "%s_segments_preview.png" % wid),
        6 if is_day else 7, "preview",
        in_path=C("%s_building.png" % wid),
        json_path=C("%s_segments.json" % wid),
        outline_path=C("%s_outline.json" % wid),
        deps=['%s_building' % wid, '%s_segjson' % wid]))

    # 9. segments SVG - COMMITTED, hand-editable cut definition: embeds the
    #    building + outline and pre-lays (stages - 1) red divider lines. It is a
    #    source of truth with NO deps, so it is skipped whenever present and
    #    artist edits are never overwritten; only --force regenerates the
    #    default lines. Phase ordering runs it after the outline.
    n_seg = len(wonder.get("stages") or []) or 3
    items.append(_item(
        "%s_segsvg" % wid, C("%s_segments.svg" % wid),
        4 if is_day else 5, "segsvg",
        in_path=C("%s_building.png" % wid),
        json_path=C("%s_outline.json" % wid),
        n_segments=n_seg))

    # 10. segments JSON - COMMITTED derived asset: the outline split into stage
    #     bands by the (edited) SVG divider lines. Depends on the outline + the
    #     segments SVG, so editing the SVG (or re-tracing the outline) and
    #     re-running recomputes this without --force.
    items.append(_item(
        "%s_segjson" % wid, C("%s_segments.json" % wid),
        5 if is_day else 6, "segjson",
        json_path=C("%s_outline.json" % wid),
        svg_path=C("%s_segments.svg" % wid),
        deps=['%s_outline' % wid, '%s_segsvg' % wid]))

    return items


def build_items(cards, wonders, styles, cards_only, wonders_only):
    """Expand cards + wonders into a flat, dependency-ordered list of items.

    Items are sorted by `phase` so every item's source is produced before it.
    """
    items = []
    if not wonders_only:
        for card in cards:
            items.append(_item(
                card["id"], os.path.join(CACHE_DIR, card["id"] + ".png"),
                0, "text2img", out_size=CARD_OUT, size=CARD_SIZE,
                prompt=build_prompt(
                    card.get("illustration_prompt"), styles["illustration"])))
    if not cards_only:
        for wonder in wonders:
            items.extend(_wonder_items(wonder, styles))
    items.sort(key=lambda it: it.get("phase", 0))     # stable: low phase first
    return items


def _cached(item, dep_chain):
    rec = dep_chain[item['key']]

    # Intermediaries have no committed output of their own; they only exist to
    # feed consumers. Rebuild one iff any consumer needs rebuilding (reverse
    # propagation). This is restricted to intermediaries so that a missing
    # consumer (e.g. a not-yet-generated outline) never forces a committed,
    # possibly hand-retouched asset (e.g. the building) to be regenerated.
    if item['intermediary']:
        for c in rec['used_by']:
            if not _cached(dep_chain[c]['item'], dep_chain):
                return False
        return True

    # Committed output: stale if missing/empty, or older than any dependency's
    # output. Deps reference item keys; comparing against each dep's *output*
    # mtime lets a hand-edited source (e.g. a segments SVG) or a retouched
    # building flow through on a plain re-run, without --force. Deps whose
    # output is absent (e.g. gitignored .src intermediaries) are ignored, so
    # committed assets are never regenerated on account of missing sources.
    out = item["out"]
    if not (os.path.exists(out) and os.path.getsize(out) > 0):
        return False
    for dkey in item['deps']:
        dep = dep_chain.get(dkey)
        if dep is None:
            continue
        dout = dep['item']['out']
        if _exists(dout) and os.path.getmtime(dout) > os.path.getmtime(out):
            return False

    return True


def _do_keyout(item, done, failed):
    """Key the monument out of its composite into the final transparent asset.

    Local-only (no backend). The mask is derived from the Day composite and
    applied to this side's composite so Day/Night cutouts register exactly.
    """
    key, out = item["key"], item["out"]
    for label, p in (("empty background", item["empty_path"]),
                     ("day composite", item["mask_path"]),
                     ("composite", item["apply_path"])):
        if not _exists(p):
            failed.append(key)
            log("FAIL  %-30s (missing %s %s; generate it first)"
                % (key, label, p))
            return
    try:
        empty = Image.open(item["empty_path"])
        mask_comp = Image.open(item["mask_path"])
        apply_comp = Image.open(item["apply_path"])
        canvas = keyout.key_out(empty, mask_comp, apply_comp)
        if canvas is None:
            failed.append(key)
            log("FAIL  %-30s (no monument detected in composite)" % key)
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        canvas.save(out)
        done.append(key)
        log("OK    %-30s keyout %s -> %s" % (key, canvas.size, out))
    except Exception as e:
        failed.append(key)
        log("FAIL  %-30s keyout error: %s" % (key, e))


def _do_outline(item, done, failed):
    """Trace the keyed building silhouette into a normalized outline polygon
    and write it as JSON. Local-only (no backend)."""
    key, out, inp = item["key"], item["out"], item["in_path"]
    if not _exists(inp):
        failed.append(key)
        log("FAIL  %-30s (missing building %s; generate it first)" % (key, inp))
        return
    try:
        data = trace_outline.outline(Image.open(inp))
        if data is None:
            failed.append(key)
            log("FAIL  %-30s (no building silhouette detected)" % key)
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        done.append(key)
        log("OK    %-30s outline %d pts -> %s" % (key, len(data["points"]), out))
    except Exception as e:
        failed.append(key)
        log("FAIL  %-30s outline error: %s" % (key, e))


def _do_preview(item, done, failed):
    """Render a gitignored QA preview PNG: the building over a neutral backdrop
    with each stage segment filled/outlined in its own colour. Falls back to an
    outline-only render when the segments JSON is missing. Local-only."""
    key, out = item["key"], item["out"]
    bld, seg_p, outl_p = item["in_path"], item["json_path"], item["outline_path"]
    if not _exists(bld):
        failed.append(key)
        log("FAIL  %-30s (missing building %s; generate it first)" % (key, bld))
        return
    try:
        img = Image.open(bld)
        if _exists(seg_p):
            with open(seg_p, encoding="utf-8") as f:
                data = json.load(f)
            preview = segment_outline.render_preview(img, data)
            what = "%d segment(s)" % len(data.get("segments") or [])
        elif _exists(outl_p):
            with open(outl_p, encoding="utf-8") as f:
                data = json.load(f)
            preview = trace_outline.render_preview(img, data)
            what = "%d pts (outline only)" % len(data.get("points") or [])
        else:
            failed.append(key)
            log("FAIL  %-30s (missing segments %s and outline %s)"
                % (key, seg_p, outl_p))
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        preview.save(out)
        done.append(key)
        log("OK    %-30s preview %s -> %s" % (key, what, out))
    except Exception as e:
        failed.append(key)
        log("FAIL  %-30s preview error: %s" % (key, e))


def _do_segsvg(item, done, failed):
    """Write the committed, hand-editable segments SVG (building + outline +
    default divider lines). Local-only (no backend)."""
    key, out, jp = item["key"], item["out"], item["json_path"]
    if not _exists(jp):
        failed.append(key)
        log("FAIL  %-30s (missing outline %s; generate it first)" % (key, jp))
        return
    try:
        with open(jp, encoding="utf-8") as f:
            data = json.load(f)
        href = os.path.basename(item["in_path"])
        n = item["n_segments"] or 3
        svg = segment_outline.build_segments_svg(href, data, n)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(svg)
        done.append(key)
        log("OK    %-30s segments svg (%d stages) -> %s" % (key, n, out))
    except Exception as e:
        failed.append(key)
        log("FAIL  %-30s segsvg error: %s" % (key, e))


def _do_segjson(item, done, failed):
    """Compute the committed segments JSON from the outline + (edited) SVG.
    Local-only (no backend)."""
    key, out = item["key"], item["out"]
    jp, sp = item["json_path"], item["svg_path"]
    for label, p in (("outline", jp), ("segments svg", sp)):
        if not _exists(p):
            failed.append(key)
            log("FAIL  %-30s (missing %s %s; generate it first)"
                % (key, label, p))
            return
    try:
        with open(jp, encoding="utf-8") as f:
            data = json.load(f)
        with open(sp, encoding="utf-8") as f:
            svg = f.read()
        dividers = segment_outline.parse_dividers(svg)
        result = segment_outline.segment_polygon(data, dividers)
        if result is None:
            failed.append(key)
            log("FAIL  %-30s (empty outline)" % key)
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        done.append(key)
        log("OK    %-30s segments %d band(s), %d divider(s) -> %s"
            % (key, len(result["segments"]), len(dividers), out))
    except Exception as e:
        failed.append(key)
        log("FAIL  %-30s segjson error: %s" % (key, e))


def _do_wideblend(item, done, failed):
    """Stitch the empty panel + its rightward extension into the committed
    3.5:1 display background. Local-only (no backend)."""
    key, out = item["key"], item["out"]
    for label, p in (("empty panel", item["panel_path"]),
                     ("extension", item["ext_path"])):
        if not _exists(p):
            failed.append(key)
            log("FAIL  %-30s (missing %s %s; generate it first)"
                % (key, label, p))
            return
    try:
        panel = Image.open(item["panel_path"])
        ext = Image.open(item["ext_path"])
        # Full native stitched panorama: NO crop to the display ratio, NO resize
        # to a display size - the background is kept in its native unscaled form.
        disp = outpaint.blend_wide(panel, ext)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        disp.save(out)
        done.append(key)
        log("OK    %-30s wideblend %s -> %s" % (key, disp.size, out))
    except Exception as e:
        failed.append(key)
        log("FAIL  %-30s wideblend error: %s" % (key, e))


def process(item, dep_chain, force, dry_run, retries, done, skipped, failed):
    key, out, mode = item["key"], item["out"], item["mode"]

    if not force and _cached(item, dep_chain):
        skipped.append(key)
        log("SKIP  %-30s (cached)" % key)
        return

    if mode in ("text2img", "edit", "composite", "extend") \
            and not (item["prompt"] or "").strip():
        failed.append(key)
        log("FAIL  %-30s (no prompt)" % key)
        return

    if dry_run:
        done.append(key)
        log("DRY   %-30s would %s" % (key, mode))
        return

    if mode == "keyout":
        _do_keyout(item, done, failed)
        return

    if mode == "wideblend":
        _do_wideblend(item, done, failed)
        return

    if mode == "outline":
        _do_outline(item, done, failed)
        return

    if mode == "preview":
        _do_preview(item, done, failed)
        return

    if mode == "segsvg":
        _do_segsvg(item, done, failed)
        return

    if mode == "segjson":
        _do_segjson(item, done, failed)
        return

    # edit, composite and extend need their source image already on disk.
    src_img = None
    if mode in ("edit", "composite", "extend"):
        sp = item["src_path"]
        if not _exists(sp):
            failed.append(key)
            log("FAIL  %-30s (missing source %s; generate its Day side first)"
                % (key, sp))
            return
        src_img = Image.open(sp)

    ok = False
    for attempt in range(1, retries + 1):
        try:
            if mode == "text2img":
                raw, backend = gi.gen_image(
                    item["prompt"], size=item["size"],
                    transparent=item["transparent"])
            elif mode == "extend":
                # seed a fresh canvas from the panel's right edge, then paint a
                # seamless continuation; store the raw extension (no crop).
                seed = outpaint.build_seed(src_img)
                raw, backend = gi.edit_image(
                    seed, item["prompt"], size=item["size"])
            else:   # edit or composite (both image-to-image)
                raw, backend = gi.edit_image(
                    src_img, item["prompt"], size=item["size"],
                    transparent=item["transparent"])
        except Exception as e:  # never let one item stop the run
            raw, backend = None, None
            log("ERR   %-30s attempt %d/%d: %s" % (key, attempt, retries, e))
        if raw is not None:
            try:
                if item["out_size"] is None:
                    # uncommitted intermediary (empty panel / extension /
                    # composite): store the raw panorama at native size, no crop.
                    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                    raw.save(out)
                    done.append(key)
                    log("OK    %-30s %s %s -> %s (intermediary)"
                        % (key, backend, raw.size, out))
                    ok = True
                    break
                if item.get("save_src"):
                    os.makedirs(SRC_DIR, exist_ok=True)
                    raw.save(item["save_src"])
                final = gi.fit(raw, item["out_size"])
                final.save(out)
                done.append(key)
                log("OK    %-30s %s %s -> %s" % (key, backend, final.size, out))
                ok = True
                break
            except Exception as e:
                log("ERR   %-30s save failed: %s" % (key, e))
        if attempt < retries:
            wait = 5 * attempt
            log("RETRY %-30s attempt %d/%d failed; waiting %ds"
                % (key, attempt, retries, wait))
            time.sleep(wait)
    if not ok:
        failed.append(key)
        log("FAIL  %-30s after %d attempt(s)" % (key, retries))


def main(argv):
    force = "--force" in argv
    dry_run = "--dry-run" in argv
    cards_only = "--cards-only" in argv
    wonders_only = "--wonders-only" in argv
    retries = 3
    skip_next = set()
    if "--retries" in argv:
        i = argv.index("--retries")
        if i + 1 < len(argv):
            retries = int(argv[i + 1])
            skip_next.add(argv[i + 1])
    args = [a for a in argv if not a.startswith("--") and a not in skip_next]

    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(SRC_DIR, exist_ok=True)
    gi.load_key_file()

    cards, wonders, styles = load_doc()
    if args:
        wanted = set(args)
        cards = [c for c in cards if c["id"] in wanted]
        wonders = [w for w in wonders if w["id"] in wanted]
        known = {c["id"] for c in cards} | {w["id"] for w in wonders}
        for m in sorted(wanted - known):
            log("WARN unknown card/wonder id: %s" % m)

    items = build_items(cards, wonders, styles, cards_only, wonders_only)

    log("=== batch start: %d item(s) [%d card(s), %d wonder(s) x %d kind(s)], "
        "force=%s dry_run=%s retries=%d ==="
        % (len(items), 0 if wonders_only else len(cards),
           0 if cards_only else len(wonders), WONDER_ITEMS_PER,
           force, dry_run, retries))

    done, skipped, failed = [], [], []
    dep_chain = {}
    for item in items:
        dep_chain[item['key']] = {'used_by': [], 'item': item}
    for item in items:
        for dep in item['deps']:
            dep_chain[dep]['used_by'].append(item['key'])
    for item in items:
        process(item, dep_chain, force, dry_run, retries, done, skipped, failed)

    log("=== batch done: %d generated, %d skipped, %d failed ==="
        % (len(done), len(skipped), len(failed)))
    if failed:
        log("FAILED IDS: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
