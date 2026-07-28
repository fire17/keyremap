#!/bin/sh
# keyremap installer — macOS / Linux / WSL. Idempotent, no sudo, no pip.
#
#   curl -fsSL https://raw.githubusercontent.com/fire17/keyremap/master/install.sh | sh
#   ./install.sh              # from a clone
#
# Installs to ~/.keyremap/app and puts a `keyremap` launcher on your PATH.
set -e

REPO_RAW="${KEYREMAP_RAW:-https://raw.githubusercontent.com/fire17/keyremap/master}"
APP="$HOME/.keyremap/app"
BIN_DIR="${KEYREMAP_BIN:-$HOME/.local/bin}"
SRC_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || echo .)"

say() { printf '%s\n' "$*"; }

# --- python check ------------------------------------------------------------
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  say "keyremap needs Python 3.10+."
  say "  macOS : brew install python   (or python.org installer)"
  say "  Linux : your distro's python3 package"
  exit 1
fi

mkdir -p "$APP" "$BIN_DIR"

# --- copy or fetch the app ---------------------------------------------------
# The file list lives in install-manifest.txt, never inline: a hardcoded list
# silently went stale once and every command crashed on a fresh install with
# "cannot import name 'miniyaml'". A test keeps the manifest complete.
MANIFEST="$APP/.install-manifest.txt"
if [ -f "$SRC_DIR/install-manifest.txt" ]; then
  cp "$SRC_DIR/install-manifest.txt" "$MANIFEST"
else
  curl -fsSL "$REPO_RAW/install-manifest.txt" -o "$MANIFEST" || {
    echo "could not fetch install-manifest.txt from $REPO_RAW"; exit 1; }
fi
FILES="$(grep -v '^#' "$MANIFEST" | grep -v '^[[:space:]]*$')"

for f in $FILES; do
  mkdir -p "$APP/$(dirname "$f")"
  if [ -f "$SRC_DIR/$f" ]; then
    # never clobber an existing config
    if [ "$f" = "config.yaml" ] && [ -f "$APP/config.yaml" ]; then continue; fi
    cp "$SRC_DIR/$f" "$APP/$f"
  else
    if [ "$f" = "config.yaml" ] && [ -f "$APP/config.yaml" ]; then continue; fi
    curl -fsSL "$REPO_RAW/$f" -o "$APP/$f"
  fi
done

# --- launcher ----------------------------------------------------------------
cat > "$BIN_DIR/keyremap" <<EOF
#!/bin/sh
exec "$PY" "$APP/remap.py" "\$@"
EOF
chmod +x "$BIN_DIR/keyremap"

say "installed: keyremap -> $BIN_DIR/keyremap"
say "app:       $APP"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say ""
     say "NOTE: $BIN_DIR is not on your PATH. Add this to your shell profile:"
     say "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
say ""
say "next:"
say "  keyremap doctor    # what this machine still needs"
say "  keyremap           # open the control room (TUI)"
say "  keyremap gui       # desktop app"
say ""
say "uninstall: rm -rf '$APP' '$BIN_DIR/keyremap'"
