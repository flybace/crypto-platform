#!/usr/bin/env bash
# crypto-platform 卸载脚本
#   ./uninstall.sh        停止并删除容器，保留数据（可重装恢复）
#   ./uninstall.sh --purge 连数据卷一起删除，删干净
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "--purge" ]; then
  read -r -p "确定删除所有数据（数据库、Redis、历史数据）吗？[y/N] " ans
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    docker compose down --volumes
    echo "已卸载并清除数据。"
  else
    echo "已取消。"
  fi
else
  docker compose down
  echo "已停止并删除容器，数据保留在 docker volume 中，重装可恢复。"
  echo "要连数据一起删：./uninstall.sh --purge"
fi
