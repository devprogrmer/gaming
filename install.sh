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

# Fail loudly if something already occupies the launcher path as a directory.
# This is exactly what produced the real-world "-bash: ./gaming: Is a directory"
# failure: a stale/partial install or a checkout where the src/gaming/ package
# was extracted alongside leaves a 'gaming' directory here, the `cat >` redirect
# below fails with "Is a directory", and the user is left with no launcher.
if [ -d "${LAUNCHER}" ]; then
    echo "error: a directory named 'gaming' already exists here:" >&2
    echo "    ${LAUNCHER}" >&2
    echo "It is blocking creation of the 'gaming' launcher script." >&2
    echo "Remove that directory if it is not needed, e.g.:" >&2
    echo "    rm -rf '${LAUNCHER}'" >&2
    echo "or run this installer from a clean, dedicated directory that does" >&2
    echo "not already contain a 'gaming' file or folder, then re-run it." >&2
    exit 1
fi

if ! cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
# Auto-generated launcher for the gaming interactive tool.
exec "${VENV_PY}" -m gaming "\$@"
EOF
then
    echo "error: could not write the launcher script at:" >&2
    echo "    ${LAUNCHER}" >&2
    echo "Check directory permissions and available disk space, then re-run." >&2
    exit 1
fi
chmod +x "${LAUNCHER}"
echo "Created launcher: ${LAUNCHER}"

if [ "${LINK_USER_BIN}" -eq 1 ]; then
    mkdir -p "${HOME}/.local/bin"
    ln -sf "${LAUNCHER}" "${HOME}/.local/bin/gaming"
    echo "Linked launcher into ${HOME}/.local/bin/gaming"
    echo "(Ensure ~/.local/bin is on your PATH.)"
fi

# ---- post-install self-check ---------------------------------------------
# Run the freshly created launcher so a broken install is caught NOW rather than
# later when the user first tries `./gaming web`.
echo "Verifying the launcher ..."
if ! SELFCHECK_OUT="$("${LAUNCHER}" --version 2>&1)"; then
    echo "error: the 'gaming' launcher was created but does not run:" >&2
    echo "${SELFCHECK_OUT}" >&2
    echo "The installation is incomplete — please report this output." >&2
    exit 1
fi
echo "Launcher OK: ${SELFCHECK_OUT}"

echo
echo "Installation complete."
echo "Start the interactive tool with:"
echo "    ${LAUNCHER}"
echo "or, if you linked it:  gaming"
