#!/usr/bin/env python3
"""
A minimal app collector for the Framework HUD. It writes obviously fake data to
~/.cache/framework-hud/app.json (or the path in HUD_APP_JSON) every 10 s, in the
shape the App panel reads. Copy it, replace fake_status() with real checks of
your own app, and run it as a user service next to hud-collect.py.

    python3 examples/example-collector.py --once    # write one file and exit

The contract (every key optional):

    at              epoch seconds of this sample; older than 60 s is a problem line
    title           panel title (default "App")
    badge           {"text": "PRIMARY", "warn": false}
    problems        ["sentences", ...] shown first, in amber, and in the top strip
    services        [{"label", "state", "port", "since", "restarts", "detail", "ok", "expected"}]
                    ok defaults to state == "active"; expected=true means "stopped on purpose"
    services_title  heading over the services column (default "services")
    facts           [["key", "value", warn?], ...] key/value rows in the right column
    facts_title     heading over the facts (default "details")
    list            [{"name", "value", "bad"}] an extra list under the facts
    list_title      heading over the list (default "list")
    events          [{"at", "what", "kind"}] merged into the last-24-hours strip;
                    kinds "ups", "role" and "job" show in amber

If the app runs as another user whose home the HUD cannot read, write the file
somewhere both can reach (for example /run/<name>/app.json from a tmpfiles.d
rule) and point HUD_APP_JSON at it in framework-hud.service.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

OUT = Path(os.environ.get("HUD_APP_JSON", str(Path.home() / ".cache" / "framework-hud" / "app.json")))
TICK = 10.0
STARTED = time.time()


def fake_status() -> dict:
    now = time.time()
    queue = random.randint(0, 40)
    return {
        "at": now,
        "title": "App · Demo App (fake data)",
        "badge": {"text": "DEMO", "warn": False},
        "problems": [f"queue backlog {queue} jobs"] if queue > 30 else [],
        "services": [
            {"label": "demo API", "port": 8080, "state": "active", "since": STARTED},
            {"label": "demo worker", "state": "active", "since": STARTED, "detail": f"{queue} jobs queued"},
            {"label": "demo replica", "state": "inactive", "expected": True, "detail": "stopped (demo)"},
        ],
        "facts": [["version", "0.0.0-demo"], ["queue", str(queue), queue > 30], ["requests/min", str(random.randint(5, 60))]],
        "list_title": "fake accounts",
        "list": [{"name": "alpha", "value": "ok"}, {"name": "beta", "value": "ok"}],
        "events": [{"at": STARTED, "what": "demo collector started", "kind": "deploy"}],
    }


def write(data: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(OUT)                    # atomic: the page never reads half a file


def main() -> int:
    while True:
        write(fake_status())
        if "--once" in sys.argv:
            print(OUT)
            return 0
        time.sleep(TICK)


if __name__ == "__main__":
    sys.exit(main())
