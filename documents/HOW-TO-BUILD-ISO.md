# Build the Horizon OS ISO

You need Ubuntu or Debian (a real PC or a virtual machine), about 25 GB of
free disk space and an internet connection.

## Commands

```bash
unzip horizon-os.zip
cd horizon-os
chmod +x build.sh scripts/*.sh
sudo ./build.sh
```

The build takes about 30–60 minutes. When it finishes, the ISO is here:

```
build/horizon-1.0-amd64.iso
```

## Test it in a virtual machine

```bash
sudo apt install qemu-system-x86 ovmf
qemu-system-x86_64 -enable-kvm -m 4G -smp 4 -cdrom build/horizon-1.0-amd64.iso
```

## Put it on a USB stick

Use balenaEtcher or Ventoy. Or use `dd` (replace sdX with your USB drive, and
be careful, because everything on that drive is erased):

```bash
sudo dd if=build/horizon-1.0-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

## No Linux machine?

Push the project to GitHub and open **Actions → Build ISO → Run workflow**.
Download the ISO from the finished run under "Artifacts".
