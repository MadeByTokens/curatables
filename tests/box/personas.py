"""Kid personas against a whole box: how children with special needs,
autistic children among them, use the screens, played out in a real
browser. See docs/testing/README.md for when and how to run them.

    python tests/box/personas.py BASE_URL OUT_DIR [--idle-minutes N] [--only NAME,NAME]

Needs a box set up by tests/box/seed.py (kids Kiki, picture PIN
frog-dog-cat, and Ben, no PIN, both with every feature on). Each
persona prints PASS or FAIL with what it saw; OUT_DIR gets a
screenshot of every failure and findings.json.
"""

import argparse
import json
import random
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PIN = ["🐸", "🐶", "🐱"]
WRONG_PIN = ["🐢", "🐢", "🐢"]
ERROR_TEXT = re.compile(r"Oops! That didn't work|Something went wrong|There is no page at this address|"
                        r'^\{"detail"|Internal Server Error|Method Not Allowed')
TAP_TARGET = 44  # CSS px: Apple's and WCAG's minimum for a finger

args = argparse.Namespace()
results: list[dict] = []


class Fail(Exception):
    pass


def sign_in(page, kid="Kiki", pin=PIN):
    page.goto(args.base + "/profiles")
    if not re.search(r"/profiles", page.url):
        return
    page.locator('form[action="/profiles/select"]', has_text=kid).locator("button").click()
    page.wait_for_load_state()
    if pin and "/profiles/" in page.url:
        for e in pin:
            page.locator("button", has_text=e).first.click()
        page.wait_for_url(lambda u: "/profiles/" not in u, timeout=20000)


def check(page, what):
    """The page is a normal kid page: no error text, not bounced out."""
    body = page.locator("body").inner_text(timeout=20000)
    if ERROR_TEXT.search(body):
        raise Fail(f"{what}: error page at {page.url}")
    if "/parent/login" in page.url:
        raise Fail(f"{what}: kid ended up at the parent login ({page.url})")


def video_ids(page):
    page.goto(args.base + "/all")
    ids = sorted(set(re.findall(r"/watch/([\w-]+)", page.content())))
    if not ids:
        raise Fail("no videos to work with: run seed.py first")
    return ids


def replayer(page):
    """Watches one favourite to the end and starts it again, many times."""
    vid = video_ids(page)[0]
    page.goto(f"{args.base}/watch/{vid}")
    for i in range(15):
        page.evaluate("() => { const v = document.querySelector('video'); v.muted = true;"
                      " v.currentTime = Math.max(0, v.duration - 0.5); return v.play(); }")
        page.wait_for_function("() => document.querySelector('video').ended", timeout=20000)
        again = page.locator("#play-again")
        if again.count() and again.is_visible():
            again.click()
        else:
            page.evaluate("() => { const v = document.querySelector('video'); v.currentTime = 0; v.play(); }")
        page.wait_for_function("() => { const v = document.querySelector('video');"
                               " return !v.paused && v.currentTime > 0.2; }", timeout=20000)
        check(page, f"replay {i}")
    page.reload()
    check(page, "reload after replays")


def repeat_tapper(page):
    """Taps the same button again and again, fast."""
    vid = video_ids(page)[1]
    page.goto(f"{args.base}/watch/{vid}")
    react = page.locator("button[data-emoji], .reaction-btn").first
    if react.count():
        for _ in range(30):
            react.click(no_wait_after=True)
        page.wait_for_timeout(1500)
        check(page, "reaction taps")
    for _ in range(20):
        page.locator(".header-nav a[href='/']").click(no_wait_after=True)
    page.wait_for_load_state()
    check(page, "Home taps")
    page.goto(args.base + "/?edit=1")
    for _ in range(10):
        move = page.get_by_role("link", name=re.compile("Move"))
        if not move.count():
            break
        move.first.click()
        page.wait_for_load_state()
        spot = page.locator('form[action="/home/move"] button')
        if spot.count():
            spot.nth(random.randrange(spot.count())).click()
            page.wait_for_load_state()
        check(page, "Home moves")


def back_masher(page):
    """Visits a few pages, then holds Back down, then Forward."""
    ids = video_ids(page)
    for path in ["/", "/all", f"/watch/{ids[0]}", "/help", f"/watch/{ids[-1]}", "/?edit=1", "/wishlist"]:
        page.goto(args.base + path)
    for i in range(25):
        page.go_back()
        if page.url.startswith(args.base):
            check(page, f"back {i}")
    for i in range(10):
        page.go_forward()
        if page.url.startswith(args.base):
            check(page, f"forward {i}")


def rapid_switcher(page):
    """Opens video after video without waiting for any to load."""
    ids = video_ids(page)
    for _ in range(15):
        page.goto(f"{args.base}/watch/{random.choice(ids)}", wait_until="commit")
    page.wait_for_load_state()
    check(page, "after switching")


def same_place(page):
    """The Home button sits in the same spot on every page."""
    ids = video_ids(page)
    spots = {}
    for path in ["/", "/all", f"/watch/{ids[0]}", "/help", "/wishlist", "/?edit=1", f"/video/{ids[0]}/edit"]:
        page.goto(args.base + path)
        box = page.locator(".header-nav a[href='/']").bounding_box()
        spots[path] = (round(box["x"]), round(box["y"])) if box else None
    if len(set(spots.values())) != 1:
        raise Fail(f"Home button moves between pages: {spots}")


