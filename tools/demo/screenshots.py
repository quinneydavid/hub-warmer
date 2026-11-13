#!/usr/bin/env python3
"""Regenerate docs/screenshots/*.png from the demo (run.py): python3 tools/demo/screenshots.py

Needs Playwright with Chromium, and optionally Pillow to make the files smaller:
    pip install playwright pillow && playwright install chromium
Takes about two minutes: it goes through setup, lets the warmer make three passes, then takes the pictures."""
import argparse, os, socket, subprocess, sys, tempfile, time, urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PLEX_URL = "http://plex.example:32400"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "screenshots"))
    ap.add_argument("--scheme", default="light", choices=("light", "dark"))
    ap.add_argument("--no-shrink", action="store_true", help="keep the full-colour files")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    data = tempfile.TemporaryDirectory(prefix="hub-warmer-demo-")
    demo = subprocess.Popen([sys.executable, os.path.join(HERE, "run.py"), "--port", str(port), "--data-dir", data.name],
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            take(browser, base, a)
            browser.close()
    finally:
        demo.terminate()
        demo.wait()
        data.cleanup()
    for name in sorted(os.listdir(a.out)):
        if name.endswith(".png"):
            path = os.path.join(a.out, name)
            if not a.no_shrink:
                shrink(path)
            print(f"{os.path.getsize(path) // 1024:5d} KB  {os.path.relpath(path, ROOT)}")


def take(browser, base, a):
    def context(width, height):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                                  color_scheme=a.scheme, locale="en-GB")
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base) else r.abort())   # local only
        return ctx

    def shot(page, name, first=None, last=None, pad=14):
        """The page from its top (or from the element `first`) down to the bottom of the element `last`."""
        page.wait_for_timeout(300)
        full = page.evaluate("[window.innerWidth, document.documentElement.scrollHeight]")
        box = lambda sel: page.locator(sel).first.bounding_box()      # noqa: E731
        top = max(0, box(first)["y"] - pad) if first else 0
        bottom = min(full[1], box(last)["y"] + box(last)["height"] + pad) if last else full[1]
        page.screenshot(path=os.path.join(a.out, name + ".png"), full_page=True,
                        clip={"x": 0, "y": top, "width": full[0], "height": bottom - top})

    def status(page):
        return page.evaluate("async () => { await load(); return D ? {state: D.state, last: D.last_warm} : {} }")

    def wait_for_pass(page, after):
        for _ in range(180):
            s = status(page)
            if s.get("state") == "idle" and s.get("last", 0) > after:
                return s["last"]
            time.sleep(1)
        raise SystemExit("the warmer did not finish a pass")

    card = lambda title: f".card:has(> h2:text-is('{title}'))"        # noqa: E731
    ctx = context(1280, 470)
    page = ctx.new_page()

    # first run: the address, then Sign in with Plex
    page.goto(base)
    page.fill("#addr", PLEX_URL)
    page.click("text=Test and save")
    page.wait_for_selector("text=Sign in with Plex")
    shot(page, "setup", last="body")
    page.click("text=Sign in with Plex")
    page.wait_for_selector("#nav a")

    # three passes: the first starts on sign-in, two more with Run now, a little apart so some rows have expired
    last = wait_for_pass(page, 0)
    for _ in range(2):
        time.sleep(25)
        page.click("#warm")
        last = wait_for_pass(page, last)
    time.sleep(3)
    status(page)

    page.set_viewport_size({"width": 1280, "height": 800})
    shot(page, "overview", last=card("Hourly load times"))
    shot(page, "slow-loads", first=card("Slow loads"), last=card("Slow loads"))

    page.evaluate("location.hash = '#accounts'")
    page.click("details.acct summary .nm:text-is('alex')")
    shot(page, "accounts", last="details.acct >> nth=-1")

    page.evaluate("location.hash = '#settings'")
    shot(page, "settings", last=card("Settings"))

    phone = context(390, 844)
    phone.add_cookies(ctx.cookies())
    page = phone.new_page()
    page.goto(base)
    page.wait_for_selector(".tiles")
    shot(page, "overview-phone", last=card("Load time distribution"))


def shrink(path):
    """256 colours are plenty for a flat page and make the file a third of the size. The most used colours
    (backgrounds, text, chart bands) are put back exactly, so the page keeps its tint."""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path).convert("RGB")
    q = im.quantize(256, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE)
    palette = q.getpalette()
    for _, rgb in sorted(im.getcolors(1 << 24))[-24:]:                # most used last, so it wins a shared slot
        i = Image.new("RGB", (1, 1), rgb).quantize(palette=q, dither=Image.Dither.NONE).getpixel((0, 0))
        palette[3 * i:3 * i + 3] = rgb
    q.putpalette(palette)
    q.save(path, optimize=True)


if __name__ == "__main__":
    main()
