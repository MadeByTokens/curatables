"""Chaos run against a Curatables box: a parent and two kids click and
type at random at the same time, plus targeted stress loops. Every page
is checked for server errors, the Oops / not-found pages and JS errors.

    python chaos.py BASE_URL PARENT_PW MINUTES OUT_DIR CLIP_DIR
"""

import asyncio
import contextlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

B, PARENT_PW, MINUTES, OUT, CLIPS = sys.argv[1], sys.argv[2], float(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5])
OUT.mkdir(parents=True, exist_ok=True)
CLIP_FILES = sorted(CLIPS.iterdir())
random.seed(int(time.time()))

NASTY = ["", " ", "x" * 5000, "😀🐸🚀" * 40, "<script>alert(1)</script>", "<b>bold</b> & \"q\" 'q'",
         "Robert'); DROP TABLE videos;--", "‮evil‬", "名前の動画", "line1\nline2\r\nline3",
         "   padded   ", "{{7*7}} ${7*7}", "../../etc/passwd", "\t", "0", "-1", "999999999999",
         "Dinos", "Trains", "Frogs", "space song", "a,b,,c, ,d", "#tag", "🙈"]

# Things that would end the test itself rather than test the app.
BLOCK = re.compile(r"/(logout|reboot|shutdown|network-restart|restart$|wifi|drives|linux-password|ssh|updates|"
                   r"backups/restore|settings/password|settings/move-data|settings/advanced|settings/update|clock|diagnostics/update-downloader|"
                   r"setup|profiles/\d+/delete)(/|$|\?)|^/parent/system/(restart|reboot|shutdown)")
ERROR_SIGNS = [("oops", re.compile(r"Oops! That didn't work|Something went wrong")),
               ("not-found", re.compile(r"There is no page at this address|Page not found")),
               ("raw-error", re.compile(r'^\{"detail"|Internal Server Error|Method Not Allowed'))]

findings: list[dict] = []
counts: dict[str, int] = {}
STOP = time.time() + MINUTES * 60
logf = open(OUT / "actions.log", "w")


def note(who, msg):
    line = f"{time.strftime('%H:%M:%S')} [{who}] {msg}"
    logf.write(line + "\n")
    logf.flush()


def finding(who, kind, detail, trail):
    findings.append({"t": time.strftime("%H:%M:%S"), "who": who, "kind": kind, "detail": detail[:500],
                     "trail": list(trail)[-6:]})
    note(who, f"!!! {kind}: {detail[:200]}")


