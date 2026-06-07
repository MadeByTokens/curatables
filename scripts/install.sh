#!/usr/bin/env bash
# Curatables installer for Debian/Ubuntu.
#
# Idempotent: safe to re-run on an existing install to upgrade deps.
#
# Steps:
#   1. apt-install system packages (ffmpeg, python, mDNS stack, ...)
#   2. install Deno under the invoking user if missing
#   3. create .venv/ in the project root and pip-install requirements.txt
#   4. run `.venv/bin/python run.py --check` as acceptance test
#
# Flags:
#   --dry-run   Validate without mutating the system: check apt package
#               names resolve, check network URLs, build a throwaway venv
#               in a tmpdir and run --check from it. No sudo needed.
#   --systemd   After a normal install, also install the systemd unit
#               and create the `curatables` service user.
#   -h, --help  Show this help.

set -euo pipefail

DRY_RUN=0
SYSTEMD=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --systemd) SYSTEMD=1 ;;
        -h|--help)
            sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

APT_PACKAGES=(
    python3 python3-venv python3-pip
    ffmpeg ca-certificates curl
    avahi-daemon libnss-mdns
)

DENO_INSTALL_URL="https://deno.land/install.sh"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

run_check() {
    local py="$1"
    say "Running acceptance test: $py run.py --check"
    "$py" run.py --check
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

    run_check "$TMPVENV/venv/bin/python" || warn "run.py --check reported issues (likely ffmpeg/deno missing on this host)"

    say "Dry run complete. No system state was modified."
    exit 0
fi

# ---- Real install below ----

if [[ "$(id -u)" -eq 0 ]]; then
    warn "Running as root. install.sh prefers to run as the user that will own .venv;"
    warn "sudo will be invoked only for apt + (optional) systemd steps."
fi

need_cmd sudo
need_cmd apt-get

say "Installing apt packages: ${APT_PACKAGES[*]}"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"

if ! command -v deno >/dev/null 2>&1; then
    say "Installing Deno for user $(id -un)"
    curl -fsSL "$DENO_INSTALL_URL" | sh
    # Deno installs to ~/.deno/bin; symlink into /usr/local/bin so systemd + other users see it.
    if [[ -x "$HOME/.deno/bin/deno" ]]; then
        sudo ln -sf "$HOME/.deno/bin/deno" /usr/local/bin/deno
    fi
else
    say "Deno already installed: $(deno --version | head -n1)"
fi

if [[ ! -d .venv ]]; then
    say "Creating virtualenv at .venv/"
    python3 -m venv .venv
fi
say "Upgrading pip and installing Python requirements"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

run_check ".venv/bin/python"

if [[ $SYSTEMD -eq 1 ]]; then
    say "Installing systemd unit"
    if ! id -u curatables >/dev/null 2>&1; then
        sudo useradd --system --home /home/curatables --create-home --shell /bin/bash curatables
    fi
    sudo cp systemd/curatables.service /etc/systemd/system/
    # The updater path-unit + service back the in-app "Update yt-dlp"
    # button: the sandboxed app drops a flag in the data dir, this
    # root-owned helper does the pip-upgrade + restart it cannot do
    # itself. See app/services/updates.py and scripts/updater.sh.
    sudo cp systemd/curatables-updater.service /etc/systemd/system/
    sudo cp systemd/curatables-updater.path /etc/systemd/system/
    sudo systemctl daemon-reload
    say "Units installed. Edit /etc/systemd/system/curatables.service to point at this checkout"
    say "(and the CURATABLES_ROOT/DATA/USER lines in curatables-updater.service if you changed paths),"
    say "then: sudo systemctl enable --now curatables curatables-updater.path"
fi

say "Install complete."
say "Start the server with: .venv/bin/python run.py"
