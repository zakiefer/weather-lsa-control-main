#!/usr/bin/env bash
# Usage: scripts/ops/sysinstall.sh <tool> [<tool> ...]
# Detect OS/package manager and install if missing. Exits 0 if already installed.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <tool> [<tool> ...]" >&2
  exit 2
fi

os="$(uname -s || echo Unknown)"
have() { command -v "$1" >/dev/null 2>&1; }

# Ensure sudo cached (user will type password once)
if [[ "$os" != "Darwin" && "$os" != "Linux" ]]; then
  echo "[sysinstall] This script supports macOS/Linux. Use winget/choco on Windows."
else
  if command -v sudo >/dev/null 2>&1; then
    sudo -v || true
  fi
fi

install_brew()   { brew install "$@"; }
install_apt()    { sudo apt-get update -y && sudo apt-get install -y "$@"; }
install_dnf()    { sudo dnf install -y "$@"; }
install_pacman() { sudo pacman -S --noconfirm "$@"; }

pm=""
if [[ "$os" == "Darwin" ]] && have brew; then
  pm="brew"
elif [[ "$os" == "Linux" ]]; then
  if have apt-get; then pm="apt"
  elif have dnf; then pm="dnf"
  elif have pacman; then pm="pacman"
  fi
fi

if [[ -z "$pm" ]]; then
  echo "[sysinstall] No supported package manager detected (brew/apt/dnf/pacman). Install one and retry." >&2
  exit 1
fi

for tool in "$@"; do
  if have "$tool"; then
    echo "[sysinstall] $tool already present"
    continue
  fi
  echo "[sysinstall] Installing $tool via $pm ..."
  case "$pm" in
    brew)   install_brew "$tool" ;;
    apt)    install_apt "$tool" ;;
    dnf)    install_dnf "$tool" ;;
    pacman) install_pacman "$tool" ;;
  esac
done

echo "[sysinstall] Done."
