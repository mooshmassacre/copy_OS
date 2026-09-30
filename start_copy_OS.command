#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 @moosmassacre <mooshmassacre@mail.com>

pause_on_error() {
    if [ -t 0 ]; then
        printf '\nPress Enter to close...'
        IFS= read -r unused
    fi
}

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)" || {
    printf 'Could not open the launcher folder.\n'
    pause_on_error
    exit 1
}
cd -- "$SCRIPT_DIR" || exit 1

if [ ! -f "$SCRIPT_DIR/copy_OS.py" ]; then
    printf 'Could not find copy_OS.py beside this launcher.\nExtract the complete ZIP and keep the launcher, script and config.json together.\n'
    pause_on_error
    exit 1
fi

# Finder may provide a different PATH from an interactive Terminal session.
COPY_OS_PYTHON=''
for candidate in "$(command -v python3 2>/dev/null)" \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /usr/bin/python3; do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
        COPY_OS_PYTHON="$candidate"
        break
    fi
done

if [ -z "$COPY_OS_PYTHON" ]; then
    printf 'Python 3.9 or newer was not found.\nInstall Python from https://www.python.org/downloads/macos/\nThen open this launcher again.\n'
    pause_on_error
    exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
"$COPY_OS_PYTHON" "$SCRIPT_DIR/copy_OS.py"
COPY_OS_EXIT=$?
if [ "$COPY_OS_EXIT" -ne 0 ]; then
    printf '\nCOPY_OS exited with code %s. Review the error above.\n' "$COPY_OS_EXIT"
    pause_on_error
fi
exit "$COPY_OS_EXIT"
