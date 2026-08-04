#!/usr/bin/env bash
# AI Quant Platform — 一键启动后端 + 前端（本地开发）
#
# 用法：
#   ./start.sh          前台启动（日志实时输出，Ctrl-C 停止全部）
#   ./start.sh -d       后台启动（detached，用 ./stop.sh 停止）
#
# 前置：backend/.venv 已装依赖、frontend/node_modules 已装依赖、backend/.env 已配置。
# 后端 http://localhost:8000，前端 http://localhost:5173

set -euo pipefail
cd "$(dirname "$0")"

DETACHED=false
[[ "${1:-}" == "-d" ]] && DETACHED=true

echo "=========================================="
echo "  AI Quant Platform 启动"
echo "=========================================="

# ---- 检查依赖 ----
if [[ ! -d backend/.venv ]]; then
    echo "❌ backend/.venv 不存在，请先创建虚拟环境并安装依赖"
    exit 1
fi
if [[ ! -d frontend/node_modules ]]; then
    echo "❌ frontend/node_modules 不存在，请先在 frontend/ 下 npm install"
    exit 1
fi
if [[ ! -f backend/.env ]]; then
    echo "❌ backend/.env 不存在，请复制 backend/.env.example 并填写配置"
    exit 1
fi

mkdir -p logs

# ---- 启动后端 ----
# 必须在 backend/ 下启动：config.py 的 env_file=".env" 是相对 cwd 的路径，
# pydantic-settings 会从 cwd 找 .env；在项目根跑会找不到 backend/.env。
echo "▶ 启动后端 (uvicorn, :8000) ..."
if $DETACHED; then
    (cd backend && nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        > ../logs/backend.log 2>&1 &)
    pgrep -f "uvicorn app.main:app" > logs/backend.pid || true
    echo "  后端已启动，日志 logs/backend.log"
else
    (cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
        > ../logs/backend.log 2>&1 &)
    pgrep -f "uvicorn app.main:app" > logs/backend.pid || true
fi

# 等后端起来
sleep 2
if ! kill -0 "$(cat logs/backend.pid)" 2>/dev/null; then
    echo "❌ 后端启动失败，查看 logs/backend.log"
    cat logs/backend.log | tail -20
    exit 1
fi
echo "  ✅ 后端已启动: http://localhost:8000  (健康检查: /api/v1/health)"

# ---- 启动前端 ----
echo "▶ 启动前端 (vite, :5173) ..."
cd frontend
if $DETACHED; then
    nohup npm run dev > ../logs/frontend.log 2>&1 &
    echo $! > ../logs/frontend.pid
else
    npm run dev > ../logs/frontend.log 2>&1 &
    echo $! > ../logs/frontend.pid
fi
cd ..
echo "  前端 PID=$(cat logs/frontend.pid)，日志 logs/frontend.log"

echo ""
echo "=========================================="
if $DETACHED; then
    echo "  ✅ 平台已后台启动"
    echo "  前端: http://localhost:5173"
    echo "  后端: http://localhost:8000"
    echo "  停止: ./stop.sh"
    echo "  日志: tail -f logs/backend.log | logs/frontend.log"
else
    echo "  ✅ 平台已启动（前台模式）"
    echo "  前端: http://localhost:5173"
    echo "  后端: http://localhost:8000"
    echo "  按 Ctrl-C 停止全部，或另开终端运行 ./stop.sh"
    echo ""
    echo "  实时日志（Ctrl-C 退出跟踪，不停止服务）:"
    echo "  --- 后端日志 ---"
    tail -f logs/backend.log &
    TAIL_PID=$!
    trap "kill $TAIL_PID 2>/dev/null; ./stop.sh" INT TERM
    wait $TAIL_PID
fi
echo "=========================================="
