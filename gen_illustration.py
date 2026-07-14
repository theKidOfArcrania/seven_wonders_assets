"""Generate the ORIGINAL bottom illustration for card_1 from a text prompt.

Pipeline: prompt (illustration\\prompt.txt) -> image-generation -> PNG.

Backends (all gpt-image-1):
  * By default the project's Azure AI Foundry resource is used via its
    OpenAI-compatible v1 endpoint
    (https://<resource>.services.ai.azure.com/openai/v1/), reached with the
    standard OpenAI client + the resource api-key.
  * If only OPENAI_API_KEY is set, public OpenAI (api.openai.com) is used.
  * Otherwise falls back to a deterministic, fully-original procedural forest
    scene drawn with PIL so the pipeline is runnable offline.

Both text-to-image (`gen_openai`/`gen_azure`) and image-to-image editing
(`edit_openai`/`edit_azure`, used to derive a Night asset from its Day image)
are supported, as is transparent output (`transparent=True`) for isolated
monuments. Generation helpers return the RAW model image at the requested size;
callers crop/resize to their target with `fit()`.

The reference photo is never read here.
"""
import os
import base64
import math
import random
from PIL import Image, ImageDraw, ImageFilter

W, H = 570, 623                      # illustration area (card width x lower area)
OUT = os.path.join("illustration", "card_1_illustration.png")
PROMPT_PATH = os.path.join("illustration", "prompt.txt")
KEY_FILE = ".openai_key"             # local secret file (never commit / print)


