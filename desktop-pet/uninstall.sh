#!/usr/bin/env bash
# Removes Pip the desktop pet completely.
# (You can also right-click Pip and choose "Remove and uninstall...")
pkill -f "python3? .*desktop-pet/pet\.py" 2>/dev/null || true
rm -rf "$HOME/.local/share/desktop-pet"
rm -f "$HOME/.local/share/applications/desktop-pet.desktop" \
      "$HOME/.config/autostart/desktop-pet.desktop" \
      "$HOME/.cache/desktop-pet.lock"
echo "Pip has been removed. Bye bye!"
