# Hardening: what makes the box safer

For developers. This page lists what Curatables does to keep the box and
the family's data safe, where each measure lives in the code, how to see
that it is working, and what to keep in mind when changing things. The
reasons, the attacker model and the risks that remain are in
[threat-model.md](threat-model.md); the parent-facing version is
[safety-model.md](safety-model.md).

The box is meant for a home network only: plain HTTP on port 80,
reached as `curatables.local`, never published to the internet.

## The operating system (Raspberry Pi image)

| Measure | Where | Check on a box |
|---|---|---|
| **Firewall.** Coming in, only TCP 80 (web), TCP 22 (SSH, answered only once turned on), UDP 5353 (mDNS: avahi, the server's announcement, the unicast helper), DHCP/DHCPv6 replies, ICMP/ICMPv6 and replies to the box's own connections. Everything else is dropped; outgoing traffic is not filtered. Loaded before the network comes up, in its own nftables table. If it can't load, the box stays reachable and Diagnostics lists the unit as failed. | `systemd/curatables-firewall.nft`, `systemd/curatables-firewall.service` | `sudo nft list table inet curatables`; `ss -tulpn` shows what listens |
| **Automatic updates.** Debian's security and stable fixes (Debian's default) and Raspberry Pi OS's own packages (kernel, firmware) install every day; when one needs a restart, the box restarts at 04:00. | `scripts/apply-system.sh` (`20auto-upgrades`, `52curatables-unattended-upgrades`) | `journalctl -u apt-daily-upgrade`; `/var/log/unattended-upgrades/` |
| **No default passwords, SSH off.** The parent password and the Linux login are set in first-run setup (or Raspberry Pi Imager); the two must differ. SSH is off until setup or Box turns it on. | `pi-gen/README.md`, `syshelper/` | `systemctl is-enabled ssh` |
| **First-run setup window.** Setup accepts a password only in the first 30 minutes after the server starts. | `parent.setup_window_minutes` | — |
| **Wi-Fi password off the boot partition** once the network is set up. | `curatables-firstboot.service` | `curatables-wifi.txt` has empty `ssid`/`psk` |
| **Logs kept on the card** (not only in RAM), size-capped, so what happened can be read after a restart. | `systemd/journald-curatables.conf` | `journalctl --list-boots` |
| **Reproducible image.** pi-gen is pinned to one commit and Python packages to hashed versions; Deno is a pinned release checked by SHA-256. | `PIGEN_COMMIT` in `pi-gen/build.sh`, `requirements.lock`, `scripts/install-deno.sh` | — |

