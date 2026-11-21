#!/usr/bin/python3
"""
The Framework HUD window (docs/DESIGN.md). Runs in your GNOME session as
framework-hud.service. Asks Mutter's idle monitor for an idle watch at
HUD_IDLE_MINUTES (default 10); when it fires, a borderless fullscreen WebKit
view of hud.html covers the monitor; a user-active watch closes it on the first
key or mouse movement. Nothing locks, nothing is controlled from here: the only
input the HUD reacts to is the one that closes it.

Data: the PAGE reads ~/.cache/framework-hud/host.json (hud-collect.py) and the
app collector's JSON (HUD_APP_JSON, default ~/.cache/framework-hud/app.json)
itself every 5 s over file:// (WebKit's allow-file-access-from-file-urls).
This process never runs JavaScript: an early build pushed the JSON in with
evaluate_javascript, and GNOME kept declaring the window "not responding"
because the UI process's JavaScriptCore blocked the main loop in
Heap::acquireAccessSlow (seen in the stack with eu-stack). GSK_RENDERER=ngl in
the unit for the same reason: the Vulkan renderer logged swapchain stalls on a
4K output.

    hud.py --show      open at once (for a look), still closes on activity

On demand: SIGUSR1 shows the HUD at once. install.sh binds Super+Shift+H in GNOME to
`systemctl --user kill --kill-whom=main -s USR1 framework-hud.service` (main process only:
WebKit's helper processes do not handle USR1 and would die). The key release that follows the
shortcut must not count as the activity that closes it, so a manual show arms the
user-active watch and the key handler only after a short grace.
"""
from __future__ import annotations

import faulthandler
import os
import signal
import sys
import time
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

IDLE_MIN = float(os.environ.get("HUD_IDLE_MINUTES", "10"))
PAGE = Path(os.environ.get("HUD_PAGE", "/usr/local/lib/framework-hud/hud.html"))
HOST_JSON = Path.home() / ".cache" / "framework-hud" / "host.json"
APP_JSON = Path(os.environ.get("HUD_APP_JSON", str(Path.home() / ".cache" / "framework-hud" / "app.json")))
PAGE_URI = PAGE.resolve().as_uri() + "?" + urllib.parse.urlencode(
    {"host": HOST_JSON.as_uri(), "app": APP_JSON.as_uri(), "idle": f"{IDLE_MIN:g}"})


