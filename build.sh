#!/usr/bin/env bash
# Horizon OS ISO builder.
#
#   sudo ./build.sh            # full build -> build/<distro>-<version>-amd64.iso
#   sudo ./build.sh <stage>    # run a single stage (see usage below)
#
# Stages run in order: deps -> bootstrap -> themes -> debs -> chroot -> iso
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
# shellcheck source=config/distro.conf
source "$ROOT_DIR/config/distro.conf"

CHROOT="$BUILD_DIR/chroot"
IMAGE="$BUILD_DIR/image"
STAGING="$BUILD_DIR/staging"
THEMES="$BUILD_DIR/themes"
DEBS="$BUILD_DIR/debs"
ISO_FILE="$BUILD_DIR/${DISTRO_ID}-${DISTRO_VERSION}-${ARCH}.iso"
VOLID="$(echo "${DISTRO_ID}_${DISTRO_VERSION}" | tr '[:lower:].' '[:upper:]_' | cut -c1-32)"

log()  { printf '\e[1;34m==>\e[0m \e[1m%s\e[0m\n' "$*"; }
warn() { printf '\e[1;33m==> WARNING:\e[0m %s\n' "$*" >&2; }
die()  { printf '\e[1;31m==> ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: sudo $0 [stage]

Stages:
  all        Run every stage below (default)
  deps       Install host build dependencies (Debian/Ubuntu hosts)
  bootstrap  Create the base Ubuntu ${UBUNTU_SUITE} system with debootstrap
  themes     Download the WhiteSur (macOS-style) themes
  debs       Build the horizon-desktop and horizon-installer packages
  chroot     Install and configure everything inside the image
  iso        Pack the squashfs and build a hybrid BIOS/UEFI (Secure Boot) ISO
  clean      Unmount everything and delete ${BUILD_DIR}
  shell      Open a root shell inside the image (for manual tweaking)
EOF
}

require_root() { [[ $EUID -eq 0 ]] || die "Run as root: sudo $0 $*"; }

# --- chroot mounts ---------------------------------------------------------

mount_chroot() {
    for d in dev dev/pts proc sys run; do mkdir -p "$CHROOT/$d"; done
    mountpoint -q "$CHROOT/dev"     || mount --bind /dev "$CHROOT/dev"
    mountpoint -q "$CHROOT/dev/pts" || mount --bind /dev/pts "$CHROOT/dev/pts"
    mountpoint -q "$CHROOT/proc"    || mount -t proc proc "$CHROOT/proc"
    mountpoint -q "$CHROOT/sys"     || mount -t sysfs sysfs "$CHROOT/sys"
    mountpoint -q "$CHROOT/run"     || mount -t tmpfs tmpfs "$CHROOT/run"
}

umount_chroot() {
    [[ -d "$CHROOT" ]] || return 0
    for d in run sys proc dev/pts dev; do
        mountpoint -q "$CHROOT/$d" && umount -lf "$CHROOT/$d" || true
    done
}
trap umount_chroot EXIT

in_chroot() { chroot "$CHROOT" /usr/bin/env -i \
    HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive "$@"; }

# --- stages ------------------------------------------------------------------

stage_deps() {
    log "Installing host dependencies"
    command -v apt-get >/dev/null || die "Automatic dependency install needs a Debian/Ubuntu host. Install manually: debootstrap squashfs-tools xorriso mtools dosfstools git librsvg2-bin imagemagick grub-common fontconfig fonts-inter dpkg-dev"
    apt-get update
    apt-get install -y debootstrap squashfs-tools xorriso mtools dosfstools \
        git librsvg2-bin imagemagick grub-common fontconfig fonts-inter dpkg-dev ca-certificates rsync
    # Older hosts don't know newer Ubuntu codenames yet; they all use the same script.
    if [[ ! -e /usr/share/debootstrap/scripts/$UBUNTU_SUITE ]]; then
        ln -s gutsy "/usr/share/debootstrap/scripts/$UBUNTU_SUITE"
    fi
}

