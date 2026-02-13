#!/data/data/com.termux/files/usr/bin/bash

# ==========================================
# Termux Alist Bot 部署脚本 (自动换源版)
# ==========================================
set -e

# 检测架构
ARCH=$(uname -m)
case $ARCH in
    aarch64)
        ALIST_ARCH="linux-arm64"
        CF_ARCH="linux-arm64"
        ;;
    arm*)
        ALIST_ARCH="linux-arm-7"
        CF_ARCH="linux-arm"
        ;;
    x86_64)
        ALIST_ARCH="linux-amd64"
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
pkg install -y python nodejs aria2 ffmpeg git vim curl wget tar openssl-tool build-essential libffi termux-tools

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

echo -e "\033[1;36m>>> [5/5] 下载核心组件 ($ARCH)...\033[0m"

# --- 1. 安装 Cloudflared ---
CLOUDFLARED_BIN="$HOME/bin/cloudflared"
if [ ! -f "$CLOUDFLARED_BIN" ]; then
    echo "⬇️ 正在下载 Cloudflared..."
    # Cloudflare 一般较稳定，暂不配置多源
    wget -O "$CLOUDFLARED_BIN" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${CF_ARCH}"
    chmod +x "$CLOUDFLARED_BIN"
    echo "✅ Cloudflared 安装完成"
else
    echo "✅ Cloudflared 已存在 ($CLOUDFLARED_BIN)"
fi

# --- 2. 安装/修复 Alist (自动换源逻辑) ---
ALIST_BIN="$HOME/bin/alist"
STABLE_VERSION="v3.41.0"
ALIST_FILE="alist.tar.gz"

# 强制停止现有进程
pm2 stop alist >/dev/null 2>&1 || true

# 定义下载源数组
MIRRORS=(
    "https://github.com/alist-org/alist/releases/download/${STABLE_VERSION}/alist-${ALIST_ARCH}.tar.gz"
    "https://mirror.ghproxy.com/https://github.com/alist-org/alist/releases/download/${STABLE_VERSION}/alist-${ALIST_ARCH}.tar.gz"
    "https://ghproxy.net/https://github.com/alist-org/alist/releases/download/${STABLE_VERSION}/alist-${ALIST_ARCH}.tar.gz"
)

echo "⬇️ 正在安装/修复 Alist (目标版本: $STABLE_VERSION)..."
echo "ℹ️ 将尝试自动切换下载源，直到下载成功。"

DOWNLOAD_SUCCESS=false

for URL in "${MIRRORS[@]}"; do
    echo "------------------------------------------------"
    echo "🌐 尝试源: $URL"
    
    # 清理旧文件
    rm -f "$ALIST_BIN" "$ALIST_FILE" alist

    # 尝试下载 (超时设置为 15秒)
    if wget --timeout=15 -O "$ALIST_FILE" "$URL"; then
        echo "📦 下载完成，正在校验..."
        
        # 1. 检查是不是压缩包 (防止下载到报错的 HTML 页面)
        if ! file "$ALIST_FILE" | grep -q "gzip compressed data"; then
            echo "⚠️  文件校验失败: 下载的不是有效的 tar.gz 包 (可能是网络拦截)"
            continue
        fi

        # 2. 尝试解压
        if tar -zxvf "$ALIST_FILE"; then
            chmod +x alist
            mv alist "$ALIST_BIN"
            rm -f "$ALIST_FILE"

            # 3. 运行测试
            echo "🧪 验证二进制文件..."
            if "$ALIST_BIN" version > /dev/null 2>&1; then
                echo "✅ Alist 安装成功！"
                DOWNLOAD_SUCCESS=true
                break # 成功则跳出循环
            else
                echo "⚠️  二进制运行失败 (架构不匹配或文件损坏)"
            fi
        else
            echo "⚠️  解压失败，文件可能已损坏"
        fi
    else
        echo "⚠️  下载连接超时或失败"
    fi
done

if [ "$DOWNLOAD_SUCCESS" = false ]; then
    echo "------------------------------------------------"
    echo "❌ 所有源均下载失败！"
    echo "💡 请尝试："
    echo "1. 开启 VPN/代理"
    echo "2. 检查网络连接"
    echo "3. 稍后重试"
    echo "------------------------------------------------"
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
# 隧道模式: quick (随机域名) 或 token (固定域名)
TUNNEL_MODE=quick
CLOUDFLARE_TOKEN=
# Alist 域名 (可选，如果不填则自动获取隧道域名)
ALIST_DOMAIN=
# 直播推流地址 (可选)
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
chmod +x start.sh update.sh monitor.sh

echo "--------------------------------------------------------"
echo "✅ Termux 环境部署完成！"
echo "--------------------------------------------------------"
echo "👉 1. 请先运行: ./setup.sh (确保 Alist 下载无误)"
echo "👉 2. 然后运行: ./start.sh"
echo "--------------------------------------------------------"
