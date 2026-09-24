"""Destroy the instance this code runs on (vast.ai injects CONTAINER_ID and CONTAINER_API_KEY).

Best effort: the launcher also destroys every instance it created, and burst.reap sweeps leftovers.
"""
import os
import sys

import requests

iid, key = os.environ.get("CONTAINER_ID"), os.environ.get("CONTAINER_API_KEY")
reason = " ".join(sys.argv[1:]) or "done"
if iid and key:
    for _ in range(3):
        try:
            r = requests.delete(f"https://console.vast.ai/api/v0/instances/{iid}/",
                                headers={"Authorization": f"Bearer {key}"}, json={}, timeout=15)
            print(f"self-destruct ({reason}): HTTP {r.status_code}", flush=True)
            if r.ok:
                break
        except requests.RequestException as e:
            print(f"self-destruct failed: {e}", flush=True)
else:
    print(f"self-destruct skipped ({reason}): not on vast.ai", flush=True)
