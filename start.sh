#!/data/data/com.termux/files/usr/bin/bash

ENV_FILE="$HOME/.env"
export PATH="$HOME/bin:$PATH"

# 1. 申请唤醒锁，防止息屏后 CPU 降频或休眠
echo "🔒 申请 Termux 唤醒锁 (Wake Lock)..."
termux-wake-lock

if [ -f "$ENV_FILE" ]; then
    echo ">>> 加载配置文件: $ENV_FILE"
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "❌ 未找到 ~/.env 文件，请先运行 ./setup.sh"
    exit 1
fi

echo "✅ 正在启动 PM2 服务组..."
# 启动所有进程 (使用 .cjs 避免 ESM 模块错误)
pm2 start ecosystem.config.cjs
pm2 save

echo "-----------------------------------"
echo "🚀 服务已在后台运行"
echo "-----------------------------------"
echo "📊 监控面板: pm2 monit"
echo "📝 查看日志: pm2 logs"
echo "🔄 重启所有: pm2 restart all"
echo "💡 提示: 请勿从多任务后台划掉 Termux"
echo "-----------------------------------"
