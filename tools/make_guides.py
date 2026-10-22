"""Make the animated guides: step-by-step screenshots of a made-up family's
box, each with the spot to tap, for the guide player on the box
(app/static/guides/) and as animated pictures for the written guides
(docs/guide/img/anim/).

    python tools/make_guides.py [--only kids|parents] [--tour ID] [--keep DIR]

Needs Playwright with Chromium (`pip install playwright && playwright
install chromium`), Pillow, and ffmpeg. It starts its own Curatables on
a fresh, throw-away data folder (a demo family with picture
thumbnails it draws itself), with a pretend YouTube, so no real video
or account ever appears. Each tour runs in English, Spanish and
Portuguese for the kids' guide, English for the parents'. Run it again
whenever a screen in a tour changes.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "app" / "static" / "guides"
DOCS_OUT = ROOT / "docs" / "guide" / "img" / "anim"
KID_LANGS = ("en", "es", "pt-BR")
KID_VIEW = {"width": 768, "height": 1024}
PARENT_VIEW = {"width": 1024, "height": 720}
PARENT_PW = "guide-parent-pass"
MIA_PIN = "512"  # frog, dog, cat
QUALITY = 72
PLAYER_STAND_IN = ("<html><body style='margin:0;height:100vh;display:flex;align-items:center;"
                   "justify-content:center;background:#222;color:#fff;font:26px sans-serif'>"
                   "&#9654;&nbsp; The video plays here</body></html>")
CLIP = Path(tempfile.gettempdir()) / "curatables-guide-clip.mp4"

# --- the made-up family's library -------------------------------------------

LIBRARY = {
    "Animals": [("\U0001f418", "Elephant bath time", "#f4a261"), ("\U0001f986", "Ducks at the pond", "#2a9d8f"),
                ("\U0001f431", "Sleepy cats", "#e9c46a"), ("\U0001f992", "Tall giraffes", "#e76f51")],
    "Trains": [("\U0001f682", "Steam train in the hills", "#264653"), ("\U0001f686", "Fast trains", "#457b9d"),
               ("\U0001f6e4️", "Building a toy railway", "#8ab17d")],
    "Songs": [("\U0001f3b5", "Counting song", "#6d597a"), ("\U0001f941", "Drum circle", "#b56576"),
              ("\U0001f327️", "Rainy day lullaby", "#355070")],
    "Space": [("\U0001f680", "Rocket launch", "#1d3557"), ("\U0001f319", "Goodnight moon", "#3d405b")],
}
# What the pretend YouTube finds.
FOUND = [("\U0001f682", "Trains for kids: big and small", "#457b9d"),
         ("\U0001f69c", "Tractors on the farm", "#8ab17d"),
         ("\U0001f681", "Helicopter ride", "#e76f51"),
         ("\U0001f69a", "Trucks at work", "#e9c46a")]


def _thumb_html(emoji: str, title: str, color: str) -> str:
    return f"""<html><body style="margin:0"><div style="width:640px;height:360px;
      background:linear-gradient(135deg,{color},#ffffff55);display:flex;align-items:center;
      justify-content:center;font-size:190px;font-family:'Noto Color Emoji',sans-serif">{emoji}</div>
      </body></html>"""


def draw_thumbnails(browser, items: list[tuple[str, str, str]]) -> dict[str, bytes]:
    """{title: JPEG bytes}: a big emoji on a colour, one per video."""
    from PIL import Image
    page = browser.new_page(viewport={"width": 640, "height": 360})
    out = {}
    for emoji, title, color in items:
        page.set_content(_thumb_html(emoji, title, color))
        png = page.screenshot()
        buf = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").save(buf, "JPEG", quality=85)
        out[title] = buf.getvalue()
    page.close()
    return out


def seed(data_dir: Path, thumbs: dict[str, bytes], kid_lang: str) -> None:
    from app.config import ensure_directories, load_config
    from app.db.connection import create_connection, get_db_path
    from app.db.schema import init_schema
    from app.models import Video
    from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
    from app.services.auth import AuthService
    from app.services.profiles import ProfileService
    from app.services.thumbnails import ThumbnailService

    config = load_config(str(data_dir))
    config.wishlist.max_results = 4
    config.save() if hasattr(config, "save") else None
    ensure_directories(config)
    AuthService(config).set_password(PARENT_PW)
    conn = create_connection(get_db_path(config.data_dir))
    init_schema(conn)
    channels, videos = ChannelRepository(conn), VideoRepository(conn)
    thumb_svc = ThumbnailService(config.data_dir)
    clip = CLIP
    if not clip.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                       "color=c=0x2a9d8f:size=640x360:rate=25:duration=3", "-f", "lavfi", "-i",
                       "sine=frequency=330:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(clip)], check=True)
    n = 0
    for channel_name, items in LIBRARY.items():
        cid = channels.create(channel_name)
        for _emoji, title, _color in items:
            vid = f"guide_{n:03d}"
            vdir = config.data_dir / "videos" / vid
            vdir.mkdir(parents=True, exist_ok=True)
            shutil.copy(clip, vdir / "video.mp4")
            path = thumb_svc.get_path(vid)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(thumbs[title])
            videos.insert(Video(video_id=vid, title=title, original_title=title, channel_id=cid,
                                channel_name=channel_name, status="active", download_status="ready",
                                storage_mode="cache", extractor="demo"))
            conn.execute("UPDATE videos SET added_at = datetime('now', ?) WHERE video_id = ?",
                         (f"-{n} hours", vid))
            n += 1
    conn.commit()
    profiles = ProfileService(ProfileRepository(conn))
    profiles.create(name="mia", display_name="Mia", avatar="unicorn", theme="playful",
                    pin=MIA_PIN, pin_style="pictures", language=kid_lang, search_mode="curated",
                    allow_wishlist=True)
    profiles.create(name="leo", display_name="Leo", avatar="rocket", allow_wishlist=True)
    conn.close()


# --- a pretend YouTube --------------------------------------------------------

def fake_backend(thumbs: dict[str, bytes]):
    from app.backends.base import VideoBackend, VideoMetadata

    def meta(i, emoji, title, color):
        raw = f"guide{i:06d}"[:11].ljust(11, "x")
        return VideoMetadata(video_id=raw, title=title, channel="Kids Channel", duration=240,
                             view_count=0, upload_date="", description="",
                             thumbnail_url=f"guide://{title}", extractor="youtube",
                             original_url=f"https://www.youtube.com/watch?v={raw}",
                             is_live=False, channel_id="UC" + "g" * 22)

    found = [meta(i, *f) for i, f in enumerate(FOUND)]

    class Pretend(VideoBackend):  # type: ignore[misc]
        last_error = None

        def search(self, query, max_results=12, newest_first=False):
            return list(found)

        def search_channels(self, query, max_results=12):
            return []

        def fetch_video_info(self, url):
            return next((m for m in found if m.video_id in url), None)

        def fetch_channel_videos(self, url, max_results=50):
            return "Kids Channel", list(found)

        def fetch_playlist(self, playlist_url, max_results=50):
            return "Kids Channel", list(found)

        def download_video(self, url, output_dir, *args, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            dest = output_dir / "video.mp4"
            shutil.copy(CLIP, dest)
            return dest

    return Pretend()


def patch_thumbnails(thumbs: dict[str, bytes]) -> None:
    """Thumbnails of the pretend results come from the drawings, not the
    internet."""
    from app.services.thumbnails import ThumbnailService

    def download(self, video_id, thumbnail_url=None):
        if thumbnail_url and thumbnail_url.startswith("guide://"):
            path = self.get_path(video_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(thumbs[thumbnail_url[len("guide://"):]])
            return path
        return None

    ThumbnailService.download = download  # type: ignore[method-assign]
    ThumbnailService.download_many = (  # type: ignore[method-assign]
        lambda self, items, workers=4: [download(self, v, u) for v, u in items])


class Server:
    def __init__(self, data_dir: Path, backend):
        import uvicorn

        from app.dependencies import get_backend
        from app.main import create_app
        app = create_app(data_dir=str(data_dir), log_level="warning")
        # Never announce "curatables.local": a real box on this network
        # owns that name.
        app.state.config.server.mdns_enabled = False
        app.dependency_overrides[get_backend] = lambda: backend
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        while not self.server.started:
            time.sleep(0.1)
        return f"http://127.0.0.1:{self.port}"

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


# --- tours ---------------------------------------------------------------------

Say = tuple  # (en, es, pt-BR) for the kids' guide, (en,) for the parents'


@dataclass
class Step:
    say: Say
    tap: Callable | None = None  # page -> Locator to tap (shown with the finger)
    fill: str = ""               # text typed into the tapped box instead of a tap
    choose: int = -1             # an option picked in the tapped list instead of a tap
    then: Callable | None = None  # extra page work after the tap


@dataclass
class Tour:
    id: str
    icon: str
    title: Say
    start: str
    steps: list[Step] = field(default_factory=list)
    signed_in: bool = True
    prepare: Callable | None = None
    needs: str = ""  # the kid switch this tour shows (see app/services/guides.py)
    # The templates the tour's pictures show (besides the menu, base.html):
    # when one changes, tests/test_guides.py asks for the tour to be made again.
    screens: tuple = ()


def L(css, nth=0, text=None):
    def find(page):
        loc = page.locator(css, has_text=text) if text else page.locator(css)
        return loc.nth(nth)
    return find


def _end_video(page):
    page.evaluate("() => { const v = document.querySelector('video');"
                  " v.dispatchEvent(new Event('ended')); }")
    page.wait_for_timeout(400)


KID_TOURS = [
    Tour("sign-in", "\U0001f464", ("Sign in", "Entrar", "Entrar"), "/profiles/switch", signed_in=False,
         screens=("kid/profiles.html", "kid/pin.html", "kid/my_home.html"),
         steps=[
        Step(("Tap your picture.", "Toca tu dibujo.", "Toque no seu desenho."),
             L('form[action="/profiles/select"] button', text="Mia")),
        Step(("Tap your secret pictures, one by one.", "Toca tus dibujos secretos, uno a uno.",
              "Toque nos seus desenhos secretos, um por um."), L('button.pin-key[data-digit="5"]')),
        Step(("The next one…", "El siguiente…", "O próximo…"), L('button.pin-key[data-digit="1"]')),
        Step(("…and the last one.", "…y el último.", "…e o último."), L('button.pin-key[data-digit="2"]')),
        Step(("You're in! This is your Home.", "¡Ya entraste! Este es tu Inicio.",
              "Você entrou! Este é o seu Início.")),
    ]),
    Tour("watch", "\U0001f3ac", ("Watch a video", "Ver un video", "Ver um vídeo"), "/",
         screens=("kid/my_home.html", "kid/watch.html"),
         steps=[
        Step(("Tap a video to watch it.", "Toca un video para verlo.", "Toque num vídeo para assistir."),
             L('a[href^="/watch/"]')),
        Step(("Tap the video to play it.", "Toca el video para empezar.",
              "Toque no vídeo para começar."), L("#player"), then=_end_video),
        Step(("At the end: Again, or Home for another one.", "Al final: Otra vez, o Inicio para otro.",
              "No fim: De novo, ou Início para outro."), L("#play-again")),
        Step(("It plays again from the start.", "Vuelve a empezar.", "Começa de novo.")),
    ]),
    Tour("home", "\U0001f3e0", ("Arrange your Home", "Arregla tu Inicio", "Arrume seu Início"), "/",
         screens=("kid/my_home.html", "kid/home_pick.html"),
         steps=[
        Step(("Tap Change my Home.", "Toca Cambiar mi Inicio.", "Toque em Mudar meu Início."),
             L('a[href="/?edit=1"]')),
        Step(("To move a video, tap Move…", "Para mover un video, toca Mover…",
              "Para mudar um vídeo de lugar, toque em Mover…"), L("a.home-tool", nth=0)),
        Step(("…then the spot where it goes.", "…y luego el lugar donde va.", "…e depois o lugar onde ele vai."),
             L("form.home-target button", nth=4)),
        Step(("Tap + to put a video in an empty spot.", "Toca + para poner un video en un lugar vacío.",
              "Toque em + para pôr um vídeo num lugar vazio."), L("a.home-empty")),
        Step(("Pick the video.", "Elige el video.", "Escolha o vídeo."), L("form.home-target button", nth=10)),
        Step(("Tap Done when it looks right.", "Toca Listo cuando esté como quieres.",
              "Toque em Pronto quando estiver do seu jeito."), L("a.home-done")),
        Step(("Your Home stays just like that.", "Tu Inicio se queda así.", "Seu Início fica assim.")),
    ]),
    Tour("channels", "\U0001f4fa", ("Channels", "Canales", "Canais"), "/",
         screens=("kid/my_home.html", "kid/channels.html", "kid/home.html", "kid/_channel_avatar.html"),
         steps=[
        Step(("Tap Channels.", "Toca Canales.", "Toque em Canais."), L('a.header-btn[href="/channels"]')),
        Step(("Tap a channel to see its videos.", "Toca un canal para ver sus videos.",
              "Toque num canal para ver os vídeos."), L("a.channel-card-link", nth=2)),
        Step(("Pick the order: A to Z goes by your names.", "Elige el orden: de la A a la Z usa tus nombres.",
              "Escolha a ordem: de A a Z usa os seus nomes."), L('button.sort-btn[value="az"]')),
        Step(("The channel keeps your order.", "El canal guarda tu orden.", "O canal guarda a sua ordem.")),
    ]),
    Tour("my-order", "\u2195\ufe0f", ("My order", "Mi orden", "Minha ordem"), "/channels",
         screens=("kid/channels.html", "kid/home.html", "kid/channel_arrange.html"), steps=[
        Step(("Open a channel.", "Abre un canal.", "Abra um canal."), L("a.channel-card-link", nth=1)),
        Step(("Tap My order. Nothing changes yet.", "Toca Mi orden. Todavía no cambia nada.",
              "Toque em Minha ordem. Nada muda ainda."), L('button.sort-btn[value="mine"]')),
        Step(("Tap Change the order.", "Toca Cambiar el orden.", "Toque em Mudar a ordem."),
             L('a[href$="/arrange"]')),
        Step(("Pick where to start from.", "Elige desde dónde empezar.", "Escolha de onde começar."),
             L('form.arrange-start button[value="made"]')),
        Step(("Move a video: To the top, the arrows, or a place number.",
              "Mueve un video: Al principio, las flechas o un número de lugar.",
              "Mova um vídeo: Para o começo, as setas ou um número de lugar."),
             L('.arrange-row button[value="top"]', nth=3)),
        Step(("Tap Save when it looks right. Undo takes back a move.",
              "Toca Guardar cuando esté bien. Deshacer quita un cambio.",
              "Toque em Salvar quando estiver certo. Desfazer volta uma mudança."),
             L('.arrange-bar button[value="save"]')),
        Step(("Saved! Undo save brings back your old order.", "¡Guardado! Deshacer lo guardado trae tu orden de antes.",
              "Salvo! Desfazer o salvo traz sua ordem de antes.")),
    ]),
    Tour("channel-look", "\u270f\ufe0f", ("Name a channel", "Nombra un canal", "Dê nome a um canal"), "/channels",
         needs="allow_customize", screens=("kid/channels.html", "kid/channel_look.html", "kid/_channel_avatar.html"),
         steps=[
        Step(("Tap Change under a channel.", "Toca Cambiar debajo de un canal.",
              "Toque em Mudar embaixo de um canal."), L("a.channel-card-change", nth=1)),
        Step(("Type your own name for it.", "Escribe tu propio nombre.", "Digite o seu próprio nome."),
             L("#name"), fill="My songs"),
        Step(("Pick a picture…", "Elige un dibujo…", "Escolha um desenho…"), L("label.emoji-choice", nth=12)),
        Step(("…and tap Save. Only you see it.", "…y toca Guardar. Solo tú lo ves.",
              "…e toque em Salvar. Só você vê."), L("button.save-btn")),
        Step(("There it is!", "¡Ahí está!", "Aí está!")),
    ]),
    Tour("new-channel", "\u2795", ("Make a channel", "Crea un canal", "Crie um canal"), "/channels",
         needs="allow_customize",
         screens=("kid/channels.html", "kid/channel_new.html", "kid/_channel_avatar.html"), steps=[
        Step(("Tap New channel.", "Toca Canal nuevo.", "Toque em Novo canal."), L('a[href="/channels/new"]')),
        Step(("Type its name.", "Escribe su nombre.", "Digite o nome dele."), L("#name"), fill="My trains"),
        Step(("Tap Create channel.", "Toca Crear canal.", "Toque em Criar canal."), L("button.save-btn")),
        Step(("It's yours! Put videos in it with Customize under a video.",
              "¡Es tuyo! Pon videos en él con Personalizar debajo de un video.",
              "É seu! Coloque vídeos nele com Personalizar embaixo de um vídeo.")),
    ]),
    Tour("search", "\U0001f50d", ("Search", "Buscar", "Buscar"), "/", needs="search",
         screens=("kid/my_home.html", "kid/search.html", "kid/_video_grid.html"),
         steps=[
        Step(("Tap Search.", "Toca Buscar.", "Toque em Buscar."), L('a.header-btn[href="/search"]')),
        Step(("Type a word, even with a spelling mistake.", "Escribe una palabra, aunque no esté bien escrita.",
              "Digite uma palavra, mesmo escrita errado."), L("#q"), fill="elefant"),
        Step(("Tap Search.", "Toca Buscar.", "Toque em Buscar."), L("button.search-btn")),
        Step(("It finds the videos that sound like it.", "Encuentra los videos que suenan parecido.",
              "Ele acha os vídeos que soam parecido.")),
    ]),
    Tour("find-new", "\U0001f52d", ("Ask for a new video", "Pide un video nuevo", "Peça um vídeo novo"), "/",
         needs="allow_wishlist", screens=("kid/my_home.html", "kid/wishlist.html"),
         steps=[
        Step(("Tap Find new.", "Toca Buscar nuevos.", "Toque em Achar novos."), L('a.header-btn[href="/wishlist"]')),
        Step(("Type what you'd like to watch.", "Escribe lo que te gustaría ver.",
              "Digite o que você gostaria de ver."), L("#wq"), fill="trains"),
        Step(("Tap Search.", "Toca Buscar.", "Toque em Buscar."), L('form.wishlist-form button[type="submit"]')),
        Step(("Tap Ask on the one you want.", "Toca Pedir en el que quieres.", "Toque em Pedir no que você quer."),
             L('form[action="/wishlist/ask"] button')),
        Step(("A grown-up looks at it, then it shows under What I asked for.",
              "Un adulto lo revisa y aparece en Lo que pedí.",
              "Um adulto olha e ele aparece em O que eu pedi.")),
    ]),
    Tour("hide", "\U0001f648", ("Hide a video", "Esconder un video", "Esconder um vídeo"), "/", needs="allow_hide",
         screens=("kid/my_home.html", "kid/watch.html"),
         steps=[
        Step(("Open a video you don't like.", "Abre un video que no te gusta.", "Abra um vídeo de que você não gosta."),
             L('a[href^="/watch/"]', nth=3)),
        Step(("Tap Hide.", "Toca Esconder.", "Toque em Esconder."), L("details.hide-video summary")),
        Step(("Tap Yes, hide it. It leaves your lists and a grown-up is told.",
              "Toca Sí, escóndelo. Sale de tus listas y se le avisa a un adulto.",
              "Toque em Sim, esconder. Ele sai das suas listas e um adulto fica sabendo."),
             L("button.hide-confirm-yes")),
        Step(("Changed your mind? Tap Undo.", "¿Cambiaste de idea? Toca Deshacer.",
              "Mudou de ideia? Toque em Desfazer."), L('form[action$="/unhide"] button')),
        Step(("It's back.", "Volvió.", "Ele voltou.")),
    ]),
]

PARENT_TOURS = [
    Tour("add-kid", "\U0001f9d2", ("Add a kid",), "/parent/",
         screens=("parent/dashboard.html", "parent/profiles.html", "parent/profile_form.html"),
         steps=[
        Step(("Open Kids.",), L('header.topnav a[href^="/parent/profiles"]')),
        Step(("Tap Add a kid.",), L('a[href="/parent/profiles/create"]')),
        Step(("Type a name.",), L("#display_name"), fill="Sam"),
        Step(("Choose a PIN, a theme and what they may do, then Add kid.",),
             L('button[type="submit"]', text="Add kid")),
        Step(("Sam is ready. Open the kid address on their tablet.",)),
    ]),
    Tour("requests", "\U0001f4ec", ("Answer a video request",), "/parent/", prepare=lambda p, base: _kid_asks(p, base),
         screens=("parent/dashboard.html", "parent/requests.html", "parent/content_preview.html"),
         steps=[
        Step(("Requests from your kids show on the dashboard.",), L('main a[href="/parent/requests"]')),
        Step(("Tap Review & add (or Not this one).",), L('a[href$="/review"]')),
        Step(("Pick the channel it goes into…",), L("#channel_id"), choose=1),
        Step(("…watch it, and tick that you watched it all…",), L("input.watched-cb")),
        Step(("…and Add. It downloads for your kid.",), L("#add-btn")),
        Step(("Done: it's in the library.",)),
    ]),
    Tour("hidden", "\U0001f648", ("A video your kid hid",), "/parent/", prepare=lambda p, base: _kid_hides(p, base),
         screens=("parent/dashboard.html", "parent/content_detail.html"),
         steps=[
        Step(("A video a kid hid shows under Needs your attention.",), L('main a[href^="/parent/content/guide_"]')),
        Step(("Delete it for everyone, keep it hidden for that kid, or show it again.",),
             L('button[value="keep"]')),
        Step(("Done.",)),
    ]),
]


def _kid_session(browser, base):
    ctx = browser.new_context(viewport=KID_VIEW)
    page = ctx.new_page()
    page.goto(base + "/profiles")
    page.locator('form[action="/profiles/select"] button', has_text="Leo").click()
    page.wait_for_load_state()
    return ctx, page


def _kid_asks(browser, base):
    ctx, page = _kid_session(browser, base)
    page.goto(base + "/wishlist?q=trains")
    page.locator('form[action="/wishlist/ask"] button').first.click()
    page.wait_for_load_state()
    ctx.close()


def _kid_hides(browser, base):
    ctx, page = _kid_session(browser, base)
    page.goto(base + "/")
    page.locator('a[href^="/watch/"]').nth(2).click()
    page.wait_for_load_state()
    page.locator("details.hide-video summary").click()
    page.locator("button.hide-confirm-yes").click()
    page.wait_for_load_state()
    ctx.close()


# --- running a tour ------------------------------------------------------------

def sign_in_kid(page, base):
    page.goto(base + "/profiles/switch")
    page.goto(base + "/profiles")
    page.locator('form[action="/profiles/select"] button', has_text="Mia").click()
    page.wait_for_load_state()
    for digit in MIA_PIN:
        page.locator(f'button.pin-key[data-digit="{digit}"]').click()
    page.wait_for_url(lambda u: "/profiles" not in u, timeout=20000)


def sign_in_parent(page, base):
    page.goto(base + "/parent/login")
    page.locator('input[type="password"]').fill(PARENT_PW)
    page.locator("form button[type=submit]").first.click()
    page.wait_for_load_state()


def webp(png: bytes) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(buf, "WEBP", quality=QUALITY, method=6)
    return buf.getvalue()


def run_tour(browser, base, tour: Tour, view, kid: bool, out_dir: Path) -> list[dict]:
    if tour.prepare:
        tour.prepare(browser, base)
    ctx = browser.new_context(viewport=view, reduced_motion="reduce")
    # The review page embeds YouTube's player; the pretend videos aren't
    # there, so a plain panel stands in for it.
    ctx.route("**/*youtube*/**", lambda route: route.fulfill(content_type="text/html", body=PLAYER_STAND_IN))
    page = ctx.new_page()
    page.set_default_timeout(15000)
    if kid and tour.signed_in:
        sign_in_kid(page, base)
    if not kid:
        sign_in_parent(page, base)
    page.goto(base + tour.start)
    page.wait_for_load_state("networkidle")
    for old in out_dir.glob(f"{tour.id}-[0-9][0-9].webp"):
        old.unlink()
    steps = []
    for n, step in enumerate(tour.steps, 1):
        box = None
        loc = step.tap(page) if step.tap else None
        if loc is not None:
            loc.scroll_into_view_if_needed()
            page.wait_for_timeout(250)
            b = loc.bounding_box()
            if b:
                box = [round(b["x"] / view["width"], 4), round(b["y"] / view["height"], 4),
                       round(b["width"] / view["width"], 4), round(b["height"] / view["height"], 4)]
        name = f"{tour.id}-{n:02d}.webp"
        (out_dir / name).write_bytes(webp(page.screenshot()))
        steps.append({"img": name, "box": box})
        if loc is not None:
            if step.fill:
                loc.fill(step.fill)
            elif step.choose >= 0:
                loc.select_option(index=step.choose)
            else:
                loc.click()
                page.wait_for_load_state()
            if step.then:
                step.then(page)
            page.wait_for_timeout(300)
    ctx.close()
    return steps


def animated(frames_dir: Path, steps: list[dict], dest: Path) -> None:
    """One animated picture for the written guide: each step, then the
    same with a ring where to tap."""
    from PIL import Image, ImageDraw
    frames, durations = [], []
    for s in steps:
        img = Image.open(frames_dir / s["img"]).convert("RGB")
        scale = 600 / img.width
        img = img.resize((600, round(img.height * scale)))
        frames.append(img)
        durations.append(900)
        if s["box"]:
            x, y, w, h = s["box"]
            cx, cy = (x + w / 2) * img.width, (y + h / 2) * img.height
            r = max(18, min(w * img.width, h * img.height) / 2 + 8)
            ring = img.copy()
            draw = ImageDraw.Draw(ring, "RGBA")
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 191, 0, 90), outline=(255, 140, 0, 255),
                         width=5)
            frames.append(ring)
            durations.append(1700)
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(dest, "WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=0,
                   quality=60, method=6)


def make(guide: str, only_tour: str, keep: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    kid = guide == "kids"
    tours = KID_TOURS if kid else PARENT_TOURS
    langs = KID_LANGS if kid else ("en",)
    view = KID_VIEW if kid else PARENT_VIEW
    from app.services.guides import screen_fingerprint
    base = "kid/base.html" if kid else "parent/base.html"
    data = {"tours": [{"id": t.id, "icon": t.icon, "needs": t.needs,
                       "screens": {s: screen_fingerprint(s) for s in (base, *t.screens)}} for t in tours],
            "langs": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        thumbs = draw_thumbnails(browser, [i for items in LIBRARY.values() for i in items] + FOUND)
        patch_thumbnails(thumbs)
        for li, lang in enumerate(langs):
            out_dir = OUT / guide / lang
            out_dir.mkdir(parents=True, exist_ok=True)
            tour_list = []
            for tour in tours:
                if only_tour and tour.id != only_tour:
                    continue
                data_dir = Path(tempfile.mkdtemp(prefix="curatables-guide-"))
                try:
                    seed(data_dir, thumbs, lang)
                    with Server(data_dir, fake_backend(thumbs)) as base:
                        steps = run_tour(browser, base, tour, view, kid, out_dir)
                finally:
                    if keep is None:
                        shutil.rmtree(data_dir, ignore_errors=True)
                for s, step in zip(steps, tour.steps, strict=True):
                    s["say"] = step.say[li]
                tour_list.append({"id": tour.id, "title": tour.title[li], "steps": steps})
                print(f"{guide} {lang} {tour.id}: {len(steps)} steps", flush=True)
                if lang == "en":
                    animated(out_dir, steps, DOCS_OUT / f"{guide}-{tour.id}.webp")
            data["langs"][lang] = tour_list
        browser.close()
    path = OUT / f"{guide}.json"
    if only_tour and path.exists():
        old = json.loads(path.read_text())
        kept = {t["id"]: t for t in old.get("tours", [])}
        data["tours"] = [t if t["id"] == only_tour else kept.get(t["id"], t) for t in data["tours"]]
        for lang, tour_list in data["langs"].items():
            merged = {t["id"]: t for t in old["langs"].get(lang, [])}
            merged.update({t["id"]: t for t in tour_list})
            data["langs"][lang] = [merged[t.id] for t in tours if t.id in merged]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", choices=("kids", "parents"))
    p.add_argument("--tour", default="")
    p.add_argument("--keep", type=Path)
    args = p.parse_args()
    for guide in ("kids", "parents"):
        if not args.only or args.only == guide:
            make(guide, args.tour, args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
