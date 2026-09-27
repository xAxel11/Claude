#!/usr/bin/env python3
"""Pip - a little desktop pet for Pop!_OS (and other Linux desktops).

Pip walks along the bottom of your screen and reacts to your system:
CPU load, memory, battery, network and plugged-in USB devices.

Right-click Pip for the menu (including "Remove pet").
"""

import fcntl
import math
import os
import random
import shutil
import signal
import sys
import time

# Wayland does not let apps position their own windows, so run through
# XWayland (available on Pop!_OS COSMIC and GNOME) to be able to walk around.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

try:
    from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
    from PyQt5.QtGui import (QColor, QCursor, QFont, QFontMetrics,
                             QPainter, QPainterPath, QPen, QRegion)
    from PyQt5.QtWidgets import (QApplication, QMenu, QMessageBox,
                                 QWidget)
except ImportError:
    sys.stderr.write("PyQt5 is missing. Install it with:\n"
                     "  sudo apt install python3-pyqt5\n")
    sys.exit(1)

APP_ID = "desktop-pet"
INSTALL_DIR = os.path.expanduser("~/.local/share/desktop-pet")
LAUNCHER_FILE = os.path.expanduser("~/.local/share/applications/desktop-pet.desktop")
AUTOSTART_FILE = os.path.expanduser("~/.config/autostart/desktop-pet.desktop")
LOCK_FILE = os.path.expanduser("~/.cache/desktop-pet.lock")

WIN_W, WIN_H = 240, 190   # window size; pet sits at the bottom centre
GROUND = WIN_H - 6        # y of the pet's feet inside the window
FPS = 30

BODY = QColor("#48b9c7")      # Pop!_OS teal
BODY_HOT = QColor("#f07b5b")
BODY_TIRED = QColor("#8fa9ad")
CHEEK = QColor(250, 164, 26, 150)  # Pop!_OS orange
INK = QColor("#2b3a3d")


