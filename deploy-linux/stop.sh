#!/bin/bash
# OBJ to 3D Tiles 转换服务 - 停止脚本

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.server.pid"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

if [ ! -f "$PID_FILE" ]; then
    echo -e "${RED}服务未在运行（未找到 PID 文件）${NC}"
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    sleep 1
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID"
    fi
    rm -f "$PID_FILE"
    echo -e "${GREEN}服务已停止 (PID: $PID)${NC}"
else
    rm -f "$PID_FILE"
    echo -e "${RED}服务进程不存在 (PID: $PID)，已清理 PID 文件${NC}"
fi
