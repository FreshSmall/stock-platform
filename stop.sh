#!/usr/bin/env bash
# AI Quant Platform — 一键停止后端 + 前端
#
# 用法：./stop.sh
# 读取 logs/{backend,frontend}.pid 杀进程；若 pid 文件不存在则按端口兜底杀。

set -uo pipefail
cd "$(dirname "$0")"

STOPPED=0

stop_by_pidfile() {
    local name=$1 pidfile=$2
    if [[ -f "$pidfile" ]]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            sleep 1
            # 仍在则强杀
            kill -9 "$pid" 2>/dev/null || true
            echo "✅ 已停止 $name (PID=$pid)"
            STOPPED=1
        else
            echo "  $name 进程已不在 (PID=$pid)"
        fi
        rm -f "$pidfile"
    fi
}

stop_by_port() {
    # macOS / Linux 兼容：按端口找监听进程并杀掉
    local name=$1 port=$2
    local pids
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$pids" | xargs kill -9 2>/dev/null || true
        echo "✅ 已按端口 :$port 停止 $name (PID: $(echo $pids | tr '\n' ' '))"
        STOPPED=1
    fi
}

echo "=========================================="
echo "  AI Quant Platform 停止"
echo "=========================================="

# 优先用 pid 文件
stop_by_pidfile "后端" logs/backend.pid
stop_by_pidfile "前端" logs/frontend.pid

# 兜底：按端口杀（pid 文件丢失 / 进程换号时）
stop_by_port "后端" 8000
stop_by_port "前端" 5173

if [[ $STOPPED -eq 0 ]]; then
    echo "  没有发现运行中的服务（端口 8000 / 5173 无进程）"
fi
echo "=========================================="
