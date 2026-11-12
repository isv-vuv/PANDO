#!/usr/bin/env sh

# Portable PANDO launcher for macOS and Linux.
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REQ="$SCRIPT_DIR/requirements.txt"
QGIS_PYTHON=""

printf '%s\n' '======================================================================'
printf '%s\n' ' PANDO - Automated Startup Script'
printf '%s\n\n' '======================================================================'

is_qgis_python() {
    [ -n "$1" ] && [ -x "$1" ] || return 1
    if "$1" -c 'from qgis.PyQt import QtCore, QtGui, QtWidgets; from qgis.core import Qgis; raise SystemExit(0 if Qgis.QGIS_VERSION_INT >= 40000 else 1)' >/dev/null 2>&1; then
        return 0
    fi
    printf '[WARNING] Skipping interpreter without a working QGIS 4 / Qt environment: %s\n' "$1" >&2
    return 1
}

find_qgis_python() {
    if [ -n "${PANDO_QGIS_PYTHON:-}" ] && is_qgis_python "$PANDO_QGIS_PYTHON"; then
        QGIS_PYTHON=$PANDO_QGIS_PYTHON
        return 0
    fi

    case "$(uname -s)" in
        Darwin)
            for candidate in \
                "$HOME"/Applications/QGIS*.app/Contents/MacOS/bin/python3 \
                "$HOME"/Applications/QGIS*.app/Contents/MacOS/python \
                /Applications/QGIS*.app/Contents/MacOS/bin/python3 \
                /Applications/QGIS*.app/Contents/MacOS/python \
                /opt/homebrew/Caskroom/qgis/*/QGIS*.app/Contents/MacOS/bin/python3 \
                /usr/local/Caskroom/qgis/*/QGIS*.app/Contents/MacOS/bin/python3
            do
                if is_qgis_python "$candidate"; then
                    QGIS_PYTHON=$candidate
                    return 0
                fi
            done
            ;;
        Linux)
            for candidate in /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
                if is_qgis_python "$candidate"; then
                    QGIS_PYTHON=$candidate
                    return 0
                fi
            done
            ;;
        *)
            printf '[ERROR] Unsupported operating system: %s\n' "$(uname -s)" >&2
            return 1
            ;;
    esac
    return 1
}

run_as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        printf '%s\n' '[ERROR] This installation requires root privileges or sudo.' >&2
        return 1
    fi
}

install_qgis() {
    case "$(uname -s)" in
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                printf '%s\n' '[ERROR] Homebrew is not installed. Install it first from https://brew.sh/.' >&2
                return 1
            fi
            brew install --cask qgis
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                run_as_root apt-get update && run_as_root apt-get install -y qgis python3-qgis
            elif command -v dnf >/dev/null 2>&1; then
                run_as_root dnf install -y qgis python3-qgis
            elif command -v apk >/dev/null 2>&1; then
                run_as_root apk add qgis py3-qgis
            elif command -v pacman >/dev/null 2>&1; then
                run_as_root pacman -S --needed qgis
            elif command -v zypper >/dev/null 2>&1; then
                run_as_root zypper --non-interactive install qgis python3-qgis
            else
                printf '%s\n' '[ERROR] No supported package manager found (apt, dnf, apk, pacman, zypper).' >&2
                return 1
            fi
            ;;
    esac
}

if ! find_qgis_python; then
    printf '%s\n\n' '[WARNING] A QGIS 4 Python environment was not found.'
    printf '%s\n' 'PANDO can install QGIS with Homebrew or the detected Linux package manager.'
    printf '%s' 'Install QGIS 4 now? (y/N): '
    read -r install_choice
    case "$install_choice" in
        y|Y|yes|YES|Yes)
            if ! install_qgis; then
                printf '%s\n' '[ERROR] QGIS installation failed or was canceled.' >&2
                exit 1
            fi
            if ! find_qgis_python; then
                printf '%s\n' '[ERROR] QGIS was installed, but no QGIS 4 Python environment was found.' >&2
                printf '%s\n' 'Set PANDO_QGIS_PYTHON to the matching Python executable and try again.' >&2
                exit 1
            fi
            ;;
        *)
            printf '%s\n' 'Please install QGIS 4 manually or set PANDO_QGIS_PYTHON.' >&2
            exit 1
            ;;
    esac
fi

printf '[OK] QGIS 4 Python environment found:\n     %s\n\n' "$QGIS_PYTHON"

printf '%s\n' '[1/2] Verifying Python dependencies...'
if ! "$QGIS_PYTHON" "$SCRIPT_DIR/core/app/app_core/check_deps.py" "$REQ"; then
    printf '%s\n' '[ERROR] Error occurred during installation of Python requirements.' >&2
    exit 1
fi

printf '%s\n' '' '[2/2] Starting PANDO Application (MainQt.py)...'
cd "$SCRIPT_DIR" || exit 1
exec "$QGIS_PYTHON" "$SCRIPT_DIR/MainQt.py"
