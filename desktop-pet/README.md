# Pip, the desktop pet

A little creature that walks along the bottom of your screen and reacts to what
your computer is doing. Made for Pop!_OS; it also works on Ubuntu and most other
Linux desktops.

## Install

```bash
unzip desktop-pet.zip
cd desktop-pet
./install.sh
```

The installer adds PyQt5 if it's missing (`sudo apt install python3-pyqt5`),
puts Pip in your app launcher and asks whether Pip should start when you log in.

To try Pip without installing, run `python3 pet.py`.

## Playing with Pip

| Do this | And Pip... |
|---|---|
| Click | giggles |
| Double-click | shows hearts |
| Drag and drop | dangles, then falls back down |
| Move your mouse near | watches your cursor |
| **Right-click** | opens the menu: pet, snack, sleep, stay still, quiet mode, start at login, computer status, **Remove pet** |

## What Pip notices

- **CPU above 85%**: turns red, sweats and runs faster
- **RAM above 90%**: complains that its head is full
- **Battery at 15% or less**: turns grey and sleepy. Plug in the charger and Pip cheers.
- **Internet drops or comes back**: gets sad, then celebrates
- **USB device plugged in or out**: "Ooh, a new gadget!"
- **Late at night**: yawns, sleeps more and tells you to go to bed

## Removing Pip

- **Right-click, then Remove pet**: closes Pip for now.
- **Right-click, then Remove and uninstall...**: closes Pip and deletes everything it installed.
- Or run `~/.local/share/desktop-pet/uninstall.sh`.

Pip only writes files in your home folder (`~/.local/share/desktop-pet`, a
launcher entry, and an autostart entry if you asked for one).

## Note on Wayland

Pop!_OS 24.04 (COSMIC) uses Wayland, and Wayland doesn't let apps move their own
windows. Pip therefore runs through XWayland, which is built in. If Pip doesn't
stay on top of other windows, that's the desktop's choice. In COSMIC you can
right-click Pip's window in the workspace overview and set it to always stay on top.
