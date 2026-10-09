#!/usr/bin/env bash
# Apply Wi-Fi settings from a plain text file on the FAT boot partition.
#
# The boot partition is the one Windows/macOS/Linux all mount when you
# insert the SD card, so non-technical users can configure Wi-Fi by
# editing a text file — no terminal, no Pi access required. Wired
# Ethernet needs nothing: Raspberry Pi OS brings eth0 up with DHCP
# automatically, so a Pi plugged into the router just gets an IP.
#
# Runs on every boot (via curatables-firstboot.service). Once the network is
# configured, the ssid and psk values are blanked in the file (NetworkManager
# keeps them in its root-only store), so the password does not stay on a
# partition any computer can read. Fill them in again and reboot to change
# networks.
#
# File format (curatables-wifi.txt), simple KEY=value lines:
#   country=US        # 2-letter Wi-Fi regulatory country (required for Wi-Fi)
#   ssid=MyNetwork
#   psk=MyPassword    # leave empty for an open network
#
# Windows line endings (CRLF) are tolerated.

set -euo pipefail

# Bookworm and later mount the FAT boot partition at /boot/firmware; older layouts
# use /boot. Take the first that has our file.
WIFI_FILE=""
for cand in ${CURATABLES_WIFI_FILE:-} /boot/firmware/curatables-wifi.txt /boot/curatables-wifi.txt; do
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
		| { grep -v '^#' || true; } | head -n1
}

COUNTRY="$(getval country)"
SSID="$(getval ssid)"
PSK="$(getval psk)"

if [ -z "$SSID" ]; then
	echo "firstboot: curatables-wifi.txt has no ssid, leaving Wi-Fi unconfigured (Ethernet still works)"
	exit 0
fi

# raspi-config nonint abstracts over the NetworkManager (bookworm and later) vs
# wpa_supplicant (older) difference and also unblocks rfkill + sets the
# regulatory domain, which Wi-Fi needs before it will associate.
if [ -n "$COUNTRY" ]; then
	echo "firstboot: setting Wi-Fi country ${COUNTRY}"
	raspi-config nonint do_wifi_country "$COUNTRY" || \
		echo "firstboot: warning: could not set Wi-Fi country"
fi

forget_credentials() {
	local tmp="${WIFI_FILE}.new"
	{
		echo "# Wi-Fi was set up on $(date -u +%Y-%m-%d). The Pi remembers the network,"
		echo "# so its name and password were removed from this file. To switch"
		echo "# networks, fill in ssid and psk again and reboot."
		sed -e 's/\r$//' \
			-e '/^# Wi-Fi was set up on /,+2d' \
			-e 's/^\([[:space:]]*ssid[[:space:]]*=\).*/\1/' \
			-e 's/^\([[:space:]]*psk[[:space:]]*=\).*/\1/' "$WIFI_FILE"
	} > "$tmp" && mv "$tmp" "$WIFI_FILE" && sync
}

echo "firstboot: configuring Wi-Fi for SSID '${SSID}'"
if raspi-config nonint do_wifi_ssid_passphrase "$SSID" "$PSK"; then
	echo "firstboot: Wi-Fi configured"
	forget_credentials || echo "firstboot: warning: could not clear the password from ${WIFI_FILE}" >&2
else
	echo "firstboot: warning: Wi-Fi configuration failed" >&2
fi
