"""Safety net: destroy any docburst instance older than --max-age minutes.

Run it on a schedule (e.g. a Claude Code routine every 30 min) in case a launcher session died
before its own cleanup ran. `python -m burst.reap --max-age 30`
"""
from __future__ import annotations

import argparse
import os
import time

from .launch import LABEL, load_dotenv
from .vast import Vast


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age", type=float, default=30, help="minutes")
    a = ap.parse_args(argv)
    load_dotenv()
    vast = Vast(os.environ.get("VAST_API_KEY", ""))
    now = time.time()
    n = 0
    for inst in vast.list():
        if str(inst.get("label", "")).startswith(LABEL + "-") and now - float(inst.get("start_date") or now) > a.max_age * 60:
            vast.destroy(inst["id"])
            print(f"destroyed {inst['id']} ({inst.get('label')}, {inst.get('actual_status')})")
            n += 1
    print(f"{n} stale instance(s) destroyed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
