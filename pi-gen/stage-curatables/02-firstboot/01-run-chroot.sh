#!/bin/bash -e
# In-chroot step: install + enable the first-boot Wi-Fi applier, which
# reads /boot/firmware/curatables-wifi.txt on each boot. Ethernet needs
# nothing — Raspberry Pi OS DHCPs eth0 automatically.

cp /opt/curatables/systemd/curatables-firstboot.service /etc/systemd/system/
systemctl enable curatables-firstboot.service
