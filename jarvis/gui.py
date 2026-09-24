"""Full-screen Iron-Man style HUD."""
import datetime
import math
import os
import time
import tkinter as tk

import customtkinter as ctk
import psutil
from PIL import Image

from . import ai, config
from .app import Jarvis
from .bridge import bridge
from .camera import draw_hud, get_camera

BG = "#050b14"
PANEL = "#0a1624"
CYAN = "#3ee6ff"
DIM = "#1b6f86"
TEXT = "#c9f6ff"
AMBER = "#ffb347"
STATE_COLORS = {"idle": CYAN, "listening": "#3cff9e", "thinking": AMBER, "speaking": "#7fdcff"}
STATE_LABELS = {"idle": "ONLINE", "listening": "LISTENING", "thinking": "PROCESSING", "speaking": "SPEAKING"}
FONT = "Consolas" if config.OS_NAME == "Windows" else "DejaVu Sans Mono"

MAP_TILES = {
    "HUD": ("https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", 19),
    "Road": ("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png", 19),
    "Satellite": ("https://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}&s=Ga", 22),
    "Hybrid": ("https://mt0.google.com/vt/lyrs=y&hl=en&x={x}&y={y}&z={z}&s=Ga", 22),
    "Terrain": ("https://mt0.google.com/vt/lyrs=p&hl=en&x={x}&y={y}&z={z}&s=Ga", 20),
}
MARKER_STYLE = dict(marker_color_circle=BG, marker_color_outside=CYAN, text_color=CYAN, font=(FONT, 11, "bold"))

ctk.set_appearance_mode("dark")


def _hwnd(win):
    import ctypes

    return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()


def _set_exstyle(win, flags):
    if config.OS_NAME != "Windows":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = _hwnd(win)
        style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, style | flags)
    except Exception:  # noqa: BLE001
        pass


def _click_through(win):
    _set_exstyle(win, 0x00080000 | 0x00000020)  # WS_EX_LAYERED | WS_EX_TRANSPARENT


def _no_activate(win):
    _set_exstyle(win, 0x08000000 | 0x00000080)  # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW


def panel(parent, title):
    frame = ctk.CTkFrame(parent, fg_color=PANEL, border_color=DIM, border_width=1, corner_radius=8)
    head = ctk.CTkFrame(frame, fg_color="transparent")
    head.pack(fill="x", padx=12, pady=(8, 4))
    ctk.CTkLabel(head, text=title, text_color=CYAN, font=(FONT, 13, "bold"), anchor="w").pack(side="left")
    frame.head = head
    return frame


