#!/bin/bash
# OBJ to 3D Tiles 转换服务 - 启动脚本
# 端口: 1986

set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/.server.pid"
PORT=1986

# 颜色输出
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

# ====== 1. Node.js 便携版 ======
NODE_DIR="$APP_DIR/node"
if [ ! -d "$NODE_DIR" ]; then
    echo -e "${YELLOW}[1/4] 解压 Node.js 便携版...${NC}"
    NODE_TAR=$(find "$APP_DIR/vendor" -name "node-*-linux-x64.tar.xz" 2>/dev/null | head -1)
    if [ -n "$NODE_TAR" ]; then
        mkdir -p "$NODE_DIR"
        tar -xJf "$NODE_TAR" -C "$NODE_DIR" --strip-components=1
        echo -e "  Node.js 已解压到 $NODE_DIR"
    else
        echo -e "  ${YELLOW}未找到 Node.js 包，后处理功能将不可用${NC}"
    fi
else
    echo -e "${GREEN}[1/4] Node.js 已就绪${NC}"
fi

# 设置 Node.js 和 gltf-transform PATH
if [ -d "$NODE_DIR/bin" ]; then
    export PATH="$NODE_DIR/bin:$PATH"
fi
GLTF_BIN="$APP_DIR/vendor/gltf-transform/node_modules/.bin"
if [ -d "$GLTF_BIN" ]; then
    export PATH="$GLTF_BIN:$PATH"
fi

# ====== 2. Obj2Tiles 工具 ======
TOOLS_DIR="$APP_DIR/tools"
mkdir -p "$TOOLS_DIR"
if [ ! -f "$TOOLS_DIR/Obj2Tiles" ]; then
    # 从 tools_linux 复制
    if [ -f "$APP_DIR/tools_linux/Obj2Tiles" ]; then
        cp "$APP_DIR/tools_linux/Obj2Tiles" "$TOOLS_DIR/Obj2Tiles"
        chmod +x "$TOOLS_DIR/Obj2Tiles"
        echo -e "${GREEN}[2/4] Obj2Tiles 已部署${NC}"
    else
        echo -e "${RED}[2/4] 警告: 未找到 Obj2Tiles Linux 二进制文件${NC}"
        echo -e "  请将 Obj2Tiles 放入 $TOOLS_DIR/ 目录"
    fi
else
    chmod +x "$TOOLS_DIR/Obj2Tiles"
    echo -e "${GREEN}[2/4] Obj2Tiles 已就绪${NC}"
fi

# ====== 3. Python 虚拟环境 ======
VENV_DIR="$APP_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}[3/4] 创建 Python 虚拟环境...${NC}"
    python3 -m venv "$VENV_DIR"
    echo -e "  安装依赖包（离线模式）..."
    "$VENV_DIR/bin/pip" install --no-index --find-links="$APP_DIR/vendor/python" flask flask-cors 2>&1 | tail -1
    echo -e "${GREEN}  依赖安装完成${NC}"
else
    echo -e "${GREEN}[3/4] Python 环境已就绪${NC}"
fi

# ====== 4. 启动服务 ======
echo -e "${YELLOW}[4/4] 启动服务 (端口: $PORT)...${NC}"
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
    echo -e "${RED}服务启动失败，请查看日志: $APP_DIR/server.log${NC}"
    cat "$APP_DIR/server.log"
    rm -f "$PID_FILE"
    exit 1
fi
