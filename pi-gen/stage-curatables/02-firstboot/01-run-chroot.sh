#!/bin/bash -e
# In-chroot step: install + enable the first-boot Wi-Fi applier, which
# reads /boot/firmware/curatables-wifi.txt on each boot. Ethernet needs
# nothing — Raspberry Pi OS DHCPs eth0 automatically. The dispatcher hook
# keeps Wi-Fi disconnected while a cable is plugged in.

cp /opt/curatables/systemd/curatables-firstboot.service /etc/systemd/system/
systemctl enable curatables-firstboot.service
install -o root -g root -m 0755 /opt/curatables/scripts/nm-wired-only.sh \
	/etc/NetworkManager/dispatcher.d/70-curatables-wired-only