def log(msg: str) -> None:
    print(f"[hud] {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


class Hud:
    def __init__(self, show_now: bool) -> None:
        self.show_now = show_now
        self.app = Gtk.Application(application_id="io.github.FrameworkHud", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.app.connect("activate", self.on_activate)
        self.window: Gtk.ApplicationWindow | None = None
        self.view: WebKit.WebView | None = None
        self.loaded = False
        self.idle_id: int | None = None
        self.active_id: int | None = None
        self.proxy: Gio.DBusProxy | None = None
        self.shown_at: float | None = None
        self.grace_until = 0.0                       # monotonic; input before this does not hide a manual show
        self.manual_grace_s = 1.5

    # ----- window
    def on_activate(self, app: Gtk.Application) -> None:
        self.window = Gtk.ApplicationWindow(application=app, title="Framework HUD", decorated=False)
        self.window.set_default_size(1920, 1080)
        self.view = WebKit.WebView()
        s = self.view.get_settings()
        s.set_enable_javascript(True)
        s.set_enable_developer_extras(False)
        s.set_enable_write_console_messages_to_stdout(True)       # JS errors land in the journal
        s.set_allow_file_access_from_file_urls(True)              # the page fetches the two JSON files itself
        s.set_allow_universal_access_from_file_urls(True)
        self.view.set_background_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=1))
        self.view.connect("context-menu", lambda *_: True)
        self.view.connect("load-changed", self.on_load)
        self.view.connect("web-process-terminated", self.on_web_process_gone)
        self.view.load_uri(PAGE_URI)
        self.window.set_child(self.view)
        self.window.set_cursor(Gdk.Cursor.new_from_name("none"))
        self.window.connect("close-request", self.on_close)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.window.add_controller(keys)
        app.hold()                                   # stay alive with no window on screen
        self.setup_idle()
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self.on_usr1)
        faulthandler.register(signal.SIGUSR2, all_threads=True)   # a Python stack dump into the journal on demand
        if self.show_now:
            GLib.timeout_add(800, lambda: (self.show(manual=True), False)[1])

    def on_usr1(self) -> bool:
        log("SIGUSR1: show on demand")
        self.show(manual=True)
        return True                                  # keep the handler

    def on_load(self, view: WebKit.WebView, event: WebKit.LoadEvent) -> None:
        if event == WebKit.LoadEvent.FINISHED:
            self.loaded = True
            log("page loaded")

    def on_web_process_gone(self, view: WebKit.WebView, reason) -> None:
        """WebKit's web process died (a stray signal, a crash): load the page again rather than show a blank."""
        log(f"web process terminated ({reason}); reloading the page")
        self.loaded = False
        GLib.timeout_add(1000, lambda: (view.load_uri(PAGE_URI), False)[1])

    def on_close(self, _w) -> bool:
        self.hide()
        return True                                  # never destroy; the service keeps the window

    def on_key(self, *_args) -> bool:
        if time.monotonic() < self.grace_until:      # the shortcut's own key release
            return True
        self.hide()                                  # belt and braces beside the user-active watch
        return True

    # ----- Mutter's idle monitor
    def setup_idle(self) -> None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.gnome.Mutter.IdleMonitor",
                                                "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor", None)
            self.proxy.connect("g-signal", self.on_signal)
            res = self.proxy.call_sync("AddIdleWatch", GLib.Variant("(t)", (int(IDLE_MIN * 60 * 1000),)),
                                       Gio.DBusCallFlags.NONE, -1, None)
            self.idle_id = res.unpack()[0]
            log(f"idle watch {self.idle_id} at {IDLE_MIN:g} min")
        except GLib.Error as e:
            log(f"idle monitor unavailable: {e.message}; retrying in 30 s")
            GLib.timeout_add_seconds(30, lambda: (self.setup_idle(), False)[1])

    def on_signal(self, _proxy, _sender, signal: str, params: GLib.Variant) -> None:
        if signal != "WatchFired":
            return
        wid = params.unpack()[0]
        if wid == self.idle_id:
            self.show()
        elif wid == self.active_id:
            self.active_id = None
            self.hide()

    def show(self, manual: bool = False) -> None:
        if self.window is None or self.window.get_visible():
            return
        display = Gdk.Display.get_default()
        monitors = display.get_monitors() if display else None
        self.window.present()
        if monitors is not None and monitors.get_n_items():
            self.window.fullscreen_on_monitor(monitors.get_item(0))
        else:
            self.window.fullscreen()
        self.shown_at = time.time()
        log("shown (manual)" if manual else "shown")
        if manual:
            self.grace_until = time.monotonic() + self.manual_grace_s
            GLib.timeout_add(int(self.manual_grace_s * 1000), lambda: (self.arm_active_watch(), False)[1])
        else:
            self.arm_active_watch()

    def arm_active_watch(self) -> None:
        if self.proxy is None or self.active_id is not None or self.window is None or not self.window.get_visible():
            return
        try:
            res = self.proxy.call_sync("AddUserActiveWatch", None, Gio.DBusCallFlags.NONE, -1, None)
            self.active_id = res.unpack()[0]
        except GLib.Error as e:
            log(f"AddUserActiveWatch failed: {e.message}")

    def hide(self) -> None:
        if self.window is not None and self.window.get_visible():
            self.window.unfullscreen()
            self.window.set_visible(False)
            log(f"hidden after {time.time() - (self.shown_at or time.time()):.0f} s")
        if self.proxy is not None and self.active_id is not None:
            try:
                self.proxy.call_sync("RemoveWatch", GLib.Variant("(u)", (self.active_id,)), Gio.DBusCallFlags.NONE, -1, None)
            except GLib.Error:
                pass
            self.active_id = None

    def run(self) -> int:
        return self.app.run([])


if __name__ == "__main__":
    sys.exit(Hud(show_now="--show" in sys.argv).run())
