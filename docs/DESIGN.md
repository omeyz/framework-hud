# Framework HUD: design

A heads-up display for a Linux workstation, on its own monitor, while nobody is at the desk. The
idea: the machine already knows its uptime, health, uplink, resource use, addresses, UPS state and
the health of the services it runs. A screen that goes dark when you walk away wastes the one
display that could show all of that from across the room. The HUD shows it only while you are
away, and gets out of the way the moment you come back.

## What is on the screen

One page, black, light monospaced type readable from across the room (sized from the viewport
width, so it fills 1080p and 4K alike). A thin top strip carries the clock, the date, the hostname
and uptime, and below it an ATTENTION line naming every problem the panels found. Six panels in two
rows. Each panel lists its problems first, in amber, as sentences ("on backup line since 09:12",
"disk 91 % full", "web healthz 502"); a panel with nothing wrong says "ok" once, dim. No red/green
dots for status words, no buttons. A strip above the footer lists the last 24 hours of events (UPS
events, boots, and whatever an app collector adds), and the footer shows how old each data source
is and how to dismiss the HUD.

### 1. Network

Local address and prefix with the subnet range worked out on the page, gateway, DNS, the DHCP lease
with its expiry as a countdown (`nmcli dev show`), link speed, Wi-Fi state, throughput now and
bytes today (`/proc/net/dev`), tailnet address and peers online (`tailscale status --json`), pings
to a few hosts, and cloudflared tunnel connections from each tunnel's local metrics `/ready`.

Uplink: the WAN IP from `https://1.1.1.1/cdn-cgi/trace` and its ASN from Team Cymru's DNS. If you
name your primary line's ASN, any other ASN means you are on the backup line; the panel says so in
amber, with since when and how much data has gone over it against an optional cap.

### 2. Power

From NUT (`upsc`): status in words ("on mains", "ON BATTERY, 44 min left"), charge with a bar,
runtime, load in watts, input and output voltage, the low-battery thresholds, the last UPS event
from upsmon's journal, and whether the NUT driver, upsd and upsmon are running.

### 3. Box

Uptime, load, CPU % with a sparkline of the last ten minutes, GPU busy and watts (amdgpu), memory,
the disk with its 7-day fill rate, NVMe throughput, temperatures and fan, chrony's offset and
source, running vs newest installed kernel (reboot pending). On the right: the top five processes
by CPU and by memory, and totals per account from the systemd user slices, so a hot machine says why.

### 4. App

One panel for whatever matters most on this machine, fed by a small collector you write. It reads
a JSON file with a title, a badge, problem sentences, a services list, key/value facts, an extra
list and events (contract in the README). The original build used this panel for a production app
with a primary/failover role badge; the panel itself knows nothing about any particular app.

### 5. Services

Your own user services (state, since when, restarts), local health URLs, and public URLs, all named
in the config.

### 6. Everything else on the box

System units you care about, one line each, with units that are off on purpose most of the time
marked as manual; your user timers (last and next run); your containers.

## Where the numbers come from

- `hud-collect.py`, a user service, every 10 s → `~/.cache/framework-hud/host.json`: panels 1, 2,
  3, 5 and 6, from what an ordinary user can read: `/sys`, `/proc`, `nmcli`, `tailscale`, `upsc`,
  HTTP to localhost, `systemctl`, `systemctl --user`, `podman`, the journal. Slow probes (egress,
  tailscale, public URLs, the journal) run every 60 s; unit states every 30 s; the rest every tick.
- An app collector, every 10 s → `app.json`: panel 4. If the app runs as another account whose home
  is private, it writes to a `/run/<name>/` directory made by a tmpfiles.d rule, world-readable,
  so the HUD in your session can read it.
- The page never talks to anything but those two files. It fetches them itself every 5 s. A source
  older than 60 s is itself a problem line.

## How it appears

- GNOME on Wayland. Mutter's `org.gnome.Mutter.IdleMonitor` is on the session bus with
  `AddIdleWatch` and `AddUserActiveWatch`. The HUD service in your session arms an idle watch at N
  minutes (default 10); when it fires the HUD window opens fullscreen; the user-active watch closes
  it on the first key or mouse move. Nothing locks; GNOME's own idle, lock and suspend settings stay
  as they are. If the desktop blanks or locks on its own, it will do so over the HUD; the original
  machine ran with blanking off so the HUD stays visible whenever the desk is empty.
- Renderer: one GTK4 + WebKitGTK window, cursor hidden, around 150 MB. Not a browser tab: it does not
  share a profile or a debugging port with any browser you run.
- The window process never runs JavaScript in the page. An early build pushed the JSON in with
  `evaluate_javascript`, and GNOME kept declaring the window "not responding" because the UI
  process's JavaScriptCore blocked the main loop. The page now fetches the two files itself over
  `file://`. The unit sets `GSK_RENDERER=ngl` because GTK's Vulkan renderer logged swapchain stalls
  on a 4K output.
- If WebKit's web process dies, the window reloads the page rather than showing a blank.
- Super+Shift+H shows the HUD at once (SIGUSR1 to the service's main process). The key release that
  follows the shortcut must not count as the activity that closes it, so a manual show arms the
  user-active watch only after a short grace.
- Read-only by design. "Control panel" means the services are listed with their state. No buttons,
  no key bindings inside the HUD, ever; the only input it reacts to is the one that closes it.
- It lives in your session, so after an unattended boot there is no HUD until someone signs in.

## Risks worth testing first

A fullscreen window over other browsers: Chromium may treat an occluded window as hidden and stop
painting, which can break screenshot-based automation running on the same desktop. If that bites,
those browsers need `--disable-backgrounding-occluded-windows`.

## Test plan

1. Idle watch at 1 min: the HUD appears within a minute of leaving the desk; a mouse move returns the
   desktop with no prompt; typing for 3 minutes never shows it.
2. Pull the UPS plug: the Power panel shows ON BATTERY with charge and runtime inside 10 s, and the
   ATTENTION strip names it.
3. `systemctl --user stop` one of the configured services for 20 s: the Services panel names it;
   start it again: "ok".
4. Reboot: the HUD returns with the session.
5. Cost: HUD memory and GPU busy % logged over a night.
