#!/usr/bin/env bash
# Curatables installer for Debian/Ubuntu/Raspberry Pi OS.
#
# Idempotent: safe to re-run to upgrade an existing install.
#
# Without flags (try it out / development): installs the system packages
# and builds .venv/ inside this checkout. Start it with
# `.venv/bin/python run.py`; data goes to ~/curatables-data.
#
# With --systemd (permanent install, runs at boot):
#   1. installs the system packages and Deno (system-wide)
#   2. creates the `curatables` service user
#   3. copies this checkout to /opt/curatables (root-owned, read-only to
#      the service) and builds /opt/curatables/.venv
#   4. if you already ran curatables from this checkout, copies your
#      library (~/curatables-data) to /home/curatables/curatables-data
#   5. installs and enables the server, updater and nightly backup units
#   6. takes a backup first when upgrading a running install
#
# Flags:
#   --dry-run   Validate without changing the system: check apt package
#               names resolve, check network URLs, build a throwaway venv
#               in a tmpdir and run --check from it. No sudo needed.
#   --systemd   Permanent install as described above.
#   -h, --help  Show this help.

set -euo pipefail

DRY_RUN=0
SYSTEMD=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --systemd) SYSTEMD=1 ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

INSTALL_ROOT=/opt/curatables
SVC_USER=curatables
SVC_DATA=/home/curatables/curatables-data

APT_PACKAGES=(
    python3 python3-venv python3-pip
    ffmpeg ca-certificates curl unzip rsync
    avahi-daemon libnss-mdns
)

DENO_INSTALL_URL="https://deno.land/install.sh"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