stage_bootstrap() {
    if [[ -x "$CHROOT/usr/bin/apt-get" ]]; then
        log "Base system already exists, skipping debootstrap (run '$0 clean' to start over)"
        return
    fi
    log "Bootstrapping Ubuntu $UBUNTU_SUITE ($ARCH)"
    mkdir -p "$CHROOT"
    debootstrap --arch="$ARCH" --variant=minbase --components=main,restricted,universe,multiverse \
        "$UBUNTU_SUITE" "$CHROOT" "$UBUNTU_MIRROR"
}

clone_theme() { # <name> <repo> <ref>
    local dest="$THEMES/$1"
    if [[ -d "$dest/.git" ]]; then
        log "Theme $1 already downloaded"
        return
    fi
    log "Downloading $1 ($3)"
    git clone --depth 1 --branch "$3" "$2" "$dest" 2>/dev/null \
        || { git clone "$2" "$dest" && git -C "$dest" checkout "$3"; }
}

stage_themes() {
    mkdir -p "$THEMES"
    clone_theme WhiteSur-kde          https://github.com/vinceliuice/WhiteSur-kde.git          "$WHITESUR_KDE_REF"
    clone_theme WhiteSur-icon-theme   https://github.com/vinceliuice/WhiteSur-icon-theme.git   "$WHITESUR_ICONS_REF"
    clone_theme WhiteSur-cursors      https://github.com/vinceliuice/WhiteSur-cursors.git      "$WHITESUR_CURSORS_REF"
}

render_assets() { # renders SVG sources to PNGs inside a package tree
    local pkg="$1"
    local wp="$pkg/usr/share/wallpapers/Horizon/contents"
    mkdir -p "$wp/images" "$wp/images_dark"
    for size in 3840x2160 2560x1440 1920x1080 2560x1600; do
        rsvg-convert -w "${size%x*}" -h "${size#*x}" \
            "$ROOT_DIR/assets/wallpaper-light.svg" -o "$wp/images/$size.png"
        rsvg-convert -w "${size%x*}" -h "${size#*x}" \
            "$ROOT_DIR/assets/wallpaper-dark.svg"  -o "$wp/images_dark/$size.png"
    done
    rsvg-convert -w 512 -h 512 "$ROOT_DIR/assets/wallpaper-light.svg" -o "$pkg/usr/share/wallpapers/Horizon/contents/screenshot.png"

    local icons="$pkg/usr/share/icons/hicolor"
    mkdir -p "$icons/scalable/apps"
    cp "$ROOT_DIR/assets/logo.svg"          "$icons/scalable/apps/horizon-logo.svg"
    cp "$ROOT_DIR/assets/logo-symbolic.svg" "$icons/scalable/apps/horizon-logo-symbolic.svg"
    cp "$ROOT_DIR/assets/launchpad.svg"     "$icons/scalable/apps/horizon-launchpad.svg"
    for s in 16 22 24 32 48 64 128 256; do
        mkdir -p "$icons/${s}x${s}/apps"
        rsvg-convert -w "$s" -h "$s" "$ROOT_DIR/assets/logo.svg" -o "$icons/${s}x${s}/apps/horizon-logo.png"
    done

    # First-login layout = default "macOS" layout + default wallpaper.
    local lnf="$pkg/usr/share/plasma/look-and-feel/org.horizon.desktop/contents"
    mkdir -p "$lnf/layouts" "$lnf/previews"
    cat "$pkg/usr/share/horizon/layouts/macos.js" "$pkg/usr/share/horizon/wallpaper.js" \
        > "$lnf/layouts/org.kde.plasma.desktop-layout.js"
    rsvg-convert -w 1200 -h 675 "$ROOT_DIR/assets/wallpaper-light.svg" -o "$lnf/previews/fullscreenpreview.jpg"  # PNG data; Qt sniffs the format
    rsvg-convert -w 480 -h 270 "$ROOT_DIR/assets/wallpaper-light.svg" -o "$lnf/previews/preview.png"
}

