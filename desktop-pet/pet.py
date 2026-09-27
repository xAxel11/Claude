#!/usr/bin/env python3
"""Pip - a little desktop pet for Pop!_OS (and other Linux desktops).

Pip walks along the bottom of your screen, plays with your mouse cursor and
reacts to your system: CPU load, memory, battery, network and USB devices.

Drag Pip around and throw it, rub it with the cursor, or right-click it for
the menu (including "Remove pet").
"""

import fcntl
import math
import os
import random
import shutil
import signal
import sys
import time

# Wayland does not let apps position their own windows, so always run through
# XWayland (built into Pop!_OS COSMIC and GNOME). Without this, dragging and
# walking can't move the window. Set PET_QT_PLATFORM to override.
os.environ["QT_QPA_PLATFORM"] = os.environ.get("PET_QT_PLATFORM", "xcb")

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
PIVOT = GROUND - 40       # body centre, used for tilting and cursor maths
FPS = 30
GRAVITY = 1.1

BODY = QColor("#48b9c7")      # Pop!_OS teal
BODY_HOT = QColor("#f07b5b")
BODY_TIRED = QColor("#8fa9ad")
BODY_ANGRY = QColor("#e0524a")
CHEEK = QColor(250, 164, 26, 150)  # Pop!_OS orange
INK = QColor("#2b3a3d")


