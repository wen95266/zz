
import logging
import asyncio
import sys
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.request import HTTPXRequest
from .config import BOT_TOKEN, validate_config
from .handlers import (
    start, trigger_stream, download_command, handle_message, 
    global_error_handler, monitor_services_job,
    add_key_command, del_key_command, list_keys_command,
    browser_command, browser_callback_handler 
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
        # 配置网络请求参数，增加超时时间以适应不稳定网络
        request = HTTPXRequest(
            connection_pool_size=8,
            read_timeout=30.0,   # 增加读取超时
            write_timeout=30.0,  # 增加写入超时
            connect_timeout=30.0 # 增加连接超时
        )

        app = ApplicationBuilder().token(BOT_TOKEN).request(request).build()
        
        # 1. 注册全局错误处理器
        app.add_error_handler(global_error_handler)
        
        # 2. 注册定时任务 (每 2 分钟检查一次服务状态)
        if app.job_queue:
            app.job_queue.run_repeating(monitor_services_job, interval=120, first=10)
        
        # 3. 注册命令处理器
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("stream", trigger_stream))
        app.add_handler(CommandHandler("dl", download_command))
        # 移除 usage 命令
        app.add_handler(CommandHandler("ls", browser_command)) 
        
        # 新增推流密钥管理命令
        app.add_handler(CommandHandler("addkey", add_key_command))
        app.add_handler(CommandHandler("delkey", del_key_command))
        app.add_handler(CommandHandler("listkeys", list_keys_command))
        
        # 4. 注册 Callback (按钮点击) 处理器
        # 正则匹配 br: 开头的 callback
        app.add_handler(CallbackQueryHandler(browser_callback_handler, pattern="^br:"))

        # 5. 注册消息处理器
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("✅ 机器人连接成功！正在监听消息...")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ 启动失败: {e}")
        print("💡 如果是网络错误，请检查是否开启了代理或 VPN。")
        sys.exit(1)
