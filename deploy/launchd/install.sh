#!/bin/bash
# 威达内网演示 · 安装 3 个 LaunchAgent（MySQL / Redis / 后端）
#
# 由用户本人执行：  bash deploy/launchd/install.sh
#
# 做的事：
#   1. 构建前端产物（pnpm build → frontend/web/dist），由后端 6100 直接托管
#   2. 把仓库里的模板替换 __HOME__ / __REPO__ 后装到 ~/Library/LaunchAgents/
#   3. 停掉当前手动起的 mysqld / redis-server / 后端 / 残留的旧 vite（否则端口冲突）
#   4. launchctl bootstrap 三个服务（RunAtLoad 开机自启 + KeepAlive 崩了自动拉起）
#
# **顺序是有意的：构建在第 1 步、bootout 在第 2 步。** 构建（含前端类型门禁）失败时脚本
# 直接退出，此时旧的服务**还在跑**，不会把演示环境拆成半死不活——这个顺序是被真实事故逼出来的：
# 先 bootout 再 build 的版本，在一次 `vue-tsc: node: not found` 上把四个服务全卸了、然后中止。
#
# **前端不再是常驻服务**：以前跑 vite dev（6110），同事每次访问都要现编译 425 个模块，
# 叠加后端冷启动，首屏经常卡到超时。现在改成构建成静态产物、由后端一个端口同时提供页面
# 与接口，同事只访问 http://<主机>:6100/web。代价是**改完前端要重新 build**——
# 重跑本脚本即可（第 1 步每次都会重建产物）。
#
# 幂等：重复执行会先 bootout 再 bootstrap。
# 卸载：bash deploy/launchd/uninstall.sh

set -euo pipefail

HOME_DIR="$HOME"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_DIR="$REPO_DIR/deploy/launchd"
DEST_DIR="$HOME_DIR/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

LABELS=(com.weida.mysql com.weida.redis com.weida.backend)

# 前端已并入后端托管，com.weida.frontend（vite dev，6110）不再装载。
# 但它可能还留在 ~/Library/LaunchAgents 里被 launchd 记着，下面照旧 bootout + 杀端口清掉，
# 否则那台老 vite 会一直占着 6110 并对外服务**过期**的页面。
LEGACY_LABELS=(com.weida.frontend)

echo "==> 仓库路径: $REPO_DIR"
echo "==> 安装到  : $DEST_DIR"
echo

if [ ! -d "$REPO_DIR/backend/.venv" ]; then
  echo "!! 找不到 $REPO_DIR/backend/.venv，请先建好后端虚拟环境" >&2
  exit 1
fi
mkdir -p "$DEST_DIR" "$HOME_DIR/Library/Logs"

# --- 1. 构建前端产物（后端 6100 直接托管 dist）-------------------------------
# 幂等：每次都重建。`vue-tsc --noEmit` 顺带当一次前端类型门禁，类型错会在这里中止。
#
# **必须先把 node 挂上 PATH**（与 plist 里那条同样的理由：launchd / 非交互 shell 不读
# `~/.zshrc`）。只调 `pnpm` 的绝对路径不够：`pnpm build` 去跑 `vue-tsc`，后者是
# `node_modules/.bin/` 下的 sh 包装脚本，内部是 `exec node`——PATH 里没有 node 就是
# `vue-tsc: line 16: exec: node: not found`，构建直接死。
# 另外系统里可能还有别的 node（本机 PATH 上就没挂 node，用户交互 shell 里另有 20.11.1，
# 低于 engines 要求的 20.19.0），所以**前置**而不是追加，确保用 `~/.local/node` 这一份。
export PATH="$HOME_DIR/.local/node/bin:$PATH"
PNPM="$HOME_DIR/.local/node/bin/pnpm"
if [ ! -x "$PNPM" ]; then
  echo "!! 找不到 $PNPM，请先安装 Node/pnpm" >&2
  exit 1
fi
echo "==> node $(node -v) / pnpm $(pnpm -v)"
echo "==> 构建前端（pnpm build，约 40 秒）"
( cd "$REPO_DIR/frontend/web" && "$PNPM" build )
if [ ! -f "$REPO_DIR/frontend/web/dist/index.html" ]; then
  echo "!! 构建产物缺失：$REPO_DIR/frontend/web/dist/index.html" >&2
  exit 1
fi

# --- 2. 先摘掉旧的 launchd 托管（重复执行时）--------------------------------
# 放在构建**之后**：走到这里就说明产物是好的，可以放心拆掉旧部署。
echo "==> 摘掉旧的 launchd 托管"
for label in "${LABELS[@]}" "${LEGACY_LABELS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null && echo "    $label 已卸下" || true
done
# 模板文件本身也删掉，免得下次开机 launchd 又把它捡回来
for label in "${LEGACY_LABELS[@]}"; do
  rm -f "$DEST_DIR/$label.plist"
done

