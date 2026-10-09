"""python3 -I /opt/curatables/scripts/curatables-helper.py [--report PATH]"""

from __future__ import annotations

import logging
import sys


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) >= 1 and argv[0] == "--report":
        from syshelper import diagnostics
        report = diagnostics.build_report()
        if len(argv) >= 2:
            diagnostics.write_text_file(argv[1], report)
        else:
            sys.stdout.write(report)
        return 0
    from syshelper import server
    server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