def blend(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


class SystemWatch:
    """Reads system state straight from /proc and /sys (no extra packages)."""

    def __init__(self):
        self._prev_cpu = None

    def cpu_percent(self):
        line = read("/proc/stat")
        if not line:
            return 0.0
        vals = [int(v) for v in line.splitlines()[0].split()[1:]]
        idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
        prev, self._prev_cpu = self._prev_cpu, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))

    @staticmethod
    def mem_percent():
        info = {}
        for line in (read("/proc/meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) if rest.split() else 0
        if not info.get("MemTotal"):
            return 0.0
        return 100.0 * (1 - info.get("MemAvailable", 0) / info["MemTotal"])

    @staticmethod
    def battery():
        """Returns (percent, status) or None when there is no battery."""
        base = "/sys/class/power_supply"
        try:
            names = os.listdir(base)
        except OSError:
            return None
        for name in names:
            if read(f"{base}/{name}/type") == "Battery":
                cap = read(f"{base}/{name}/capacity")
                if cap and cap.isdigit():
                    return int(cap), read(f"{base}/{name}/status") or "Unknown"
        return None

    @staticmethod
    def online():
        """True/False for network link, None if it can't be told."""
        try:
            ifaces = [i for i in os.listdir("/sys/class/net") if i != "lo"]
        except OSError:
            return None
        if not ifaces:
            return None
        return any(read(f"/sys/class/net/{i}/operstate") == "up" for i in ifaces)

    @staticmethod
    def usb_count():
        try:
            return len([d for d in os.listdir("/sys/bus/usb/devices") if ":" not in d])
        except OSError:
            return None

    @staticmethod
    def uptime_hours():
        up = read("/proc/uptime")
        return float(up.split()[0]) / 3600 if up else 0.0


class Pet(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Desktop Pet")
        self.resize(WIN_W, WIN_H)

        self.sys = SystemWatch()
        self.sys.cpu_percent()  # prime the CPU delta

        # Motion and behaviour
        area = self.area()
        self.px = float(random.randint(area.left(), max(area.left(), area.right() - WIN_W)))
        self.py = float(self.ground_y())
        self.vy = 0.0
        self.dir = random.choice((-1, 1))
        self.state = "walk"
        self.state_frames = FPS * 4
        self.phase = 0.0
        self.blink = 0
        self.blink_wait = FPS * 3
        self.drag_offset = None
        self.press_pos = None
        self.stay_still = False
        self.forced_sleep = False
        self.quiet = False

        # Moods driven by the system
        self.hot = 0.0          # 0..1, CPU heat
        self.tired = False      # low battery
        self.happy_frames = 0
        self.eating_frames = 0
        self.sad_frames = 0
        self.surprised_frames = 0
        self.cpu_high_count = 0

        # Speech bubble and particles
        self.bubble_text = ""
        self.bubble_frames = 0
        self.particles = []     # [kind, x, y, vx, vy, life]
        self._mask_key = None

        # Things we compare against to notice changes
        self.last_battery = self.sys.battery()
        self.last_online = self.sys.online()
        self.last_usb = self.sys.usb_count()
        self.cooldowns = {}
        self.start_time = time.time()

        self.move(int(self.px), int(self.py))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000 // FPS)

        self.sys_timer = QTimer(self)
        self.sys_timer.timeout.connect(self.check_system)
        self.sys_timer.start(3000)

        QTimer.singleShot(800, lambda: self.say(random.choice(
            ["Hi! I'm Pip.", "Hello there!", "Pip reporting for duty!"]), 4))
        QTimer.singleShot(5500, lambda: self.say("Right-click me for options.", 4))

    # ---------- geometry ----------

    def area(self):
        centre = QPoint(int(getattr(self, "px", 0)) + WIN_W // 2,
                        int(getattr(self, "py", 0)) + GROUND)
        screen = QApplication.screenAt(centre) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def ground_y(self):
        return self.area().bottom() - GROUND + 1

    def x_limits(self):
        a = self.area()
        return a.left() - (WIN_W // 2 - 42), a.right() - WIN_W // 2 - 42

    # ---------- talking and moods ----------

    def say(self, text, seconds=4, force=False):
        if self.quiet and not force:
            return
        self.bubble_text = text
        self.bubble_frames = int(seconds * FPS)

    def ready(self, key, seconds):
        """Rate-limits reactions so Pip doesn't nag."""
        now = time.time()
        if now - self.cooldowns.get(key, 0) < seconds:
            return False
        self.cooldowns[key] = now
        return True

    def make_happy(self, text=None):
        self.happy_frames = FPS * 3
        for _ in range(5):
            self.particles.append(["heart", WIN_W / 2 + random.uniform(-25, 25),
                                   GROUND - 85, random.uniform(-0.6, 0.6),
                                   random.uniform(-1.8, -1.0), FPS * 2])
        if text:
            self.say(text, 3)

    def wake(self):
        if self.state == "sleep":
            self.forced_sleep = False
            self.set_state("idle", FPS * 2)
            self.surprised_frames = FPS // 2

    def set_state(self, state, frames):
        self.state = state
        self.state_frames = frames

    def choose_next_state(self):
        hour = time.localtime().tm_hour
        sleepy = self.tired or hour >= 23 or hour < 5
        r = random.random()
        if self.stay_still:
            self.set_state("idle", FPS * random.randint(3, 8))
        elif r < (0.25 if sleepy else 0.06):
            self.set_state("sleep", FPS * random.randint(8, 20))
        elif r < 0.62:
            if random.random() < 0.35:
                self.dir *= -1
            self.set_state("walk", FPS * random.randint(3, 10))
        else:
            self.set_state("idle", FPS * random.randint(2, 6))
            if random.random() < 0.15 and self.ready("chatter", 90):
                self.say(random.choice(self.chatter()), 4)

    def chatter(self):
        lines = ["La la la...", "Nice desktop!", "What are we building today?",
                 "Remember to drink some water.", "*looks around*",
                 "Pop!_OS is my favourite home."]
        hours = self.sys.uptime_hours()
        if hours > 4:
            lines.append(f"Computer's been on {int(hours)} hours. Stretch break?")
        if time.time() - self.start_time > 3600:
            lines.append("We've been hanging out for a while :)")
        return lines

    # ---------- system reactions ----------

    def check_system(self):
        cpu = self.sys.cpu_percent()
        if cpu > 85:
            self.cpu_high_count += 1
            if self.cpu_high_count >= 2:
                if self.hot < 0.5 and self.ready("cpu", 120):
                    self.wake()
                    self.say(f"Phew! CPU at {int(cpu)}%. It's hot in here!", 4)
                self.hot = 1.0
        else:
            self.cpu_high_count = 0
            if cpu < 60:
                self.hot = max(0.0, self.hot - 0.34)

        mem = self.sys.mem_percent()
        if mem > 90 and self.ready("mem", 300):
            self.surprised_frames = FPS
            self.say(f"My head is full! RAM at {int(mem)}%.", 4)

        bat = self.sys.battery()
        if bat:
            pct, status = bat
            prev_status = self.last_battery[1] if self.last_battery else None
            if status in ("Charging", "Full") and prev_status == "Discharging":
                self.wake()
                self.tired = False
                self.make_happy("Mmm, power! Thank you!")
            elif status == "Discharging" and prev_status in ("Charging", "Full", "Not charging"):
                self.say("Unplugged! Adventure mode.", 3)
            self.tired = status == "Discharging" and pct <= 15
            if self.tired and self.ready("battery", 300):
                self.say(f"Battery at {pct}%... so sleepy...", 4)
        self.last_battery = bat

        online = self.sys.online()
        if self.last_online is True and online is False:
            self.wake()
            self.sad_frames = FPS * 6
            self.say("Uh oh, the internet is gone!", 4)
        elif self.last_online is False and online is True:
            self.make_happy("Yay, we're back online!")
        self.last_online = online

        usb = self.sys.usb_count()
        if usb is not None and self.last_usb is not None:
            if usb > self.last_usb:
                self.wake()
                self.surprised_frames = FPS
                self.say("Ooh, a new gadget!", 3)
            elif usb < self.last_usb:
                self.say("Bye bye, gadget!", 3)
        self.last_usb = usb

        hour = time.localtime().tm_hour
        if (hour >= 23 or hour < 5) and self.state != "sleep" and self.ready("late", 1800):
            self.say("It's late... you should sleep too.", 4)

    # ---------- animation loop ----------

    def tick(self):
        if self.drag_offset is None:
            ground = self.ground_y()
            lo, hi = self.x_limits()
            if self.state == "fall":
                self.vy += 1.3
                self.py += self.vy
                if self.py >= ground:
                    self.py = ground
                    if self.vy > 14:
                        self.surprised_frames = FPS // 2
                        self.say(random.choice(["Oof!", "Wheee... ouch.", "Safe landing!"]), 2)
                    self.vy = 0
                    self.set_state("idle", FPS * 2)
            else:
                self.py = ground
                if self.state == "walk":
                    speed = 1.6 + 2.2 * self.hot - (0.8 if self.tired else 0)
                    self.px += self.dir * speed
                    self.phase += 0.16 * speed
                    if self.px <= lo:
                        self.px, self.dir = lo, 1
                    elif self.px >= hi:
                        self.px, self.dir = hi, -1
                elif self.state == "sleep" and random.random() < 1 / 40:
                    self.particles.append(["z", WIN_W / 2 + 20, GROUND - 70, 0.4, -0.7, FPS * 2])
                if self.state != "sleep" or not self.forced_sleep:
                    self.state_frames -= 1
                    if self.state_frames <= 0:
                        self.choose_next_state()
            self.px = max(lo, min(hi, self.px))
            self.move(int(self.px), int(self.py))

        if self.hot > 0.5 and random.random() < 1 / 25:
            self.particles.append(["drop", WIN_W / 2 + random.choice((-30, 30)),
                                   GROUND - 60, 0, 0.8, FPS])

        # Blinking
        if self.blink > 0:
            self.blink -= 1
        else:
            self.blink_wait -= 1
            if self.blink_wait <= 0:
                self.blink = 5
                self.blink_wait = random.randint(FPS * 2, FPS * 6)

        for name in ("happy_frames", "eating_frames", "sad_frames",
                     "surprised_frames", "bubble_frames"):
            setattr(self, name, max(0, getattr(self, name) - 1))

        for p in self.particles:
            p[1] += p[3]
            p[2] += p[4]
            p[5] -= 1
        self.particles = [p for p in self.particles if p[5] > 0]

        self.update_mask()
        self.update()

    # ---------- input ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_offset = e.globalPos() - QPoint(int(self.px), int(self.py))
            self.press_pos = e.globalPos()
            self.wake()

    def mouseMoveEvent(self, e):
        if self.drag_offset is not None:
            pos = e.globalPos() - self.drag_offset
            if (e.globalPos() - self.press_pos).manhattanLength() > 4:
                self.state = "drag"
            self.px, self.py = float(pos.x()), float(pos.y())
            self.move(pos)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.drag_offset is not None:
            self.drag_offset = None
            if self.state == "drag":
                self.vy = 0
                self.set_state("fall", 0)
                self.px = max(self.x_limits()[0], min(self.x_limits()[1], self.px))
            else:
                self.make_happy(random.choice(["Hehe!", "That tickles!", "Hi!"]))

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.make_happy("I love you too!")

    def contextMenuEvent(self, e):
        menu = QMenu(self)

        pet = menu.addAction("Pet Pip")
        pet.triggered.connect(lambda: self.make_happy("Purrr..."))
        feed = menu.addAction("Give a snack")
        feed.triggered.connect(self.feed)

        sleep = menu.addAction("Wake up" if self.state == "sleep" else "Go to sleep")
        sleep.triggered.connect(self.toggle_sleep)

        still = menu.addAction("Stay still")
        still.setCheckable(True)
        still.setChecked(self.stay_still)
        still.toggled.connect(self.set_stay_still)

        quiet = menu.addAction("Quiet mode (no talking)")
        quiet.setCheckable(True)
        quiet.setChecked(self.quiet)
        quiet.toggled.connect(self.set_quiet)

        auto = menu.addAction("Start when I log in")
        auto.setCheckable(True)
        auto.setChecked(os.path.exists(AUTOSTART_FILE))
        auto.toggled.connect(self.set_autostart)

        status = menu.addAction("How's the computer?")
        status.triggered.connect(self.report_status)

        menu.addSeparator()
        remove = menu.addAction("Remove pet")
        remove.triggered.connect(self.remove_pet)
        uninstall = menu.addAction("Remove and uninstall...")
        uninstall.triggered.connect(self.uninstall)

        menu.exec_(e.globalPos())

    # ---------- menu actions ----------

    def feed(self):
        self.wake()
        self.eating_frames = FPS * 2
        self.make_happy(random.choice(["Nom nom nom!", "Yummy!", "Cookie!!"]))

    def toggle_sleep(self):
        if self.state == "sleep":
            self.wake()
            self.say("I'm up, I'm up!", 2)
        else:
            self.forced_sleep = True
            self.set_state("sleep", FPS * 60)
            self.say("Good night...", 2)

    def set_stay_still(self, on):
        self.stay_still = on
        if on and self.state == "walk":
            self.set_state("idle", FPS * 5)

    def set_quiet(self, on):
        self.quiet = on
        if on:
            self.bubble_frames = 0

    def set_autostart(self, on):
        if on:
            os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
            with open(AUTOSTART_FILE, "w") as f:
                f.write(desktop_entry())
            self.say("I'll see you next time you log in!", 3)
        elif os.path.exists(AUTOSTART_FILE):
            os.remove(AUTOSTART_FILE)
            self.say("OK, I won't start at login.", 3)

    def report_status(self):
        parts = [f"CPU {int(self.sys.cpu_percent())}%",
                 f"RAM {int(self.sys.mem_percent())}%"]
        bat = self.sys.battery()
        if bat:
            parts.append(f"Battery {bat[0]}% ({bat[1].lower()})")
        online = self.sys.online()
        if online is not None:
            parts.append("Online" if online else "Offline")
        self.say(" | ".join(parts), 6, force=True)

    def remove_pet(self):
        QApplication.quit()

    def uninstall(self):
        answer = QMessageBox.question(
            self, "Uninstall Desktop Pet",
            "Remove Pip and delete its launcher, autostart entry and installed files?")
        if answer != QMessageBox.Yes:
            return
        for path in (LAUNCHER_FILE, AUTOSTART_FILE, LOCK_FILE):
            if os.path.exists(path):
                os.remove(path)
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.realpath(here) == os.path.realpath(INSTALL_DIR):
            shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        QApplication.quit()

    # ---------- drawing ----------

    def bubble_rect(self, fm):
        text_rect = fm.boundingRect(QRect(0, 0, WIN_W - 30, 200),
                                    Qt.TextWordWrap | Qt.AlignCenter, self.bubble_text)
        w, h = text_rect.width() + 20, text_rect.height() + 12
        top = GROUND - 100 - h
        return QRect((WIN_W - w) // 2, max(0, top), w, h)

    def bubble_font(self):
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        return font

    def update_mask(self):
        # Only the pet (and its bubble) catch clicks; the rest is click-through.
        bubble = None
        if self.bubble_frames > 0:
            bubble = self.bubble_rect(QFontMetrics(self.bubble_font()))
        key = (bubble.getRect() if bubble else None)
        if key == self._mask_key:
            return
        self._mask_key = key
        region = QRegion(QRect(WIN_W // 2 - 62, GROUND - 105, 124, 111))
        if bubble:
            region = region.united(QRegion(bubble.adjusted(-2, -2, 2, 12)))
        self.setMask(region)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self.draw_pet(p)
        self.draw_particles(p)
        if self.bubble_frames > 0 and self.bubble_text:
            self.draw_bubble(p)
        p.end()

    def draw_pet(self, p):
        cx = WIN_W / 2
        sleeping = self.state == "sleep"
        dragged = self.state == "drag"
        walking = self.state == "walk"
        step = math.sin(self.phase) if walking else 0.0
        bob = abs(step) * 3 if walking else (math.sin(time.time() * 2) * 1.2 if sleeping else 0)
        if self.state == "idle" and self.happy_frames:
            bob = abs(math.sin(self.happy_frames * 0.4)) * 8  # happy hops

        body_w, body_h = 76.0, 62.0
        body_cy = GROUND - 12 - body_h / 2 - bob
        if dragged:
            body_cy += math.sin(time.time() * 10) * 2

        # Shadow
        if not dragged and self.state != "fall":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 45))
            p.drawEllipse(QPointF(cx, GROUND - 2), 32 - bob, 5)

        color = BODY
        if self.tired:
            color = BODY_TIRED
        color = blend(color, BODY_HOT, self.hot)
        outline = color.darker(170)

        # Feet (dangle when dragged)
        p.setPen(QPen(outline, 2.5))
        p.setBrush(color.darker(115))
        if dragged or self.state == "fall":
            for fx in (-16, 16):
                p.drawEllipse(QPointF(cx + fx, body_cy + body_h / 2 + 8), 8, 7)
        elif not sleeping:
            p.drawEllipse(QPointF(cx - 16 + step * 7, GROUND - 7 - max(0, step) * 4), 11, 7)
            p.drawEllipse(QPointF(cx + 16 - step * 7, GROUND - 7 - max(0, -step) * 4), 11, 7)

        # Ears
        tilt = 3 * self.dir if walking else 0
        for side in (-1, 1):
            ear = QPainterPath()
            base_x = cx + side * 22
            ear.moveTo(base_x - 13, body_cy - body_h / 2 + 12)
            ear.lineTo(base_x + side * 6 + tilt, body_cy - body_h / 2 - 18 + (6 if sleeping else 0))
            ear.lineTo(base_x + 13, body_cy - body_h / 2 + 8)
            ear.closeSubpath()
            p.setBrush(color)
            p.drawPath(ear)
            inner = QPainterPath()
            inner.moveTo(base_x - 6, body_cy - body_h / 2 + 8)
            inner.lineTo(base_x + side * 4 + tilt, body_cy - body_h / 2 - 9 + (6 if sleeping else 0))
            inner.lineTo(base_x + 6, body_cy - body_h / 2 + 6)
            inner.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(CHEEK)
            p.drawPath(inner)
            p.setPen(QPen(outline, 2.5))

        # Body
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, body_cy), body_w / 2, body_h / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 60))
        p.drawEllipse(QPointF(cx - 14, body_cy - 16), 14, 8)  # shine

        # Belly
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QPointF(cx, body_cy + 14), 20, 12)

        # Cheeks
        p.setBrush(QColor(255, 120, 120, 150) if self.happy_frames else CHEEK)
        p.drawEllipse(QPointF(cx - 24, body_cy + 4), 7, 4.5)
        p.drawEllipse(QPointF(cx + 24, body_cy + 4), 7, 4.5)

        self.draw_face(p, cx, body_cy, sleeping, dragged)

    def draw_face(self, p, cx, cy, sleeping, dragged):
        eye_y = cy - 5
        pen = QPen(INK, 2.5, Qt.SolidLine, Qt.RoundCap)
        closed = sleeping or self.blink > 0 or self.tired and self.blink_wait % 90 < 30

        if self.happy_frames and not sleeping:
            # ^ ^ eyes
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for ex in (-13, 13):
                path = QPainterPath()
                path.moveTo(cx + ex - 6, eye_y + 2)
                path.quadTo(cx + ex, eye_y - 7, cx + ex + 6, eye_y + 2)
                p.drawPath(path)
        elif closed:
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for ex in (-13, 13):
                path = QPainterPath()
                path.moveTo(cx + ex - 6, eye_y)
                path.quadTo(cx + ex, eye_y + 5, cx + ex + 6, eye_y)
                p.drawPath(path)
        else:
            # Look at the mouse when it's close, otherwise where we're walking
            look = QPointF(self.dir * 2.5, 0)
            cursor = QCursor.pos()
            gx, gy = self.px + cx, self.py + cy
            dx, dy = cursor.x() - gx, cursor.y() - gy
            dist = math.hypot(dx, dy)
            if 0 < dist < 350:
                look = QPointF(dx / dist * 3, dy / dist * 3)
            size = 8.5 if self.surprised_frames or dragged else 7
            for ex in (-13, 13):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("white"))
                p.drawEllipse(QPointF(cx + ex, eye_y), size, size + 1)
                p.setBrush(INK)
                p.drawEllipse(QPointF(cx + ex + look.x(), eye_y + look.y()), 4, 4.5)
                p.setBrush(QColor("white"))
                p.drawEllipse(QPointF(cx + ex + look.x() + 1.5, eye_y + look.y() - 1.8), 1.4, 1.4)

        # Mouth
        mouth_y = cy + 8
        p.setPen(QPen(INK, 2.2, Qt.SolidLine, Qt.RoundCap))
        if self.eating_frames:
            chew = 2 + abs(math.sin(self.eating_frames * 0.6)) * 3
            p.setBrush(QColor("#7a2e2e"))
            p.drawEllipse(QPointF(cx, mouth_y + 1), 4, chew)
        elif self.surprised_frames or dragged:
            p.setBrush(QColor("#7a2e2e"))
            p.drawEllipse(QPointF(cx, mouth_y + 2), 3.5, 4.5)
        elif sleeping:
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(cx - 3, mouth_y + 1), QPointF(cx + 3, mouth_y + 1))
        else:
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            sad = self.sad_frames > 0 or self.tired
            curve = -4 if sad else (6 if self.happy_frames else 4)
            path.moveTo(cx - 6, mouth_y + (3 if sad else 0))
            path.quadTo(cx, mouth_y + curve, cx + 6, mouth_y + (3 if sad else 0))
            p.drawPath(path)

    def draw_particles(self, p):
        for kind, x, y, _vx, _vy, life in self.particles:
            alpha = int(255 * min(1.0, life / 15))
            if kind == "heart":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(240, 80, 110, alpha))
                s = 5
                path = QPainterPath()
                path.moveTo(x, y + s)
                path.cubicTo(x - 2 * s, y - s / 2, x - s, y - 2 * s, x, y - s / 2)
                path.cubicTo(x + s, y - 2 * s, x + 2 * s, y - s / 2, x, y + s)
                p.drawPath(path)
            elif kind == "z":
                font = QFont()
                font.setBold(True)
                font.setPointSize(9 + int((FPS * 2 - life) / 12))
                p.setFont(font)
                p.setPen(QColor(80, 110, 140, alpha))
                p.drawText(QPointF(x, y), "z")
            elif kind == "drop":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(120, 190, 255, alpha))
                path = QPainterPath()
                path.moveTo(x, y - 5)
                path.quadTo(x + 4, y + 1, x, y + 3)
                path.quadTo(x - 4, y + 1, x, y - 5)
                p.drawPath(path)

    def draw_bubble(self, p):
        font = self.bubble_font()
        p.setFont(font)
        rect = self.bubble_rect(QFontMetrics(font))
        alpha = int(240 * min(1.0, self.bubble_frames / 8))
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 10, 10)
        tail = QPainterPath()
        tx = WIN_W / 2 + 8
        tail.moveTo(tx - 7, rect.bottom() - 1)
        tail.lineTo(tx + 2, rect.bottom() + 10)
        tail.lineTo(tx + 7, rect.bottom() - 1)
        path = path.united(tail)
        p.setPen(QPen(QColor(43, 58, 61, alpha), 2))
        p.setBrush(QColor(255, 255, 255, alpha))
        p.drawPath(path)
        p.setPen(QColor(43, 58, 61, alpha))
        p.drawText(rect.adjusted(10, 6, -10, -6), Qt.TextWordWrap | Qt.AlignCenter,
                   self.bubble_text)


def desktop_entry():
    script = os.path.abspath(__file__)
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Desktop Pet\n"
            "Comment=A little creature that walks on your screen\n"
            f"Exec={sys.executable} \"{script}\"\n"
            f"Icon={os.path.join(os.path.dirname(script), 'icon.svg')}\n"
            "Terminal=false\n"
            "Categories=Amusement;\n"
            "X-GNOME-Autostart-enabled=true\n")


def main():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Pip is already running.")
        return

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    pet = Pet()
    pet.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
