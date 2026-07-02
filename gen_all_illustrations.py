"""Batch-generate the bottom illustration for every card in cards.yaml.

For each card it builds  <illustration_prompt>  +  the shared
meta.illustration_style  block, sends it to the same image backends used by
gen_illustration.py (FLUX-2-pro on Azure Foundry -> Azure OpenAI -> OpenAI),
and caches the resulting PNG in  illustration_cache/<id>.png .

Design goals (per request):
  * Cache folder, one PNG per card id.
  * Skip a card if its PNG already exists (unless --force).
  * Retry transient failures; never stop the whole run on a single failure.
  * Log everything (skips, successes, failures) to illustration_cache/generation.log
    and print a summary of any cards that failed at the end.

The procedural offline fallback is intentionally NOT used here: it always
draws the same forest scene, which is wrong for 79 of the 80 cards. A card
that cannot be generated is logged as failed so it can be retried on a
later run (existing PNGs are skipped, so re-running only retries failures).

Usage:
  python gen_all_illustrations.py            # generate all missing
  python gen_all_illustrations.py --force    # regenerate everything
  python gen_all_illustrations.py a b c      # only these card ids
  python gen_all_illustrations.py --dry-run  # list work, call no backend
  python gen_all_illustrations.py --retries 5
"""
import os
import sys
import time
import datetime

import yaml

import gen_illustration as gi

CARDS_YAML = "cards.yaml"
CACHE_DIR = "illustration_cache"
LOG_PATH = os.path.join(CACHE_DIR, "generation.log")


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_cards():
    with open(CARDS_YAML, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    style = doc.get("meta", {}).get("illustration_style", "") or ""
    # send only the actual "Style: ..." guidance, not the human-facing preamble
    idx = style.find("Style:")
    style = style[idx:].strip() if idx != -1 else style.strip()
    cards = []
    for age in ("age_I", "age_II", "age_III"):
        for c in doc.get("cards", {}).get(age, []) or []:
            cards.append(c)
    return cards, style


def build_prompt(card, style):
    scene = (card.get("illustration_prompt") or "").strip()
    if style:
        return scene + "\n\n" + style
    return scene


def generate(prompt):
    """Try the real image backends in order. Returns (PIL.Image|None, backend)."""
    img = gi.gen_flux(prompt)
    if img is not None:
        return img, "flux-2-pro"
    img = gi.gen_azure(prompt)
    if img is not None:
        return img, "azure-openai"
    img = gi.gen_openai(prompt)
    if img is not None:
        return img, "openai"
    return None, None


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    force = "--force" in argv
    dry_run = "--dry-run" in argv
    retries = 3
    if "--retries" in argv:
        i = argv.index("--retries")
        if i + 1 < len(argv):
            retries = int(argv[i + 1])
            args = [a for a in args if a != argv[i + 1]]

    os.makedirs(CACHE_DIR, exist_ok=True)
    gi.load_key_file()

    cards, style = load_cards()
    if args:
        wanted = set(args)
        cards = [c for c in cards if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cards}
        for m in sorted(missing):
            log("WARN unknown card id: %s" % m)

    log("=== batch start: %d card(s), force=%s dry_run=%s retries=%d ==="
        % (len(cards), force, dry_run, retries))

    done, skipped, failed = [], [], []

    for card in cards:
        cid = card["id"]
        out = os.path.join(CACHE_DIR, cid + ".png")

        if not force and os.path.exists(out) and os.path.getsize(out) > 0:
            skipped.append(cid)
            log("SKIP  %-22s (cached)" % cid)
            continue

        if not (card.get("illustration_prompt") or "").strip():
            failed.append(cid)
            log("FAIL  %-22s (no illustration_prompt)" % cid)
            continue

        if dry_run:
            done.append(cid)
            log("DRY   %-22s would generate" % cid)
            continue

        prompt = build_prompt(card, style)
        ok = False
        for attempt in range(1, retries + 1):
            try:
                img, backend = generate(prompt)
            except Exception as e:  # never let one card stop the run
                img, backend = None, None
                log("ERR   %-22s attempt %d/%d: %s" % (cid, attempt, retries, e))
            if img is not None:
                try:
                    img.save(out)
                    done.append(cid)
                    log("OK    %-22s %s %s -> %s"
                        % (cid, backend, img.size, out))
                    ok = True
                    break
                except Exception as e:
                    log("ERR   %-22s save failed: %s" % (cid, e))
            if attempt < retries:
                wait = 5 * attempt
                log("RETRY %-22s attempt %d/%d failed; waiting %ds"
                    % (cid, attempt, retries, wait))
                time.sleep(wait)
        if not ok:
            failed.append(cid)
            log("FAIL  %-22s after %d attempt(s)" % (cid, retries))

    log("=== batch done: %d generated, %d skipped, %d failed ==="
        % (len(done), len(skipped), len(failed)))
    if failed:
        log("FAILED IDS: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
