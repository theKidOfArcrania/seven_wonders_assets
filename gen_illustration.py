"""Generate the ORIGINAL bottom illustration for card_1 from a text prompt.

Pipeline: prompt (illustration\\prompt.txt) -> image-generation -> PNG.

Two backends:
  * If OPENAI_API_KEY is set, calls the OpenAI Images API (model gpt-image-1)
    to synthesize an original painting from the prompt.
  * Otherwise falls back to a deterministic, fully-original procedural forest
    scene drawn with PIL so the pipeline is runnable offline.

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

    The file holds ONLY the secret key (a single line). It is wired to the
    Azure resource by default; set AZURE_OPENAI_API_KEY/OPENAI_API_KEY env
    vars directly to override. The key is never printed.
    """
    if not os.path.exists(KEY_FILE):
        return
    with open(KEY_FILE, encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        return
    # default to the project's Azure AI Foundry resource (endpoint is not secret)
    os.environ.setdefault(
        "AZURE_FOUNDRY_ENDPOINT", "https://wanghenry-testai.services.ai.azure.com/"
    )
    os.environ.setdefault("FLUX_DEPLOYMENT", "flux.2-pro")
    os.environ.setdefault("AZURE_FOUNDRY_API_KEY", key)
    # also wire the legacy Azure OpenAI / OpenAI paths in case they're used
    os.environ.setdefault(
        "AZURE_OPENAI_ENDPOINT", "https://wanghenry-testai.openai.azure.com/"
    )
    os.environ.setdefault("AZURE_OPENAI_API_KEY", key)


def read_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as f:
        return f.read().strip()


def gen_flux(prompt):
    """Generate via Black Forest Labs FLUX-2-pro on Azure AI Foundry.

    FLUX is NOT OpenAI-compatible, so it uses the BFL provider route directly:
      POST {endpoint}providers/blackforestlabs/v1/flux-2-pro?api-version=preview

    Requires:
      * AZURE_FOUNDRY_ENDPOINT  e.g. https://<resource>.services.ai.azure.com/
      * FLUX_DEPLOYMENT         deployment name, lower-case (e.g. flux.2-pro)
    Auth (either one):
      * AZURE_FOUNDRY_API_KEY   resource key, OR
      * EntraID via azure-identity (DefaultAzureCredential / `az login`)
    Returns a PIL.Image or None on failure.
    """
    endpoint = os.environ.get("AZURE_FOUNDRY_ENDPOINT")
    deployment = os.environ.get("FLUX_DEPLOYMENT")
    if not endpoint or not deployment:
        return None
    try:
        import requests
    except Exception:
        return None
    if not endpoint.endswith("/"):
        endpoint += "/"
    url = f"{endpoint}providers/blackforestlabs/v1/flux-2-pro?api-version=preview"

    headers = {"Content-Type": "application/json"}
    if os.environ.get("AZURE_FOUNDRY_API_KEY"):
        headers["api-key"] = os.environ["AZURE_FOUNDRY_API_KEY"]
    else:
        try:
            from azure.identity import DefaultAzureCredential
            tok = DefaultAzureCredential().get_token(
                "https://cognitiveservices.azure.com/.default"
            )
            headers["Authorization"] = f"Bearer {tok.token}"
        except Exception:
            return None

    # generate aspect-matched, slightly oversampled; _b64_to_card crops to area
    body = {
        "prompt": prompt,
        "n": 1,
        "width": 1024,
        "height": 1120,
        "output_format": "png",
        "model": deployment,
    }
    # FLUX content moderation is non-deterministic: an identical prompt may be
    # refused (empty data, stop_reason="refusal") then accepted. Retry a few times.
    attempts = int(os.environ.get("FLUX_MAX_ATTEMPTS", "8"))
    last = None
    for i in range(attempts):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=180)
            if r.status_code != 200:
                print(f"FLUX generation failed: HTTP {r.status_code} {r.text[:200]}")
                return None
            j = r.json()
            data = j.get("data") or []
            if data:
                return _b64_to_card(data[0]["b64_json"])
            last = j.get("stop_reason")
            print(f"FLUX attempt {i + 1}/{attempts}: no image (stop_reason={last}); retrying")
        except Exception as e:
            print("FLUX image generation failed:", e)
            return None
    print(f"FLUX gave up after {attempts} attempts (last stop_reason={last})")
    return None


def gen_openai(prompt):
    """Generate via OpenAI Images API. Returns a PIL.Image or None on failure."""
    try:
        from openai import OpenAI
    except Exception:
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        client = OpenAI()
        resp = client.images.generate(
            model="gpt-image-1", prompt=prompt, size="1024x1024", n=1,
        )
        return _b64_to_card(resp.data[0].b64_json)
    except Exception as e:
        print("OpenAI image generation failed:", e)
        return None


def gen_azure(prompt):
    """Generate via Azure OpenAI Images (Microsoft-internal path).

    Requires:
      * AZURE_OPENAI_ENDPOINT          e.g. https://<resource>.openai.azure.com
      * AZURE_OPENAI_IMAGE_DEPLOYMENT  the image model deployment name
                                       (e.g. a gpt-image-1 or dall-e-3 deployment)
    Auth (either one):
      * AZURE_OPENAI_API_KEY           resource key, OR
      * EntraID via azure-identity (DefaultAzureCredential / `az login`)
    Returns a PIL.Image or None on failure.
    """
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_IMAGE_DEPLOYMENT")
    if not endpoint or not deployment:
        return None
    try:
        from openai import AzureOpenAI
    except Exception:
        return None
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    try:
        if os.environ.get("AZURE_OPENAI_API_KEY"):
            client = AzureOpenAI(
                azure_endpoint=endpoint, api_version=api_version,
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
            )
        else:
            from azure.identity import (
                DefaultAzureCredential, get_bearer_token_provider,
            )
            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            client = AzureOpenAI(
                azure_endpoint=endpoint, api_version=api_version,
                azure_ad_token_provider=token_provider,
            )
        resp = client.images.generate(
            model=deployment, prompt=prompt, size="1024x1024", n=1,
        )
        return _b64_to_card(resp.data[0].b64_json)
    except Exception as e:
        print("Azure OpenAI image generation failed:", e)
        return None


def _b64_to_card(b64_json):
    """Decode a base64 PNG and crop/resize to the card illustration area."""
    from io import BytesIO
    img = Image.open(BytesIO(base64.b64decode(b64_json))).convert("RGB")
    ar = W / H
    cw = int(img.height * ar)
    img = img.crop(((img.width - cw) // 2, 0,
                    (img.width - cw) // 2 + cw, img.height))
    return img.resize((W, H), Image.LANCZOS)


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
    img, backend = gen_flux(prompt), "flux-2-pro"
    if img is None:
        img, backend = gen_azure(prompt), "azure-openai"
    if img is None:
        img, backend = gen_openai(prompt), "openai"
    if img is None:
        img, backend = gen_procedural(), "procedural-fallback"
    os.makedirs("illustration", exist_ok=True)
    img.save(OUT)
    print(f"wrote {OUT} ({backend}) {img.size}")


if __name__ == "__main__":
    main()
