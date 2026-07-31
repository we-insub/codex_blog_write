#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MATO_RUNTIME_ROOT=${MATO_BLOG_RUNTIME_HOME:-"$HOME/.googleblog/mato-blog-codex"}

if [ -x "$MATO_RUNTIME_ROOT/.venv/bin/python" ] \
    && "$MATO_RUNTIME_ROOT/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    exec "$MATO_RUNTIME_ROOT/.venv/bin/python" "$SCRIPT_DIR/bootstrap.py" "$@"
fi

for MATO_PYTHON in \
    python3.14 python3.13 python3.12 python3.11 python3.10 \
    /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 \
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
    python3 python; do
    if command -v "$MATO_PYTHON" >/dev/null 2>&1 \
        && "$MATO_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        exec "$MATO_PYTHON" "$SCRIPT_DIR/bootstrap.py" "$@"
    fi
done

echo "Python 3.10 이상을 찾지 못했습니다. Python을 설치한 뒤 다시 실행해 주세요." >&2
exit 2
