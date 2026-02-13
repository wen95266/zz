
#!/data/data/com.termux/files/usr/bin/bash

ENV_FILE="$HOME/.env"
export PATH="$HOME/bin:$PATH"
DATA_DIR="$HOME/alist-data"

# 1. 申请唤醒锁
echo "🔒 申请 Termux 唤醒锁 (Wake Lock)..."
termux-wake-lock

if [ -f "$ENV_FILE" ]; then
    echo ">>> 检查环境变量配置..."
    BOT_TOKEN=$(grep -E "^BOT_TOKEN=" "$ENV_FILE" | head -n 1 | cut -d '=' -f 2- | tr -d '"' | tr -d "'" | tr -d '\r')
else
    echo "❌ 未找到 ~/.env 文件，请先运行 ./setup.sh"
    exit 1
fi

# --- 环境变量检查 ---
if [ -z "$BOT_TOKEN" ]; then
    echo "--------------------------------------------------------"
    echo "⚠️  检测到 BOT_TOKEN 为空！"
    echo "Bot 无法启动。请先编辑配置文件填入 Telegram Bot Token。"
    echo "👉 命令: nano ~/.env"
    echo "--------------------------------------------------------"
    exit 1
fi

# 2. 检查核心组件是否存在
echo "🔍 检查组件完整性..."
MISSING_FILES=0

if [ ! -f "$HOME/bin/alist" ]; then
    echo "❌ 缺失文件: ~/bin/alist"
    MISSING_FILES=1
else
    echo "🧪 验证 Alist 二进制..."
    if ! "$HOME/bin/alist" version > /dev/null 2>&1; then
         echo "❌ Alist 文件似乎已损坏，无法运行。"
         rm -f "$HOME/bin/alist"
         MISSING_FILES=1
    fi
fi

if [ ! -f "$HOME/bin/cloudflared" ]; then
    echo "❌ 缺失文件: ~/bin/cloudflared"
    MISSING_FILES=1
fi

if [ $MISSING_FILES -eq 1 ]; then
    echo "-----------------------------------"
    echo "⚠️ 检测到核心组件缺失或损坏！"
    echo "请务必重新运行安装脚本进行修复："
    echo "👉 ./setup.sh"
    echo "-----------------------------------"
    exit 1
fi

# 确保 Alist 数据目录存在并修复权限
mkdir -p "$DATA_DIR"
chmod -R 755 "$DATA_DIR"

# 3. 生成 PM2 配置文件
echo "⚙️ 生成 PM2 任务配置..."
if [ -f "generate-config.js" ]; then
    node generate-config.js
else
    echo "❌ 错误: 找不到 generate-config.js 文件"
    exit 1
fi

# 4. 清理旧的 JS/CJS 配置文件
echo "🧹 清理旧配置文件..."
rm -f ecosystem.config.js ecosystem.config.cjs pm2.config.cjs

# 5. 重置 PM2 状态
echo "🔄 重置 PM2 进程状态..."
pm2 kill > /dev/null 2>&1 || true
sleep 2

echo "✅ 正在启动 PM2 服务组..."

# 6. 启动服务
pm2 start ecosystem.config.json
pm2 save

echo "⏳ 等待服务启动 (5秒)..."
sleep 5

# 7. 检查服务状态

# --- Alist 检查 ---
if pm2 list | grep "alist" | grep -q "online"; then
    echo "✅ Alist 进程已启动"
    echo "🔍 正在测试本地连接 (http://127.0.0.1:5244)..."
    if curl --connect-timeout 3 -s -I http://127.0.0.1:5244 > /dev/null; then
        echo "✅ 本地连接成功！Alist 正在运行。"
    else
        echo "⚠️  注意: Alist 进程在运行，但无法通过 127.0.0.1 连接。"
        echo "   这可能是 Cloudflare 报错 1033/530 的原因。"
    fi
else
    echo "❌ Alist 启动失败！"
    pm2 logs alist --lines 10 --nostream
fi

# --- Tunnel 检查 (提取临时 URL) ---
if pm2 list | grep "tunnel" | grep -q "online"; then
    echo "✅ Cloudflared Tunnel 启动成功"
    echo "🔎 正在获取公网链接 (请稍候)..."
    
    # 尝试循环 15 秒获取 URL
    TUNNEL_URL=""
    for i in {1..15}; do
        # 从日志读取 trycloudflare 链接
        LOGS=$(pm2 logs tunnel --lines 50 --nostream 2>&1)
        URL=$(echo "$LOGS" | grep -o 'https://[-a-zA-Z0-9]*\.trycloudflare\.com' | tail -n 1)
        if [ -n "$URL" ]; then
            TUNNEL_URL="$URL"
            break
        fi
        sleep 1
    done

    if [ -n "$TUNNEL_URL" ]; then
        echo "--------------------------------------------------------"
        echo -e "\033[1;32m🎉 您的公网访问地址: \033[0m"
        echo -e "\033[1;32m$TUNNEL_URL\033[0m"
        echo "--------------------------------------------------------"
        echo "👉 请在 Telegram Bot 中点击「☁️ 隧道」确认链接是否更新。"
    else
        echo "⚠️  暂未获取到链接。这可能是因为网络较慢。"
        echo "👉 请稍后在 Bot 中点击「☁️ 隧道」查看。"
    fi

else
    echo "❌ Cloudflared Tunnel 启动失败！"
    pm2 logs tunnel --lines 10 --nostream
fi

# --- 获取密码 ---
if pm2 list | grep "alist" | grep -q "online"; then
    echo "-----------------------------------"
    echo "🔑 检查 Alist 登录状态..."
    
    if [ -f "$HOME/.alist_pass" ]; then
        PASS=$(cat "$HOME/.alist_pass")
        echo "👤 用户名: admin"
        echo "🔑 密码: $PASS (已保存)"
    else
        ADMIN_INFO=$("$HOME/bin/alist" admin --data "$DATA_DIR" 2>/dev/null)
        if echo "$ADMIN_INFO" | grep -q "hash value"; then
            echo "⚠️  管理员密码已初始化 (加密存储)。"
            echo "👉 ./set_pass.sh 您的新密码"
        elif [ -n "$ADMIN_INFO" ]; then
            echo "$ADMIN_INFO"
        else
            echo "⚠️ 无法自动获取密码，请尝试运行: ./set_pass.sh 123456"
        fi
    fi
fi

echo "-----------------------------------"
echo "🚀 服务检查完成"
echo "-----------------------------------"
