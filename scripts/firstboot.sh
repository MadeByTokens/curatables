#!/usr/bin/env bash
# Apply Wi-Fi settings from a plain text file on the FAT boot partition.
#
# The boot partition is the one Windows/macOS/Linux all mount when you
# insert the SD card, so non-technical users can configure Wi-Fi by
# editing a text file — no terminal, no Pi access required. Wired
# Ethernet needs nothing: Raspberry Pi OS brings eth0 up with DHCP
# automatically, so a Pi plugged into the router just gets an IP.
#
# Runs on every boot (via curatables-firstboot.service). The network is saved
# as a NetworkManager profile that reconnects on its own. Once the Pi has
# actually connected, the ssid and psk values are blanked in the file
# (NetworkManager keeps them in its root-only store), so the password does
# not stay on a partition any computer can read. Fill them in again and
# reboot to change networks. Progress and errors go to
# curatables-firstboot.log next to the file, because Raspberry Pi OS keeps
# the journal in RAM only.
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

LOG_FILE="$(dirname "$WIFI_FILE")/curatables-firstboot.log"

say() {
	echo "firstboot: $*"
	{ echo "$(date -u '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; } 2>/dev/null || true
}

if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 300 ]; then
	tail -n 200 "$LOG_FILE" > "${LOG_FILE}.new" && mv "${LOG_FILE}.new" "$LOG_FILE"
fi

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

# raspi-config sets the regulatory domain and unblocks rfkill, which Wi-Fi
# needs before it will associate.
if [ -n "$COUNTRY" ]; then
	say "setting Wi-Fi country ${COUNTRY}"
	raspi-config nonint do_wifi_country "$COUNTRY" || say "warning: could not set Wi-Fi country"
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

CON_NAME="curatables-wifi"
WAIT_SECONDS="${CURATABLES_WIFI_WAIT:-120}"
STEP_SECONDS="${CURATABLES_WIFI_STEP:-5}"

wifi_iface() {
	nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2 == "wifi" { print $1; exit }'
}

ssid_visible() {
	nmcli -t -e no -f SSID device wifi list ifname "$1" 2>/dev/null | grep -Fxq -- "$SSID"
}

say "configuring Wi-Fi for SSID '${SSID}'"

IFACE=""
waited=0
while :; do
	IFACE="$(wifi_iface)"
	[ -n "$IFACE" ] && break
	if [ "$waited" -ge "$WAIT_SECONDS" ]; then
		say "error: no Wi-Fi device appeared after ${WAIT_SECONDS}s (is NetworkManager running?)"
		exit 0
	fi
	sleep "$STEP_SECONDS"
	waited=$((waited + STEP_SECONDS))
done
nmcli radio wifi on >/dev/null 2>&1 || true

nmcli connection delete "$CON_NAME" >/dev/null 2>&1 || true
add_args=(connection add type wifi con-name "$CON_NAME" ifname "$IFACE" ssid "$SSID"
	connection.autoconnect yes)
if [ -n "$PSK" ]; then
	add_args+=(wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK")
fi
if ! out="$(nmcli "${add_args[@]}" 2>&1)"; then
	say "error: could not save the network: ${out//"$PSK"/***}"
	exit 0
fi
say "saved the network as '${CON_NAME}'; it reconnects on its own from now on"

seen=0
connected=0
waited=0
while [ "$waited" -lt "$WAIT_SECONDS" ]; do
	nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1 || true
	sleep "$STEP_SECONDS"
	waited=$((waited + STEP_SECONDS))
	if ssid_visible "$IFACE"; then
		seen=1
		if out="$(nmcli --wait 30 connection up "$CON_NAME" ifname "$IFACE" 2>&1)"; then
			connected=1
			break
		fi
		say "connect attempt failed: ${out//"$PSK"/***}"
		waited=$((waited + 30))
	fi
done

if [ "$connected" = 1 ]; then
	say "connected to '${SSID}'"
	forget_credentials || say "warning: could not clear the password from ${WIFI_FILE}"
elif [ "$seen" = 0 ]; then
	say "error: '${SSID}' was not found in ${WAIT_SECONDS}s. Check the name (it is case-sensitive) and that the Pi is in range. The password stays in the file for the next boot."
else
	say "error: '${SSID}' is in range but the connection failed. Check the password. It stays in the file for the next boot."
fi
