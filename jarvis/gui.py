"""Full-screen Iron-Man style HUD."""
import datetime
import math
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import psutil
from PIL import Image

from . import config
from .app import Jarvis
from .bridge import bridge
from .camera import get_camera

BG = "#050b14"
PANEL = "#0a1624"
CYAN = "#3ee6ff"
DIM = "#1b6f86"
TEXT = "#c9f6ff"
STATE_COLORS = {"idle": CYAN, "listening": "#3cff9e", "thinking": "#ffb347", "speaking": "#7fdcff"}
STATE_LABELS = {"idle": "ONLINE", "listening": "LISTENING", "thinking": "PROCESSING", "speaking": "SPEAKING"}
FONT = "Consolas" if config.OS_NAME == "Windows" else "DejaVu Sans Mono"

ctk.set_appearance_mode("dark")


def panel(parent, title):
    frame = ctk.CTkFrame(parent, fg_color=PANEL, border_color=DIM, border_width=1, corner_radius=8)
    ctk.CTkLabel(frame, text=title, text_color=CYAN, font=(FONT, 13, "bold"), anchor="w").pack(
        fill="x", padx=12, pady=(8, 4)
    )
    return frame


class JarvisGUI(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title("J.A.R.V.I.S.")
        self.geometry("1400x850")
        self.state_name = "idle"
        self.tick = 0
        self.camera_on = False
        self._camera_img = None
        self._capture_img = None

        self._build_layout()
        self._bind_keys()
        if config.START_FULLSCREEN:
            self.after(100, lambda: self.attributes("-fullscreen", True))

        self.jarvis = Jarvis()
        self.after(33, self._animate)
        self.after(50, self._drain_bridge)
        self.after(500, self._update_stats)
        self.after(800, self.jarvis.greet)

    # ================================================================ layout
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=0, minsize=300)
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
            row.pack(fill="x", padx=12, pady=4)
            label = ctk.CTkLabel(row, text=f"{name}  --", text_color=TEXT, font=(FONT, 12), anchor="w")
            label.pack(fill="x")
            bar = ctk.CTkProgressBar(row, progress_color=CYAN, fg_color="#10283a", height=8)
            bar.set(0)
            bar.pack(fill="x", pady=(2, 0))
            self.bars[name] = (label, bar)
        self.net_label = ctk.CTkLabel(stats, text="", text_color=DIM, font=(FONT, 11), anchor="w")
        self.net_label.pack(fill="x", padx=12, pady=(4, 10))
        self._net_last = psutil.net_io_counters()

        cam = panel(left, "OPTICAL SENSOR")
        cam.pack(fill="both", expand=True, pady=(10, 0))
        self.cam_switch = ctk.CTkSwitch(
            cam, text="Camera", text_color=TEXT, progress_color=CYAN, command=self._toggle_camera_switch
        )
        self.cam_switch.pack(anchor="w", padx=12)
        self.cam_view = ctk.CTkLabel(cam, text="camera offline", text_color=DIM, font=(FONT, 11))
        self.cam_view.pack(fill="both", expand=True, padx=8, pady=8)

    def _build_center(self):
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.grid(row=1, column=1, sticky="nsew", padx=8, pady=10)
        center.grid_rowconfigure(0, weight=3)
        center.grid_rowconfigure(2, weight=2)
        center.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(center, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.status = ctk.CTkLabel(center, text="ONLINE", text_color=CYAN, font=(FONT, 16, "bold"))
        self.status.grid(row=1, column=0, pady=4)

        log_panel = panel(center, "COMMUNICATIONS LOG")
        log_panel.grid(row=2, column=0, sticky="nsew")
        self.log = ctk.CTkTextbox(
            log_panel, fg_color=BG, text_color=TEXT, font=(FONT, 14), wrap="word", border_width=0
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log.tag_config("you", foreground="#ffffff")
        self.log.tag_config("jarvis", foreground=CYAN)
        self.log.tag_config("tool", foreground="#6b8ea0")
        self.log.tag_config("system", foreground="#ffb347")
        self.log.configure(state="disabled")

    def _build_right(self):
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=2, sticky="nsew", padx=(8, 16), pady=10)

        map_panel = panel(right, "GLOBAL POSITIONING")
        map_panel.pack(fill="both", expand=True)
        self.map = None
        try:
            import tkintermapview

            self.map = tkintermapview.TkinterMapView(map_panel, corner_radius=6)
            self.map.pack(fill="both", expand=True, padx=8)
            self.map.set_position(51.5074, -0.1278)  # London until asked otherwise
            self.map.set_zoom(4)
            toggle = ctk.CTkSegmentedButton(
                map_panel, values=["road", "satellite"], command=self._set_map_type,
                selected_color=DIM, text_color=TEXT,
            )
            toggle.set("road")
            toggle.pack(pady=6)
            self.map_toggle = toggle
        except Exception as err:  # noqa: BLE001
            ctk.CTkLabel(map_panel, text=f"map unavailable: {err}", text_color=DIM).pack(expand=True)

        cap = panel(right, "LAST CAPTURE")
        cap.pack(fill="x", pady=(10, 0))
        self.capture_view = ctk.CTkLabel(cap, text="no captures yet", text_color=DIM, height=180)
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
        self.destroy()

    def _toggle_camera_switch(self):
        self._set_camera(bool(self.cam_switch.get()))

    def _set_camera(self, on):
        cam = get_camera()
        if on and not cam.start():
            self.append_log("No camera found.", "system")
            on = False
        if not on:
            cam.stop()
            self.cam_view.configure(image=None, text="camera offline")
        self.camera_on = on
        self.cam_switch.select() if on else self.cam_switch.deselect()
        if on:
            self._update_camera()

    def _update_camera(self):
        if not self.camera_on:
            return
        frame = get_camera().latest_frame()
        if frame is not None:
            import cv2

            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            w = max(self.cam_view.winfo_width() - 8, 200)
            h = int(w * img.height / img.width)
            self._camera_img = ctk.CTkImage(img, size=(w, h))
            self.cam_view.configure(image=self._camera_img, text="")
        self.after(50, self._update_camera)

    def _set_map_type(self, kind):
        if not self.map:
            return
        if kind == "satellite":
            self.map.set_tile_server("https://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}&s=Ga", max_zoom=22)
        else:
            self.map.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

    def show_map(self, lat, lon, label, zoom, map_type):
        if not self.map:
            return
        self._set_map_type(map_type)
        self.map_toggle.set(map_type if map_type in ("road", "satellite") else "road")
        self.map.delete_all_marker()
        self.map.set_position(lat, lon, marker=True, text=label)
        self.map.set_zoom(zoom)

    def show_image(self, path):
        try:
            img = Image.open(path)
        except OSError:
            return
        w = max(self.capture_view.winfo_width() - 8, 300)
        h = min(int(w * img.height / img.width), 260)
        self._capture_img = ctk.CTkImage(img, size=(int(h * img.width / img.height), h))
        self.capture_view.configure(image=self._capture_img, text="")

    def append_log(self, text, who):
        prefix = {"you": "YOU  › ", "jarvis": "JARVIS › ", "tool": "  ⚙ ", "system": "  ! "}.get(who, "")
        stamp = datetime.datetime.now().strftime("%H:%M ")
        self.log.configure(state="normal")
        self.log.insert("end", stamp + prefix + text + "\n", who)
        self.log.configure(state="disabled")
        self.log.see("end")

    def set_state(self, state):
        self.state_name = state
        self.status.configure(text=STATE_LABELS.get(state, state.upper()), text_color=STATE_COLORS.get(state, CYAN))

    # ================================================================ loops
    def _drain_bridge(self):
        while not bridge.queue.empty():
            kind, args = bridge.queue.get_nowait()
            if kind == "log":
                self.append_log(args[1], args[0])
            elif kind == "state":
                self.set_state(args[0])
            elif kind == "map":
                self.show_map(*args)
            elif kind == "image":
                self.show_image(args[0])
            elif kind == "camera":
                self._set_camera(args[0])
            elif kind == "clipboard":
                self.clipboard_clear()
                self.clipboard_append(args[0])
                self.update()
                args[1].set()
            elif kind == "confirm":
                message, done, result = args
                result["ok"] = messagebox.askyesno("J.A.R.V.I.S. — confirm action", message, parent=self)
                done.set()
        self.after(50, self._drain_bridge)

    def _update_stats(self):
        self.clock.configure(text=datetime.datetime.now().strftime("%H:%M:%S   %a %d %b %Y"))
        values = {
            "CPU": psutil.cpu_percent(),
            "MEMORY": psutil.virtual_memory().percent,
            "DISK": psutil.disk_usage("/").percent if config.OS_NAME != "Windows" else psutil.disk_usage("C:\\").percent,
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
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        cx, cy = w / 2, h / 2
        R = max(min(w, h) * 0.44, 40)
        t = self.tick
        self.tick += 1
        color = STATE_COLORS.get(self.state_name, CYAN)
        speed = {"idle": 0.6, "listening": 1.5, "thinking": 3.5, "speaking": 1.2}.get(self.state_name, 1)

        # outer tick ring
        for i in range(72):
            a = math.radians(i * 5 + t * 0.2 * speed)
            inner = R * (0.93 if i % 6 else 0.88)
            c.create_line(cx + inner * math.cos(a), cy + inner * math.sin(a),
                          cx + R * math.cos(a), cy + R * math.sin(a), fill=DIM, width=2)
        c.create_oval(cx - R * 1.02, cy - R * 1.02, cx + R * 1.02, cy + R * 1.02, outline=DIM, width=1)

        # rotating arc segments
        for i, (radius, extent, width) in enumerate([(0.80, 70, 4), (0.68, 110, 3), (0.56, 50, 6), (0.45, 140, 2)]):
            r = R * radius
            direction = 1 if i % 2 == 0 else -1
            for k in range(2 if i != 3 else 1):
                start = (t * speed * (i + 1) * direction + k * 180) % 360
                c.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=extent,
                             style="arc", outline=color, width=width)

        # pulsing core
        amp = 0.18 if self.state_name == "speaking" else 0.10 if self.state_name == "listening" else 0.04
        pulse = 1 + amp * math.sin(t / (3 if self.state_name == "speaking" else 8))
        core = R * 0.30 * pulse
        for j, shade in enumerate(("#0b3a4d", "#0f5d78", color)):
            rr = core * (1 - j * 0.28)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=color, width=2,
                          fill=shade if j < 2 else "")
        c.create_text(cx, cy, text="J.A.R.V.I.S", fill=TEXT, font=(FONT, max(int(R * 0.07), 9), "bold"))
        self.after(33, self._animate)
