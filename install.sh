#!/usr/bin/env bash
#
# gaming — installer / dependency bootstrap (Linux, macOS, Git Bash, WSL).
#
# Creates an isolated virtual environment, installs the package into it, and
# drops a launcher so you can start the interactive tool by typing `gaming`.
#
# Usage:
#   ./install.sh            # install into ./.venv and create a launcher
#   ./install.sh --user     # also symlink the launcher into ~/.local/bin
#
# The tool itself has no third-party runtime dependencies (standard library
# only), so this bootstrap is fast and works offline once Python is present.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_DIR}/.venv"
LINK_USER_BIN=0

for arg in "$@"; do
    case "$arg" in
        --user) LINK_USER_BIN=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# ---- locate a suitable Python (3.11+) ------------------------------------
find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON="$(find_python || true)"
if [ -z "${PYTHON}" ]; then
    echo "error: Python 3.11+ is required but was not found on PATH." >&2
    echo "Install Python 3.11 or newer and re-run this script." >&2
    exit 1
fi
echo "Using Python: $("${PYTHON}" --version 2>&1) ($(command -v "${PYTHON}"))"

# ---- create the virtual environment --------------------------------------
if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating virtual environment in ${VENV_DIR} ..."
    "${PYTHON}" -m venv "${VENV_DIR}"
fi

# Resolve the venv's python across layouts (bin on POSIX, Scripts on Windows).
if [ -x "${VENV_DIR}/bin/python" ]; then
    VENV_PY="${VENV_DIR}/bin/python"
elif [ -x "${VENV_DIR}/Scripts/python.exe" ]; then
    VENV_PY="${VENV_DIR}/Scripts/python.exe"
else
    echo "error: could not locate the virtual environment's Python." >&2
    exit 1
fi

# ---- install the package --------------------------------------------------
echo "Upgrading pip ..."
"${VENV_PY}" -m pip install --quiet --upgrade pip

echo "Installing gaming ..."
"${VENV_PY}" -m pip install --quiet "${REPO_DIR}"

# ---- create a convenient launcher ----------------------------------------
LAUNCHER="${REPO_DIR}/gaming"
cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
# Auto-generated launcher for the gaming interactive tool.
exec "${VENV_PY}" -m gaming "\$@"
EOF
chmod +x "${LAUNCHER}"
echo "Created launcher: ${LAUNCHER}"

if [ "${LINK_USER_BIN}" -eq 1 ]; then
    mkdir -p "${HOME}/.local/bin"
    ln -sf "${LAUNCHER}" "${HOME}/.local/bin/gaming"
    echo "Linked launcher into ${HOME}/.local/bin/gaming"
    echo "(Ensure ~/.local/bin is on your PATH.)"
fi

echo
echo "Installation complete."
echo "Start the interactive tool with:"
echo "    ${LAUNCHER}"
echo "or, if you linked it:  gaming"