def load_key_file():
    """Load the API key from a local .openai_key file into the environment.

    The file holds ONLY the secret key (a single line). By default it wires the
    project's **Azure AI Foundry** resource, whose OpenAI-compatible v1 endpoint
    (https://<resource>.services.ai.azure.com/openai/v1/) serves gpt-image-1.
    Set the env vars directly to override, or point at public OpenAI by setting
    OPENAI_API_KEY and leaving AZURE_FOUNDRY_ENDPOINT unset. The key is never
    printed.
    """
    if not os.path.exists(KEY_FILE):
        return
    with open(KEY_FILE, encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        return
    # default to the project's Azure AI Foundry resource (endpoint is not secret).
    # Its OpenAI-compatible v1 route is reached with the standard OpenAI client.
    os.environ.setdefault(
        "AZURE_FOUNDRY_ENDPOINT", "https://wanghenry-testai2.services.ai.azure.com/"
    )
    os.environ.setdefault("AZURE_FOUNDRY_API_KEY", key)


def read_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read().strip()


def _decode(b64_json):
    """Decode a base64 image into a PIL.Image at the model's native size.

    Transparent PNGs keep their alpha (mode RGBA); opaque images stay RGB.
    """
    from io import BytesIO
    return Image.open(BytesIO(base64.b64decode(b64_json)))


def fit(img, out_size=None):
    """Center-crop `img` to the target aspect then resize to `out_size`.

    The crop preserves the target aspect ratio regardless of the source
    orientation (portrait card, wide panorama, or tall monument), so the same
    path serves cards and every wonder illustration kind. Alpha is preserved
    when the source image has a transparent background.
    """
    out_w, out_h = out_size or (W, H)
    img = img.convert("RGBA") if "A" in img.getbands() else img.convert("RGB")
    ar = out_w / out_h
    cw = int(img.height * ar)
    if cw <= img.width:
        left = (img.width - cw) // 2
        img = img.crop((left, 0, left + cw, img.height))
    else:
        ch = int(img.width / ar)
        top = (img.height - ch) // 2
        img = img.crop((0, top, img.width, top + ch))
    return img.resize((out_w, out_h), Image.LANCZOS)


def _img_to_png_buf(img, name="source.png"):
    """Serialize a PIL.Image to a named in-memory PNG for the edit endpoint."""
    from io import BytesIO
    buf = BytesIO()
    img.convert("RGBA").save(buf, format="PNG")
    buf.seek(0)
    buf.name = name
    return buf


def _transparent_kwargs(transparent):
    return {"background": "transparent", "output_format": "png"} if transparent else {}


def _call_with_optional(fn, base_kwargs, optional):
    """Call `fn(**base_kwargs, **optional)`, retrying without any optional
    kwargs the installed SDK/endpoint rejects (older SDKs lack e.g.
    input_fidelity). Returns the response."""
    try:
        return fn(**base_kwargs, **optional)
    except TypeError:
        return fn(**base_kwargs)


def _image_client():
    """Build a gpt-image-1 client + model name, or (None, None, None).

    Prefers the Azure AI Foundry resource via its OpenAI-compatible v1 endpoint
    (base_url=https://<resource>.services.ai.azure.com/openai/v1/), reached with
    the STANDARD OpenAI client and the resource api-key. Falls back to public
    OpenAI (api.openai.com) when only OPENAI_API_KEY is set.

    Returns (client, model, label). For the Foundry route `model` is the image
    DEPLOYMENT name (AZURE_FOUNDRY_IMAGE_DEPLOYMENT, default "gpt-image-1.5"); the
    preview API surface is selected with ?api-version=preview unless overridden.
    """
    try:
        from openai import OpenAI
    except Exception:
        return None, None, None
    endpoint = os.environ.get("AZURE_FOUNDRY_ENDPOINT")
    key = os.environ.get("AZURE_FOUNDRY_API_KEY")
    if endpoint and key:
        base_url = endpoint.rstrip("/") + "/openai/v1/"
        model = os.environ.get("AZURE_FOUNDRY_IMAGE_DEPLOYMENT", "gpt-image-1.5")
        kwargs = {"base_url": base_url, "api_key": key}
        api_version = os.environ.get("AZURE_FOUNDRY_API_VERSION", "preview")
        if api_version:
            kwargs["default_query"] = {"api-version": api_version}
        return OpenAI(**kwargs), model, "azure-foundry"
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAI(), os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1.5"), "openai"
    return None, None, None


def gen_image(prompt, size="1024x1024", transparent=False):
    """Text-to-image via gpt-image-1 (Azure Foundry v1, else public OpenAI).

    `size` must be a gpt-image-1 value (1024x1024 / 1024x1536 / 1536x1024).
    Returns (RAW PIL.Image, backend_label) or (None, None). Callers crop to
    their final target with `fit()`.
    """
    client, model, label = _image_client()
    if client is None:
        return None, None
    try:
        resp = client.images.generate(
            model=model, prompt=prompt, size=size, n=1,
            **_transparent_kwargs(transparent),
        )
        return _decode(resp.data[0].b64_json), label
    except Exception as e:
        print("image generation failed:", e)
        return None, None


def edit_image(src_img, prompt, size="1024x1024", transparent=False,
               input_fidelity="high"):
    """Image-to-image edit via gpt-image-1 (Azure Foundry v1, else public OpenAI).

    Feeds `src_img` (a Day PIL.Image) plus a relight instruction to derive the
    matching Night asset while preserving composition (input_fidelity="high").
    Returns (RAW PIL.Image, backend_label) or (None, None).
    """
    client, model, label = _image_client()
    if client is None:
        return None, None
    try:
        base = dict(
            model=model, image=_img_to_png_buf(src_img), prompt=prompt,
            size=size, n=1, **_transparent_kwargs(transparent),
        )
        resp = _call_with_optional(
            client.images.edit, base, {"input_fidelity": input_fidelity},
        )
        return _decode(resp.data[0].b64_json), label
    except Exception as e:
        print("image edit failed:", e)
        return None, None


def gen_procedural(seed=7):
    """Deterministic, original painterly forest scene (offline fallback)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H), (28, 40, 22))
    d = ImageDraw.Draw(img, "RGBA")

    # canopy light gradient (warm light from upper-right through green)
    for y in range(H):
        t = y / H
        base = (
            int(34 + 26 * (1 - t)),
            int(58 + 34 * (1 - t)),
            int(24 + 14 * (1 - t)),
        )
        d.line([(0, y), (W, y)], fill=base)

    # warm light shafts from the upper area
    shafts = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shafts)
    for i in range(7):
        x0 = rnd.randint(int(W * 0.25), int(W * 0.85))
        w = rnd.randint(30, 80)
        sd.polygon(
            [(x0, 0), (x0 + w, 0), (x0 + w + 120, H), (x0 + 120, H)],
            fill=(255, 224, 150, 26),
        )
    shafts = shafts.filter(ImageFilter.GaussianBlur(30))
    img = Image.alpha_composite(img.convert("RGBA"), shafts).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    # distant foliage blobs
    for _ in range(260):
        x = rnd.randint(0, W)
        y = rnd.randint(0, int(H * 0.7))
        r = rnd.randint(8, 34)
        g = rnd.randint(40, 110)
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=(rnd.randint(20, 60), g, rnd.randint(15, 45), 70))

    # tree trunks
    def trunk(cx, top, bot, width, col):
        for y in range(top, bot, 3):
            t = (y - top) / max(1, bot - top)
            w = width * (0.7 + 0.5 * t)
            shade = int(col[0] * (0.6 + 0.4 * (1 - t)))
            d.rectangle([cx - w / 2, y, cx + w / 2, y + 3],
                        fill=(shade, int(shade * 0.62), int(shade * 0.4), 255))

    trunk(150, 0, H, 70, (96, 60, 34))
    trunk(300, 0, H, 84, (104, 66, 38))
    trunk(470, 0, H, 52, (84, 52, 30))
    trunk(60, 40, H, 40, (78, 48, 28))

    # cut stump with pale wood (center-bottom)
    d.ellipse([250, 470, 330, 520], fill=(190, 150, 96, 255))
    d.ellipse([258, 476, 322, 512], fill=(214, 178, 120, 255))

    # ground / undergrowth
    for y in range(int(H * 0.78), H):
        t = (y - H * 0.78) / (H * 0.22)
        d.line([(0, y), (W, y)],
               fill=(int(40 + 30 * t), int(40 + 26 * t), int(20 + 12 * t)))
    for _ in range(120):
        x = rnd.randint(0, W)
        y = rnd.randint(int(H * 0.78), H)
        d.line([(x, y), (x + rnd.randint(-30, 30), y + rnd.randint(2, 14))],
               fill=(rnd.randint(60, 120), rnd.randint(50, 90), 30, 160),
               width=2)

    # two simplified figures (white tunics)
    # left kneeling woodcutter
    d.polygon([(120, 360), (150, 350), (158, 410), (118, 415)],
              fill=(225, 222, 210, 255))
    d.ellipse([128, 332, 150, 354], fill=(180, 140, 110, 255))
    d.line([(150, 350), (185, 320)], fill=(60, 45, 30, 255), width=5)  # axe haft
    # right standing axeman
    d.polygon([(360, 360), (398, 360), (404, 470), (356, 470)],
              fill=(232, 228, 214, 255))
    d.ellipse([368, 330, 394, 356], fill=(176, 134, 104, 255))
    d.line([(360, 410), (300, 440)], fill=(40, 30, 22, 255), width=6)  # axe
    d.polygon([(296, 426), (312, 432), (304, 452)], fill=(120, 120, 128, 255))

    return img.filter(ImageFilter.GaussianBlur(0.6))


def main():
    load_key_file()
    prompt = read_prompt()
    raw, backend = gen_image(prompt)
    if raw is not None:
        img = fit(raw, (W, H))
    else:
        img, backend = gen_procedural(), "procedural-fallback"
    os.makedirs("illustration", exist_ok=True)
    img.save(OUT)
    print(f"wrote {OUT} ({backend}) {img.size}")


if __name__ == "__main__":
    main()