build_deb() { # <package dir name>
    local name="$1" src="$ROOT_DIR/packages/$1" tree="$STAGING/$1"
    log "Building $name.deb"
    rm -rf "$tree"
    mkdir -p "$tree"
    cp -a "$src/." "$tree/"
    case "$name" in
        horizon-desktop)
            render_assets "$tree" ;;
        horizon-installer)
            local brand="$tree/etc/calamares/branding/horizon"
            rsvg-convert -w 256 -h 256 "$ROOT_DIR/assets/logo.svg" -o "$brand/logo.png"
            rsvg-convert -w 900 -h 400 "$ROOT_DIR/assets/wallpaper-light.svg" -o "$brand/welcome.png" ;;
        horizon-grub-theme)
            "$ROOT_DIR/scripts/make-grub-theme.sh" "$tree/usr/share/grub/themes/horizon" "$DISTRO_NAME" "$ROOT_DIR/assets" ;;
    esac

    # Substitute branding placeholders in every text file.
    grep -rIlE '@(DISTRO|UBUNTU|LIVE)_' "$tree" | while read -r f; do
        sed -i \
            -e "s|@DISTRO_NAME@|$DISTRO_NAME|g" \
            -e "s|@DISTRO_ID@|$DISTRO_ID|g" \
            -e "s|@DISTRO_VERSION@|$DISTRO_VERSION|g" \
            -e "s|@DISTRO_CODENAME@|$DISTRO_CODENAME|g" \
            -e "s|@DISTRO_URL@|$DISTRO_URL|g" \
            -e "s|@UBUNTU_SUITE@|$UBUNTU_SUITE|g" \
            -e "s|@LIVE_USER@|$LIVE_USER|g" "$f"
    done || true

    find "$tree" -type d -exec chmod 755 {} +
    find "$tree" -type f -exec chmod 644 {} +
    chmod 755 "$tree"/DEBIAN/{postinst,prerm,postrm,preinst} 2>/dev/null || true
    find "$tree/usr/bin" "$tree/usr/libexec" -type f -exec chmod 755 {} + 2>/dev/null || true

    local size
    size=$(du -sk --exclude=DEBIAN "$tree" | cut -f1)
    echo "Installed-Size: $size" >> "$tree/DEBIAN/control"
    mkdir -p "$DEBS"
    dpkg-deb --root-owner-group -Zxz --build "$tree" "$DEBS/${name}_${DISTRO_VERSION}_all.deb"
}

stage_debs() {
    local tool
    for tool in rsvg-convert convert grub-mkfont fc-match dpkg-deb; do
        command -v "$tool" >/dev/null || die "$tool missing — run '$0 deps' first"
    done
    rm -f "$DEBS"/*.deb
    # Every folder in packages/ with a DEBIAN/control becomes a .deb.
    local dir
    for dir in "$ROOT_DIR"/packages/*/; do
        [[ -f $dir/DEBIAN/control ]] && build_deb "$(basename "$dir")"
    done
}

