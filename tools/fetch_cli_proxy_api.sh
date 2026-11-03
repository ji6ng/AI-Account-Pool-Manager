#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-v8.0.40}"
VERSION="v${VERSION#v}"
case "$(uname -s)" in
    Darwin) TARGET_OS=darwin ;;
    Linux) TARGET_OS=linux ;;
    *) echo 'Supported systems: macOS, Linux' >&2; exit 1 ;;
esac
case "$(uname -m)" in
    arm64|aarch64) ARCH=aarch64 ;;
    x86_64|amd64) ARCH=amd64 ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
ASSET="CLIProxyAPI_${VERSION#v}_${TARGET_OS}_${ARCH}.tar.gz"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
BASE="https://github.com/router-for-me/CLIProxyAPI/releases/download/$VERSION"
curl --fail --location --retry 3 --connect-timeout 20 --speed-time 60 --speed-limit 1024 "$BASE/$ASSET?download=1&ts=$(date +%s)" -o "$TMP_DIR/$ASSET"
curl --fail --location --retry 3 --connect-timeout 20 --speed-time 60 --speed-limit 1024 "$BASE/checksums.txt" -o "$TMP_DIR/checksums.txt"
(cd "$TMP_DIR"; awk -v asset="$ASSET" '$2 == asset || $2 == "*" asset {print}' checksums.txt > selected.sha256
    [[ -s selected.sha256 ]] || { echo "No checksum for $ASSET" >&2; exit 1; }
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 -c selected.sha256
    else
        sha256sum -c selected.sha256
    fi)
tar -xzf "$TMP_DIR/$ASSET" -C "$TMP_DIR"
[[ -f "$TMP_DIR/cli-proxy-api" ]] || { echo "Release is missing cli-proxy-api" >&2; exit 1; }
DEST="${DEST:-$ROOT/vendor/cli-proxy-api}"
mkdir -p "$(dirname "$DEST")"
install -m 755 "$TMP_DIR/cli-proxy-api" "$DEST"
if [[ "$TARGET_OS" == darwin ]]; then
    xattr -d com.apple.quarantine "$DEST" 2>/dev/null || true
fi
echo "Installed $VERSION ($ARCH): $DEST"
