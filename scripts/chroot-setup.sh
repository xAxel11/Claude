#!/usr/bin/env bash
# Runs INSIDE the image chroot (called by build.sh). Installs and configures
# everything that ends up on the live ISO and on installed systems.
set -Eeuo pipefail

W=/tmp/horizon
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

log()  { printf '\e[1;32m  ->\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m  -> WARNING:\e[0m %s\n' "$*" >&2; }
read_list() { grep -v '^\s*#' "$1" | sed 's/#.*//' | awk 'NF{print $1}'; }

# Don't start services while building the image.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod +x /usr/sbin/policy-rc.d
trap 'rm -f /usr/sbin/policy-rc.d' EXIT

echo "$LIVE_HOSTNAME" > /etc/hostname
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost ip6-localhost ip6-loopback\n' "$LIVE_HOSTNAME" > /etc/hosts

# --- APT sources ----------------------------------------------------------------
log "Configuring APT sources"
rm -f /etc/apt/sources.list
cat > /etc/apt/sources.list.d/ubuntu.sources <<EOF
Types: deb
URIs: $UBUNTU_MIRROR
Suites: $UBUNTU_SUITE $UBUNTU_SUITE-updates $UBUNTU_SUITE-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu
Suites: $UBUNTU_SUITE-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

# Horizon is snap-free: block snapd so nothing can pull it back in.
cat > /etc/apt/preferences.d/no-snap.pref <<'EOF'
# Horizon OS ships Flatpak (Flathub) instead of Snap.
Package: snapd
Pin: release a=*
Pin-Priority: -10
EOF

apt-get update
"${APT[@]}" install systemd-sysv dbus ca-certificates curl gpg locales apt-utils
dbus-uuidgen > /etc/machine-id
ln -sf /etc/machine-id /var/lib/dbus/machine-id

# --- Locale ------------------------------------------------------------------------
log "Setting locale $DEFAULT_LOCALE"
locale-gen "$DEFAULT_LOCALE"
update-locale LANG="$DEFAULT_LOCALE"

# --- Packages ------------------------------------------------------------------------
log "Installing required packages"
mapfile -t required < <(read_list "$W/config/packages.list")
"${APT[@]}" install "${required[@]}"

log "Installing optional packages"
for pkg in $(read_list "$W/config/packages-optional.list"); do
    "${APT[@]}" install "$pkg" >/dev/null 2>&1 && log "  + $pkg" || warn "optional package '$pkg' not available, skipped"
done

log "Installing Firefox from Mozilla's APT repository (native .deb, not snap)"
install -d -m 0755 /etc/apt/keyrings
if curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg -o /etc/apt/keyrings/packages.mozilla.org.asc; then
    cat > /etc/apt/sources.list.d/mozilla.sources <<'EOF'
Types: deb
URIs: https://packages.mozilla.org/apt
Suites: mozilla
Components: main
Signed-By: /etc/apt/keyrings/packages.mozilla.org.asc
EOF
    cat > /etc/apt/preferences.d/mozilla.pref <<'EOF'
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
EOF
    apt-get update
    "${APT[@]}" install firefox || warn "Firefox install failed"
else
    warn "Could not reach packages.mozilla.org — Firefox not installed"
fi

log "Installing live-session packages"
mapfile -t live < <(read_list "$W/config/packages-live.list")
"${APT[@]}" install "${live[@]}"

# --- macOS-style themes ----------------------------------------------------------------
log "Installing WhiteSur themes (KDE, icons, cursors)"
(cd "$W/themes/WhiteSur-kde" && ./install.sh) || warn "WhiteSur-kde installer reported an error"
(cd "$W/themes/WhiteSur-icon-theme" && ./install.sh -a -b) \
    || (cd "$W/themes/WhiteSur-icon-theme" && ./install.sh) || warn "WhiteSur icon installer reported an error"
(cd "$W/themes/WhiteSur-cursors" && ./install.sh) || warn "WhiteSur cursor installer reported an error"

# --- Horizon packages ----------------------------------------------------------------------
log "Installing Horizon packages"
"${APT[@]}" install "$W"/debs/*.deb

log "Removing unwanted packages"
for pkg in $(read_list "$W/config/packages-remove.list"); do
    if dpkg -s "$pkg" >/dev/null 2>&1; then
        "${APT[@]}" purge "$pkg" || warn "could not remove $pkg"
    fi
done

# --- Offline bootloader payload for the installer ----------------------------------------------
# The live system can't have grub-pc and grub-efi-amd64 installed at the same
# time, so we keep the .debs around and the installer picks the right set.
log "Caching bootloader packages for offline installs"
for kind in efi bios; do
    rm -rf "/var/lib/horizon/bootloader/$kind"
    mkdir -p "/var/lib/horizon/bootloader/$kind"
done
(cd /var/lib/horizon/bootloader/efi  && apt-get download grub-efi-amd64 grub-efi-amd64-signed shim-signed)
(cd /var/lib/horizon/bootloader/bios && apt-get download grub-pc)
chown -R root:root /var/lib/horizon

# --- Flatpak ------------------------------------------------------------------------------
log "Enabling Flathub"
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo \
    || warn "Could not add Flathub (it can be added later from Discover)"

# --- Live session ---------------------------------------------------------------------------
log "Configuring live session (user: $LIVE_USER)"
cat > /etc/casper.conf <<EOF
# Live session settings used by casper at boot.
export USERNAME="$LIVE_USER"
export USERFULLNAME="Live session user"
export HOST="$LIVE_HOSTNAME"
export BUILD_SYSTEM="Ubuntu"
export FLAVOUR="$DISTRO_NAME"
EOF

# --- Theme sanity check ----------------------------------------------------------------------
log "Verifying theme names used by the default configuration"
check() { [[ -e "$1" ]] && log "  ok  $1" || warn "missing $1 — update packages/horizon-desktop/etc/xdg to match the installed theme names"; }
check /usr/share/color-schemes/WhiteSur.colors
check /usr/share/color-schemes/WhiteSurDark.colors
check /usr/share/plasma/desktoptheme/WhiteSur
check /usr/share/aurorae/themes/WhiteSur
check /usr/share/icons/WhiteSur
check /usr/share/icons/WhiteSur-cursors
check /usr/share/Kvantum/WhiteSur
echo "Installed theme names (for reference):"
ls /usr/share/color-schemes /usr/share/plasma/desktoptheme /usr/share/aurorae/themes /usr/share/Kvantum 2>/dev/null | sed 's/^/    /'

# --- Kernel / initramfs ---------------------------------------------------------------------------
log "Updating initramfs"
kver=$(ls -1 /lib/modules | sort -V | tail -n1)
ln -sf "vmlinuz-$kver"    /boot/vmlinuz
ln -sf "initrd.img-$kver" /boot/initrd.img
update-initramfs -c -k "$kver" 2>/dev/null || update-initramfs -u -k "$kver"

# --- Cleanup --------------------------------------------------------------------------------------
log "Cleaning up"
apt-get clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin /tmp/* /var/tmp/* /root/.bash_history
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id /etc/resolv.conf
ln -sf ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
find /var/log -type f -exec truncate -s 0 {} +
log "Chroot configuration complete"