`scripts/apply-system.sh` installs all of the above from the code tree.
The image build runs it, and so does every **Box → Updates** update, so
a release can change system settings without re-flashing. Own installs
(`scripts/install.sh`) get none of this table: their owners run their
own firewall and updates ([deployment.md](deployment.md#firewall-on-your-own-install)).
The diagnostics report (**Box → Diagnostics**, or
`curatables-diagnose.txt` on the boot partition) prints the loaded
firewall table and each unit's state.

## The services

| Measure | Where |
|---|---|
| The server runs as the unprivileged `curatables` user, with only `CAP_NET_BIND_SERVICE` (port 80), `NoNewPrivileges`, `ProtectSystem=strict`, the data dir as its only writable path, `PrivateTmp` and restricted address families. | `systemd/curatables.service` |
| The code in `/opt/curatables` is root-owned, read-only to the server. | `scripts/install.sh`, the image build |
| The mDNS unicast helper has only `CAP_NET_RAW`, a dynamic user and no file access; its packet filter passes nothing but mDNS queries. | `systemd/curatables-mdns-unicast.service`, `scripts/mdns-unicast.py` |
| Root actions (passwords, SSH, Wi-Fi, drives, restarts, updates) go through one root helper on a Unix socket that only root and the service user can use. It runs a fixed list of actions with checked parameters and never a shell. | `syshelper/`, `syshelper/registry.py` |
| The yt-dlp updater checks a new version inside the server's sandbox before installing it; root never imports it. | `scripts/updater.sh`, `scripts/ytdlp_update.py` |
| Curatables updates are accepted only from the repository's latest GitHub release over HTTPS, matching its `SHA256SUMS`, unpacked without links leaving the folder. | `syshelper/upgrade.py` |

`systemd-analyze security curatables.service` scores the server unit.

## The web app

| Measure | Where | Test |
|---|---|---|
| Parent password PBKDF2-SHA256 (600,000 rounds), PINs PBKDF2 (100,000), constant-time compares. | `app/services/auth.py` | `tests/test_auth_hardening.py` |
| Login and PIN attempt limits, kept in the database so a restart doesn't reset them. | `app/services/auth.py`, `app/services/profiles.py` | `tests/test_rate_limit.py`, `tests/test_auth_hardening.py` |
| Signed session cookie, `SameSite=Strict`, `HttpOnly`; a password change ends every session. | `app/main.py` | `tests/test_auth_hardening.py` |
| CSRF token on every state-changing request (a short, listed set of exemptions). | `app/middleware/csrf.py` | `tests/test_csrf.py` |
| Answers only to the box's own names and IP addresses (stops DNS rebinding). | `app/middleware/host_check.py` | `tests/test_host_check.py` |
| Content-Security-Policy with scripts only from the app (no inline code), `nosniff`, no framing. | `app/main.py` | `tests/test_parent_ux.py::test_templates_have_no_inline_scripts_or_handlers` |
| Request body limits checked before anything reads the body. | `app/middleware/body_size.py` | `tests/test_body_size.py` |
| Uploads: codec allow-list, pictures checked by their bytes, kid uploads to shared channels held for the parent. | `app/services/ingest.py`, upload routes | |
| Fetches the server makes for others' links (thumbnails, lookups, shared lists) go only to public internet addresses, pinned after the check. | `app/services/safe_fetch.py`, `YtdlpBackend._blocked` | `tests/test_safe_fetch.py`, `tests/test_backend_lan_urls.py` |
| Kids reach YouTube only through search phrases and ids the server checks, behind a word filter, optional trusted channels, a cache and rate limits. | `app/services/wishlist.py`, `app/services/search_gate.py` | `tests/test_wishlist.py` |
| Fail-closed channel visibility: a kid sees only channels they are allowed; anonymous visitors see no videos. | `ContentService.can_view`, `ChannelRepository.list_visible_to` | `tests/test_visibility.py` |

## When you change things

- **A new network service or port** must be added to
  `systemd/curatables-firewall.nft` (and to
  `tests/test_apply_system.py::test_the_firewall_lets_in_what_the_box_is_for`),
  or the box drops its traffic. Outgoing connections need nothing.
- **A new systemd unit** goes in `UNITS` (and `ENABLE` if it has an
  `[Install]` section) in `scripts/apply-system.sh`; tests check both.
  Give it the narrowest sandbox that works, like the units above.
- **Anything that needs root** is a new `syshelper` action with checked
  parameters, never a shell, never a path the service user can redirect.
- **Page scripts** go in `app/static/`; an inline `<script>`, `on…=`
  handler or `javascript:` URL fails the tests.
- **A route that changes state** gets CSRF; an exemption needs a reason
  in `app/middleware/csrf.py` and in threat-model.md.
- **A fetch of a URL someone else supplied** goes through `safe_fetch`.
- **A new risk you accept** goes in threat-model.md's "Residual risks"
  table, so it is written down rather than forgotten.

## Checking a release image

`scripts/qemu-box.sh` boots a release image on a laptop as a whole
system (see [testing/README.md](testing/README.md)). On it:
`sudo nft list table inet curatables`, `ss -tulpn`,
`systemctl --failed`, and opening `http://localhost:<port>/` from the
laptop. mDNS can't be tried there (QEMU's user network carries no
multicast); try `curatables.local` on a real box.
