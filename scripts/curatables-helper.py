#!/usr/bin/env python3
"""Entry point for the root system helper (see syshelper/__init__.py).

Run with python3 -I: the code directory is added explicitly, so nothing
from the working directory or the environment is imported.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from syshelper.__main__ import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
