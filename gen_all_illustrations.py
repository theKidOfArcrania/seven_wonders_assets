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
import os
import sys
import time
import datetime

import yaml
from PIL import Image

import gen_illustration as gi
import keyout
import outpaint

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
WONDER_ITEMS_PER = 6          # illustration, panel, extension, background,
#                               composite, building (per side)

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
        "panel_path": None, "ext_path": None,
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
            prompt=build_prompt(
                wonder.get("background_prompt"), styles["background"])))
    else:
        items.append(_item(
            "%s_panel" % wid, night_panel_src, 1, "edit",
            size=BACKGROUND_SIZE, prompt=NIGHT_EDIT_PROMPTS["background"],
            src_path=day_panel_src))

    # 3. rightward extension (.src intermediary) - seeded from this side's panel
    items.append(_item(
        "%s_extension" % wid, ext_src, 1 if is_day else 2, "extend",
        size=outpaint.PANEL, src_path=panel_src,
        prompt=outpaint.EXTEND_RIGHT if is_day else outpaint.EXTEND_RIGHT_NIGHT))

    # 4. display background - COMMITTED. panel + extension stitched into the full
    #    native wide panorama (no crop, no resize).
    items.append(_item(
        "%s_background" % wid, C("%s_background.png" % wid),
        2 if is_day else 3, "wideblend",
        panel_path=panel_src, ext_path=ext_src))

    # 5. composite - UNCOMMITTED intermediary (.src only, narrow panel, no crop)
    if is_day:
        place = PLACE_TEMPLATE.format(
            monument=(wonder.get("building_prompt") or "").strip())
        items.append(_item(
            "%s_composite" % wid, day_comp_src, 1, "composite",
            size=BACKGROUND_SIZE, prompt=place, src_path=day_panel_src))
        apply_path = day_comp_src
    else:
        night_comp_src = S("%s_night_composite.png" % base)
        items.append(_item(
            "%s_composite" % wid, night_comp_src, 2, "composite",
            size=BACKGROUND_SIZE, prompt=NIGHT_EDIT_PROMPTS["composite"],
            src_path=day_comp_src))
        apply_path = night_comp_src

    # 6. building - FINAL committed asset, keyed out of the narrow composite at
    #    NATIVE size (no crop, no resize; monument stays in place). The mask is
    #    ALWAYS derived from the Day composite so Day/Night cutouts register.
    items.append(_item(
        "%s_building" % wid, C("%s_building.png" % wid),
        2 if is_day else 3, "keyout",
        transparent=True,
        empty_path=day_panel_src, mask_path=day_comp_src, apply_path=apply_path))

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


def _cached(item):
    out = item["out"]
    if not (os.path.exists(out) and os.path.getsize(out) > 0):
        return False
    # a Day source item is only fully cached once its .src copy also exists,
    # otherwise its Night counterpart would have nothing to edit.
    ss = item.get("save_src")
    if ss and not (os.path.exists(ss) and os.path.getsize(ss) > 0):
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


def process(item, force, dry_run, retries, done, skipped, failed):
    key, out, mode = item["key"], item["out"], item["mode"]

    if not force and _cached(item):
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
    for item in items:
        process(item, force, dry_run, retries, done, skipped, failed)

    log("=== batch done: %d generated, %d skipped, %d failed ==="
        % (len(done), len(skipped), len(failed)))
    if failed:
        log("FAILED IDS: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
