#!/usr/bin/env bash
# Installs Pip the desktop pet for the current user (no system files touched
# except the PyQt5 package if it's missing).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/desktop-pet"
LAUNCHER="$HOME/.local/share/applications/desktop-pet.desktop"
AUTOSTART="$HOME/.config/autostart/desktop-pet.desktop"

if ! python3 -c "import PyQt5" 2>/dev/null; then
    echo "Pip needs PyQt5. Installing it (you may be asked for your password)..."
    sudo apt install -y python3-pyqt5
fi

mkdir -p "$DEST" "$(dirname "$LAUNCHER")"
cp "$SRC/pet.py" "$SRC/icon.svg" "$SRC/uninstall.sh" "$DEST/"
chmod +x "$DEST/pet.py" "$DEST/uninstall.sh"

write_entry() {
    cat > "$1" <<ENTRY
[Desktop Entry]
Type=Application
Name=Desktop Pet
Comment=A little creature that walks on your screen
Exec=/usr/bin/env python3 "$DEST/pet.py"
Icon=$DEST/icon.svg
Terminal=false
Categories=Amusement;
X-GNOME-Autostart-enabled=true
ENTRY
}
write_entry "$LAUNCHER"
echo "Added 'Desktop Pet' to your app launcher."

read -r -p "Start Pip automatically when you log in? [y/N] " answer || answer=n
if [[ "$answer" =~ ^[Yy] ]]; then
    mkdir -p "$(dirname "$AUTOSTART")"
    write_entry "$AUTOSTART"
    echo "Pip will start at login (change it any time from Pip's right-click menu)."
fi

nohup python3 "$DEST/pet.py" >/dev/null 2>&1 &
echo "Pip is running! Right-click it for options, including 'Remove pet'."