class Actor:
    def __init__(self, name, ctx, kid=None, pin=None):
        self.name, self.ctx, self.kid, self.pin = name, ctx, kid, pin
        self.trail: list[str] = []
        self.page = None

    async def start(self):
        self.page = await self.ctx.new_page()
        self.page.set_default_timeout(45000)
        self.page.on("pageerror", lambda e: finding(self.name, "js-error", str(e), self.trail))
        self.page.on("dialog", lambda d: asyncio.ensure_future(
            d.accept() if random.random() < 0.8 else d.dismiss()))
        self.page.on("download", lambda d: asyncio.ensure_future(self._download(d)))
        self.page.on("response", self._response)

    async def _download(self, d):
        try:
            path = await d.path()
            size = await asyncio.to_thread(os.path.getsize, path) if path else 0
            counts["downloads"] = counts.get("downloads", 0) + 1
            note(self.name, f"downloaded {d.suggested_filename} {size} bytes")
            if size == 0:
                finding(self.name, "empty-download", d.url, self.trail)
        except Exception as e:
            finding(self.name, "download-failed", f"{d.url}: {e}", self.trail)

    def _response(self, r):
        if r.status >= 500 and r.status != 503:
            finding(self.name, f"http-{r.status}", f"{r.request.method} {r.url}", self.trail)

    async def check_page(self):
        try:
            body = await self.page.locator("body").inner_text(timeout=15000)
        except Exception:
            return
        for kind, rx in ERROR_SIGNS:
            if rx.search(body):
                finding(self.name, kind, self.page.url, self.trail)

    async def ensure_signed_in(self):
        url = self.page.url
        if self.kid is None:
            if "/parent/login" in url or not url.startswith(B):
                await self.page.goto(B + "/parent/login")
                if "/parent/login" in self.page.url:
                    await self.page.locator('input[type="password"]').fill(PARENT_PW)
                    await self.page.locator("form button[type=submit]").first.click()
                    await self.page.wait_for_load_state()
            return
        if "/parent" in url or not url.startswith(B):
            await self.page.goto(B + "/")
        if re.search(r"/profiles(\?|$|/)", self.page.url):
            await self.page.goto(B + "/profiles")
            btn = self.page.locator('form[action="/profiles/select"]', has_text=self.kid).locator("button")
            if await btn.count() == 0:
                return
            await btn.first.click()
            await self.page.wait_for_load_state()
            if self.pin:
                for e in self.pin:
                    await self.page.locator("button", has_text=e).first.click()
                await self.page.wait_for_url(lambda u: "/profiles/" not in u, timeout=20000)
            note(self.name, "signed in")

    async def fill_form(self, form):
        for el in await form.locator("input, textarea, select").all():
            # Fields come and go as the page reacts; a field that can't
            # be filled is simply left alone.
            with contextlib.suppress(Exception):
                if not await el.is_visible():
                    continue
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                typ = (await el.get_attribute("type") or "").lower()
                if tag == "select":
                    opts = await el.locator("option").evaluate_all("os => os.map(o => o.value)")
                    if opts:
                        await el.select_option(random.choice(opts))
                elif typ in ("checkbox", "radio"):
                    if random.random() < 0.5:
                        await el.click()
                elif typ == "file":
                    await el.set_input_files(str(random.choice(CLIP_FILES)))
                elif typ in ("hidden", "submit", "button", "password"):
                    continue
                elif typ == "number":
                    await el.fill(random.choice(["0", "1", "3", "-5", "100000", "2.5", ""]))
                else:
                    if random.random() < 0.85:
                        await el.fill(random.choice(NASTY))

    async def step(self):
        await self.ensure_signed_in()
        r = random.random()
        if r < 0.05:
            self.trail.append("BACK " + self.page.url)
            await self.page.go_back()
            return
        if r < 0.09:
            self.trail.append("RELOAD " + self.page.url)
            await self.page.reload()
            return
        if r < 0.12:
            # Straight to an old address from the trail, like a stale tab.
            olds = [t.split(" ", 1)[1] for t in self.trail if t.startswith(("GOTO", "LINK"))]
            if olds:
                u = random.choice(olds)
                self.trail.append("STALE " + u)
                await self.page.goto(u)
                return
        links = await self.page.locator("a[href]").evaluate_all(
            "as => as.filter(a => a.offsetParent !== null).map(a => a.getAttribute('href'))")
        links = [h for h in links if h and h.startswith("/") and not BLOCK.search(h.split('#')[0])
                 and not h.startswith(("/media/video", "/static/"))]
        if not self.kid:
            links = [h for h in links if not h.startswith(("/profiles", "/watch"))] or links
        buttons = await self.page.locator("form button, form input[type=submit], summary").all()
        usable = []
        for b in buttons:
            with contextlib.suppress(Exception):  # gone since it was listed
                if not await b.is_visible():
                    continue
                action = await b.evaluate(
                    "b => (b.form || b.closest('form') || {}).getAttribute ? (b.form || b.closest('form')).getAttribute('action') || '' : 'summary'")
                path = action.replace(B, "")
                if path != "summary" and BLOCK.search(path):
                    continue
                if not self.kid and path.startswith("/profiles"):
                    continue
                usable.append((b, path))
        if usable and (random.random() < 0.55 or not links):
            b, path = random.choice(usable)
            label = (await b.inner_text()).strip()[:30] if path != "summary" else "summary"
            if path != "summary":
                form = b.locator("xpath=ancestor::form[1]")
                if await form.count():
                    await self.fill_form(form)
            self.trail.append(f"SUBMIT {path} [{label}] on {self.page.url}")
            note(self.name, self.trail[-1])
            counts[self.name + "-submit"] = counts.get(self.name + "-submit", 0) + 1
            await b.click(no_wait_after=False, timeout=30000)
            if random.random() < 0.08:  # impatient double tap; often the button is gone
                with contextlib.suppress(Exception):
                    await b.click(timeout=2000)
                    self.trail.append("DOUBLE-TAP")
            await self.page.wait_for_load_state()
            return
        if links:
            h = random.choice(links)
            self.trail.append("LINK " + B + h)
            note(self.name, "LINK " + h)
            counts[self.name + "-link"] = counts.get(self.name + "-link", 0) + 1
            await self.page.goto(B + h)

    async def run(self):
        await self.start()
        await self.page.goto(B + ("/parent/" if self.kid is None else "/"))
        while time.time() < STOP:
            try:
                await self.step()
                await self.check_page()
            except Exception as e:
                msg = str(e).split("\n")[0]
                note(self.name, f"step error: {msg[:200]}")
                counts[self.name + "-stuck"] = counts.get(self.name + "-stuck", 0) + 1
                try:
                    await self.page.goto(B + ("/parent/" if self.kid is None else "/"))
                except Exception:
                    await asyncio.sleep(5)