class JarvisGUI(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title("J.A.R.V.I.S.")
        self.geometry("1400x850")
        self.state_name = "idle"
        self.tick = 0
        self.camera_on = False
        self.map_expanded = False
        self.map_markers = []
        self.map_paths = []
        self._camera_img = None
        self._capture_img = None
        self._code_window = None
        self._was_present = False
        self._absent_since = time.time()
        self.mini = False
        self._pill = None
        self._was_fullscreen = config.START_FULLSCREEN
        self._last_foreign_hwnd = None
        self._last_reply = ""
        bridge.gui_attached = True

        self._build_layout()
        self._bind_keys()
        if config.START_FULLSCREEN:
            self.after(100, lambda: self.attributes("-fullscreen", True))

        self.jarvis = Jarvis()
        self.after(33, self._animate)
        self.after(50, self._drain_bridge)
        self.after(500, self._update_stats)
        self.after(800, self.jarvis.greet)
        if config.OS_NAME == "Windows":
            self.after(300, self._track_foreground)

    # ================================================================ layout
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=0, minsize=320)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=460)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(10, 0))
        ctk.CTkLabel(header, text="J.A.R.V.I.S.", text_color=CYAN, font=(FONT, 26, "bold")).pack(side="left")
        ctk.CTkLabel(
            header, text="  JUST A RATHER VERY INTELLIGENT SYSTEM", text_color=DIM, font=(FONT, 12)
        ).pack(side="left", pady=(8, 0))
        self.clock = ctk.CTkLabel(header, text="", text_color=TEXT, font=(FONT, 22, "bold"))
        self.clock.pack(side="right")

        # AI provider switch
        ai_box = ctk.CTkFrame(header, fg_color=PANEL, border_color=DIM, border_width=1, corner_radius=8)
        ai_box.pack(side="right", padx=20)
        ctk.CTkLabel(ai_box, text="AI CORE", text_color=DIM, font=(FONT, 11, "bold")).pack(side="left", padx=(10, 6))
        self.ai_switch = ctk.CTkSegmentedButton(
            ai_box, values=[ai.LABELS[p] for p in ai.PROVIDERS], command=self._on_ai_switch,
            selected_color="#155a78", selected_hover_color="#1d7699", unselected_color=BG,
            text_color=TEXT, font=(FONT, 12, "bold"),
        )
        self.ai_switch.pack(side="left", pady=6)
        self.ai_switch.set(ai.LABELS[ai.provider()])
        self.model_label = ctk.CTkLabel(ai_box, text="", text_color=DIM, font=(FONT, 11), width=190, anchor="w")
        self.model_label.pack(side="left", padx=10)

        self._build_left()
        self._build_center()
        self._build_right()
        self._build_input_bar()

    def _build_left(self):
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=10)

        stats = panel(left, "SYSTEM DIAGNOSTICS")
        stats.pack(fill="x")
        self.bars = {}
        for name in ("CPU", "MEMORY", "DISK", "BATTERY"):
            row = ctk.CTkFrame(stats, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=3)
            label = ctk.CTkLabel(row, text=f"{name}  --", text_color=TEXT, font=(FONT, 12), anchor="w")
            label.pack(fill="x")
            bar = ctk.CTkProgressBar(row, progress_color=CYAN, fg_color="#10283a", height=8)
            bar.set(0)
            bar.pack(fill="x", pady=(2, 0))
            self.bars[name] = (label, bar)
        self.net_label = ctk.CTkLabel(stats, text="", text_color=DIM, font=(FONT, 11), anchor="w")
        self.net_label.pack(fill="x", padx=12, pady=(4, 10))
        self._net_last = psutil.net_io_counters()

        cam = panel(left, "OPTICAL SENSORS")
        cam.pack(fill="both", expand=True, pady=(10, 0))
        self.cam_switch = ctk.CTkSwitch(
            cam.head, text="", width=40, progress_color=CYAN, command=self._toggle_camera_switch
        )
        self.cam_switch.pack(side="right")
        self.cam_view = ctk.CTkLabel(cam, text="sensors offline", text_color=DIM, font=(FONT, 11))
        self.cam_view.pack(fill="x", padx=8, pady=(4, 4))
        self.sensor_label = ctk.CTkLabel(cam, text="", text_color=TEXT, font=(FONT, 11), anchor="w", justify="left")
        self.sensor_label.pack(fill="x", padx=12, pady=(0, 8))

    def _build_center(self):
        self.center = ctk.CTkFrame(self, fg_color="transparent")
        self.center.grid(row=1, column=1, sticky="nsew", padx=8, pady=10)
        self.center.grid_rowconfigure(0, weight=3)
        self.center.grid_rowconfigure(2, weight=2)
        self.center.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.center, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.status = ctk.CTkLabel(self.center, text="ONLINE", text_color=CYAN, font=(FONT, 16, "bold"))
        self.status.grid(row=1, column=0, pady=4)

        log_panel = panel(self.center, "COMMUNICATIONS LOG")
        log_panel.grid(row=2, column=0, sticky="nsew")
        self.log = ctk.CTkTextbox(
            log_panel, fg_color=BG, text_color=TEXT, font=(FONT, 14), wrap="word", border_width=0
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log.tag_config("you", foreground="#ffffff")
        self.log.tag_config("jarvis", foreground=CYAN)
        self.log.tag_config("tool", foreground="#6b8ea0")
        self.log.tag_config("system", foreground=AMBER)
        self.log.configure(state="disabled")

    def _build_right(self):
        self.right = ctk.CTkFrame(self, fg_color="transparent")
        self.right.grid(row=1, column=2, sticky="nsew", padx=(8, 16), pady=10)

        map_panel = panel(self.right, "GLOBAL POSITIONING")
        map_panel.pack(fill="both", expand=True)
        small = dict(width=30, height=24, fg_color="#0f3346", hover_color="#155a78", text_color=TEXT, font=(FONT, 12))
        ctk.CTkButton(map_panel.head, text="⛶", command=lambda: self.expand_map(not self.map_expanded), **small).pack(
            side="right", padx=2)
        ctk.CTkButton(map_panel.head, text="✕", command=self.clear_map, **small).pack(side="right", padx=2)
        self.map = None
        try:
            import tkintermapview

            self.map = tkintermapview.TkinterMapView(map_panel, corner_radius=6, bg_color=PANEL)
            self.map.pack(fill="both", expand=True, padx=8)
            self.map.canvas.configure(bg="#0b0f14")  # dark while tiles load
            self.map_toggle = ctk.CTkSegmentedButton(
                map_panel, values=list(MAP_TILES), command=self._set_map_type,
                selected_color="#155a78", unselected_color=BG, text_color=TEXT, font=(FONT, 11),
            )
            self.map_toggle.pack(pady=6)
            self._set_map_type("HUD")
            self.map.set_position(51.5074, -0.1278)  # London until asked otherwise
            self.map.set_zoom(3)
        except Exception as err:  # noqa: BLE001
            ctk.CTkLabel(map_panel, text=f"map unavailable: {err}", text_color=DIM).pack(expand=True)

        self.capture_panel = panel(self.right, "LAST CAPTURE")
        self.capture_panel.pack(fill="x", pady=(10, 0))
        self.capture_view = ctk.CTkLabel(self.capture_panel, text="no captures yet", text_color=DIM, height=160)
        self.capture_view.pack(fill="x", padx=8, pady=(0, 8))

    def _build_input_bar(self):
        bar = ctk.CTkFrame(self, fg_color=PANEL, border_color=DIM, border_width=1, corner_radius=8)
        bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 14))
        bar.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            bar, placeholder_text="Type a command, or press the mic…  (F11 fullscreen · Ctrl+Q quit)",
            fg_color=BG, border_color=DIM, text_color=TEXT, font=(FONT, 15), height=42,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        self.entry.bind("<Return>", lambda _e: self._send())

        btn = dict(fg_color="#0f3346", hover_color="#155a78", text_color=TEXT, height=42, font=(FONT, 13, "bold"))
        ctk.CTkButton(bar, text="SEND", width=90, command=self._send, **btn).grid(row=0, column=1, padx=4)
        ctk.CTkButton(bar, text="🎤 MIC", width=100, command=self.jarvis_listen, **btn).grid(row=0, column=2, padx=4)
        ctk.CTkButton(bar, text="■ STOP", width=90, command=lambda: self.jarvis.stop_speaking(), **btn).grid(
            row=0, column=3, padx=4
        )
        self.wake_switch = ctk.CTkSwitch(
            bar, text=f'Wake word "{config.WAKE_WORD.title()}"', text_color=TEXT, progress_color=CYAN,
            command=lambda: self.jarvis.set_wake_word(bool(self.wake_switch.get())),
        )
        self.wake_switch.grid(row=0, column=4, padx=10)
        self.mute_switch = ctk.CTkSwitch(
            bar, text="Mute voice", text_color=TEXT, progress_color=CYAN,
            command=lambda: setattr(self.jarvis.voice, "muted", bool(self.mute_switch.get())),
        )
        self.mute_switch.grid(row=0, column=5, padx=(4, 14))

    def _bind_keys(self):
        self.bind("<F11>", lambda _e: self.attributes("-fullscreen", not self.attributes("-fullscreen")))
        self.bind("<Escape>", lambda _e: self.attributes("-fullscreen", False))
        self.bind("<Control-q>", lambda _e: self._quit())
        self.bind("<Control-m>", lambda _e: self.jarvis_listen())
        self.bind("<Control-h>", lambda _e: self.set_mini(not self.mini))
        self.protocol("WM_DELETE_WINDOW", self._quit)

    # ================================================================ actions
    def _send(self):
        text = self.entry.get()
        self.entry.delete(0, "end")
        self.jarvis.submit(text)

    def jarvis_listen(self):
        self.jarvis.listen_once()

    def _quit(self):
        get_camera().stop()
        bridge.gui_attached = False
        self.destroy()

    def _on_ai_switch(self, label):
        name = {v: k for k, v in ai.LABELS.items()}[label]
        if not self.jarvis.set_provider(name):
            self.ai_switch.set(ai.LABELS[ai.provider()])

    # ---------------------------------------------------------------- camera
    def _toggle_camera_switch(self):
        self._set_camera(bool(self.cam_switch.get()))

    def _set_camera(self, on):
        cam = get_camera()
        if on and not cam.start():
            self.append_log("No camera found.", "system")
            on = False
        if not on:
            cam.stop()
            self.cam_view.configure(image=None, text="sensors offline")
            self.sensor_label.configure(text="")
        self.camera_on = on
        self.cam_switch.select() if on else self.cam_switch.deselect()
        if on:
            self._update_camera()

    def _update_camera(self):
        if not self.camera_on:
            return
        cam = get_camera()
        frame = cam.latest_frame()
        if frame is not None:
            import cv2

            frame = draw_hud(frame, cam.sensors, self.tick)
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            w = max(self.cam_view.winfo_width() - 8, 200)
            h = int(w * img.height / img.width)
            self._camera_img = ctk.CTkImage(img, size=(w, h))
            self.cam_view.configure(image=self._camera_img, text="")
            self._update_sensors(cam.sensors)
        self.after(50, self._update_camera)

    def _update_sensors(self, s):
        faces = s.get("faces", [])
        dist = f"  ·  {faces[0]['distance_m']:.1f} m" if faces else ""
        present = bool(faces)
        self.sensor_label.configure(
            text=f"SUBJECTS {len(faces)}{dist}\nMOTION {s.get('motion', 0):.0f}%   LIGHT {s.get('light', 0):.0f}%"
        )
        now = time.time()
        if present and not self._was_present and now - self._absent_since > 120:
            self.jarvis.on_presence()
        if not present and self._was_present:
            self._absent_since = now
        if present:
            self._was_present = True
        elif s.get("present_since") is None:
            self._was_present = False

    # ---------------------------------------------------------------- map
    def _set_map_type(self, kind):
        if not self.map:
            return
        kind = {k.lower(): k for k in MAP_TILES}.get(kind.lower(), "HUD")
        url, max_zoom = MAP_TILES[kind]
        self.map.set_tile_server(url, max_zoom=max_zoom)
        self.map_toggle.set(kind)

    def show_map(self, lat, lon, label, zoom, map_type, clear):
        if not self.map:
            return
        if map_type:
            self._set_map_type(map_type)
        if clear:
            self.clear_map()
        self.map.set_position(lat, lon)
        self.map.set_zoom(zoom)
        self.add_marker(lat, lon, label)

    def add_marker(self, lat, lon, label):
        if self.map:
            self.map_markers.append(self.map.set_marker(lat, lon, text=label, **MARKER_STYLE))

    def draw_route(self, points, label):
        if not self.map or len(points) < 2:
            return
        self.clear_map()
        self.map_paths.append(self.map.set_path(points, color=CYAN, width=4))
        self.add_marker(*points[0], "START")
        self.add_marker(*points[-1], label.upper())
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        pad_lat = (max(lats) - min(lats)) * 0.15 + 0.002
        pad_lon = (max(lons) - min(lons)) * 0.15 + 0.002
        self.map.fit_bounding_box((max(lats) + pad_lat, min(lons) - pad_lon), (min(lats) - pad_lat, max(lons) + pad_lon))

    def clear_map(self):
        if not self.map:
            return
        for m in self.map_markers:
            m.delete()
        for p in self.map_paths:
            p.delete()
        self.map_markers, self.map_paths = [], []

    def expand_map(self, on):
        """Swap the map into the big centre area (and back)."""
        self.map_expanded = on
        if on:
            self.right.grid_configure(column=1, columnspan=2)
            self.center.grid_remove()
            self.capture_panel.pack_forget()
        else:
            self.right.grid_configure(column=2, columnspan=1)
            self.center.grid()
            self.capture_panel.pack(fill="x", pady=(10, 0))

    def _draw_map_overlay(self):
        """Holographic HUD overlay drawn on top of the map tiles."""
        if not self.map:
            return
        c = self.map.canvas
        c.delete("hud")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        cx, cy = w / 2, h / 2
        col = CYAN
        # corner brackets
        L = 22
        for (x, y, dx, dy) in ((6, 6, 1, 1), (w - 6, 6, -1, 1), (6, h - 6, 1, -1), (w - 6, h - 6, -1, -1)):
            c.create_line(x, y, x + dx * L, y, fill=col, width=2, tags="hud")
            c.create_line(x, y, x, y + dy * L, fill=col, width=2, tags="hud")
        # centre reticle
        r = 16
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=col, width=1, tags="hud")
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c.create_line(cx + dx * (r - 6), cy + dy * (r - 6), cx + dx * (r + 10), cy + dy * (r + 10),
                          fill=col, width=1, tags="hud")
        # radar sweep: a fading wedge behind a bright leading edge
        R = min(w, h) * 0.45
        deg = (self.tick * 3) % 360
        for k, shade in enumerate(("#0d4a5c", "#0a3947", "#082a35")):
            c.create_arc(cx - R, cy - R, cx + R, cy + R, start=-deg + 8 * k, extent=8, style="pieslice",
                         outline="", fill=shade, tags="hud")
        a = math.radians(deg)
        c.create_line(cx, cy, cx + R * math.cos(a), cy + R * math.sin(a), fill=col, width=2, tags="hud")
        c.create_oval(cx - R, cy - R, cx + R, cy + R, outline=DIM, dash=(2, 6), tags="hud")
        c.create_oval(cx - R / 2, cy - R / 2, cx + R / 2, cy + R / 2, outline=DIM, dash=(2, 6), tags="hud")
        # readouts
        lat, lon = self.map.get_position()
        ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
        c.create_rectangle(8, h - 30, 330, h - 8, fill=BG, outline=DIM, tags="hud")
        c.create_text(16, h - 19, anchor="w", fill=col, font=(FONT, 10, "bold"), tags="hud",
                      text=f"LAT {abs(lat):8.4f}°{ns}   LON {abs(lon):8.4f}°{ew}   Z{int(self.map.zoom)}")
        blink = "●" if (self.tick // 15) % 2 else "○"
        c.create_text(w - 14, 18, anchor="e", fill=col, font=(FONT, 10, "bold"), tags="hud",
                      text=f"{blink} GEO-TRACK ACTIVE   PINS {len(self.map_markers)}")
        c.tag_raise("hud")

    # ---------------------------------------------------------------- misc views
    def show_image(self, path):
        try:
            img = Image.open(path)
        except OSError:
            return
        w = max(self.capture_view.winfo_width() - 8, 300)
        h = min(int(w * img.height / img.width), 240)
        self._capture_img = ctk.CTkImage(img, size=(int(h * img.width / img.height), h))
        self.capture_view.configure(image=self._capture_img, text="")

    def show_code(self, title, code):
        win = self._code_window
        if win is None or not win.winfo_exists():
            win = ctk.CTkToplevel(self, fg_color=BG)
            win.geometry("900x650")
            win.attributes("-topmost", True)
            win.header = ctk.CTkLabel(win, text="", text_color=CYAN, font=(FONT, 13, "bold"), anchor="w")
            win.header.pack(fill="x", padx=12, pady=(10, 0))
            win.text = ctk.CTkTextbox(win, fg_color=PANEL, text_color=TEXT, font=(FONT, 13), wrap="none",
                                      border_color=DIM, border_width=1)
            win.text.pack(fill="both", expand=True, padx=10, pady=10)
            self._code_window = win
        win.title(f"J.A.R.V.I.S. — {title}")
        win.header.configure(text=f"CODE  ›  {title}")
        win.text.configure(state="normal")
        win.text.delete("1.0", "end")
        win.text.insert("1.0", code)
        win.text.configure(state="disabled")
        win.lift()

    def append_log(self, text, who):
        if who == "jarvis":
            self._last_reply = text
        elif who == "tool":
            self._last_reply = "⚙ " + text
        elif who == "you":
            self._last_reply = "› " + text
        prefix = {"you": "YOU  › ", "jarvis": "JARVIS › ", "tool": "  ⚙ ", "system": "  ! "}.get(who, "")
        stamp = datetime.datetime.now().strftime("%H:%M ")
        self.log.configure(state="normal")
        self.log.insert("end", stamp + prefix + text + "\n", who)
        self.log.configure(state="disabled")
        self.log.see("end")

    def set_state(self, state):
        self.state_name = state
        self.status.configure(text=STATE_LABELS.get(state, state.upper()), text_color=STATE_COLORS.get(state, CYAN))

    # ---------------------------------------------------------------- desktop control helpers
    def _request_mini(self, on, done):
        self.set_mini(on)
        self.after(350 if on else 50, done.set)  # let the screen redraw before a screenshot

    def set_mini(self, on):
        """Mini mode: hide the full HUD and show a small always-on-top pill instead."""
        if on == self.mini:
            return
        self.mini = on
        if on:
            self._was_fullscreen = bool(self.attributes("-fullscreen"))
            self.attributes("-fullscreen", False)
            self.withdraw()
            self._show_pill()
        else:
            if self._pill is not None:
                self._pill.withdraw()
            self.deiconify()
            if self._was_fullscreen:
                self.attributes("-fullscreen", True)
            self.lift()
            self.focus_force()
        self.update_idletasks()

    def _show_pill(self):
        if self._pill is None:
            pill = tk.Toplevel(self, bg=BG, highlightbackground=CYAN, highlightthickness=1)
            pill.overrideredirect(True)
            pill.attributes("-topmost", True)
            self._pill_canvas = tk.Canvas(pill, width=54, height=54, bg=BG, highlightthickness=0)
            self._pill_canvas.pack(side="left", padx=6, pady=4)
            box = tk.Frame(pill, bg=BG)
            box.pack(side="left", fill="both", expand=True)
            self._pill_status = tk.Label(box, text="", fg=CYAN, bg=BG, font=(FONT, 10, "bold"), anchor="w")
            self._pill_status.pack(fill="x")
            self._pill_text = tk.Label(box, text="", fg=TEXT, bg=BG, font=(FONT, 9), anchor="w",
                                       justify="left", wraplength=250)
            self._pill_text.pack(fill="x")
            btns = tk.Frame(pill, bg=BG)
            btns.pack(side="right", padx=6)
            style = dict(bg="#0f3346", fg=TEXT, activebackground="#155a78", activeforeground=TEXT,
                         relief="flat", font=(FONT, 10, "bold"), width=4, cursor="hand2")
            tk.Button(btns, text="MIC", command=self.jarvis_listen, **style).pack(pady=2)
            tk.Button(btns, text="HUD", command=lambda: self.set_mini(False), **style).pack(pady=2)
            self._pill = pill
            self.update_idletasks()
            _no_activate(pill)  # clicking the pill must not steal focus from the app being controlled
        w, h = 400, 66
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self._pill.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 70}")
        self._pill.deiconify()
        self._pill.lift()

    def _draw_pill(self):
        c = self._pill_canvas
        c.delete("all")
        color = STATE_COLORS.get(self.state_name, CYAN)
        t = self.tick
        c.create_oval(4, 4, 50, 50, outline=DIM, width=1)
        for i, (r, ext) in enumerate(((22, 80), (16, 120))):
            start = (t * (4 if self.state_name == "thinking" else 1.5) * (1 if i == 0 else -1)) % 360
            c.create_arc(27 - r, 27 - r, 27 + r, 27 + r, start=start, extent=ext, style="arc", outline=color, width=3)
        pr = 7 + 2 * math.sin(t / 5)
        c.create_oval(27 - pr, 27 - pr, 27 + pr, 27 + pr, fill=color, outline="")
        self._pill_status.configure(text=f"J.A.R.V.I.S · {STATE_LABELS.get(self.state_name, '')}", fg=color)
        reply = self._last_reply
        self._pill_text.configure(text=reply if len(reply) < 90 else reply[:87] + "…")

    def show_pointer(self, x, y, done):
        """Animated targeting ring where Jarvis is about to click."""
        size = 96
        key = "#010203"
        win = tk.Toplevel(self, bg=key)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        if config.OS_NAME == "Windows":
            win.attributes("-transparentcolor", key)
        else:
            win.attributes("-alpha", 0.85)
        win.geometry(f"{size}x{size}+{x - size // 2}+{y - size // 2}")
        c = tk.Canvas(win, width=size, height=size, bg=key, highlightthickness=0)
        c.pack()
        self.update_idletasks()
        _click_through(win)
        m = size / 2

        def frame(i=0):
            if i > 7:
                win.destroy()
                done.set()
                return
            c.delete("all")
            r = 44 - i * 4.5
            c.create_oval(m - r, m - r, m + r, m + r, outline=CYAN, width=3)
            c.create_oval(m - r / 2, m - r / 2, m + r / 2, m + r / 2, outline=AMBER, width=2)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c.create_line(m + dx * (r + 2), m + dy * (r + 2), m + dx * (r - 10), m + dy * (r - 10),
                              fill=CYAN, width=2)
            win.after(38, frame, i + 1)

        frame()

    def _track_foreground(self):
        """Remember the last window that isn't Jarvis, to hand focus back before typing (Windows)."""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if hwnd and pid.value != os.getpid():
                self._last_foreign_hwnd = hwnd
        except Exception:  # noqa: BLE001
            return
        self.after(300, self._track_foreground)

    def _refocus(self, done):
        try:
            import ctypes

            user32 = ctypes.windll.user32
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
            if pid.value == os.getpid() and self._last_foreign_hwnd:
                user32.SetForegroundWindow(self._last_foreign_hwnd)
        except Exception:  # noqa: BLE001
            pass
        self.after(150, done.set)

    def ask_confirm(self, message, done, result):
        """Themed Yes/No dialog that doesn't freeze the HUD while it waits."""
        win = ctk.CTkToplevel(self, fg_color=PANEL)
        win.title("J.A.R.V.I.S. — confirm action")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        ctk.CTkLabel(win, text="⚠  CONFIRM ACTION", text_color=AMBER, font=(FONT, 15, "bold")).pack(
            padx=20, pady=(16, 6), anchor="w")
        ctk.CTkLabel(win, text=message[:1500], text_color=TEXT, font=(FONT, 12), justify="left",
                     wraplength=560, anchor="w").pack(padx=20, pady=6, anchor="w")

        def answer(ok):
            result["ok"] = ok
            done.set()
            win.destroy()

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(pady=(8, 16))
        ctk.CTkButton(row, text="YES, DO IT", width=140, fg_color="#155a78", hover_color="#1d7699",
                      font=(FONT, 13, "bold"), command=lambda: answer(True)).pack(side="left", padx=8)
        ctk.CTkButton(row, text="NO", width=140, fg_color="#3a1a1a", hover_color="#5a2424",
                      font=(FONT, 13, "bold"), command=lambda: answer(False)).pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", lambda: answer(False))
        win.bind("<Return>", lambda _e: answer(True))
        win.bind("<Escape>", lambda _e: answer(False))
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"+{(sw - win.winfo_width()) // 2}+{(sh - win.winfo_height()) // 3}")
        win.lift()
        win.focus_force()

    # ================================================================ loops
    def _drain_bridge(self):
        handlers = {
            "log": lambda who, text: self.append_log(text, who),
            "state": self.set_state,
            "map": self.show_map,
            "marker": self.add_marker,
            "route": self.draw_route,
            "map_clear": self.clear_map,
            "map_expand": self.expand_map,
            "image": self.show_image,
            "code": self.show_code,
            "camera": self._set_camera,
            "provider": lambda name: self.ai_switch.set(ai.LABELS[name]),
            "mini": self._request_mini,
            "pointer": self.show_pointer,
            "refocus": self._refocus,
        }
        while not bridge.queue.empty():
            kind, args = bridge.queue.get_nowait()
            try:
                if kind == "clipboard":
                    self.clipboard_clear()
                    self.clipboard_append(args[0])
                    self.update()
                    args[1].set()
                elif kind == "confirm":
                    self.ask_confirm(*args)
                elif kind in handlers:
                    handlers[kind](*args)
            except Exception as err:  # noqa: BLE001  (never let one bad message kill the HUD)
                self.append_log(f"display error ({kind}): {err}", "system")
        self.after(50, self._drain_bridge)

    def _update_stats(self):
        self.clock.configure(text=datetime.datetime.now().strftime("%H:%M:%S   %a %d %b %Y"))
        self.model_label.configure(text=f"model: {ai.current_model()}")
        values = {
            "CPU": psutil.cpu_percent(),
            "MEMORY": psutil.virtual_memory().percent,
            "DISK": psutil.disk_usage("C:\\" if config.OS_NAME == "Windows" else "/").percent,
        }
        battery = psutil.sensors_battery()
        values["BATTERY"] = battery.percent if battery else None
        for name, value in values.items():
            label, bar = self.bars[name]
            if value is None:
                label.configure(text=f"{name}  n/a")
                continue
            label.configure(text=f"{name}  {value:.0f}%")
            bar.set(value / 100)
        net = psutil.net_io_counters()
        down = (net.bytes_recv - self._net_last.bytes_recv) / 1024
        up = (net.bytes_sent - self._net_last.bytes_sent) / 1024
        self._net_last = net
        self.net_label.configure(text=f"NET  ↓ {down:,.0f} KB/s   ↑ {up:,.0f} KB/s")
        self.after(1000, self._update_stats)

    def _animate(self):
        self.tick += 1
        if self.mini and self._pill is not None:
            self._draw_pill()
        if self.map and self.tick % 2 == 0:
            self._draw_map_overlay()
        if self.map_expanded:
            self.after(33, self._animate)
            return
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        cx, cy = w / 2, h / 2
        R = max(min(w, h) * 0.44, 40)
        t = self.tick
        color = STATE_COLORS.get(self.state_name, CYAN)
        speed = {"idle": 0.6, "listening": 1.5, "thinking": 3.5, "speaking": 1.2}.get(self.state_name, 1)

        for i in range(72):  # outer tick ring
            a = math.radians(i * 5 + t * 0.2 * speed)
            inner = R * (0.93 if i % 6 else 0.88)
            c.create_line(cx + inner * math.cos(a), cy + inner * math.sin(a),
                          cx + R * math.cos(a), cy + R * math.sin(a), fill=DIM, width=2)
        c.create_oval(cx - R * 1.02, cy - R * 1.02, cx + R * 1.02, cy + R * 1.02, outline=DIM, width=1)

        for i, (radius, extent, width) in enumerate([(0.80, 70, 4), (0.68, 110, 3), (0.56, 50, 6), (0.45, 140, 2)]):
            r = R * radius
            direction = 1 if i % 2 == 0 else -1
            for k in range(2 if i != 3 else 1):
                start = (t * speed * (i + 1) * direction + k * 180) % 360
                c.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=extent,
                             style="arc", outline=color, width=width)

        amp = 0.18 if self.state_name == "speaking" else 0.10 if self.state_name == "listening" else 0.04
        pulse = 1 + amp * math.sin(t / (3 if self.state_name == "speaking" else 8))
        core = R * 0.30 * pulse
        for j, shade in enumerate(("#0b3a4d", "#0f5d78", color)):
            rr = core * (1 - j * 0.28)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=color, width=2,
                          fill=shade if j < 2 else "")
        c.create_text(cx, cy - R * 0.02, text="J.A.R.V.I.S", fill=TEXT, font=(FONT, max(int(R * 0.05), 8), "bold"))
        c.create_text(cx, cy + R * 0.09, text=ai.LABELS[ai.provider()].upper(), fill=DIM,
                      font=(FONT, max(int(R * 0.045), 8)))
        self.after(33, self._animate)
