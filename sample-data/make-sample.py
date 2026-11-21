#!/usr/bin/env python3
"""
Writes sample-data/host.json and sample-data/app.json: obviously fake data in the
shape hud.html reads, with timestamps relative to now so nothing shows as stale.

    python3 sample-data/make-sample.py
    python3 -m http.server 8765 --bind 127.0.0.1
    open "http://127.0.0.1:8765/hud.html?host=sample-data/host.json&app=sample-data/app.json"

Every name, address and number below is made up. The addresses come from the
documentation ranges (RFC 5737) and private 10.0.0.0/8.
"""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
now = time.time()
GB = 1024 ** 3

host = {
    "at": now - 3, "hostname": "demo-box",
    "box": {
        "load": [1.42, 1.18, 0.97], "uptime_s": 3 * 86400 + 5 * 3600 + 12 * 60, "nproc": 16,
        "cpu_model": "Example CPU 9000 (16 threads)",
        "cpu": 11.6, "cpu_hist": [8, 9, 12, 10, 7, 9, 14, 22, 18, 12, 9, 8, 11, 10, 9, 13, 12, 11, 10, 12],
        "mem": {"total": 64 * GB, "available": 41 * GB, "swap_total": 8 * GB, "swap_free": 8 * GB},
        "disk": {"total": 1800 * GB, "used": 640 * GB, "fill_per_day": 1.8 * GB,
                 "io": {"read_bps": 2.1 * 1024 ** 2, "write_bps": 0.6 * 1024 ** 2}},
        "hwmon": {"cpu": 48.5, "gpu": 41.0, "nvme": 39.9, "nic": 52.0, "fan_rpm": 1150, "gpu_w": 9.4, "gpu_busy": 3},
        "procs": {
            "top_cpu": [{"pid": 1201, "name": "example-api", "user": "demo", "cpu": 6.2, "rss": 310 * 1024 ** 2},
                        {"pid": 1302, "name": "postgres", "user": "demo·ctr", "cpu": 2.4, "rss": 520 * 1024 ** 2},
                        {"pid": 980, "name": "gnome-shell", "user": "demo", "cpu": 1.1, "rss": 410 * 1024 ** 2},
                        {"pid": 1404, "name": "hud-collect.py", "user": "demo", "cpu": 0.6, "rss": 28 * 1024 ** 2},
                        {"pid": 711, "name": "tailscaled", "user": "root", "cpu": 0.3, "rss": 60 * 1024 ** 2}],
            "top_mem": [{"pid": 1302, "name": "postgres", "user": "demo·ctr", "cpu": 2.4, "rss": 520 * 1024 ** 2},
                        {"pid": 980, "name": "gnome-shell", "user": "demo", "cpu": 1.1, "rss": 410 * 1024 ** 2},
                        {"pid": 1201, "name": "example-api", "user": "demo", "cpu": 6.2, "rss": 310 * 1024 ** 2},
                        {"pid": 1505, "name": "WebKitWebProcess", "user": "demo", "cpu": 0.2, "rss": 150 * 1024 ** 2},
                        {"pid": 711, "name": "tailscaled", "user": "root", "cpu": 0.3, "rss": 60 * 1024 ** 2}],
            "accounts": {"demo": {"cpu": 9.8, "mem": 6.2 * GB, "tasks": 412, "containers": 2},
                         "builder": {"cpu": 0.4, "mem": 0.9 * GB, "tasks": 40, "containers": 1}},
        },
        "chrony": {"offset_s": 0.000041, "source": "time.example.org", "leap": "Normal"},
    },
    "net": {
        "nic": "eth0", "gateway_label": "router", "expected_link_mbps": 2500,
        "speed_mbps": 2500, "oper": "up", "wifi_oper": "down",
        "rx_bps": 3.2e6, "tx_bps": 0.8e6, "today_rx": 14.2 * GB, "today_tx": 2.9 * GB,
        "gateway": "10.0.0.1", "dhcp_expiry": now + 3 * 3600, "dhcp_lease_s": 4 * 3600,
        "addrs": ["10.0.0.20/24"], "dns": ["10.0.0.1"], "tailnet_ip": "fd7a:115c:a1e0::20",
        "ping_ms": {"gateway": 0.3, "1.1.1.1": 4.1, "nas": 0.9},
    },
    "ups": {
        "status": "OL", "charge": 100, "runtime_s": 2580, "load_pct": 14, "realpower_w": 126.0, "nominal_w": 900.0,
        "input_v": 120.0, "output_v": 120.0, "charge_low": 15, "runtime_low_s": 300,
        "model": "Example UPS 1500", "test_result": "Done and passed",
        "units": {"nut-driver@myups.service": "active", "nut-server.service": "active", "nut-monitor.service": "active"},
    },
    "services": {
        "units": {"example-api.service": {"label": "example API :8000", "active": "active", "since": now - 2 * 86400, "restarts": 0},
                  "example-web.service": {"label": "example web :3000", "active": "active", "since": now - 2 * 86400, "restarts": 1},
                  "example-worker.service": {"label": "background worker", "active": "active", "since": now - 5 * 3600, "restarts": 0}},
        "health": {"api": {"code": 200, "ms": 12}, "web": {"code": 200, "ms": 31}},
    },
    "misc": {
        "system": {"nut-server.service": {"label": "upsd", "active": "active", "since": now - 3 * 86400, "manual": False},
                   "sshd.service": {"label": "sshd", "active": "active", "since": now - 3 * 86400, "manual": False},
                   "tailscaled.service": {"label": "tailscaled", "active": "active", "since": now - 3 * 86400, "manual": False},
                   "chronyd.service": {"label": "chronyd", "active": "active", "since": now - 3 * 86400, "manual": False},
                   "cups.service": {"label": "CUPS :631", "active": "active", "since": now - 3 * 86400, "manual": False},
                   "example-lab.service": {"label": "lab VM (manual)", "active": "inactive", "since": None, "manual": True}},
        "user_timers": {"backup-home.timer": {"last": now - 7 * 3600, "next": now + 17 * 3600},
                        "dns-update.timer": {"last": now - 240, "next": now + 60},
                        "uptime-check.timer": {"last": now - 30, "next": now + 30}},
        "failed_system": [], "failed_user": [],
        "user_containers": [{"name": "example-db", "status": "Up 2 days (healthy)"},
                            {"name": "example-cache", "status": "Up 2 days"}],
    },
    "tunnels": {"web": {"port": 20241, "connections": 4, "up": True, "expected": 4}},
    "slow": {
        "at": now - 25,
        "egress": {"ip": "203.0.113.45", "colo": "XYZ", "asns": ["64500"], "mode": "primary", "label": "fiber",
                   "checked": True, "failed": False, "backup_cap_bytes": None},
        "tailscale": {"online": True, "dns": "demo-box.example", "peers_online": 5, "peers": 7},
        "public": {"https://www.example.com/healthz": {"code": 200, "ms": 140}},
        "events": [{"at": now - 9 * 3600, "what": "UPS: on battery", "kind": "ups"},
                   {"at": now - 9 * 3600 + 40, "what": "UPS: back on mains", "kind": "ups"}],
        "kernel": {"running": "6.17.4-200.fc43.x86_64", "newest": "6.17.4-200.fc43.x86_64"},
        "tpm_unlock": True,
    },
}