# --- 3. 停掉占着端口的手动进程，避免冲突 -------------------------------------
# 按端口杀，不按命令行关键字：后端实际命令行是相对路径 `main.py run --env=dev`，
# 关键字匹配不可靠；且 `pkill -f` 会匹配到自己所在 shell 的命令行（自杀）。
kill_port() {
  local port="$1" label="$2" pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -z "$pids" ]; then return 0; fi
  echo "$pids" | xargs kill 2>/dev/null || true
  # 变量一律用 ${} 包住：bash 在 UTF-8 locale 下会把紧跟 $var 的中文字符
  # 当成变量名的一部分（$label（ → 变量名 `label（端口`），set -u 下直接报错退出。
  echo "    ${label}（端口 ${port}）已发停止信号"
}

echo "==> 停止当前手动启动的进程"
kill_port 3306 mysqld
kill_port 6379 redis-server
kill_port 6100 后端
kill_port 6110 残留的vite

# 等端口真正释放（最多 15 秒），否则 bootstrap 起来会撞端口
for i in $(seq 1 15); do
  busy=0
  for p in 3306 6379 6100 6110; do
    lsof -nP -iTCP:"$p" -sTCP:LISTEN -t >/dev/null 2>&1 && busy=1
  done
  if [ "$busy" -eq 0 ]; then break; fi
  sleep 1
done
if [ "$busy" -eq 1 ]; then
  echo "!! 仍有端口被占，继续装载（可能冲突）"
fi

# --- 4. 渲染模板并装载 ------------------------------------------------------
for label in "${LABELS[@]}"; do
  src="$SRC_DIR/$label.plist"
  dst="$DEST_DIR/$label.plist"

  if [ ! -f "$src" ]; then
    echo "!! 缺模板: $src" >&2
    exit 1
  fi

  sed -e "s|__HOME__|$HOME_DIR|g" -e "s|__REPO__|$REPO_DIR|g" "$src" > "$dst"
  chmod 644 "$dst"

  if launchctl bootstrap "$DOMAIN" "$dst" 2>/dev/null; then
    echo "==> $label 已装载"
  else
    # `bootout` 只是发信号就返回，mysqld 还要 3~4 秒才真正退完。紧接着 bootstrap 会撞上
    # 「上一个实例还在拆」，launchd 只说一句 I/O error（被下面的 2>/dev/null 吞掉），
    # 而 kickstart 也救不了——那个 label 此刻还没注册进域。2026-10-12 就这样静默漏掉了
    # MySQL：3306 没人听、后端在 KeepAlive 里空转，脚本却只报一句「装载失败」。
    # 等一拍再试一次，顺序仍与原先一致（bootstrap → kickstart）。
    sleep 3
    if launchctl bootstrap "$DOMAIN" "$dst" 2>/dev/null; then
      echo "==> $label 已装载（重试）"
    elif launchctl kickstart -k "$DOMAIN/$label" 2>/dev/null; then
      # 已经加载过（重复执行本脚本的常见情形）：改用 kickstart 重新拉起
      echo "==> $label 已重新拉起"
    else
      echo "!! $label 装载失败，检查 $dst" >&2
    fi
  fi
done

# --- 5. 等待并报告 ----------------------------------------------------------
echo
echo "==> 等待服务起来（最多 60 秒）"
for i in $(seq 1 30); do
  ok=0
  nc -z 127.0.0.1 3306 2>/dev/null && ok=$((ok+1))
  nc -z 127.0.0.1 6379 2>/dev/null && ok=$((ok+1))
  nc -z 127.0.0.1 6100 2>/dev/null && ok=$((ok+1))
  [ "$ok" -eq 3 ] && break
  sleep 2
done

echo
echo "==> 端口状态"
lsof -nP -iTCP:3306 -iTCP:6379 -iTCP:6100 -sTCP:LISTEN 2>/dev/null || echo "  （有端口还没起来，看日志）"

# 自检：真的能从 6100 拿到前端首页，才算这条链路是通的
echo
if curl -fsS -o /dev/null "http://127.0.0.1:6100/web/"; then
  echo "==> 前端首页自检：http://127.0.0.1:6100/web/ 可访问 ✓"
else
  echo "!! 前端首页取不到，检查 ~/Library/Logs/weida-backend.log" >&2
fi

echo
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
LOCAL_NAME="$(scutil --get LocalHostName 2>/dev/null || true)"
echo "==> 同事访问入口（只这一个端口）"
[ -n "$LOCAL_NAME" ] && echo "    http://$LOCAL_NAME.local:6100/web   （推荐，不随 IP 漂移）"
[ -n "$LAN_IP" ]     && echo "    http://$LAN_IP:6100/web"
echo
echo "==> 日志：~/Library/Logs/weida-{mysql,redis,backend}.log"
echo
echo "==> 改了前端代码之后：重跑本脚本（会重新 pnpm build），或手动"
echo "    cd frontend/web && pnpm build && launchctl kickstart -k gui/$(id -u)/com.weida.backend"
echo
echo "睡眠设置核对（应为 sleep 0 / displaysleep 0）："
echo "    pmset -g | grep -E 'sleep|displaysleep'"
