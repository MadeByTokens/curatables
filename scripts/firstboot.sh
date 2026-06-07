#!/usr/bin/env bash
# Apply Wi-Fi settings from a plain text file on the FAT boot partition.
#
# The boot partition is the one Windows/macOS/Linux all mount when you
# insert the SD card, so non-technical users can configure Wi-Fi by
# editing a text file — no terminal, no Pi access required. Wired
# Ethernet needs nothing: Raspberry Pi OS brings eth0 up with DHCP
# automatically, so a Pi plugged into the router just gets an IP.
#
# Runs on every boot (via curatables-firstboot.service): edit the file and
# reboot to change networks. Applying the same settings twice is harmless.
#
# File format (curatables-wifi.txt), simple KEY=value lines:
#   country=US        # 2-letter Wi-Fi regulatory country (required for Wi-Fi)
#   ssid=MyNetwork
#   psk=MyPassword    # leave empty for an open network
#
# Windows line endings (CRLF) are tolerated.

set -euo pipefail

# Bookworm mounts the FAT boot partition at /boot/firmware; older layouts
# use /boot. Take the first that has our file.
WIFI_FILE=""
for cand in /boot/firmware/curatables-wifi.txt /boot/curatables-wifi.txt; do
	if [ -f "$cand" ]; then
		WIFI_FILE="$cand"
		break
	fi
done

[ -n "$WIFI_FILE" ] || { echo "firstboot: no curatables-wifi.txt found, skipping Wi-Fi setup"; exit 0; }

# Pull a KEY's value, stripping a trailing CR (Windows) and surrounding
# whitespace. Ignores comment (#) lines.
getval() {
	local key="$1"
	sed -n "s/\r$//; s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$WIFI_FILE" \
		| grep -v '^#' | head -n1
}

COUNTRY="$(getval country)"
SSID="$(getval ssid)"
PSK="$(getval psk)"

if [ -z "$SSID" ]; then
	echo "firstboot: curatables-wifi.txt has no ssid, leaving Wi-Fi unconfigured (Ethernet still works)"
	exit 0
fi

# raspi-config nonint abstracts over the NetworkManager (bookworm) vs
# wpa_supplicant (older) difference and also unblocks rfkill + sets the
# regulatory domain, which Wi-Fi needs before it will associate.
if [ -n "$COUNTRY" ]; then
	echo "firstboot: setting Wi-Fi country ${COUNTRY}"
	raspi-config nonint do_wifi_country "$COUNTRY" || \
		echo "firstboot: warning: could not set Wi-Fi country"
fi

echo "firstboot: configuring Wi-Fi for SSID '${SSID}'"
if raspi-config nonint do_wifi_ssid_passphrase "$SSID" "$PSK"; then
	echo "firstboot: Wi-Fi configured"
else
	echo "firstboot: warning: Wi-Fi configuration failed" >&2
fi
