
#!/data/data/com.termux/files/usr/bin/bash

# ==========================================
# Termux Alist Bot 部署脚本 (官方源版)
# ==========================================
set -e

# 检测架构 (仅用于 Cloudflared)
ARCH=$(uname -m)
case $ARCH in
    aarch64)
        CF_ARCH="linux-arm64"
        ;;
    arm*)
        CF_ARCH="linux-arm"
        ;;
    x86_64)
        CF_ARCH="linux-amd64"
        ;;
    *)
        echo "❌ 不支持的架构: $ARCH"
        exit 1
        ;;
esac

echo -e "\033[1;36m>>> [1/5] 更新 Termux 基础环境...\033[0m"
# 使用 || true 防止源更新失败导致脚本退出
pkg update -y || true
pkg upgrade -y || true

echo -e "\033[1;36m>>> [2/5] 安装必要依赖...\033[0m"
# ⚡️ 关键修改: 直接安装 alist 包 (Termux 官方源已收录，无需手动下载)
pkg install -y python nodejs aria2 ffmpeg git vim curl wget tar openssl-tool build-essential libffi termux-tools ca-certificates alist

# --- 修复 Termux DNS (解决 Cloudflared 无法解析的问题) ---
# Cloudflared (Go程序) 在 Termux 下经常因为找不到 resolv.conf 而尝试连接 [::1]:53 导致报错
RESOLV_CONF="$PREFIX/etc/resolv.conf"
if [ ! -f "$RESOLV_CONF" ] || [ ! -s "$RESOLV_CONF" ]; then
    echo "🔧 修复 DNS 配置 (创建 $RESOLV_CONF)..."
    mkdir -p "$(dirname "$RESOLV_CONF")"
    echo "nameserver 8.8.8.8" > "$RESOLV_CONF"
    echo "nameserver 1.1.1.1" >> "$RESOLV_CONF"
else
    echo "✅ DNS 配置已存在"
fi

echo -e "\033[1;36m>>> [3/5] 安装 Python 库...\033[0m"
# Termux 禁止使用 pip 升级自身，这里只安装依赖包
if [ -f "bot/requirements.txt" ]; then
    pip install -r bot/requirements.txt
else
    pip install python-telegram-bot requests psutil python-dotenv
fi

echo -e "\033[1;36m>>> [4/5] 安装 PM2 (进程守护)...\033[0m"
if ! command -v pm2 &> /dev/null; then
    npm install -g pm2
else
    echo "PM2 已安装"
fi

# 准备 bin 目录
mkdir -p "$HOME/bin"
export PATH="$HOME/bin:$PATH"

echo -e "\033[1;36m>>> [5/5] 配置核心组件...\033[0m"

# --- 1. 安装 Cloudflared ---
CLOUDFLARED_BIN="$HOME/bin/cloudflared"
if [ ! -f "$CLOUDFLARED_BIN" ]; then
    echo "⬇️ 正在下载 Cloudflared..."
    # Cloudflare 一般较稳定，暂不配置多源
    wget -O "$CLOUDFLARED_BIN" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${CF_ARCH}"
    chmod +x "$CLOUDFLARED_BIN"
    echo "✅ Cloudflared 下载完成"
else
    echo "✅ Cloudflared 已存在 ($CLOUDFLARED_BIN)"
fi

# 验证 Cloudflared 二进制
echo "🧪 验证 Cloudflared 运行..."
if "$CLOUDFLARED_BIN" --version > /dev/null; then
    echo "✅ Cloudflared 运行正常！"
else
    echo "⚠️  Cloudflared 运行失败 (架构不匹配或文件损坏)"
    echo "尝试删除并重新运行 setup..."
    rm -f "$CLOUDFLARED_BIN"
    # 如果是第一次运行失败，可以考虑这里不强制退出，或者提醒用户
    echo "❌ 请尝试重新运行 ./setup.sh 下载正确版本。"
fi

# --- 2. 配置 Alist (官方源) ---
ALIST_BIN="$HOME/bin/alist"

# 强制停止现有进程
pm2 stop alist >/dev/null 2>&1 || true

echo "⚙️ 配置 Alist..."

# 检查 pkg 是否成功安装 alist
if command -v alist &> /dev/null; then
    SYSTEM_ALIST=$(command -v alist)
    echo "✅ 检测到系统内置 Alist: $SYSTEM_ALIST"
    
    # 建立软链接，确保兼容 start.sh 和 generate-config.js
    rm -f "$ALIST_BIN"
    ln -sf "$SYSTEM_ALIST" "$ALIST_BIN"
    
    echo "🔗 已创建链接: ~/bin/alist -> $SYSTEM_ALIST"
    
    # 验证版本
    echo "🧪 验证 Alist 运行..."
    if "$ALIST_BIN" version > /dev/null 2>&1; then
        echo "✅ Alist 运行正常！"
    else
        echo "⚠️  Alist 运行失败，请检查 pkg 安装。"
        exit 1
    fi
else
    echo "❌ 错误: 未找到 alist 命令。"
    echo "尝试手动安装: pkg install alist"
    exit 1
fi

# --- 3. 生成配置文件 ---
ENV_FILE="$HOME/.env"
echo "📝 配置文件路径: $ENV_FILE"

if [ ! -f "$ENV_FILE" ]; then
    echo "生成默认配置文件: ~/.env"
    cat <<EOT >> "$ENV_FILE"
# ==============================
# Termux Bot 配置文件
# ==============================
BOT_TOKEN=
ADMIN_ID=

# 9. Alist 密码 (推荐配置)
# 填入你的 Alist 密码，Bot 将直接使用此密码登录，无需自动抓取
ALIST_PASSWORD=

# 隧道模式: quick (随机域名) 或 token (固定域名)
TUNNEL_MODE=quick
CLOUDFLARE_TOKEN=
# Alist 域名 (可选，如果不填则自动获取隧道域名)
ALIST_DOMAIN=
# 直播推流基础地址 (例如 rtmp://ip:port/live/)
TG_RTMP_URL=
# Aria2 密钥 (默认无需修改)
ARIA2_RPC_SECRET=
# GitHub 多账号配置
GITHUB_ACCOUNTS_LIST=
EOT
else
    echo "✅ 配置文件已存在，跳过覆盖。"
fi

# --- 4. 配置 Aria2 ---
ARIA2_DIR="$HOME/.aria2"
mkdir -p "$ARIA2_DIR"
touch "$ARIA2_DIR/aria2.session"
if [ ! -f "$ARIA2_DIR/aria2.conf" ]; then
    cat <<EOT > "$ARIA2_DIR/aria2.conf"
dir=$HOME/downloads
input-file=$ARIA2_DIR/aria2.session
save-session=$ARIA2_DIR/aria2.session
save-session-interval=60
force-save=true
enable-rpc=true
rpc-allow-origin-all=true
rpc-listen-all=true
rpc-port=6800
max-concurrent-downloads=3
user-agent=Mozilla/5.0
EOT
fi

# --- 5. 赋予脚本执行权限 ---
echo "🔧 设置脚本权限..."
chmod +x start.sh update.sh monitor.sh set_pass.sh

echo "--------------------------------------------------------"
echo "✅ Termux 环境部署完成！"
echo "--------------------------------------------------------"
echo "👉 1. 请先运行: ./setup.sh"
echo "👉 2. 然后运行: ./start.sh"
echo "--------------------------------------------------------"