# Run as root: directly if we already are, else through sudo.
as_root() { if [[ "$(id -u)" -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

run_check() {
    local py="$1" root="$2"
    say "Running acceptance test: $py $root/run.py --check"
    "$py" "$root/run.py" --check
}

if [[ $DRY_RUN -eq 1 ]]; then
    say "DRY RUN — no system changes will be made"

    need_cmd apt-cache
    say "Validating apt package names resolve"
    missing=()
    for pkg in "${APT_PACKAGES[@]}"; do
        if ! apt-cache policy "$pkg" 2>/dev/null | grep -q 'Candidate:'; then
            missing+=("$pkg")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        warn "apt packages not resolvable on this host: ${missing[*]}"
        warn "(this host may not be Debian/Ubuntu — OK on a dev box)"
    else
        say "All ${#APT_PACKAGES[@]} apt packages resolve."
    fi

    need_cmd curl
    say "Checking Deno install URL is reachable"
    curl -fsI "$DENO_INSTALL_URL" >/dev/null || warn "Deno URL unreachable: $DENO_INSTALL_URL"

    TMPVENV="$(mktemp -d)"
    trap 'rm -rf "$TMPVENV"' EXIT
    say "Building throwaway venv in $TMPVENV"
    need_cmd python3
    python3 -m venv "$TMPVENV/venv"
    "$TMPVENV/venv/bin/pip" install --quiet --upgrade pip
    say "pip install -r requirements.txt (throwaway)"
    "$TMPVENV/venv/bin/pip" install --quiet -r requirements.txt

    run_check "$TMPVENV/venv/bin/python" "$REPO_ROOT" \
        || warn "run.py --check reported issues (likely ffmpeg/deno missing on this host)"

    say "Dry run complete. No system state was modified."
    exit 0
fi

# ---- Real install below ----

need_cmd apt-get
[[ "$(id -u)" -eq 0 ]] || need_cmd sudo

say "Installing apt packages: ${APT_PACKAGES[*]}"
as_root apt-get update
as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"

if ! command -v deno >/dev/null 2>&1; then
    say "Installing Deno to /usr/local (system-wide, visible to the service)"
    curl -fsSL "$DENO_INSTALL_URL" | as_root env DENO_INSTALL=/usr/local sh -s -- -y
else
    say "Deno already installed: $(deno --version | head -n1)"
fi

if [[ $SYSTEMD -eq 0 ]]; then
    if [[ "$(id -u)" -eq 0 ]]; then
        warn "Running as root: .venv/ and ~/curatables-data will belong to root."
        warn "For a try-out install run this as your normal user; for a permanent one use --systemd."
    fi
    if [[ ! -d .venv ]]; then
        say "Creating virtualenv at .venv/"
        python3 -m venv .venv
    fi
    say "Upgrading pip and installing Python requirements"
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
    run_check ".venv/bin/python" "$REPO_ROOT"
    say "Install complete."
    say "Start the server with: .venv/bin/python run.py"
    say "To make it permanent (starts at boot, port 80): $0 --systemd"
    exit 0
fi

# ---- --systemd: permanent install under /opt/curatables ----

if systemctl is-active --quiet curatables.service 2>/dev/null \
        && [[ -x "$INSTALL_ROOT/scripts/backup.sh" ]]; then
    say "Existing install is running; taking a backup before upgrading"
    as_root install -d -o "$SVC_USER" -g "$SVC_USER" -m 0700 /var/lib/curatables-backups
    as_root runuser -u "$SVC_USER" -- env CURATABLES_DATA_DIR="$SVC_DATA" \
        "$INSTALL_ROOT/scripts/backup.sh" /var/lib/curatables-backups \
        || die "backup failed; not upgrading. Fix the backup or remove the old install first."
fi

if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    say "Creating service user $SVC_USER"
    as_root useradd --system --create-home --home-dir /home/curatables \
        --shell /usr/sbin/nologin "$SVC_USER"
fi

if [[ "$REPO_ROOT" != "$INSTALL_ROOT" ]]; then
    say "Copying $REPO_ROOT to $INSTALL_ROOT"
    as_root install -d -m 0755 "$INSTALL_ROOT"
    as_root rsync -a --delete \
        --exclude .git --exclude .venv --exclude __pycache__ \
        --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache \
        "$REPO_ROOT"/ "$INSTALL_ROOT"/
fi
as_root chown -R root:root "$INSTALL_ROOT"

say "Building $INSTALL_ROOT/.venv"
[[ -d "$INSTALL_ROOT/.venv" ]] || as_root python3 -m venv "$INSTALL_ROOT/.venv"
as_root "$INSTALL_ROOT/.venv/bin/pip" install --upgrade pip
as_root "$INSTALL_ROOT/.venv/bin/pip" install -r "$INSTALL_ROOT/requirements.txt"
run_check "$INSTALL_ROOT/.venv/bin/python" "$INSTALL_ROOT"

# Carry over a library created by an earlier try-out run.
INVOKER="${SUDO_USER:-$(id -un)}"
INVOKER_HOME="$(getent passwd "$INVOKER" | cut -d: -f6)"
OLD_DATA="$INVOKER_HOME/curatables-data"
if [[ "$INVOKER" != "$SVC_USER" && -f "$OLD_DATA/config.json" ]] \
        && ! as_root test -f "$SVC_DATA/config.json"; then
    say "Copying your existing library from $OLD_DATA to $SVC_DATA"
    as_root install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$SVC_DATA"
    as_root cp -a "$OLD_DATA"/. "$SVC_DATA"/
    as_root "$INSTALL_ROOT/.venv/bin/python" - "$SVC_DATA" <<'PYEOF'
import json, sys
path = sys.argv[1] + "/config.json"
with open(path) as f:
    cfg = json.load(f)
cfg.setdefault("storage", {})["path"] = sys.argv[1]
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF
    say "(The original stays in $OLD_DATA; delete it once the service works.)"
fi
as_root install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$SVC_DATA"
as_root chown -R "$SVC_USER:$SVC_USER" "$SVC_DATA"

say "Installing systemd units"
for unit in curatables.service curatables-updater.service curatables-updater.path \
            curatables-backup.service curatables-backup.timer; do
    as_root install -m 0644 "$INSTALL_ROOT/systemd/$unit" /etc/systemd/system/
done
as_root systemctl daemon-reload
as_root systemctl enable curatables.service curatables-updater.path curatables-backup.timer
as_root systemctl restart curatables.service
as_root systemctl start curatables-updater.path curatables-backup.timer

say "Install complete. Curatables is running on port 80:"
say "  parents: http://curatables.local/parent/    kids: http://curatables.local/"
say "Logs: journalctl -u curatables -f"
say "Media on an external drive? See: sudo $INSTALL_ROOT/scripts/setup-external-drive.sh"
