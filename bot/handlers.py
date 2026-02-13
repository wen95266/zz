
import traceback
import html
import json
import logging
import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from .config import MAIN_MENU, ADMIN_MENU, STREAM_MENU, check_auth, get_account_count, ADMIN_ID, TG_RTMP_URL_ENV
from .system import (
    get_system_stats, 
    get_log_file_path,
    get_public_url, 
    get_admin_pass, 
    restart_pm2_services, 
    add_aria2_task,
    check_services_health,
    get_aria2_status
)
from .github import trigger_stream_action, get_all_usage_stats
from .stream_manager import add_key, delete_key, get_key, get_all_keys, get_default_key

logger = logging.getLogger(__name__)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🚨 Bot 发生错误: {context.error}")
        except:
            pass

# --- 定时任务 ---
LAST_SERVICE_STATUS = {}
async def monitor_services_job(context: ContextTypes.DEFAULT_TYPE):
    global LAST_SERVICE_STATUS
    current_status = check_services_health()
    alerts = []
    for svc, is_running in current_status.items():
        if LAST_SERVICE_STATUS.get(svc, True) and not is_running:
            alerts.append(f"❌ 服务挂掉: `{svc}`")
        elif not LAST_SERVICE_STATUS.get(svc, False) and is_running:
             alerts.append(f"✅ 服务已恢复: `{svc}`")
    LAST_SERVICE_STATUS = current_status
    if alerts and ADMIN_ID:
        try:
            alert_msg = "🔔 *系统监控报告*\n\n" + "\n".join(alerts)
            await context.bot.send_message(chat_id=ADMIN_ID, text=alert_msg, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

# --- 命令处理器 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    await show_main_menu(update)

async def show_main_menu(update: Update):
    markup = ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)
    await update.message.reply_text("🤖 *Termux 控制台*", reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    if not context.args: 
        await update.message.reply_text("用法: `/dl http://example.com/file.zip`", parse_mode=ParseMode.MARKDOWN)
        return
    success, msg = add_aria2_task(context.args[0])
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def trigger_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    
    # 解析参数: /stream <path> [key_name]
    args = context.args
    if not args:
        await update.message.reply_text(
            "📺 *推流用法:*\n"
            "1️⃣ 使用默认密钥:\n`/stream /movie.mp4`\n"
            "2️⃣ 使用指定频道密钥:\n`/stream /movie.mp4 体育台`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    path = args[0]
    key_alias = args[1] if len(args) > 1 else None
    
    # 1. 获取基础推流地址 (服务器地址)
    base_rtmp = TG_RTMP_URL_ENV
    if not base_rtmp:
        await update.message.reply_text("❌ 未在 .env 配置基础推流地址 (TG_RTMP_URL)！\n请填入服务器地址，例如: `rtmp://live.server.com/app/`")
        return

    stream_key = None
    display_name = "默认"

    # 2. 查找密钥
    if key_alias:
        stream_key = get_key(key_alias)
        if not stream_key:
            await update.message.reply_text(f"❌ 找不到名为 `{key_alias}` 的密钥配置。", parse_mode=ParseMode.MARKDOWN)
            return
        display_name = key_alias
    else:
        # 默认取第一个
        default_name, default_key = get_default_key()
        if default_key:
            stream_key = default_key
            display_name = default_name
    
    # 3. 拼接完整地址
    target_rtmp = ""
    if stream_key:
        # 拼接: base + key
        # 确保 base_rtmp 以 / 结尾 (如果 key 不以 / 开头)
        if not base_rtmp.endswith("/") and not stream_key.startswith("/"):
            base_rtmp += "/"
        target_rtmp = base_rtmp + stream_key
    else:
        # 如果没有保存任何密钥，假设 env 里填的是完整地址 (兼容旧版)
        target_rtmp = base_rtmp
        display_name = "System Env"

    base_url = get_public_url()
    if not base_url:
        await update.message.reply_text("❌ 隧道未启动，无法生成外网链接")
        return

    await update.message.reply_text(f"🚀 正在准备推流...\n📄 文件: `{path}`\n📺 频道: `{display_name}`", parse_mode=ParseMode.MARKDOWN)
    
    success, msg, _ = trigger_stream_action(base_url, path, target_rtmp)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def add_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("用法: `/addkey <名称> <密钥>`\n例如: `/addkey 体育台 live_xxxx123`", parse_mode=ParseMode.MARKDOWN)
        return
    
    name = args[0]
    key = args[1]
    add_key(name, key)
    await update.message.reply_text(f"✅ 已保存密钥: `{name}`", parse_mode=ParseMode.MARKDOWN)

async def del_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("用法: `/delkey <名称>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    name = context.args[0]
    if delete_key(name):
        await update.message.reply_text(f"🗑 已删除: `{name}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ 未找到: `{name}`", parse_mode=ParseMode.MARKDOWN)

async def list_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    keys = get_all_keys()
    
    # 获取基础 URL 用于展示
    base_rtmp = TG_RTMP_URL_ENV or "❌ 未配置 (.env)"

    if not keys:
        msg = "📭 当前没有保存的密钥。"
        msg += f"\n(将直接使用基础地址: `{base_rtmp}`)"
    else:
        msg = f"📺 *已保存的推流配置:*\n🔗 基础服务器: `{base_rtmp}`\n\n"
        for name, k in keys.items():
            # 隐藏部分 Key 保护隐私
            mask_k = k[:4] + "***" + k[-4:] if len(k) > 8 else "***"
            msg += f"🔸 *{name}*: `{mask_k}`\n"
    
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# --- 消息处理器 ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    text = update.message.text
    
    # 主菜单
    if text == "📊 状态": await send_status(update, context)
    elif text == "📥 任务": await send_tasks(update, context)
    elif text == "☁️ 隧道": await send_tunnel(update, context)
    elif text == "⬇️ 下载": await send_download_help(update, context)
    elif text == "📺 推流设置": await show_stream_menu(update, context) # 新菜单入口
    elif text == "📝 日志": await send_logs(update, context)
    elif text == "⚙️ 管理": await show_admin_menu(update, context)
    elif text == "❓ 帮助": await send_help(update, context)
    
    # 管理菜单
    elif text == "🔄 重启服务": await restart_services(update, context)
    elif text == "🔑 查看密码": await send_admin_pass(update, context)
    elif text == "📉 GitHub 用量": await send_usage_stats(update, context)
    
    # 推流菜单
    elif text == "👀 查看配置": await list_keys_command(update, context)
    elif text == "➕ 添加配置": await send_add_key_help(update, context)
    elif text == "🗑 删除配置": await send_del_key_help(update, context)
    elif text == "🔙 返回主菜单": await start(update, context)

# --- 辅助函数 ---

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = ReplyKeyboardMarkup(ADMIN_MENU, resize_keyboard=True)
    await update.message.reply_text("⚙️ *系统管理*", reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

async def show_stream_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = ReplyKeyboardMarkup(STREAM_MENU, resize_keyboard=True)
    await update.message.reply_text("📺 *推流配置管理*", reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

async def send_add_key_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *添加推流密钥*\n\n"
        "请使用命令添加密钥 (Key)，Bot 会自动拼接在基础地址后面。\n"
        "格式: `/addkey <名称> <密钥>`\n\n"
        "例如：\n"
        "`/addkey 电影台 live_xxxx123`",
        parse_mode=ParseMode.MARKDOWN
    )

async def send_del_key_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keys = get_all_keys()
    msg = "🗑 *删除推流密钥*\n请使用命令: `/delkey <名称>`\n\n"
    if keys:
        msg += "可选名称:\n" + "\n".join([f"`{k}`" for k in keys.keys()])
    else:
        msg += "(当前列表为空)"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def send_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_system_stats()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def send_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = get_aria2_status()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_file = get_log_file_path("alist")
    if os.path.exists(log_file):
        await update.message.reply_text("📂 正在上传 Alist 日志文件...")
        await update.message.reply_document(document=open(log_file, 'rb'))
    else:
        await update.message.reply_text("❌ 日志文件不存在")

async def send_tunnel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = get_public_url()
    await update.message.reply_text(f"☁️ *Cloudflare:* `{url if url else 'N/A'}`", parse_mode=ParseMode.MARKDOWN)

async def restart_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 正在重启服务... (Bot 可能会短暂离线)")
    restart_pm2_services()

async def send_admin_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = get_admin_pass()
    await update.message.reply_text(f"🔑 *Alist 密码:*\n`{res}`", parse_mode=ParseMode.MARKDOWN)

async def send_usage_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = get_all_usage_stats()
    msg = "📉 *GitHub 用量:*\n\n" + ("\n".join(results) if results else "未配置")
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def send_download_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⬇️ *下载功能*\n"
        "发送 `/dl <链接>` 让 Aria2 下载文件。\n"
        "文件将保存到 Termux 的 `~/downloads` 目录。",
        parse_mode=ParseMode.MARKDOWN
    )

async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Termux Bot 使用指南*\n\n"
        "1. *文件管理*: 访问 Cloudflare 链接进入 Alist。\n"
        "2. *离线下载*: `/dl <url>`\n"
        "3. *推流*: `/stream <path> [频道名]`\n"
        "4. *多频道*: 在“推流设置”中添加不同频道的 Key。\n"
        "5. *自动更新*: 修改 GitHub 代码后，Bot 会自动同步。"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
