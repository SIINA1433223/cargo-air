#!/bin/bash
# 威达内网演示 · 卸载全部 LaunchAgent（不动数据、不动 ~/.local 下的服务本体）
#
# 由用户本人执行：  bash deploy/launchd/uninstall.sh

set -euo pipefail

DEST_DIR="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
LABELS=(com.weida.mysql com.weida.redis com.weida.backend)
# 前端 vite 已并入后端托管，但老机器上可能还装着它，一并卸干净
LEGACY_LABELS=(com.weida.frontend)

for label in "${LABELS[@]}" "${LEGACY_LABELS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null && echo "==> $label 已卸载" || echo "    $label 本来就没装载"
  rm -f "$DEST_DIR/$label.plist"
done

echo
echo "==> 已卸载。数据与日志都还在（~/Library/Logs/weida-*.log）。"
echo "    睡眠设置若也要还原：sudo pmset -a sleep 1 displaysleep 10 disablesleep 0"