def blend(a, b, t):
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


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
        self.setMouseTracking(True)   # hover and rubbing without clicking
        self.setWindowTitle("Desktop Pet")
        self.resize(WIN_W, WIN_H)

        self.sys = SystemWatch()
        self.sys.cpu_percent()  # prime the CPU delta

        # Motion and behaviour
        area = self.area()
        self.px = float(random.randint(area.left(), max(area.left(), area.right() - WIN_W)))
        self.py = float(self.ground_y())
        self.vx = self.vy = 0.0
        self.air_max_speed = 0.0
        self.dir = random.choice((-1, 1))
        self.state = "walk"
        self.state_frames = FPS * 4
        self.moving = False
        self.phase = 0.0
        self.tilt = 0.0
        self.squash_frames = 0
        self.blink = 0
        self.blink_wait = FPS * 3
        self.stay_still = False
        self.follow = False
        self.forced_sleep = False
        self.quiet = False

        # Mouse
        self.drag_offset = None
        self.press_pos = None
        self.drag_samples = []  # recent (time, QPoint) for throwing
        self.drag_vx = 0.0
        self.clicks = []
        self.cursor = QCursor.pos()
        self.cursor_speed = 0.0
        self.cursor_still = 0   # frames since the cursor last moved
        self.hovering = False
        self.hover_frames = 0
        self.rub = 0.0
        self.last_hover_pos = None

        # Moods
        self.hot = 0.0          # 0..1, CPU heat
        self.tired = False      # low battery
        self.happy_frames = 0
        self.eating_frames = 0
        self.sad_frames = 0
        self.surprised_frames = 0
        self.angry_frames = 0
        self.dizzy_frames = 0
        self.dance_frames = 0
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
        QTimer.singleShot(5500, lambda: self.say("Drag me, throw me, or right-click me!", 4))

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

    def centre(self):
        """Pet's body centre in screen coordinates."""
        return QPointF(self.px + WIN_W / 2, self.py + PIVOT)

    def on_ground(self):
        return self.state not in ("drag", "air")

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
        self.angry_frames = 0
        for _ in range(5):
            self.particles.append(["heart", WIN_W / 2 + random.uniform(-25, 25),
                                   GROUND - 85, random.uniform(-0.6, 0.6),
                                   random.uniform(-1.8, -1.0), FPS * 2])
        if text:
            self.say(text, 3)

    def resting_state(self):
        return "chase" if self.follow else "idle"

    def wake(self):
        if self.state == "sleep":
            self.forced_sleep = False
            self.set_state(self.resting_state(), FPS * 2)
            self.surprised_frames = FPS // 2

    def set_state(self, state, frames=0):
        self.state = state
        self.state_frames = frames

    def launch(self, vx, vy):
        """Send Pip flying (jumps, throws, startles)."""
        self.vx, self.vy = vx, vy
        self.air_max_speed = 0.0
        self.set_state("air")

    def choose_next_state(self):
        hour = time.localtime().tm_hour
        sleepy = self.tired or hour >= 23 or hour < 5
        r = random.random()
        if self.follow:
            self.set_state("chase")
        elif self.stay_still:
            self.set_state("idle", FPS * random.randint(3, 8))
        elif self.cursor_nearby() and r < 0.4:
            self.set_state("approach", FPS * 6)
            if self.ready("curious", 60):
                self.say(random.choice(["Whatcha doing?", "Ooh, the cursor!",
                                        "Can I help?"]), 3)
        elif r < (0.25 if sleepy else 0.05):
            self.set_state("sleep", FPS * random.randint(8, 20))
        elif r < 0.12 and self.ready("dance", 120):
            self.start_dance()
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
                 "Pop!_OS is my favourite home.", "Try throwing me!"]
        hours = self.sys.uptime_hours()
        if hours > 4:
            lines.append(f"Computer's been on {int(hours)} hours. Stretch break?")
        if time.time() - self.start_time > 3600:
            lines.append("We've been hanging out for a while :)")
        return lines

    def start_dance(self):
        self.wake()
        self.dance_frames = FPS * 5
        self.set_state("dance", FPS * 5)
        self.say(random.choice(["Dance party!", "*wiggle wiggle*", "Music time!"]), 3)

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

    # ---------- cursor ----------

    def update_cursor(self):
        pos = QCursor.pos()
        moved = (pos - self.cursor).manhattanLength()
        self.cursor_speed = self.cursor_speed * 0.5 + moved * 0.5
        self.cursor_still = 0 if moved else self.cursor_still + 1
        self.cursor = pos

        self.rub *= 0.93
        if self.hovering:
            self.hover_frames += 1
            if self.hover_frames == FPS * 2 and self.state != "sleep" and self.ready("hover", 20):
                self.say(random.choice(["Hehe, hi there!", "*sniff sniff*", "Is that for me?"]), 2)

        if self.state in ("drag", "air", "sleep") or self.dizzy_frames:
            return
        c = self.centre()
        dist = math.hypot(pos.x() - c.x(), pos.y() - c.y())
        # Cursor zoomed past right next to Pip: jump in fright
        if dist < 100 and self.cursor_speed > 45 and self.hover_frames < FPS // 2 \
                and self.ready("startle", 6):
            self.surprised_frames = FPS
            self.launch(-math.copysign(3, pos.x() - c.x()), -10)
            self.say(random.choice(["Eek!", "Whoa!", "Careful!"]), 2)

    def cursor_nearby(self):
        """Cursor recently moved and is close to Pip's patch of floor."""
        c = self.centre()
        return (self.cursor_still < FPS * 3
                and abs(self.cursor.x() - c.x()) < 600
                and self.area().bottom() - self.cursor.y() < 320)

    def walk_toward_cursor(self, stop, max_speed):
        target = self.cursor.x() - WIN_W / 2
        dx = target - self.px
        self.moving = abs(dx) > stop
        if self.moving:
            self.dir = 1 if dx > 0 else -1
            speed = min(max_speed, abs(dx) - stop)
            self.px += self.dir * speed
            self.phase += 0.16 * speed
        return dx

    # ---------- animation loop ----------

    def tick(self):
        self.update_cursor()
        if self.state == "drag":
            self.drag_vx *= 0.8
        elif self.state == "air":
            self.step_air()
        else:
            self.step_ground()
        if self.state != "drag":
            self.move(int(self.px), int(self.py))

        self.update_tilt()
        self.spawn_ambient_particles()

        # Blinking
        if self.blink > 0:
            self.blink -= 1
        else:
            self.blink_wait -= 1
            if self.blink_wait <= 0:
                self.blink = 5
                self.blink_wait = random.randint(FPS * 2, FPS * 6)

        for name in ("happy_frames", "eating_frames", "sad_frames", "surprised_frames",
                     "bubble_frames", "angry_frames", "squash_frames", "dance_frames"):
            setattr(self, name, max(0, getattr(self, name) - 1))
        if self.dizzy_frames:
            self.dizzy_frames -= 1
            if self.dizzy_frames == 0 and random.random() < 0.5:
                self.say(random.choice(["Again! Again!", "That was fun!", "*shakes head*"]), 3)

        for p in self.particles:
            p[1] += p[3]
            p[2] += p[4]
            p[5] -= 1
        self.particles = [p for p in self.particles if p[5] > 0]

        self.update_mask()
        self.update()

    def step_ground(self):
        self.py = self.ground_y()
        lo, hi = self.x_limits()
        self.moving = False

        if self.dizzy_frames:
            pass  # too dizzy to go anywhere
        elif self.state == "walk":
            speed = 1.6 + 2.2 * self.hot - (0.8 if self.tired else 0)
            self.moving = True
            self.px += self.dir * speed
            self.phase += 0.16 * speed
            if self.px <= lo:
                self.px, self.dir = lo, 1
            elif self.px >= hi:
                self.px, self.dir = hi, -1
        elif self.state == "approach":
            self.walk_toward_cursor(stop=70, max_speed=2.4)
        elif self.state == "chase":
            dx = self.walk_toward_cursor(stop=6, max_speed=4.0)
            head_y = self.py + GROUND - 85
            height = head_y - self.cursor.y()
            if abs(dx) < 60 and 30 < height < 420 and self.ready("jump", 1.2):
                self.launch(clamp(dx / 12, -4, 4), -min(24, math.sqrt(2 * GRAVITY * height) + 2))
                if random.random() < 0.3:
                    self.say(random.choice(["Hup!", "Almost!", "Gonna get it!"]), 1.5)

        if self.state in ("walk", "idle", "approach", "dance", "sleep") \
                and not (self.state == "sleep" and self.forced_sleep):
            self.state_frames -= 1
            if self.state_frames <= 0:
                self.choose_next_state()
        self.px = clamp(self.px, lo, hi)

    def step_air(self):
        lo, hi = self.x_limits()
        ground = self.ground_y()
        top = self.area().top() - (GROUND - 110)
        self.vy += GRAVITY
        self.vx *= 0.995
        self.px += self.vx
        self.py += self.vy
        self.air_max_speed = max(self.air_max_speed, math.hypot(self.vx, self.vy))

        if self.px < lo or self.px > hi:
            self.px = clamp(self.px, lo, hi)
            self.vx = -self.vx * 0.6
            self.squash_frames = 5
            if abs(self.vx) > 6 and self.ready("wall", 2):
                self.say(random.choice(["Boing!", "Ouch, a wall!", "Bonk!"]), 1.5)
        if self.py < top:
            self.py, self.vy = top, abs(self.vy) * 0.5

        if self.follow and self.ready("catch", 5):
            c = self.centre()
            if math.hypot(self.cursor.x() - c.x(), self.cursor.y() - (c.y() - 45)) < 35:
                self.make_happy("Got it!")
            else:
                self.cooldowns["catch"] = 0

        if self.py >= ground:
            self.py = ground
            impact = self.vy
            self.squash_frames = 6
            self.dust()
            if impact > 12:
                self.vy = -impact * 0.4
                self.vx *= 0.7
            else:
                self.land()

    def land(self):
        self.vx = self.vy = 0.0
        speed = self.air_max_speed
        if speed > 30:
            self.dizzy_frames = FPS * 3
            self.say(random.choice(["Whoa... the room is spinning...", "@_@", "Wheee... ugh."]), 3)
        elif speed > 15 and random.random() < 0.6:
            self.say(random.choice(["Wheee!", "Safe landing!", "Ta-da!"]), 2)
        self.set_state(self.resting_state(), int(FPS * 1.5))

    def update_tilt(self):
        target = 0.0
        if self.state == "drag":
            target = clamp(-self.drag_vx * 2.5, -45, 45) + math.sin(time.time() * 6) * 4
        elif self.state == "air":
            if self.air_max_speed > 30:
                self.tilt += self.vx * 2.5   # spinning through the air
                return
            target = clamp(self.vx * 2, -25, 25)
        elif self.state == "dance":
            target = math.sin(self.dance_frames * 0.35) * 16
        elif self.dizzy_frames:
            target = math.sin(self.dizzy_frames * 0.3) * 10
        elif self.state not in ("sleep",):
            # Lean a little towards a nearby cursor
            c = self.centre()
            dx = self.cursor.x() - c.x()
            if abs(dx) < 250 and abs(self.cursor.y() - c.y()) < 250:
                target = clamp(dx / 30, -7, 7)
        # Unwind any spin the short way round, then ease towards the target
        self.tilt = (self.tilt + 180) % 360 - 180
        self.tilt += (target - self.tilt) * 0.25

    def spawn_ambient_particles(self):
        if self.state == "sleep" and random.random() < 1 / 40:
            self.particles.append(["z", WIN_W / 2 + 20, GROUND - 70, 0.4, -0.7, FPS * 2])
        if self.hot > 0.5 and random.random() < 1 / 25:
            self.particles.append(["drop", WIN_W / 2 + random.choice((-30, 30)),
                                   GROUND - 60, 0, 0.8, FPS])
        if self.angry_frames and random.random() < 1 / 6:
            self.particles.append(["puff", WIN_W / 2 + random.choice((-22, 22)),
                                   GROUND - 95, random.uniform(-0.3, 0.3), -1.2, FPS])
        if self.state == "dance" and random.random() < 1 / 10:
            self.particles.append(["note", WIN_W / 2 + random.uniform(-45, 45),
                                   GROUND - 80, random.uniform(-0.5, 0.5), -1.3, FPS * 2])

    def dust(self):
        for side in (-1, 1):
            for _ in range(3):
                self.particles.append(["dust", WIN_W / 2 + side * 25, GROUND - 4,
                                       side * random.uniform(0.8, 2.2),
                                       random.uniform(-0.8, -0.2), FPS // 2])

    # ---------- input ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_offset = e.globalPos() - QPoint(int(self.px), int(self.py))
            self.press_pos = e.globalPos()
            self.drag_samples = [(time.time(), e.globalPos())]
            self.drag_vx = 0.0
        elif e.button() == Qt.MiddleButton:
            self.start_dance()

    def mouseMoveEvent(self, e):
        if self.drag_offset is None:
            # Hovering: moving the cursor back and forth over Pip is a rub
            if self.last_hover_pos is not None:
                self.rub += (e.globalPos() - self.last_hover_pos).manhattanLength()
            self.last_hover_pos = e.globalPos()
            if self.rub > 500 and self.ready("rub", 3):
                self.rub = 0
                if self.state == "sleep":
                    self.make_happy()
                    self.say("*purrs in sleep*", 2)
                else:
                    self.make_happy(random.choice(["Purrr...", "Ooh, that's the spot!",
                                                   "More scratches!", "Hehe, that tickles!"]))
            return

        pos = e.globalPos()
        if self.state != "drag" and (pos - self.press_pos).manhattanLength() > 4:
            was_sleeping = self.state == "sleep"
            self.forced_sleep = False
            self.set_state("drag")
            self.surprised_frames = FPS // 2
            if was_sleeping:
                self.say("Huh?! Where are we going?", 2)
            elif self.ready("pickup", 8):
                self.say(random.choice(["Wheee!", "Up we go!", "Put me down!", "I can fly!"]), 2)
        if self.state == "drag":
            now = time.time()
            self.drag_samples = [(t, p) for t, p in self.drag_samples if now - t < 0.12]
            self.drag_samples.append((now, pos))
            prev = self.drag_samples[0][1]
            self.drag_vx = self.drag_vx * 0.5 + (pos.x() - prev.x()) / max(1, len(self.drag_samples)) * 0.5
            top_left = pos - self.drag_offset
            self.px, self.py = float(top_left.x()), float(top_left.y())
            self.move(top_left)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.drag_offset is None:
            return
        self.drag_offset = None
        if self.state == "drag":
            # Throw with the speed the mouse was moving at
            (t0, p0), (t1, p1) = self.drag_samples[0], self.drag_samples[-1]
            dt = max(t1 - t0, 1 / FPS)
            vx = (p1.x() - p0.x()) / dt / FPS
            vy = (p1.y() - p0.y()) / dt / FPS
            if time.time() - t1 > 0.08:   # mouse had stopped before letting go
                vx = vy = 0.0
            self.launch(clamp(vx, -45, 45), clamp(vy, -45, 45))
        else:
            self.poke()

    def poke(self):
        now = time.time()
        self.clicks = [t for t in self.clicks if now - t < 3] + [now]
        self.wake()
        if len(self.clicks) >= 5:
            self.clicks = []
            self.angry_frames = FPS * 4
            self.happy_frames = 0
            self.say(random.choice(["Hey! Stop poking me!", "Grrr!", "That's enough!"]), 3)
        elif self.angry_frames:
            self.say("Hmph!", 1.5)
        else:
            if self.on_ground():
                self.launch(0, -7)   # little hop
            self.make_happy(random.choice(["Hehe!", "That tickles!", "Hi!", "Boop!"]))

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.make_happy("I love you too!")

    def enterEvent(self, _):
        self.hovering = True
        self.hover_frames = 0
        self.last_hover_pos = None

    def leaveEvent(self, _):
        self.hovering = False
        self.hover_frames = 0
        self.last_hover_pos = None

    def contextMenuEvent(self, e):
        menu = QMenu(self)

        pet = menu.addAction("Pet Pip")
        pet.triggered.connect(lambda: self.make_happy("Purrr..."))
        feed = menu.addAction("Give a snack")
        feed.triggered.connect(self.feed)
        dance = menu.addAction("Dance!")
        dance.triggered.connect(self.start_dance)

        sleep = menu.addAction("Wake up" if self.state == "sleep" else "Go to sleep")
        sleep.triggered.connect(self.toggle_sleep)

        follow = menu.addAction("Follow my cursor")
        follow.setCheckable(True)
        follow.setChecked(self.follow)
        follow.toggled.connect(self.set_follow)

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
            self.follow = False
            self.forced_sleep = True
            self.set_state("sleep", FPS * 60)
            self.say("Good night...", 2)

    def set_follow(self, on):
        self.follow = on
        if on:
            self.stay_still = False
            self.wake()
            if self.on_ground():
                self.set_state("chase")
            self.say("Wait for me!", 2)
        elif self.state == "chase":
            self.set_state("idle", FPS * 2)

    def set_stay_still(self, on):
        self.stay_still = on
        if on:
            self.follow = False
            if self.state in ("walk", "chase", "approach"):
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
        top = GROUND - 112 - h
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
        wide = self.state in ("drag", "air", "dance") or abs(self.tilt) > 4 or bool(self.dizzy_frames)
        key = (bubble.getRect() if bubble else None, wide)
        if key == self._mask_key:
            return
        self._mask_key = key
        if wide:
            region = QRegion(QRect(WIN_W // 2 - 85, GROUND - 125, 170, 131))
        else:
            region = QRegion(QRect(WIN_W // 2 - 62, GROUND - 108, 124, 114))
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
        airborne = self.state == "air"
        step = math.sin(self.phase) if self.moving else 0.0
        bob = abs(step) * 3 if self.moving else (math.sin(time.time() * 2) * 1.2 if sleeping else 0)
        if self.state == "dance":
            bob = abs(math.sin(self.dance_frames * 0.35)) * 10
        elif self.state == "idle" and self.happy_frames:
            bob = abs(math.sin(self.happy_frames * 0.4)) * 8  # happy hops

        body_w, body_h = 76.0, 62.0
        if self.squash_frames:
            body_w, body_h = 86.0, 52.0
        elif airborne and self.vy < -4:
            body_w, body_h = 70.0, 68.0   # stretch while rising
        body_cy = GROUND - 12 - body_h / 2 - bob

        # Shadow stays flat on the floor (only when on the floor)
        if self.on_ground():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 45))
            p.drawEllipse(QPointF(cx, GROUND - 2), 32 - bob, 5)

        p.save()
        p.translate(cx, PIVOT)
        p.rotate(self.tilt)
        p.translate(-cx, -PIVOT)

        color = BODY_TIRED if self.tired else BODY
        color = blend(color, BODY_HOT, self.hot)
        if self.angry_frames:
            color = blend(color, BODY_ANGRY, 0.85)
        outline = color.darker(170)

        # Feet: kick while dangling, walk on the ground
        p.setPen(QPen(outline, 2.5))
        p.setBrush(color.darker(115))
        if dragged or airborne:
            kick = math.sin(time.time() * 14) * 4 if dragged else 0
            p.drawEllipse(QPointF(cx - 16, body_cy + body_h / 2 + 8 + kick), 8, 7)
            p.drawEllipse(QPointF(cx + 16, body_cy + body_h / 2 + 8 - kick), 8, 7)
        elif not sleeping:
            p.drawEllipse(QPointF(cx - 16 + step * 7, GROUND - 7 - max(0, step) * 4), 11, 7)
            p.drawEllipse(QPointF(cx + 16 - step * 7, GROUND - 7 - max(0, -step) * 4), 11, 7)

        # Ears perk up when the cursor is close, droop when asleep or sad
        c = self.centre()
        near = math.hypot(self.cursor.x() - c.x(), self.cursor.y() - c.y()) < 160
        perk = -5 if (near or self.hovering) and not sleeping else 0
        droop = 6 if sleeping or self.sad_frames or self.tired else 0
        tilt = 3 * self.dir if self.moving else 0
        for side in (-1, 1):
            base_x = cx + side * 22
            top = body_cy - body_h / 2
            ear = QPainterPath()
            ear.moveTo(base_x - 13, top + 12)
            ear.lineTo(base_x + side * (6 + droop) + tilt, top - 18 + droop + perk)
            ear.lineTo(base_x + 13, top + 8)
            ear.closeSubpath()
            p.setBrush(color)
            p.drawPath(ear)
            inner = QPainterPath()
            inner.moveTo(base_x - 6, top + 8)
            inner.lineTo(base_x + side * (4 + droop) + tilt, top - 9 + droop + perk)
            inner.lineTo(base_x + 6, top + 6)
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

        # Cheeks (blush when happy or being hovered)
        blush = self.happy_frames or self.hovering
        p.setBrush(QColor(255, 120, 120, 170) if blush else CHEEK)
        p.drawEllipse(QPointF(cx - 24, body_cy + 4), 7, 4.5)
        p.drawEllipse(QPointF(cx + 24, body_cy + 4), 7, 4.5)

        self.draw_face(p, cx, body_cy, sleeping, dragged or airborne)
        if self.dizzy_frames:
            self.draw_stars(p, cx, body_cy - body_h / 2 - 8)
        p.restore()

    def draw_face(self, p, cx, cy, sleeping, flying):
        eye_y = cy - 5
        pen = QPen(INK, 2.5, Qt.SolidLine, Qt.RoundCap)
        closed = sleeping or self.blink > 0 or self.tired and self.blink_wait % 90 < 30

        if self.dizzy_frames:
            # Spiral eyes
            p.setPen(QPen(INK, 2))
            p.setBrush(Qt.NoBrush)
            spin = self.dizzy_frames * 0.4
            for ex in (-13, 13):
                path = QPainterPath()
                for i in range(40):
                    a = spin + i * 0.35
                    r = 0.2 * i
                    pt = QPointF(cx + ex + math.cos(a) * r, eye_y + math.sin(a) * r)
                    path.moveTo(pt) if i == 0 else path.lineTo(pt)
                p.drawPath(path)
        elif self.happy_frames and not sleeping and not self.angry_frames:
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
            c = self.centre()
            dx, dy = self.cursor.x() - c.x(), self.cursor.y() - c.y()
            dist = math.hypot(dx, dy)
            if 0 < dist < 450:
                look = QPointF(dx / dist * 3, dy / dist * 3)
            size = 8.5 if self.surprised_frames or flying else 7
            for ex in (-13, 13):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("white"))
                p.drawEllipse(QPointF(cx + ex, eye_y), size, size + 1)
                p.setBrush(INK)
                p.drawEllipse(QPointF(cx + ex + look.x(), eye_y + look.y()), 4, 4.5)
                p.setBrush(QColor("white"))
                p.drawEllipse(QPointF(cx + ex + look.x() + 1.5, eye_y + look.y() - 1.8), 1.4, 1.4)

        if self.angry_frames:
            # Cross eyebrows
            p.setPen(QPen(INK, 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 21, eye_y - 12), QPointF(cx - 7, eye_y - 7))
            p.drawLine(QPointF(cx + 21, eye_y - 12), QPointF(cx + 7, eye_y - 7))

        # Mouth
        mouth_y = cy + 8
        p.setPen(QPen(INK, 2.2, Qt.SolidLine, Qt.RoundCap))
        if self.eating_frames:
            chew = 2 + abs(math.sin(self.eating_frames * 0.6)) * 3
            p.setBrush(QColor("#7a2e2e"))
            p.drawEllipse(QPointF(cx, mouth_y + 1), 4, chew)
        elif self.angry_frames:
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 6, mouth_y + 3)
            path.quadTo(cx, mouth_y - 2, cx + 6, mouth_y + 3)
            p.drawPath(path)
        elif self.dizzy_frames:
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 7, mouth_y + 2)
            for i in range(1, 5):
                path.lineTo(cx - 7 + i * 3.5, mouth_y + (0 if i % 2 else 3))
            p.drawPath(path)
        elif self.surprised_frames or flying:
            p.setBrush(QColor("#7a2e2e"))
            p.drawEllipse(QPointF(cx, mouth_y + 2), 3.5, 4.5)
        elif sleeping:
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(cx - 3, mouth_y + 1), QPointF(cx + 3, mouth_y + 1))
        else:
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            sad = self.sad_frames > 0 or self.tired
            curve = -4 if sad else (6 if self.happy_frames or self.hovering else 4)
            path.moveTo(cx - 6, mouth_y + (3 if sad else 0))
            path.quadTo(cx, mouth_y + curve, cx + 6, mouth_y + (3 if sad else 0))
            p.drawPath(path)

    def draw_stars(self, p, cx, y):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffd23f"))
        t = time.time() * 5
        for i in range(3):
            a = t + i * 2 * math.pi / 3
            sx, sy = cx + math.cos(a) * 30, y + math.sin(a) * 7
            path = QPainterPath()
            for k in range(10):
                r = 6 if k % 2 == 0 else 2.5
                ang = -math.pi / 2 + k * math.pi / 5
                pt = QPointF(sx + math.cos(ang) * r, sy + math.sin(ang) * r)
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            path.closeSubpath()
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
            elif kind == "puff":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(150, 150, 150, alpha // 2))
                r = 3 + (FPS - life) / 5
                p.drawEllipse(QPointF(x, y), r, r)
            elif kind == "dust":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(160, 150, 140, alpha // 2))
                p.drawEllipse(QPointF(x, y), 3, 3)
            elif kind == "note":
                p.setPen(QPen(QColor(90, 70, 160, alpha), 2))
                p.setBrush(QColor(90, 70, 160, alpha))
                p.drawEllipse(QPointF(x, y), 3.5, 2.8)
                p.drawLine(QPointF(x + 3.2, y), QPointF(x + 3.2, y - 11))
                p.drawLine(QPointF(x + 3.2, y - 11), QPointF(x + 8, y - 8))

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
