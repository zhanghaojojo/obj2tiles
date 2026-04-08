#!/bin/bash
# OBJ to 3D Tiles 转换服务 - 启动脚本
# 端口: 1986 | 纯 Python 技术栈（无 Node.js）

set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.server.pid"
PORT=1986

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OBJ to 3D Tiles 转换服务${NC}"
echo -e "${GREEN}========================================${NC}"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo -e "${YELLOW}服务已在运行 (PID: $OLD_PID, 端口: $PORT)${NC}"
        echo -e "如需重启，请先执行 ./stop.sh"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# ====== 1/3 KTX-Software (toktx) ======
KTX_DIR="$APP_DIR/ktx"
if [ ! -d "$KTX_DIR" ]; then
    KTX_TAR=$(find "$APP_DIR/vendor" -name "KTX-Software-*-Linux-*.tar.bz2" 2>/dev/null | head -1)
    if [ -n "$KTX_TAR" ]; then
        echo -e "${YELLOW}[1/3] 解压 KTX-Software...${NC}"
        mkdir -p "$KTX_DIR"
        python3 -c "
import tarfile, os, sys
with tarfile.open(sys.argv[1], 'r:bz2') as t:
    for m in t.getmembers():
        parts = m.name.split('/', 1)
        if len(parts) > 1:
            m.name = parts[1]
        else:
            continue
        t.extract(m, sys.argv[2])
" "$KTX_TAR" "$KTX_DIR"
    else
        echo -e "${YELLOW}[1/3] 未找到 KTX-Software 包，KTX2 压缩将不可用${NC}"
    fi
else
    echo -e "${GREEN}[1/3] KTX-Software 已就绪${NC}"
fi
if [ -d "$KTX_DIR/bin" ]; then
    export PATH="$KTX_DIR/bin:$PATH"
    export LD_LIBRARY_PATH="$KTX_DIR/lib:${LD_LIBRARY_PATH:-}"
fi

# ====== 2/3 Obj2Tiles ======
TOOLS_DIR="$APP_DIR/tools"
mkdir -p "$TOOLS_DIR"
if [ -f "$TOOLS_DIR/Obj2Tiles" ]; then
    chmod +x "$TOOLS_DIR/Obj2Tiles"
    echo -e "${GREEN}[2/3] Obj2Tiles 已就绪${NC}"
else
    echo -e "${RED}[2/3] 警告: 未找到 Obj2Tiles，请放入 $TOOLS_DIR/ 目录${NC}"
fi

# ====== 3/3 Python 虚拟环境 ======
VENV_DIR="$APP_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}[3/3] 创建 Python 虚拟环境...${NC}"
    python3 -m venv "$VENV_DIR"
    echo -e "  安装依赖包（离线模式）..."
    # 检测 Python 版本，选择对应的 wheels 目录
    PY_VER=$("$VENV_DIR/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo -e "  Python 版本: $PY_VER"
    if [ "$(echo "$PY_VER" | cut -d. -f2)" -le 8 ]; then
        WHEELS_DIR="$APP_DIR/vendor/python37"
        echo -e "  使用 Python 3.7/3.8 兼容包"
    else
        WHEELS_DIR="$APP_DIR/vendor/python"
    fi
    "$VENV_DIR/bin/pip" install --no-index --find-links="$WHEELS_DIR" \
        flask flask-cors Pillow numpy DracoPy pyproj certifi \
        importlib-metadata zipp typing-extensions markupsafe \
        click itsdangerous jinja2 blinker werkzeug 2>&1 | tail -5
    echo -e "${GREEN}  依赖安装完成${NC}"
else
    echo -e "${GREEN}[3/3] Python 环境已就绪${NC}"
fi

# ====== 启动服务 ======
echo -e "${YELLOW}启动服务 (端口: $PORT)...${NC}"
mkdir -p "$APP_DIR/uploads" "$APP_DIR/output"

cd "$APP_DIR"
nohup "$VENV_DIR/bin/python" app.py > "$APP_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

sleep 2
if kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  服务启动成功!${NC}"
    echo -e "${GREEN}  地址: http://localhost:$PORT${NC}"
    echo -e "${GREEN}  PID:  $SERVER_PID${NC}"
    echo -e "${GREEN}  日志: $APP_DIR/server.log${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo -e "${RED}服务启动失败，请查看日志:${NC}"
    cat "$APP_DIR/server.log"
    rm -f "$PID_FILE"
    exit 1
fi
