#!/usr/bin/env bash
set -euo pipefail

# API Key Tester 启动脚本

cd "$(dirname "$0")"

ACTION="${1:-start}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-venv}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-requirements.txt}"

if [ "$ACTION" = "stop" ]; then
    PIDS="$(pgrep -f 'python.*app\.py' || true)"
    if [ -z "$PIDS" ]; then
        echo "未找到正在运行的 app.py 进程。"
        exit 0
    fi

    echo "正在停止以下 app.py 进程："
    ps -fp $PIDS
    kill $PIDS
    exit 0
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "错误：未找到 $PYTHON_BIN，请先安装 Python 3。"
    exit 1
fi

SYSTEM_PYTHON_VERSION="$("$PYTHON_BIN" - <<'PY'
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
)"

if ! "$PYTHON_BIN" - <<'PY'
import sys
min_version = (3, 8, 5)
sys.exit(0 if sys.version_info >= min_version else 1)
PY
then
    echo "错误：当前系统 Python 版本为 ${SYSTEM_PYTHON_VERSION}，本项目需要 Python 3.8.5 或更高版本。"
    exit 1
fi

if [ -x "$VENV_DIR/bin/python" ]; then
    VENV_VERSION="$("$VENV_DIR/bin/python" - <<'PY'
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
)"
    if ! "$VENV_DIR/bin/python" - <<'PY'
import sys
min_version = (3, 8, 5)
sys.exit(0 if sys.version_info >= min_version else 1)
PY
    then
        echo "检测到旧虚拟环境 ${VENV_VERSION}，正在重建：$VENV_DIR"
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "未找到虚拟环境，正在创建：$VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

VENV_PYTHON="$VENV_DIR/bin/python"

VENV_PYTHON_VERSION="$("$VENV_PYTHON" - <<'PY'
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
)"

if ! "$VENV_PYTHON" - <<'PY'
import sys
min_version = (3, 8, 5)
sys.exit(0 if sys.version_info >= min_version else 1)
PY
then
    echo "错误：虚拟环境 Python 版本为 ${VENV_PYTHON_VERSION}，本项目需要 Python 3.8.5 或更高版本。"
    exit 1
fi

if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "检查依赖环境..."
    "$VENV_PYTHON" -m pip install --upgrade pip
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_FILE"
else
    echo "警告：未找到 $REQUIREMENTS_FILE，跳过依赖安装。"
fi

"$VENV_PYTHON" app.py
