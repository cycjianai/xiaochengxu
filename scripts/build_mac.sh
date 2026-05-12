#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv-build"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build script must run on macOS."
  exit 1
fi

PY_VERSION="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
echo "Using Python ${PY_VERSION}"

python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r "$ROOT_DIR/requirements.txt"
python -m pip install -r "$ROOT_DIR/requirements-build.txt"

cd "$ROOT_DIR"
export WX_SNIFFER_PRODUCT_NAME="${WX_SNIFFER_PRODUCT_NAME:-MTCenter}"
pyinstaller --clean --noconfirm "$ROOT_DIR/wx-sniffer.spec"

echo
echo "Build complete."
echo "App bundle: $ROOT_DIR/dist/${WX_SNIFFER_PRODUCT_NAME}.app"
echo
echo "To produce a DMG, run:"
echo "  hdiutil create -volname ${WX_SNIFFER_PRODUCT_NAME} -srcfolder \"$ROOT_DIR/dist/${WX_SNIFFER_PRODUCT_NAME}.app\" -ov -format UDZO \"$ROOT_DIR/dist/${WX_SNIFFER_PRODUCT_NAME}.dmg\""
