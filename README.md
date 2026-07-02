# 7 Wonders card generator

Renders every card in the 7 Wonders base game as an SVG (and PNG) from a single
data file. One generic template drives all 80 cards: the card's `type` picks the
top-panel colour and banner end-symbol, real hand-drawn icons are pulled from a
shared icon sheet, and an original illustration is generated for the bottom art
panel.

## Layout of the repo
| file | role |
|------|------|
| `cards.yaml` | the card database - 80 base-game cards (name, type, build cost, benefit, chains, appears-at, illustration prompt). Values were read from reference photos, not memory. |
| `seven_wonders_icons.svg` | shared icon sheet. Elements are extracted by their `inkscape:label` (resources, VP, coin, shield, science, wonders, trade bars, chain symbols). Native positions/sizes are auto-measured. |
| `gen_card.py` | builds a card's SVG + PNG from `cards.yaml`, embedding the cached illustration. |
| `gen_illustration.py` | image-generation backend for one illustration (FLUX/Azure Foundry -> Azure OpenAI -> OpenAI, with an offline procedural fallback). |
| `gen_all_illustrations.py` | batch-generates the bottom illustration for every card into `illustration_cache/`. |
| `illustration_cache/<id>.png` | one cached illustration per card id (consumed by `gen_card.py`). |
| `out/svg/<id>.svg`, `out/png/<id>.png` | rendered cards (PNG is 570x870). |

## Setup
```
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install pillow numpy resvg-py pyyaml
# optional, for real illustration generation:
#   .venv\Scripts\python.exe -m pip install openai requests
#   then set OPENAI_API_KEY (or an Azure Foundry / Azure OpenAI key)
```

## Rendering cards
```
.venv\Scripts\python.exe gen_card.py <id> [<id> ...]   # build specific cards
.venv\Scripts\python.exe gen_card.py --all             # build every card
.venv\Scripts\python.exe gen_card.py --list            # list card ids
.venv\Scripts\python.exe gen_card.py --all --no-png    # SVG only (skip raster)
```
If a card has no cached illustration in `illustration_cache/`, its bottom art
panel renders as a labelled grey placeholder so the layout is still correct.

## Generating illustrations
```
.venv\Scripts\python.exe gen_all_illustrations.py            # generate all missing
.venv\Scripts\python.exe gen_all_illustrations.py --force    # regenerate everything
.venv\Scripts\python.exe gen_all_illustrations.py <id> ...   # only these cards
.venv\Scripts\python.exe gen_all_illustrations.py --dry-run  # list work, call no backend
```
Each card's `illustration_prompt` is combined with the shared
`meta.illustration_style` block, sent to the image backend, and cached as
`illustration_cache/<id>.png`. Existing PNGs are skipped, so re-running only
retries failures; results and failures are logged to
`illustration_cache/generation.log`.

## How a card is drawn
- **Template.** Fixed geometry (570x870, rounded corners, top banner, Greek-key
  meander band, left cost/chain ribbon, central benefit medallion).
- **Icons.** Real art from `seven_wonders_icons.svg` is used wherever available:
  resources, victory points, coins, military shields (drawn as N shields, not a
  number), science symbols (no drop shadow), wonders (full for the decorators
  guild, partial elsewhere), trade arrows, and chain symbols. Native centres and
  sizes are measured by rendering each element alone and reading its alpha
  bounding box, so nothing is hand-coded.
- **Procedural emblems.** Card-type emblems (banner scroll ends and the small
  `{card}` glyphs) are drawn procedurally by shape.
- **Placeholders.** Anything without vector art (e.g. chain-in symbols not yet on
  the sheet) renders as a labelled placeholder box, flagging the gap without
  breaking the layout.

## Notes
- Reference photos are only used to author `cards.yaml`; they are never embedded
  in any SVG or render.
- `.openai_key` (git-ignored) may hold a single-line API key; it is loaded into
  the environment and never printed.
