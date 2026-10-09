#!/usr/bin/env bash
# Install a pinned Deno release (yt-dlp's JavaScript runtime for YouTube)
# to PREFIX/bin/deno, checking the download against the SHA-256 below.
#
#   scripts/install-deno.sh [PREFIX]      # default PREFIX: /usr/local
#
# To move to a newer release, update DENO_VERSION and both checksums
# (from the release's deno-<target>.zip.sha256sum files).

set -euo pipefail

DENO_VERSION="2.9.7"
SHA256_aarch64="c832298b1ad4422481334855f6003e0f54145762c5a134f20a489511d2f65bbf"
SHA256_x86_64="c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490"

PREFIX="${1:-/usr/local}"
TARGET_BIN="${PREFIX}/bin/deno"

case "$(uname -m)" in
	aarch64 | arm64) arch=aarch64; want="$SHA256_aarch64" ;;
	x86_64 | amd64) arch=x86_64; want="$SHA256_x86_64" ;;
	*) echo "install-deno: no Deno build for $(uname -m)" >&2; exit 1 ;;
esac

if [ -x "$TARGET_BIN" ] && "$TARGET_BIN" --version 2>/dev/null | grep -q "^deno ${DENO_VERSION} "; then
	echo "install-deno: deno ${DENO_VERSION} already installed at ${TARGET_BIN}"
	exit 0
fi

zip="deno-${arch}-unknown-linux-gnu.zip"
url="https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/${zip}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "install-deno: downloading ${url}"
curl -fsSL --retry 3 -o "${work}/${zip}" "$url"
got="$(sha256sum "${work}/${zip}" | cut -d' ' -f1)"
if [ "$got" != "$want" ]; then
	echo "install-deno: checksum mismatch for ${zip} (got ${got}, expected ${want})" >&2
	exit 1
fi
unzip -q -o "${work}/${zip}" deno -d "$work"
install -D -m 0755 "${work}/deno" "$TARGET_BIN"
echo "install-deno: installed $("$TARGET_BIN" --version | head -n1) at ${TARGET_BIN}"
