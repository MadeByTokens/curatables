#!/bin/sh
# NetworkManager dispatcher: while an Ethernet cable is connected, keep
# Wi-Fi disconnected; when the cable goes away, bring Wi-Fi back.
#
# Installed as /etc/NetworkManager/dispatcher.d/70-curatables-wired-only.
# NetworkManager calls it as: <interface> <action>.
#
# It only flips the device's autoconnect flag, which NetworkManager does
# not keep across reboots, so a Pi that boots without a cable always
# tries Wi-Fi. Saved Wi-Fi profiles are never touched. While the system
# helper is testing a new Wi-Fi network it creates WIFI_TEST_FLAG, and
# Wi-Fi coming up is left alone until the test is over.

IFACE="$1"
ACTION="$2"
WIFI_TEST_FLAG="${CURATABLES_WIFI_TEST_FLAG:-/run/curatables-wifi-test}"

[ -n "$IFACE" ] || exit 0

wired_connected() {
	nmcli -t -f TYPE,STATE device 2>/dev/null | grep -q '^ethernet:connected'
}

wifi_devices() {
	nmcli -t -f DEVICE,TYPE device 2>/dev/null | awk -F: '$2 == "wifi" { print $1 }'
}

wifi_off() {
	for dev in $(wifi_devices); do
		nmcli device set "$dev" autoconnect no >/dev/null 2>&1
		nmcli device disconnect "$dev" >/dev/null 2>&1
	done
	logger -t curatables-network "Ethernet is connected; Wi-Fi turned off"
}

wifi_on() {
	for dev in $(wifi_devices); do
		nmcli device set "$dev" autoconnect yes >/dev/null 2>&1
		nmcli device connect "$dev" >/dev/null 2>&1 &
	done
	logger -t curatables-network "Ethernet is gone; Wi-Fi turned back on"
}

kind="$(nmcli -g GENERAL.TYPE device show "$IFACE" 2>/dev/null)"

case "${kind}:${ACTION}" in
ethernet:up)
	wifi_off
	;;
ethernet:down)
	wired_connected || wifi_on
	;;
wifi:up)
	[ -e "$WIFI_TEST_FLAG" ] && exit 0
	if wired_connected; then wifi_off; fi
	;;
esac
exit 0