async def kid_targeted(name, ctx, kid, pin):
    """Download everything, hammer Home and Hide."""
    a = Actor(name, ctx, kid, pin)
    await a.start()
    await a.page.goto(B + "/")
    while time.time() < STOP:
        try:
            await a.ensure_signed_in()
            await a.page.goto(B + "/all")
            ids = sorted(set(re.findall(r"/watch/([\w-]+)", await a.page.content())))
            for vid in ids:
                r = await a.page.request.get(f"{B}/media/video/{vid}?download=1")
                counts["api-downloads"] = counts.get("api-downloads", 0) + 1
                if r.status not in (200, 403, 404):
                    finding(name, f"download-{r.status}", vid, a.trail)
                elif r.status == 200 and len(await r.body()) == 0:
                    finding(name, "download-empty", vid, a.trail)
            # Home: random adds / moves / removes, as fast as possible.
            for _ in range(15):
                op = random.choice(["add", "move", "remove", "hide", "unhide"])
                vid = random.choice(ids) if ids else "nope"
                if op == "add":
                    data = {"video_id": vid, "slot": str(random.choice([0, 1, 5, 23, 24, -1, 99]))}
                elif op == "move":
                    data = {"source": str(random.randint(-1, 25)), "target": str(random.randint(-1, 25))}
                else:
                    data = {"video_id": vid}
                path = {"add": "/home/add", "move": "/home/move", "remove": "/home/remove",
                        "hide": f"/video/{vid}/hide", "unhide": f"/video/{vid}/unhide"}[op]
                # Through the real page, so the form's CSRF token comes along.
                token = await a.page.evaluate(
                    "() => (document.querySelector('input[name=csrf_token]') || {}).value || ''")
                if not token and ids:
                    await a.page.goto(f"{B}/watch/{ids[0]}")
                    token = await a.page.evaluate(
                        "() => (document.querySelector('input[name=csrf_token]') || {}).value || ''")
                if not token:
                    note(name, "no videos to take a form token from; skipping")
                    break
                r = await a.page.request.post(B + path, form=data, max_redirects=0,
                                              headers={"X-CSRF-Token": token})
                a.trail.append(f"POST {path} {data}")
                if r.status >= 400 and not (r.status == 403 and op in ("hide", "unhide")):
                    finding(name, f"targeted-{op}-{r.status}", json.dumps(data), a.trail)
            await a.page.goto(B + "/")
            await a.check_page()
            cards = await a.page.locator(".home-grid .video-card").count()
            note(name, f"home cards: {cards}")
        except Exception as e:
            note(name, f"targeted error: {str(e).splitlines()[0][:200]}")
            await asyncio.sleep(3)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ipad = dict(viewport={"width": 820, "height": 1180}, has_touch=True, accept_downloads=True)
        phone = dict(viewport={"width": 375, "height": 667}, is_mobile=True, has_touch=True, accept_downloads=True)
        actors = [
            Actor("parent", await browser.new_context(**phone)),
            Actor("kiki", await browser.new_context(**ipad), "Kiki", ["🐸", "🐶", "🐱"]),
            Actor("ben", await browser.new_context(**ipad), "Ben"),
        ]
        jobs = [a.run() for a in actors]
        jobs.append(kid_targeted("kiki-hammer", await browser.new_context(**ipad), "Kiki", ["🐸", "🐶", "🐱"]))
        await asyncio.gather(*jobs)
        await browser.close()
    (OUT / "findings.json").write_text(json.dumps(findings, indent=1, ensure_ascii=False))
    (OUT / "counts.json").write_text(json.dumps(counts, indent=1))
    kinds: dict[str, int] = {}
    for f in findings:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    print("counts", json.dumps(counts))
    print("findings", json.dumps(kinds))


asyncio.run(main())
