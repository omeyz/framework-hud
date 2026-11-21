#!/usr/bin/python3
"""
The Framework HUD's host collector: runs as your user, every 10 s, writes
~/.cache/framework-hud/host.json for hud.py / hud.html. Read-only by design
(docs/DESIGN.md). Panels fed from here: Network, Power (UPS), Box, Services,
Everything else, plus the host side of the last-24-hours strip.

What to watch comes from ~/.config/framework-hud/config.json (or the file named
by HUD_CONFIG); see examples/config.example.json. Every key is optional.

Every probe is wrapped: a failing probe yields null for its field, never a
missing file. Slow probes (egress, tailscale, public URLs, the journal) run
every 60 s; the fast ones every tick.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
OUT_DIR = HOME / ".cache" / "framework-hud"
OUT = OUT_DIR / "host.json"
HIST = OUT_DIR / "history.json"
CONFIG = Path(os.environ.get("HUD_CONFIG", str(HOME / ".config" / "framework-hud" / "config.json")))
TICK = float(os.environ.get("HUD_TICK_S", "10"))
SLOW_EVERY = 60.0


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def default_nic() -> str:
    """The interface that carries the default route."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                return f[0]
    except OSError:
        pass
    return "eth0"


def default_wifi() -> str | None:
    return next((p.name for p in sorted(Path("/sys/class/net").glob("wl*"))), None)


CFG = load_config()
NIC = os.environ.get("HUD_NIC") or CFG.get("nic") or default_nic()
WIFI = os.environ.get("HUD_WIFI") or CFG.get("wifi") or default_wifi()
UPS = os.environ.get("HUD_UPS") or CFG.get("ups")                     # e.g. "myups@localhost"; null = no UPS panel data
UPS_UNITS = ([f"nut-driver@{UPS.split('@')[0]}.service", "nut-server.service", "nut-monitor.service"] if UPS else [])
DISK_PATH = CFG.get("disk_path", "/")
DISK_DEVICE = CFG.get("disk_device", "nvme0n1")
PINGS: dict = CFG.get("pings") or {}                                  # {"name": "host"}; empty = the gateway + 1.1.1.1
UPLINK = CFG.get("uplink") or {}                                      # {"primary_asn", "primary_label", "backup_label", "backup_cap_gb"}
SYSTEM_UNITS = [(u, label) for u, label in (CFG.get("system_units") or [])]
MANUAL_UNITS = set(CFG.get("manual_units") or [])                    # units that are off on purpose most of the time
USER_UNITS = [(u, label) for u, label in (CFG.get("user_units") or [])]
HEALTH_CHECKS: dict = CFG.get("health_checks") or {}                 # {"name": "http://127.0.0.1:8000/healthz"}
PUBLIC_URLS: list = CFG.get("public_urls") or []                     # checked every 60 s
USER_TIMERS = CFG.get("user_timers")                                 # null = the next few of all your user timers
TUNNELS: dict = CFG.get("tunnels") or {}                              # {"name": metrics port}: cloudflared /ready
TUNNEL_CONNS = int(CFG.get("tunnel_connections", 4))
ACCOUNTS: dict | None = CFG.get("accounts")                          # {"user": uid}; null = every login account
EXPECTED_LINK_MBPS = CFG.get("expected_link_mbps")
GATEWAY_LABEL = CFG.get("gateway_label", "")


def run(cmd: list[str], timeout: float = 8, **kw) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, **kw)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def read(path: str | Path) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def http(url: str, timeout: float = 5, headers: dict | None = None) -> tuple[int | None, str | None, float]:
    t0 = time.monotonic()
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "framework-hud"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(65536).decode("utf-8", "replace"), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, time.monotonic() - t0
    except Exception:                                           # noqa: BLE001
        return None, None, time.monotonic() - t0


