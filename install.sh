#!/bin/bash
# Install or update the Framework HUD for one desktop user (run as root).
#
#   sudo ./install.sh            # installs for the user who ran sudo
#   sudo HUD_USER=alice ./install.sh
#
# Idempotent. Puts the window, the page and the host collector under /usr/local/lib/framework-hud,
# the two user units in /etc/systemd/user, a starter config in ~/.config/framework-hud/config.json
# (only if there is none yet), binds Super+Shift+H to "show the HUD now", then enables both units
# for that user. Nothing here changes how the desktop idles, locks or sleeps.
set -euo pipefail
umask 022
HERE=$(cd "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "install.sh: run as root (sudo ./install.sh)" >&2; exit 2; }
U=${HUD_USER:-${SUDO_USER:-}}
[ -n "$U" ] && [ "$U" != root ] || { echo "install.sh: set HUD_USER to the desktop user" >&2; exit 2; }
U_UID=$(id -u "$U"); U_GID=$(id -g "$U"); U_HOME=$(getent passwd "$U" | cut -d: -f6)

echo "== python bits"
/usr/bin/python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("WebKit","6.0"); from gi.repository import Gtk, WebKit' \
  || { echo "install.sh: python3-gobject + gtk4 + webkitgtk6.0 are needed (Fedora: dnf install python3-gobject gtk4 webkitgtk6.0)" >&2; exit 1; }

echo "== files"
install -d -m 0755 /usr/local/lib/framework-hud
install -m 0755 -o root -g root "$HERE/hud.py" "$HERE/hud-collect.py" /usr/local/lib/framework-hud/
install -m 0644 -o root -g root "$HERE/hud.html" /usr/local/lib/framework-hud/hud.html
install -d -m 0755 /etc/systemd/user
install -m 0644 -o root -g root "$HERE/systemd/framework-hud.service" "$HERE/systemd/framework-hud-collect.service" /etc/systemd/user/
install -d -m 0700 -o "$U_UID" -g "$U_GID" "$U_HOME/.config/framework-hud" "$U_HOME/.cache/framework-hud"
if [ ! -e "$U_HOME/.config/framework-hud/config.json" ]; then
  install -m 0600 -o "$U_UID" -g "$U_GID" "$HERE/examples/config.example.json" "$U_HOME/.config/framework-hud/config.json"
  echo "   wrote a starter config.json: edit it to name your NIC, UPS and services"
fi

echo "== shortcut: Super+Shift+H shows the HUD on demand (GNOME custom keybinding; SIGUSR1 to the service)"
runuser -u "$U" -- env DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$U_UID/bus" /usr/bin/python3 - <<'PY' || echo "   (no GNOME session bus for the user yet: sign in and run install.sh again to bind the key)"
import ast, subprocess
S = "org.gnome.settings-daemon.plugins.media-keys"
P = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/framework-hud/"
def g(*a): return subprocess.run(["gsettings", *a], capture_output=True, text=True, check=True).stdout.strip()
cur = g("get", S, "custom-keybindings")
paths = ast.literal_eval(cur.split(" ", 1)[1] if cur.startswith("@as") else cur)
if P not in paths:
    paths.append(P)
    g("set", S, "custom-keybindings", str(paths))
K = f"{S}.custom-keybinding:{P}"
g("set", K, "name", "Framework HUD")
g("set", K, "command", "systemctl --user kill --kill-whom=main -s USR1 framework-hud.service")   # main only: WebKit's helpers would die of USR1
g("set", K, "binding", "<Super><Shift>h")
print("   " + g("get", K, "binding") + " -> " + g("get", K, "command"))
PY

echo "== units"
systemctl --user -M "$U@" daemon-reload
systemctl --user -M "$U@" enable --now framework-hud-collect.service
systemctl --user -M "$U@" restart framework-hud-collect.service
systemctl --user -M "$U@" enable --now framework-hud.service
systemctl --user -M "$U@" restart framework-hud.service
sleep 12
echo "== state"
systemctl --user -M "$U@" is-active framework-hud-collect.service framework-hud.service | paste - - | sed 's/^/   collect, hud: /'
f="$U_HOME/.cache/framework-hud/host.json"
if [ -s "$f" ]; then echo "   $f: $(stat -c %s "$f") bytes, $(( $(date +%s) - $(stat -c %Y "$f") )) s old"; else echo "   $f: MISSING" >&2; fi
echo "installed. A look now: Super+Shift+H, or: systemctl --user kill --kill-whom=main -s USR1 framework-hud.service"