def big_zoom(page):
    """200% browser zoom (half the CSS width): no sideways scrolling, and
    every button big enough for a finger."""
    page.set_viewport_size({"width": 410, "height": 590})
    ids = video_ids(page)
    small = []
    for path in ["/", "/all", f"/watch/{ids[0]}", "/?edit=1", "/wishlist", "/help"]:
        page.goto(args.base + path)
        wide = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if wide > 1:
            raise Fail(f"{path} scrolls sideways by {wide}px at 200% zoom")
        for el in page.locator("main a, main button, .header-nav a").all():
            if not el.is_visible():
                continue
            b = el.bounding_box()
            if b and (b["width"] < TAP_TARGET or b["height"] < TAP_TARGET):
                small.append(f"{path} '{el.inner_text().strip()[:20]}' {int(b['width'])}x{int(b['height'])}")
    if small:
        raise Fail(f"{len(small)} targets under {TAP_TARGET}px: " + "; ".join(small[:12]))


def still(page):
    """Reduce Motion on: nothing on the screen moves."""
    page.emulate_media(reduced_motion="reduce")
    ids = video_ids(page)
    for path in ["/", "/wishlist?q=trains", f"/watch/{ids[0]}", "/?edit=1"]:
        page.goto(args.base + path)
        moving = page.evaluate("() => [...document.querySelectorAll('*')].filter(e => {"
                               " const s = getComputedStyle(e); return (s.animationName && s.animationName !== 'none')"
                               " || parseFloat(s.transitionDuration) > 0; }).map(e => e.className).slice(0, 5)")
        if moving:
            raise Fail(f"{path}: moving with Reduce Motion on: {moving}")


def key_masher(page):
    """Hammers the keyboard in the search box."""
    page.goto(args.base + "/search")
    box = page.locator("input[type=search], input[name=q]").first
    junk = ["aaaaaaaaaaaa", "🦖🦖🦖🦖", "dinasor", "TRAINS!!!", "   ", "<>", "'", "%", "\\", "ééé", "x" * 300]
    for _ in range(20):
        box.fill(random.choice(junk))
        box.press("Enter")
        page.wait_for_load_state()
        check(page, "search")
        box = page.locator("input[type=search], input[name=q]").first


def long_pause(page):
    """Leaves a video open for a long time, then comes back and taps."""
    vid = video_ids(page)[0]
    page.goto(f"{args.base}/watch/{vid}")
    page.wait_for_timeout(args.idle_minutes * 60 * 1000)
    page.locator(".header-nav a[href='/']").click()
    page.wait_for_load_state()
    if "/profiles" in page.url:
        raise Fail(f"signed out after {args.idle_minutes} minutes on a page")
    check(page, "after the pause")


def pin_storm(page):
    """Taps the wrong pictures until locked out: the message is calm and
    Back works. Leaves Kiki locked on this address for a few minutes."""
    page.goto(args.base + "/profiles/switch")
    for _ in range(6):
        page.goto(args.base + "/profiles")
        page.locator('form[action="/profiles/select"]', has_text="Kiki").locator("button").click()
        page.wait_for_load_state()
        if not page.locator(".pin-pad").count():
            break
        for e in WRONG_PIN:
            page.locator("button", has_text=e).first.click()
        page.wait_for_load_state()
    text = page.locator("main").inner_text()
    if "Wait a little" not in text:
        raise Fail(f"no lock message after wrong PINs: {text[:200]}")
    for harsh in ("invalid", "denied", "error", "failed", "blocked"):
        if harsh in text.lower():
            raise Fail(f"lock message says '{harsh}': {text[:200]}")
    page.get_by_role("link", name=re.compile("Back")).click()
    page.wait_for_load_state()
    if not page.url.rstrip("/").endswith("/profiles"):
        raise Fail(f"Back from the lock went to {page.url}")


PERSONAS = [replayer, repeat_tapper, back_masher, rapid_switcher, same_place, big_zoom, still,
            key_masher, long_pause, pin_storm]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("base")
    p.add_argument("out", type=Path)
    p.add_argument("--idle-minutes", type=float, default=20)
    p.add_argument("--only", default="")
    p.parse_args(namespace=args)
    args.base = args.base.rstrip("/")
    args.out.mkdir(parents=True, exist_ok=True)
    chosen = [f for f in PERSONAS if not args.only or f.__name__ in args.only.split(",")]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for persona in chosen:
            ctx = browser.new_context(viewport={"width": 820, "height": 1180}, has_touch=True)
            page = ctx.new_page()
            page.set_default_timeout(45000)
            js_errors = []
            page.on("pageerror", lambda e, errs=js_errors: errs.append(str(e)))
            started = time.time()
            try:
                if persona is not pin_storm:
                    sign_in(page)
                persona(page)
                if js_errors:
                    raise Fail(f"JavaScript errors: {js_errors[:3]}")
                outcome, detail = "PASS", ""
            except Exception as e:
                outcome, detail = "FAIL", str(e).splitlines()[0][:600]
                page.screenshot(path=str(args.out / f"{persona.__name__}.png"), full_page=True)
            took = round(time.time() - started)
            results.append({"persona": persona.__name__, "outcome": outcome, "detail": detail, "seconds": took})
            print(f"{outcome} {persona.__name__} ({took}s) {detail}", flush=True)
            ctx.close()
        browser.close()
    (args.out / "findings.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
