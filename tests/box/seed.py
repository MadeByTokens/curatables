"""Readies a fresh box for chaos.py.

    python seed.py BASE_URL setup PARENT_PW ADMIN_PW     # both setup steps, SSH on
    python seed.py BASE_URL content PARENT_PW CLIP_DIR   # videos and two kids
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

B, STEP, PW = sys.argv[1], sys.argv[2], sys.argv[3]


async def login(page):
    await page.goto(B + "/parent/login")
    if "/parent/login" in page.url:
        await page.locator('input[type="password"]').fill(PW)
        await page.locator("form button[type=submit]").first.click()
        await page.wait_for_load_state()


async def setup(page, admin_pw):
    await page.goto(B + "/")
    await page.get_by_label("Password", exact=True).fill(PW)
    await page.get_by_label("Type it again").fill(PW)
    await page.get_by_role("button", name="Set password & start").click()
    await page.wait_for_url("**/setup/system")
    await page.get_by_label("New password for admin").fill(admin_pw)
    await page.get_by_label("Type it again").fill(admin_pw)
    await page.get_by_label("Allow SSH logins").check()
    await page.get_by_role("button", name="Set password & finish").click()
    await page.wait_for_url(lambda u: "/setup" not in u, timeout=180000)
    print("setup done:", page.url)


def _clips(folder):
    return [c for c in sorted(Path(folder).glob("*.mp4")) if c.name != "broken.mp4"]


async def content(page, clips):
    await login(page)
    for clip in _clips(clips):
        await page.goto(B + "/parent/upload")
        await page.locator('input[name="title"]').fill(clip.stem)
        await page.locator('input[type="file"]').set_input_files(str(clip))
        await page.get_by_role("button", name="Start upload").click()
        await page.wait_for_url(lambda u: "/parent/content" in u, timeout=180000)
        print("uploaded", clip.stem)
    for name, pin in (("Kiki", ("5", "1", "2")), ("Ben", None)):
        await page.goto(B + "/parent/profiles/create")
        await page.locator('input[name="display_name"]').fill(name)
        if pin:
            await page.locator('input[name="pin_style"][value="pictures"]').check()
            for i, v in enumerate(pin, 1):
                await page.select_option(f'select[name="pin_pic_{i}"]', v)
        await page.select_option('select[name="search_mode"]', index=1)
        for box in ("allow_reactions", "allow_comments", "allow_tags", "allow_customize", "allow_upload",
                    "allow_download", "allow_hide", "allow_wishlist"):
            await page.locator(f'input[name="{box}"]').check()
        if name == "Kiki":
            await page.select_option('select[name="auto_add"]', "1")
            await page.locator('input[name="add_channel"]').fill("https://www.youtube.com/@klt")
        await page.get_by_role("button", name="Add kid").click()
        await page.wait_for_load_state()
        print("kid", name, page.url)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(120000)
        if STEP == "setup":
            await setup(page, sys.argv[4])
        else:
            await content(page, sys.argv[4])
        await browser.close()


asyncio.run(main())
