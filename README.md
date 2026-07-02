# Card SVG reproduction (7 Wonders "Lumber Yard", card_1)

Reproduces the UI of `reference/card_1.png` as a hand-authored SVG, scores it
against the reference, and generates an original illustration for the exempt
bottom art panel.

## Setup
```
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install pillow numpy resvg-py
# optional, for real image generation / adversary vision review:
#   .venv\Scripts\python.exe -m pip install openai   (and set OPENAI_API_KEY)
```

## Pipeline
| step | script | output |
|------|--------|--------|
| 1. vector UI | `gen_svg.py` | `card_1.svg` |
| 2. score UI vs reference | `evaluate.py` | `out/render.png`, `out/diff.png`, metrics |
| 3. adversary review | `adversary.py` | prioritized fixes (uses a vision LLM if `OPENAI_API_KEY` set) |
| 4. original bottom art | `gen_illustration.py` | `illustration/card_1_illustration.png` |
| 5. full composite | `build.py` | `out/card_1_final.png` |

Iterate steps 1-3 (edit `gen_svg.py` from the diff/adversary feedback) until the
error fraction stops improving.

```
.venv\Scripts\python.exe build.py        # full card
.venv\Scripts\python.exe evaluate.py     # score just the vector UI
```

## Evaluation rule
A UI pixel is an ERROR when the normalized RGB distance to the reference is
>= 5%. Only the UI region (`y < 247`) is scored; the bottom illustration is
EXEMPT. Because the reference is a photograph of a physical card, a strict
per-pixel <5% everywhere is unreachable (even a blurred copy of the reference
fails ~30% of pixels). The goal is therefore to minimize the failing fraction.

Current: ~26.6% UI pixels failing. The flat red field is at its photographic
noise floor (~6%); the rest is irreducible high-frequency detail (Greek-key
meander, title glyphs, hand-painted log icon) plus sensor noise.

## Notes
- The reference photo is never embedded in the SVG or the final render; colors
  and positions were only measured to inform the design.
- `gen_illustration.py` produces an original painterly scene from
  `illustration/prompt.txt` (OpenAI `gpt-image-1` if configured, otherwise a
  deterministic procedural fallback).