stage_chroot() {
    [[ -x "$CHROOT/usr/bin/apt-get" ]] || die "No base system — run '$0 bootstrap' first"
    [[ -d "$THEMES/WhiteSur-kde" ]]   || die "Themes missing — run '$0 themes' first"
    ls "$DEBS"/*.deb >/dev/null 2>&1  || die "Packages missing — run '$0 debs' first"

    log "Preparing chroot"
    mount_chroot
    cp /etc/resolv.conf "$CHROOT/etc/resolv.conf" 2>/dev/null || echo "nameserver 1.1.1.1" > "$CHROOT/etc/resolv.conf"

    rm -rf "$CHROOT/tmp/horizon"
    mkdir -p "$CHROOT/tmp/horizon"
    cp -a "$ROOT_DIR/config" "$ROOT_DIR/scripts" "$THEMES" "$DEBS" "$CHROOT/tmp/horizon/"

    log "Configuring the system inside the chroot (this takes a while)"
    in_chroot \
        DISTRO_NAME="$DISTRO_NAME" DISTRO_ID="$DISTRO_ID" DISTRO_VERSION="$DISTRO_VERSION" \
        DISTRO_CODENAME="$DISTRO_CODENAME" DISTRO_URL="$DISTRO_URL" \
        UBUNTU_SUITE="$UBUNTU_SUITE" UBUNTU_MIRROR="$UBUNTU_MIRROR" \
        LIVE_USER="$LIVE_USER" LIVE_HOSTNAME="$LIVE_HOSTNAME" DEFAULT_LOCALE="$DEFAULT_LOCALE" \
        bash /tmp/horizon/scripts/chroot-setup.sh

    rm -rf "$CHROOT/tmp/horizon"
    umount_chroot
}

stage_iso() {
    [[ -e "$CHROOT/boot/vmlinuz" ]] || die "No kernel in the image — run '$0 chroot' first"
    log "Assembling ISO tree"
    rm -rf "$IMAGE"
    mkdir -p "$IMAGE"/{casper,boot/grub/fonts,boot/grub/themes,EFI/boot,.disk,isolinux}

    cp -L "$CHROOT/boot/vmlinuz"    "$IMAGE/casper/vmlinuz"
    cp -L "$CHROOT/boot/initrd.img" "$IMAGE/casper/initrd"
    cp "$CHROOT/usr/share/grub/unicode.pf2" "$IMAGE/boot/grub/fonts/" 2>/dev/null || true
    if [[ -d $CHROOT/usr/share/grub/themes/horizon ]]; then
        cp -r "$CHROOT/usr/share/grub/themes/horizon" "$IMAGE/boot/grub/themes/"
    else
        warn "GRUB theme not installed in the image — the boot menu will be plain text"
    fi
    # memtest86+ ships one image that boots on both BIOS and UEFI.
    if [[ -e $CHROOT/boot/mt86+x64 ]]; then
        mkdir -p "$IMAGE/boot/memtest"
        cp "$CHROOT/boot/mt86+x64" "$IMAGE/boot/memtest/mt86+x64"
    fi

    sed -e "s|@DISTRO_NAME@|$DISTRO_NAME|g" -e "s|@DISTRO_ID@|$DISTRO_ID|g" "$ROOT_DIR/iso/grub.cfg" > "$IMAGE/boot/grub/grub.cfg"
    cp "$IMAGE/boot/grub/grub.cfg" "$IMAGE/EFI/boot/grub.cfg"

    echo "$DISTRO_NAME $DISTRO_VERSION \"$DISTRO_CODENAME\" - Release $ARCH ($(date +%Y%m%d))" > "$IMAGE/.disk/info"
    echo "$DISTRO_URL" > "$IMAGE/.disk/release_notes_url"
    touch "$IMAGE/$DISTRO_ID"

    log "Writing manifests"
    chroot "$CHROOT" dpkg-query -W --showformat='${Package} ${Version}\n' > "$IMAGE/casper/filesystem.manifest"
    grep -v '^#' "$ROOT_DIR/config/packages-live.list" | grep . | cut -d' ' -f1 \
        | { cat; echo casper; echo horizon-installer; } > "$IMAGE/casper/filesystem.manifest-remove"

    log "Compressing root filesystem ($SQUASHFS_COMP) — grab a coffee"
    local comp_args=(-comp "$SQUASHFS_COMP")
    [[ $SQUASHFS_COMP == zstd ]] && comp_args+=(-Xcompression-level 19)
    [[ $SQUASHFS_COMP == xz ]]   && comp_args+=(-Xbcj x86 -b 1M)
    mksquashfs "$CHROOT" "$IMAGE/casper/filesystem.squashfs" -noappend -wildcards \
        "${comp_args[@]}" -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*'
    du -sx --block-size=1 "$CHROOT" | cut -f1 > "$IMAGE/casper/filesystem.size"

    log "Preparing Secure Boot capable EFI loader (shim + signed GRUB)"
    local boot="$BUILD_DIR/bootfiles" bl="$CHROOT/var/lib/horizon/bootloader/efi"
    rm -rf "$boot"; mkdir -p "$boot/x"
    for deb in "$bl"/shim-signed_*.deb "$bl"/grub-efi-amd64-signed_*.deb; do
        dpkg-deb -x "$deb" "$boot/x"
    done
    first_existing() { local f; for f in "$@"; do [[ -e $f ]] && { echo "$f"; return; }; done; }
    local shim grub
    shim=$(first_existing "$boot"/x/usr/lib/shim/shimx64.efi.signed.latest "$boot"/x/usr/lib/shim/shimx64.efi.signed)
    grub=$(first_existing "$boot"/x/usr/lib/grub/x86_64-efi-signed/gcdx64.efi.signed "$boot"/x/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed)
    [[ -n $shim && -n $grub ]] || die "Could not find signed shim/grub binaries"
    cp "$shim" "$IMAGE/EFI/boot/bootx64.efi"
    cp "$grub" "$IMAGE/EFI/boot/grubx64.efi"
    cp "$boot/x/usr/lib/shim/mmx64.efi" "$IMAGE/EFI/boot/mmx64.efi" 2>/dev/null || true

    # FAT image for the El Torito EFI boot entry.
    local efiimg="$IMAGE/isolinux/efiboot.img"
    dd if=/dev/zero of="$efiimg" bs=1M count=12 status=none
    mkfs.vfat -F 16 -n EFIBOOT "$efiimg" >/dev/null
    mmd -i "$efiimg" ::/EFI ::/EFI/boot
    mcopy -i "$efiimg" "$IMAGE"/EFI/boot/* ::/EFI/boot/

    log "Preparing legacy BIOS loader"
    mkdir -p "$CHROOT/tmp/isogrub"
    cp "$IMAGE/boot/grub/grub.cfg" "$CHROOT/tmp/isogrub/grub.cfg"
    chroot "$CHROOT" grub-mkstandalone --format=i386-pc --output=/tmp/isogrub/core.img \
        --install-modules="linux16 linux normal iso9660 biosdisk memdisk search tar ls" \
        --modules="linux16 linux normal iso9660 biosdisk search test echo halt reboot sleep true \
                   all_video gfxterm gfxmenu gfxterm_background font jpeg png" \
        --locales="" --fonts="" "boot/grub/grub.cfg=/tmp/isogrub/grub.cfg"
    cat "$CHROOT/usr/lib/grub/i386-pc/cdboot.img" "$CHROOT/tmp/isogrub/core.img" > "$IMAGE/isolinux/bios.img"
    rm -rf "$CHROOT/tmp/isogrub"

    (cd "$IMAGE" && find . -type f ! -name md5sum.txt ! -path './isolinux/*' -print0 | xargs -0 md5sum > md5sum.txt)

    log "Building ISO"
    xorriso -as mkisofs \
        -iso-level 3 -full-iso9660-filenames -J -joliet-long \
        -volid "$VOLID" \
        -output "$ISO_FILE" \
        -eltorito-boot isolinux/bios.img -no-emul-boot -boot-load-size 4 -boot-info-table \
            --eltorito-catalog boot/grub/boot.cat --grub2-boot-info \
            --grub2-mbr "$CHROOT/usr/lib/grub/i386-pc/boot_hybrid.img" \
        -partition_offset 16 --mbr-force-bootable \
        -append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b "$efiimg" -appended_part_as_gpt \
        -iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7 \
        -eltorito-alt-boot -e '--interval:appended_partition_2:all::' -no-emul-boot \
        "$IMAGE"

    (cd "$BUILD_DIR" && sha256sum "$(basename "$ISO_FILE")" > "$(basename "$ISO_FILE").sha256")
    log "Done: $ISO_FILE ($(du -h "$ISO_FILE" | cut -f1))"
    echo "Test it with:  qemu-system-x86_64 -enable-kvm -m 4G -smp 4 -cdrom $ISO_FILE"
    echo "UEFI test:     qemu-system-x86_64 -enable-kvm -m 4G -smp 4 -bios /usr/share/ovmf/OVMF.fd -cdrom $ISO_FILE"
}

stage_clean() {
    umount_chroot
    log "Removing $BUILD_DIR"
    rm -rf "$BUILD_DIR"
}

stage_shell() {
    mount_chroot
    log "Entering image shell — type 'exit' when done"
    chroot "$CHROOT" /bin/bash -l || true
}

# --- main ----------------------------------------------------------------------

STAGE="${1:-all}"
case "$STAGE" in
    -h|--help|help) usage; exit 0 ;;
esac
require_root "$@"
mkdir -p "$BUILD_DIR"

case "$STAGE" in
    all)
        stage_deps; stage_bootstrap; stage_themes; stage_debs; stage_chroot; stage_iso ;;
    deps|bootstrap|themes|debs|chroot|iso|clean|shell)
        "stage_$STAGE" ;;
    *) usage; exit 1 ;;
esac