def tcp(host: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def show(unit: str, props: list[str], user: bool = False) -> dict:
    cmd = ["systemctl"] + (["--user"] if user else []) + ["show", unit, "-p", ",".join(props), "--no-pager"]
    out = run(cmd) or ""
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def ts_epoch(s: str | None) -> float | None:
    """systemd's 'Sun 2026-11-08 02:06:50 PST' → epoch, or None."""
    if not s or s in ("n/a", "0"):
        return None
    try:
        return time.mktime(time.strptime(" ".join(s.split()[1:3]), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def unit_state(unit: str, user: bool = False) -> dict:
    d = show(unit, ["ActiveState", "SubState", "ExecMainStartTimestamp", "ActiveEnterTimestamp", "NRestarts",
                    "Result", "ExecMainExitTimestamp", "ExecMainStatus", "Description", "InactiveEnterTimestamp"], user)
    return {
        "active": d.get("ActiveState"), "sub": d.get("SubState"),
        "since": ts_epoch(d.get("ActiveEnterTimestamp")) or ts_epoch(d.get("ExecMainStartTimestamp")),
        "restarts": int(d.get("NRestarts") or 0), "result": d.get("Result"),
        "last_run": ts_epoch(d.get("ExecMainExitTimestamp")), "last_status": d.get("ExecMainStatus"),
        "description": d.get("Description"), "failed_at": ts_epoch(d.get("InactiveEnterTimestamp")),
    }


def login_accounts() -> dict[str, int]:
    out = {}
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")):
            out[p.pw_name] = p.pw_uid
    return out


def cpu_model() -> str | None:
    for line in (read("/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            return re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
    return None


class Collector:
    def __init__(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.hist = self._load_hist()
        self.prev_cpu = None
        self.prev_net = None
        self.prev_disk = None
        self.prev_procs: dict[int, tuple[float, float]] = {}
        self.prev_slices: dict[str, tuple[float, float]] = {}
        self.slow: dict = {}
        self.slow_at = 0.0
        self.medium: dict = {}
        self.medium_at = 0.0
        self.cpu_hist: list[float] = []
        self.accounts = ACCOUNTS if ACCOUNTS is not None else login_accounts()
        self.cpu_model = cpu_model()

    # ----- history: disk fill, bytes at midnight, bytes when backup internet started
    def _load_hist(self) -> dict:
        try:
            return json.loads(HIST.read_text())
        except (OSError, ValueError):
            return {"disk": [], "day": None, "day_bytes": None, "backup_since": None, "backup_bytes": None}

    def _save_hist(self) -> None:
        tmp = HIST.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.hist))
        tmp.replace(HIST)

    # ----- fast probes
    def cpu(self) -> tuple[float | None, list[float]]:
        line = (read("/proc/stat") or "").splitlines()[0].split()
        if len(line) < 5:
            return None, []
        vals = list(map(int, line[1:]))
        idle = vals[3] + vals[4]
        total = sum(vals)
        pct = None
        if self.prev_cpu:
            dt, di = total - self.prev_cpu[0], idle - self.prev_cpu[1]
            pct = round(100 * (1 - di / dt), 1) if dt > 0 else None
        self.prev_cpu = (total, idle)
        if pct is not None:
            self.cpu_hist = (self.cpu_hist + [pct])[-60:]
        return pct, self.cpu_hist

    def mem(self) -> dict:
        d = {}
        for line in (read("/proc/meminfo") or "").splitlines():
            k, _, v = line.partition(":")
            d[k] = int(v.split()[0]) * 1024 if v.strip() else 0
        return {"total": d.get("MemTotal"), "available": d.get("MemAvailable"),
                "swap_total": d.get("SwapTotal"), "swap_free": d.get("SwapFree")}

    def disk(self) -> dict:
        try:
            st = os.statvfs(DISK_PATH)
        except OSError:
            return {}
        total = st.f_blocks * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        now = time.time()
        samples = [s for s in self.hist.get("disk", []) if now - s[0] < 8 * 86400]
        if not samples or now - samples[-1][0] > 6 * 3600:
            samples.append([now, used])
        self.hist["disk"] = samples
        rate = None
        if len(samples) >= 2 and samples[-1][0] - samples[0][0] > 3600:
            rate = (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]) * 86400   # bytes/day
        # disk throughput
        rd = wr = None
        for line in (read("/proc/diskstats") or "").splitlines():
            f = line.split()
            if len(f) > 10 and f[2] == DISK_DEVICE:
                rd, wr = int(f[5]) * 512, int(f[9]) * 512
        io = None
        if rd is not None and self.prev_disk:
            dt = now - self.prev_disk[0]
            if dt > 0:
                io = {"read_bps": (rd - self.prev_disk[1]) / dt, "write_bps": (wr - self.prev_disk[2]) / dt}
        if rd is not None:
            self.prev_disk = (now, rd, wr)
        return {"total": total, "used": used, "fill_per_day": rate, "io": io}

    def hwmon(self) -> dict:
        out: dict = {}
        names = {"k10temp": "cpu", "coretemp": "cpu", "amdgpu": "gpu", "nvme": "nvme", "cros_ec": "board", "acpitz": "acpi"}
        for h in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
            name = (read(h / "name") or "").strip()
            t = read(h / "temp1_input")
            key = names.get(name) or ("nic" if name.startswith(("r8169", "r8125", "igc", "atlantic")) else None)
            if t and key and key not in out:
                out[key] = round(int(t) / 1000, 1)
            if "fan_rpm" not in out and (f := read(h / "fan1_input")):
                out["fan_rpm"] = int(f)
            if name == "amdgpu" and (p := read(h / "power1_average")):
                out["gpu_w"] = round(int(p) / 1e6, 1)
        for g in sorted(Path("/sys/class/drm").glob("card*/device/gpu_busy_percent")):
            if (v := read(g)):
                out["gpu_busy"] = int(v)
                break
        return out

    def load_uptime(self) -> dict:
        la = (read("/proc/loadavg") or "0 0 0").split()[:3]
        up = float((read("/proc/uptime") or "0").split()[0])
        return {"load": [float(x) for x in la], "uptime_s": up, "nproc": os.cpu_count(), "cpu_model": self.cpu_model}

    def processes(self) -> dict:
        """Top five by CPU over the last tick and by RSS, plus totals per account."""
        now = time.monotonic()
        hz = os.sysconf("SC_CLK_TCK")
        cur: dict[int, tuple[float, float]] = {}
        rows = []
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():
                continue
            pid = int(p.name)
            try:
                stat = (p / "stat").read_text()
                uid = p.stat().st_uid
                rss_line = next((l for l in (p / "status").read_text().splitlines() if l.startswith("VmRSS:")), None)
            except OSError:
                continue
            name = stat[stat.find("(") + 1:stat.rfind(")")]
            f = stat[stat.rfind(")") + 2:].split()
            cpu_s = (int(f[11]) + int(f[12])) / hz
            cur[pid] = (now, cpu_s)
            rss = int(rss_line.split()[1]) * 1024 if rss_line else 0
            pct = None
            if pid in self.prev_procs:
                t0, c0 = self.prev_procs[pid]
                if now - t0 > 0:
                    pct = 100 * (cpu_s - c0) / (now - t0)
            rows.append({"pid": pid, "name": name[:24], "user": self._user(uid), "cpu": pct, "rss": rss})
        self.prev_procs = cur
        by_cpu = sorted((r for r in rows if r["cpu"] is not None), key=lambda r: -r["cpu"])[:5]
        by_mem = sorted(rows, key=lambda r: -r["rss"])[:5]
        # per-account totals from the systemd user slices
        slices = {}
        for user, uid in self.accounts.items():
            d = show(f"user-{uid}.slice", ["CPUUsageNSec", "MemoryCurrent", "TasksCurrent"])
            try:
                cpu_ns = int(d.get("CPUUsageNSec", "0"))
                memc = int(d.get("MemoryCurrent", "0")) if d.get("MemoryCurrent", "").isdigit() else None
            except ValueError:
                continue
            pct = None
            if user in self.prev_slices:
                t0, c0 = self.prev_slices[user]
                if now - t0 > 0:
                    pct = 100 * (cpu_ns - c0) / 1e9 / (now - t0)
            self.prev_slices[user] = (now, cpu_ns)
            conmon = sum(1 for r in rows if r["name"] == "conmon" and r["user"] == user)
            slices[user] = {"cpu": pct, "mem": memc, "tasks": int(d.get("TasksCurrent") or 0), "containers": conmon}
        return {"top_cpu": by_cpu, "top_mem": by_mem, "accounts": slices}

    _users: dict[int, str] = {}
    _subuid: list[tuple[str, int, int]] | None = None

    def _user(self, uid: int) -> str:
        """Account name; a rootless container's mapped uid (from /etc/subuid) shows as 'owner·ctr'."""
        if uid not in self._users:
            try:
                self._users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                if self._subuid is None:
                    self._subuid = []
                    for line in (read("/etc/subuid") or "").splitlines():
                        try:
                            owner, start, count = line.split(":")
                            self._subuid.append((owner, int(start), int(start) + int(count)))
                        except ValueError:
                            continue
                self._users[uid] = next((f"{o}·ctr" for o, lo, hi in self._subuid if lo <= uid < hi), str(uid))
        return self._users[uid]

    def network(self) -> dict:
        out: dict = {"nic": NIC, "gateway_label": GATEWAY_LABEL, "expected_link_mbps": EXPECTED_LINK_MBPS}
        out["speed_mbps"] = int((read(f"/sys/class/net/{NIC}/speed") or "0").strip() or 0)
        out["oper"] = (read(f"/sys/class/net/{NIC}/operstate") or "").strip()
        out["wifi_oper"] = (read(f"/sys/class/net/{WIFI}/operstate") or "").strip() if WIFI else ""
        rx = tx = None
        for line in (read("/proc/net/dev") or "").splitlines():
            if line.strip().startswith(NIC + ":"):
                f = line.split(":", 1)[1].split()
                rx, tx = int(f[0]), int(f[8])
        now = time.time()
        if rx is not None:
            if self.prev_net:
                dt = now - self.prev_net[0]
                if dt > 0:
                    out["rx_bps"] = (rx - self.prev_net[1]) / dt * 8
                    out["tx_bps"] = (tx - self.prev_net[2]) / dt * 8
            self.prev_net = (now, rx, tx)
            day = time.strftime("%Y-%m-%d")
            if self.hist.get("day") != day or self.hist.get("day_bytes") is None:
                self.hist["day"], self.hist["day_bytes"] = day, [rx, tx]
            out["today_rx"] = rx - self.hist["day_bytes"][0]
            out["today_tx"] = tx - self.hist["day_bytes"][1]
            out["_counters"] = [rx, tx]
        nm = run(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,DHCP4.OPTION", "dev", "show", NIC]) or ""
        addrs, dns = [], []
        for line in nm.splitlines():
            k, _, v = line.partition(":")
            if k.startswith("IP4.ADDRESS"):
                addrs.append(v)
            elif k == "IP4.GATEWAY":
                out["gateway"] = v
            elif k.startswith("IP4.DNS"):
                dns.append(v)
            elif k.startswith("DHCP4.OPTION") and "expiry = " in v:
                out["dhcp_expiry"] = int(v.split("= ")[1])
            elif k.startswith("DHCP4.OPTION") and "dhcp_lease_time = " in v:
                out["dhcp_lease_s"] = int(v.split("= ")[1])
        out["addrs"], out["dns"] = addrs, dns
        out["tailnet_ip"] = (run(["tailscale", "ip", "-4"]) or "").strip() or None
        pings = PINGS or {k: v for k, v in (("gateway", out.get("gateway")), ("1.1.1.1", "1.1.1.1")) if v}
        for name, host in pings.items():
            o = run(["ping", "-n", "-c", "1", "-W", "1", host], timeout=3)
            m = re.search(r"time=([\d.]+) ms", o or "")
            out.setdefault("ping_ms", {})[name] = float(m.group(1)) if m else None
        return out

    def ups(self) -> dict:
        if not UPS:
            return {"disabled": True}
        o = run(["upsc", UPS], timeout=5)
        if not o:
            return {"error": "upsc did not answer"}
        d = {}
        for line in o.splitlines():
            k, _, v = line.partition(": ")
            d[k.strip()] = v.strip()
        try:
            return {
                "status": d.get("ups.status"), "charge": int(float(d.get("battery.charge", "nan"))),
                "runtime_s": int(float(d.get("battery.runtime", "nan"))), "load_pct": int(float(d.get("ups.load", "nan"))),
                "realpower_w": float(d.get("ups.realpower", "nan")), "nominal_w": float(d.get("ups.realpower.nominal", "nan")),
                "input_v": float(d.get("input.voltage", "nan")), "output_v": float(d.get("output.voltage", "nan")),
                "charge_low": int(float(d.get("battery.charge.low", "nan"))),
                "runtime_low_s": int(float(d.get("battery.runtime.low", "nan"))),
                "model": d.get("device.model"), "test_result": d.get("ups.test.result"),
                "units": {u: unit_state(u)["active"] for u in UPS_UNITS},
            }
        except ValueError:
            return {"status": d.get("ups.status"), "raw": d}

    def services(self) -> dict:
        """Your user services and local health URLs (config: user_units, health_checks)."""
        out: dict = {"units": {}, "health": {}}
        for u, label in USER_UNITS:
            out["units"][u] = {"label": label, **unit_state(u, user=True)}
        for name, url in HEALTH_CHECKS.items():
            code, _, dt = http(url, 4)
            out["health"][name] = {"code": code, "ms": round(dt * 1000)}
        return out

    def misc_fast(self) -> dict:
        out: dict = {"system": {}, "user_timers": {}, "failed_system": [], "failed_user": []}
        for u, label in SYSTEM_UNITS:
            out["system"][u] = {"label": label, "manual": u in MANUAL_UNITS, **unit_state(u)}
        try:
            timers = json.loads(run(["systemctl", "--user", "list-timers", "-o", "json", "--all"]) or "[]")
        except ValueError:
            timers = []
        if USER_TIMERS is None:
            timers = sorted((t for t in timers if t.get("next")), key=lambda t: t["next"])[:8]
        else:
            timers = [t for t in timers if t.get("unit") in USER_TIMERS]
        for t in timers:
            out["user_timers"][t["unit"]] = {"next": (t.get("next") or 0) / 1e6 or None, "last": (t.get("last") or 0) / 1e6 or None,
                                             "activates": t.get("activates")}
        for scope, key in (([], "failed_system"), (["--user"], "failed_user")):
            try:
                units = json.loads(run(["systemctl", *scope, "list-units", "--state=failed", "-o", "json"]) or "[]")
            except ValueError:
                units = []
            out[key] = [u.get("unit") for u in units]
        try:
            out["user_containers"] = [{"name": c.get("Names", [None])[0], "status": c.get("Status")}
                                      for c in json.loads(run(["podman", "ps", "--format", "json"]) or "[]")]
        except ValueError:
            out["user_containers"] = None
        return out

    def tunnels(self) -> dict:
        """cloudflared metrics listeners named in the config ({"name": port}); /ready gives readyConnections.
        No account, zone or tunnel IDs are needed: it only asks the local cloudflared process."""
        out = {}
        for name, port in TUNNELS.items():
            code, body, _ = http(f"http://127.0.0.1:{int(port)}/ready", 2)
            conns = None
            if body:
                try:
                    conns = json.loads(body).get("readyConnections")
                except ValueError:
                    conns = None
            out[name] = {"port": int(port), "connections": conns, "up": code == 200, "expected": TUNNEL_CONNS}
        return out

    def chrony(self) -> dict:
        o = run(["chronyc", "tracking"]) or ""
        out = {}
        for line in o.splitlines():
            if line.startswith("System time"):
                m = re.search(r"([\d.]+) seconds (fast|slow)", line)
                if m:
                    out["offset_s"] = float(m.group(1)) * (1 if m.group(2) == "fast" else -1)
            elif line.startswith("Reference ID"):
                m = re.search(r"\((.+)\)", line)
                out["source"] = m.group(1) if m else line.split(":", 1)[1].strip()
            elif line.startswith("Leap status"):
                out["leap"] = line.split(":", 1)[1].strip()
        return out

    # ----- slow probes (every 60 s)
    def slow_probes(self) -> dict:
        out: dict = {}
        # egress IP and ASN: tells the primary line from a backup one when uplink.primary_asn is set
        code, body, _ = http("https://1.1.1.1/cdn-cgi/trace", 6)
        ip = colo = None
        if body:
            kv = dict(l.split("=", 1) for l in body.splitlines() if "=" in l)
            ip, colo = kv.get("ip"), kv.get("colo")
        asns: set[str] = set()
        if ip and shutil.which("dig"):
            rev = ".".join(reversed(ip.split(".")))
            o = run(["dig", "+short", "+time=3", "+tries=2", "TXT", f"{rev}.origin.asn.cymru.com"], timeout=8) or ""
            for line in o.splitlines():
                asns.update(line.strip('"').split("|")[0].split())
        mode = None
        primary = str(UPLINK.get("primary_asn") or "")
        if asns and primary:
            mode = "primary" if primary in asns else "backup"
        label = {"primary": UPLINK.get("primary_label", "primary line"),
                 "backup": UPLINK.get("backup_label", "backup line")}.get(mode or "", "")
        out["egress"] = {"ip": ip, "colo": colo, "asns": sorted(asns), "mode": mode, "label": label,
                         "checked": bool(primary), "failed": ip is None,
                         "backup_cap_bytes": (UPLINK.get("backup_cap_gb") or 0) * 1e9 or None}
        # backup-internet usage baseline
        if mode == "backup" and self.prev_net:
            if not self.hist.get("backup_since"):
                self.hist["backup_since"] = time.time()
                self.hist["backup_bytes"] = [self.prev_net[1], self.prev_net[2]]
        elif mode == "primary":
            self.hist["backup_since"] = None
            self.hist["backup_bytes"] = None
        if self.hist.get("backup_bytes") and self.prev_net:
            out["backup_usage"] = {"since": self.hist["backup_since"],
                                   "bytes": (self.prev_net[1] - self.hist["backup_bytes"][0]) + (self.prev_net[2] - self.hist["backup_bytes"][1])}
        # tailscale
        try:
            ts = json.loads(run(["tailscale", "status", "--json"], timeout=8) or "{}")
            peers = ts.get("Peer", {}).values()
            out["tailscale"] = {"online": ts.get("Self", {}).get("Online"), "dns": (ts.get("Self", {}).get("DNSName") or "").rstrip("."),
                                "peers_online": sum(1 for p in peers if p.get("Online")), "peers": len(ts.get("Peer", {}))} if ts else None
        except ValueError:
            out["tailscale"] = None
        # public probes
        out["public"] = {}
        for url in PUBLIC_URLS:
            code, _, dt = http(url, 8)
            out["public"][url] = {"code": code, "ms": round(dt * 1000)}
        out["events"] = self.events()
        out["kernel"] = self.kernel()
        out["tpm_unlock"] = self.tpm()
        return out

    def events(self) -> list[dict]:
        """Last 24 h, host side: UPS events and boots. An app collector can add its own events."""
        ev: list[dict] = []
        o = run(["journalctl", "-u", "nut-monitor", "--since=-24h", "-o", "json", "--no-pager"], timeout=10) or ""
        words = {"on battery": "UPS: on battery", "back on mains": "UPS: back on mains", "nearly flat": "UPS: low battery, shutting down",
                 "forced shutdown": "UPS: forced shutdown", "lost contact": "UPS: lost contact", "talking to the UPS again": "UPS: contact back"}
        for line in o.splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            msg = j.get("MESSAGE") or ""
            for k, label in words.items():
                if k in msg:
                    ev.append({"at": int(j["__REALTIME_TIMESTAMP"]) / 1e6, "what": label, "kind": "ups"})
                    break
        o = run(["journalctl", "--list-boots", "-o", "json", "--no-pager"], timeout=10) or "[]"
        try:
            for b in json.loads(o):
                at = b.get("first_entry", 0) / 1e6
                if time.time() - at < 24 * 3600:
                    ev.append({"at": at, "what": "boot", "kind": "boot"})
        except ValueError:
            pass
        return ev

    def kernel(self) -> dict:
        running = os.uname().release
        installed = sorted((p.name for p in Path("/lib/modules").iterdir()), key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"[.-]", s)])
        return {"running": running, "newest": installed[-1] if installed else None}

    def tpm(self) -> bool | None:
        o = run(["journalctl", "-b", "-o", "cat", "--no-pager", "-u", "systemd-cryptsetup@*"], timeout=10)
        if o is None:
            return None
        return ("TPM2" in o or "tpm2" in o) or None

    # ----- the tick
    def tick(self) -> None:
        now = time.time()
        cpu_pct, cpu_hist = self.cpu()
        data = {
            "at": now, "hostname": socket.gethostname(),
            "box": {**self.load_uptime(), "cpu": cpu_pct, "cpu_hist": cpu_hist, "mem": self.mem(), "disk": self.disk(),
                    "hwmon": self.hwmon(), "procs": self.processes(), "chrony": self.chrony()},
            "net": self.network(), "ups": self.ups(),
        }
        # unit states, containers and tunnels change rarely: every 30 s (each is a dozen subprocesses)
        if now - self.medium_at >= 30 or not self.medium:
            self.medium = {"services": self.services(), "misc": self.misc_fast(), "tunnels": self.tunnels()}
            self.medium_at = now
        data.update(self.medium)
        if now - self.slow_at >= SLOW_EVERY or not self.slow:
            try:
                self.slow = self.slow_probes()
            except Exception as e:                              # noqa: BLE001
                self.slow = {"error": f"{type(e).__name__}: {e}"}
            self.slow_at = now
            self._save_hist()
        data["slow"] = {**self.slow, "at": self.slow_at}
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(OUT)


def main() -> int:
    c = Collector()
    once = "--once" in sys.argv
    while True:
        t0 = time.monotonic()
        try:
            c.tick()
        except Exception as e:                                  # noqa: BLE001
            print(f"[hud-collect] tick failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        if once:
            print(OUT)
            return 0
        time.sleep(max(1.0, TICK - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main())
