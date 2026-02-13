
#!/data/data/com.termux/files/usr/bin/bash

ENV_FILE="$HOME/.env"
export PATH="$HOME/bin:$PATH"
DATA_DIR="$HOME/alist-data"

# 1. 申请唤醒锁
echo "🔒 申请 Termux 唤醒锁 (Wake Lock)..."
termux-wake-lock

if [ -f "$ENV_FILE" ]; then
    echo ">>> 检查环境变量配置..."
    # ⚠️ 关键修改: 不再使用 source "$ENV_FILE"
    # 因为 GITHUB_ACCOUNTS_LIST 包含 "|" 符号，直接 source 会导致 Bash 将其解析为管道命令而报错
    # 这里只提取 BOT_TOKEN 用于检查，其他变量交给 Python (dotenv) 安全处理
    
    # 使用 grep 和 cut 安全提取 BOT_TOKEN (去除引号和回车符)
    BOT_TOKEN=$(grep -E "^BOT_TOKEN=" "$ENV_FILE" | head -n 1 | cut -d '=' -f 2- | tr -d '"' | tr -d "'" | tr -d '\r')
    TUNNEL_MODE=$(grep -E "^TUNNEL_MODE=" "$ENV_FILE" | head -n 1 | cut -d '=' -f 2- | tr -d '"' | tr -d "'" | tr -d '\r')
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
# --------------------

# 2. 检查核心组件是否存在
echo "🔍 检查组件完整性..."
MISSING_FILES=0

if [ ! -f "$HOME/bin/alist" ]; then
    echo "❌ 缺失文件: ~/bin/alist"
    MISSING_FILES=1
else
    # 尝试运行 alist version 检查文件是否损坏
    echo "🧪 验证 Alist 二进制..."
    if ! "$HOME/bin/alist" version > /dev/null 2>&1; then
         echo "❌ Alist 文件似乎已损坏，无法运行。"
         echo "💡 检测到文件损坏，正在尝试自动修复..."
         # 自动删除损坏文件，提示用户运行 setup
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
    
    # ⚡️ 新增: 深度连接检查 (解决 Error 1033 诊断问题)
    echo "🔍 正在测试本地连接 (http://127.0.0.1:5244)..."
    if curl --connect-timeout 3 -s -I http://127.0.0.1:5244 > /dev/null; then
        echo "✅ 本地连接成功！Alist 正在运行。"
    else
        echo "⚠️  注意: Alist 进程在运行，但无法通过 127.0.0.1 连接。"
        echo "   这可能是 Cloudflare 报错 1033 的原因。"
        echo "   尝试重启: ./start.sh"
    fi
else
    echo "❌ Alist 启动失败！"
    echo "📋 Alist 日志:"
    pm2 logs alist --lines 10 --nostream
fi

# --- Tunnel 检查 ---
if pm2 list | grep "tunnel" | grep -q "online"; then
    echo "✅ Cloudflared Tunnel 启动成功"
    
    # 针对 Token 模式用户的特别提示
    if [[ "$TUNNEL_MODE" == "token" ]]; then
        echo "-----------------------------------"
        echo "📢 Cloudflare 后台配置指南 (解决 Error 1033):"
        echo "请确保在 Cloudflare Zero Trust 面板 -> Public Hostname 设置如下:"
        echo "1. Service Type (协议): HTTP"
        echo "2. URL (地址): 127.0.0.1:5244"
        echo "⚠️  不要填 localhost，必须填 127.0.0.1"
        echo "-----------------------------------"
    fi
else
    echo "❌ Cloudflared Tunnel 启动失败！"
    echo "📋 Tunnel 日志:"
    pm2 logs tunnel --lines 10 --nostream
    echo "💡 提示: 如果是'Exec format error'，请重新运行 ./setup.sh 下载正确版本。"
fi

# --- 获取密码 ---
if pm2 list | grep "alist" | grep -q "online"; then
    echo "-----------------------------------"
    echo "🔑 检查 Alist 登录状态..."
    
    # 优先检查是否存在预设密码文件
    if [ -f "$HOME/.alist_pass" ]; then
        PASS=$(cat "$HOME/.alist_pass")
        echo "👤 用户名: admin"
        echo "🔑 密码: $PASS (已保存)"
    else
        # 自动获取
        ADMIN_INFO=$("$HOME/bin/alist" admin --data "$DATA_DIR" 2>/dev/null)
        
        # 优化: 检查是否包含哈希存储的提示
        if echo "$ADMIN_INFO" | grep -q "hash value"; then
            echo "⚠️  管理员密码已初始化 (加密存储)。"
            echo "💡 如果您忘记了密码，请运行以下命令设置新密码："
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
echo "👉 如果推流失败或打不开网页，请检查上方日志。"
echo "-----------------------------------"