app = {
    "at": now - 4,
    "title": "App · Example Shop (demo)",
    "badge": {"text": "PRIMARY", "warn": False},
    "problems": ["1 job failed today: nightly-report"],
    "services_title": "services",
    "services": [
        {"label": "API", "port": 8010, "state": "active", "since": now - 2 * 86400, "detail": "healthz 200 · up 2 d 0 h"},
        {"label": "web", "port": 3010, "state": "active", "since": now - 2 * 86400},
        {"label": "database", "port": 5432, "state": "active", "since": now - 2 * 86400, "detail": "healthy · 1.2 GB"},
        {"label": "queue worker", "state": "active", "since": now - 3 * 3600, "restarts": 2},
        {"label": "replica sync", "state": "inactive", "expected": True, "detail": "stopped (by design)"},
    ],
    "facts_title": "jobs",
    "facts": [["timers", "24 active · next: cleanup 12 min"],
              ["failed", "nightly-report 04:10", True],
              ["backups", "database ✓ 02:30 · files ✓ 03:15"],
              ["deploy", "abc1234 at 09:05"]],
    "list_title": "accounts",
    "list": [{"name": "account-a", "value": "ok 10:02"}, {"name": "account-b", "value": "ok 10:01"},
             {"name": "marketplace", "value": "ok 09:58"}],
    "events": [{"at": now - 3 * 3600, "what": "deploy abc1234", "kind": "deploy"},
               {"at": now - 6 * 3600, "what": "job failed: nightly-report", "kind": "job"}],
}

(HERE / "host.json").write_text(json.dumps(host, indent=1))
(HERE / "app.json").write_text(json.dumps(app, indent=1))
print(HERE / "host.json", HERE / "app.json")
