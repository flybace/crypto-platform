#!/usr/bin/env bash
# crypto-platform 一键安装脚本（Linux / macOS）
#
# 用法（一条命令完成 拉取代码 + 安装启动）：
#   curl -fsSL https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh | bash
#
# 或在已克隆的仓库里直接运行：
#   ./install.sh
#
# 私有仓库：先在 GitHub 生成只读 token（Fine-grained，Contents: Read-only，
# 仅勾选本仓库），然后：
#   export GITHUB_TOKEN=你的token
#   curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
#     https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh | bash
#
# 重复运行 = 更新：git pull + 重新构建启动。
# 环境变量（可选）：
#   CRYPTO_INSTALL_DIR    安装目录（默认 ~/crypto-platform）
#   CRYPTO_ADMIN_USERNAME 管理员用户名（默认 flybace）
#   CRYPTO_ADMIN_PASSWORD 管理员密码（默认随机生成并打印）
#   CRYPTO_BIND_ADDRESS   监听地址（默认 127.0.0.1）
set -euo pipefail

REPO_URL="https://github.com/flybace/crypto-platform.git"
RAW_URL="https://raw.githubusercontent.com/flybace/crypto-platform/main/install.sh"
INSTALL_DIR="${CRYPTO_INSTALL_DIR:-$HOME/crypto-platform}"
ADMIN_USER="${CRYPTO_ADMIN_USERNAME:-flybace}"
BIND_ADDRESS="${CRYPTO_BIND_ADDRESS:-127.0.0.1}"

log() { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[install] 错误：\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 1. 自举：不在仓库里就先拉代码 ----------
git_clone() {
  # 私有仓库用 token 鉴权；extraHeader 不会把 token 存进 .git/config
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -c "http.extraHeader=Authorization: Bearer ${GITHUB_TOKEN}" clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  else
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
}
git_pull() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -C "$INSTALL_DIR" -c "http.extraHeader=Authorization: Bearer ${GITHUB_TOKEN}" pull --ff-only
  else
    git -C "$INSTALL_DIR" pull --ff-only
  fi
}
if [ ! -f "./compose.yaml" ] || [ ! -d "./.git" ]; then
  command -v git >/dev/null 2>&1 || die "需要先安装 git：https://git-scm.com/downloads"
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    log "拉取代码到 $INSTALL_DIR ..."
    git_clone || die "git clone 失败。私有仓库请先 export GITHUB_TOKEN=只读token 再运行（见脚本头部说明）"
  else
    log "更新代码 ..."
    git_pull || log "git pull 失败，继续用本地代码安装"
  fi
  # 重新执行仓库内的正式脚本（避免管道中的副本与仓库版本不一致）
  if [ "$0" != "$INSTALL_DIR/install.sh" ]; then
    exec bash "$INSTALL_DIR/install.sh"
  fi
  cd "$INSTALL_DIR"
else
  # 已在仓库内：顺手更新到最新
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git -c "http.extraHeader=Authorization: Bearer ${GITHUB_TOKEN}" pull --ff-only 2>/dev/null || true
  else
    git pull --ff-only 2>/dev/null || true
  fi
fi

cd "$(dirname "$0")"
log "安装目录：$(pwd)"

# ---------- 2. 检查 Docker ----------
command -v docker >/dev/null 2>&1 || die "需要先安装 Docker：https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "Docker Compose 插件不可用，请升级 Docker 到 20.10+"

# ---------- 3. 生成 .env（不存在才生成，不覆盖已有配置） ----------
rand() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}"; }
if [ ! -f ".env" ]; then
  log "生成 .env 配置 ..."
  ADMIN_PASS="${CRYPTO_ADMIN_PASSWORD:-$(rand 16)}"
  {
    echo "# crypto-platform 本地配置（由 install.sh 生成，请勿提交）"
    echo "CRYPTO_ADMIN_USERNAME=$ADMIN_USER"
    echo "CRYPTO_ADMIN_PASSWORD=$ADMIN_PASS"
    echo "CRYPTO_SESSION_SECRET=$(rand 32)"
    echo "CRYPTO_POSTGRES_PASSWORD=$(rand 24)"
    echo "CRYPTO_BIND_ADDRESS=$BIND_ADDRESS"
    echo "CRYPTO_EXECUTION_MODE=DISABLED"
  } > .env
  chmod 600 .env
  GENERATED_PASS="$ADMIN_PASS"
else
  log ".env 已存在，保留现有配置"
  GENERATED_PASS=""
fi

# ---------- 4. 构建并启动 ----------
log "构建并启动服务（首次需要几分钟） ..."
docker compose up -d --build || die "docker compose 启动失败"

# ---------- 5. 等待后端健康 ----------
log "等待后端就绪 ..."
BACKEND_PORT="${CRYPTO_BACKEND_PORT:-8290}"
for i in $(seq 1 36); do
  if curl -sf --max-time 5 "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    log "后端健康检查通过"
    break
  fi
  if [ "$i" -eq 36 ]; then
    die "后端 3 分钟内未就绪，请运行 docker compose logs backend 查看日志"
  fi
  sleep 5
done

# ---------- 6. 打印访问信息 ----------
# shellcheck disable=SC1091
set -a; . ./.env; set +a
FRONTEND_PORT="${CRYPTO_FRONTEND_PORT:-4191}"
echo ""
echo "=============================================="
echo "  crypto-platform 安装完成"
echo "  访问地址： http://127.0.0.1:${FRONTEND_PORT}"
echo "  用户名：   ${CRYPTO_ADMIN_USERNAME:-flybace}"
if [ -n "${GENERATED_PASS:-}" ]; then
  echo "  密码：     ${GENERATED_PASS}  ← 请妥善保存，仅显示一次"
else
  echo "  密码：     （沿用 .env 中的已有密码）"
fi
echo "  执行模式： DISABLED（模拟盘安全，真实下单默认关闭）"
echo "=============================================="
echo ""
echo "常用命令："
echo "  查看日志： docker compose logs -f"
echo "  更新版本： ./install.sh  （重复运行即可）"
echo "  停止服务： docker compose stop"
echo "  卸载：     ./uninstall.sh"
