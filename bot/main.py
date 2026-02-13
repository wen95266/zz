
import logging
import asyncio
import sys
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from .config import BOT_TOKEN, validate_config
from .handlers import (
    start, trigger_stream, download_command, handle_message, 
    send_usage_stats, global_error_handler, monitor_services_job,
    add_key_command, del_key_command, list_keys_command
)

# 配置日志到标准输出
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    print("---------------------------------------")
    print("🚀 Termux Bot 进程正在启动...")
    print("---------------------------------------")

    validate_config()
    
    # 建立支持 JobQueue 的 Application
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # 1. 注册全局错误处理器
        app.add_error_handler(global_error_handler)
        
        # 2. 注册定时任务 (每 2 分钟检查一次服务状态)
        if app.job_queue:
            app.job_queue.run_repeating(monitor_services_job, interval=120, first=10)
        
        # 3. 注册命令处理器
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stream", trigger_stream))
        app.add_handler(CommandHandler("dl", download_command))
        app.add_handler(CommandHandler("usage", send_usage_stats))
        
        # 新增推流密钥管理命令
        app.add_handler(CommandHandler("addkey", add_key_command))
        app.add_handler(CommandHandler("delkey", del_key_command))
        app.add_handler(CommandHandler("listkeys", list_keys_command))
        
        # 4. 注册消息处理器
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("✅ 机器人连接成功！正在监听消息...")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}")
        print("💡 如果是网络错误，请检查是否开启了代理或 VPN。")
        sys.exit(1)
