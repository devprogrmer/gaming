#!/usr/bin/env bash
#
# gaming — in-place updater (Linux, macOS, Git Bash, WSL).
#
# Upgrades an existing installation to a new release *without* deleting the
# previous one first. It reuses the virtual environment created by install.sh,
# optionally pulls the latest source with git, and reinstalls the package with
# `pip install --upgrade`. Your scan history, settings, and custom ranges live
# outside the install tree and are never touched.
#
# Usage:
#   ./update.sh                    # git pull (if a checkout) + upgrade in place
#   ./update.sh --no-pull          # reinstall the current source without pulling
#   ./update.sh --version v0.1.0   # switch to a specific release (incl. older)
#   ./update.sh --list             # list the available release versions
#
# Prerequisite: the project was already installed with ./install.sh (a .venv
# exists in this directory).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
PASS_ARGS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-pull) PASS_ARGS+=("--no-pull") ;;
        --list) PASS_ARGS+=("--list") ;;
        --version)
            shift
            [ "$#" -gt 0 ] || { echo "error: --version requires a release, e.g. v0.1.0" >&2; exit 2; }
            PASS_ARGS+=("--version" "$1")
            ;;
        --version=*) PASS_ARGS+=("--version" "${1#*=}") ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

# ---- locate the existing virtual environment -----------------------------
if [ -x "${VENV_DIR}/bin/python" ]; then
    VENV_PY="${VENV_DIR}/bin/python"
elif [ -x "${VENV_DIR}/Scripts/python.exe" ]; then
    VENV_PY="${VENV_DIR}/Scripts/python.exe"
else
    echo "error: no virtual environment found in ${VENV_DIR}." >&2
    echo "Run ./install.sh first to create the initial installation." >&2
    exit 1
fi

echo "Using existing installation: ${VENV_PY}"

# ---- update in place via the package's own updater -----------------------
# Pin the source to this directory so the updater upgrades the right checkout.
exec "${VENV_PY}" -m gaming update --source "${REPO_DIR}" "${PASS_ARGS[@]}"
