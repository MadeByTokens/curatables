"""requirements.lock pins every package of requirements.txt the Pi image
installs, within the ranges requirements.txt allows."""

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
UNPINNED = {"yt-dlp", "yt-dlp-ejs"}  # always the newest; see the lock's header


def _locked() -> dict[str, str]:
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", (ROOT / "requirements.lock").read_text(), re.M)
    return {canonicalize_name(name): version for name, version in pins}


def _required() -> list[Requirement]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return [Requirement(line) for line in lines if line.strip() and not line.startswith("#")]


def test_the_lock_pins_every_requirement_within_its_range():
    locked = _locked()
    for req in _required():
        name = canonicalize_name(req.name)
        if name in UNPINNED:
            assert name not in locked
            continue
        assert name in locked, f"{req.name} is missing from requirements.lock"
        assert req.specifier.contains(locked[name], prereleases=True), \
            f"{req.name}=={locked[name]} is outside {req.specifier}"


def test_every_pin_has_a_hash():
    text = (ROOT / "requirements.lock").read_text()
    blocks = re.split(r"^(?=[A-Za-z0-9_.-]+==)", text, flags=re.M)[1:]
    assert blocks and all("--hash=sha256:" in block for block in blocks)
