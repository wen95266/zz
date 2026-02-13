
import traceback
import html
import json
import logging
import os
import math
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
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
from .alist_api import fetch_file_list

logger = logging.getLogger(__name__)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if ADMIN_ID:
        try:
            # 错误通知不使用 Markdown，防止报错本身再次报错
            err_msg = str(context.error)[:500]
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🚨 Bot 发生错误: {err_msg}")
        except: pass

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
        except: pass

# --- 文件浏览器逻辑 ---

ITEMS_PER_PAGE = 10

def escape_md(text):
    """简单的 Markdown 转义 (主要处理反引号，用于代码块内)"""
    if not text: return ""
    return text.replace("`", "'")

async def render_browser(update: Update, context: ContextTypes.DEFAULT_TYPE, path="/", page=0, edit_msg=False):
    """核心渲染函数：渲染文件列表按钮"""
    
    # 1. 获取文件列表
    files, err = fetch_file_list(path, page=1, per_page=200) 
    
    if err:
        # ⚠️ 修复: 错误信息可能包含特殊字符 (如 Python 报错中的下划线)，必须放入代码块中
        safe_path = escape_md(path)
        safe_err = escape_md(str(err))
        text = f"❌ *读取失败*: `{safe_path}`\n\n🔻 *原因*:\n```\n{safe_err}\n```"
        
        if edit_msg: await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    # 2. 排序: 文件夹在前
    files.sort(key=lambda x: (not x['is_dir'], x['name']))

    # 3. 内存分页
    total_items = len(files)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_files = files[start_idx:end_idx]

    # 4. 存储上下文
    context.user_data['browser'] = {
        'path': path,
        'page': page,
        'files': current_files 
    }

    # 5. 构建键盘
    keyboard = []
    
    for idx, f in enumerate(current_files):
        icon = "📂" if f['is_dir'] else "📄"
        keyboard.append([InlineKeyboardButton(f"{icon} {f['name']}", callback_data=f"br:clk:{idx}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data="br:pg:prev"))
    
    if path != "/":
        nav_row.append(InlineKeyboardButton("🆙 返回上级", callback_data="br:nav:up"))
    else:
        nav_row.append(InlineKeyboardButton("🏠 根目录", callback_data="br:nav:root"))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data="br:pg:next"))
    
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("❌ 关闭", callback_data="br:close")])

    markup = InlineKeyboardMarkup(keyboard)
    # 路径也可能包含特殊字符
    safe_path = escape_md(path)
    text = f"📂 *当前路径:* `{safe_path}`\n📄 共 {total_items} 项 (第 {page+1}/{total_pages or 1} 页)"

    if edit_msg:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

async def browser_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理浏览器按钮点击"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split(':')
    action = parts[1]
    
    browser_data = context.user_data.get('browser', {})
    current_path = browser_data.get('path', '/')
    current_page = browser_data.get('page', 0)
    current_files = browser_data.get('files', [])

    if action == "close":
        await query.delete_message()
        return

    if action == "nav":
        target = parts[2]
        if target == "root":
            await render_browser(update, context, "/", 0, True)
        elif target == "up":
            parent = os.path.dirname(current_path.rstrip('/'))
            if not parent: parent = "/"
            await render_browser(update, context, parent, 0, True)
        return

    if action == "pg":
        direction = parts[2]
        new_page = current_page - 1 if direction == "prev" else current_page + 1
        await render_browser(update, context, current_path, new_page, True)
        return

    if action == "clk":
        idx = int(parts[2])
        if idx >= len(current_files): return
        
        item = current_files[idx]
        # 修复路径拼接 (Windows/Linux)
        item_path = os.path.join(current_path, item['name']).replace("\\", "/")
        
        if item['is_dir']:
            await render_browser(update, context, item_path, 0, True)
        else:
            keyboard = [
                [InlineKeyboardButton("📺 推流 (Stream)", callback_data=f"br:act:stream:{idx}")],
                [InlineKeyboardButton("⬇️ 下载 (Download)", callback_data=f"br:act:dl:{idx}")],
                [InlineKeyboardButton("🔙 返回列表", callback_data="br:act:back")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            
            size_mb = round(item.get('size', 0) / (1024*1024), 2)
            # ⚠️ 修复: 文件名包含反引号或下划线时会导致 Markdown 解析错误
            safe_name = escape_md(item['name'])
            safe_path = escape_md(item_path)
            
            msg = f"📄 *已选中文件:*\n`{safe_name}`\n\n📏 大小: {size_mb} MB\n🔗 路径: `{safe_path}`"
            await query.edit_message_text(msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        return

    if action == "act":
        sub_act = parts[2]
        if sub_act == "back":
            await render_browser(update, context, current_path, current_page, True)
            return
        
        idx = int(parts[3])
        if idx >= len(current_files): return
        item = current_files[idx]
        full_path = os.path.join(current_path, item['name']).replace("\\", "/")
        
        if sub_act == "stream":
            context.args = [full_path] 
            safe_name = escape_md(item['name'])
            await query.message.reply_text(f"🚀 已选择文件，准备推流...\n📄 `{safe_name}`", parse_mode=ParseMode.MARKDOWN)
            await trigger_stream_logic(update, context, full_path)
            
        elif sub_act == "dl":
            base_url = get_public_url()
            if not base_url:
                await query.message.reply_text("❌ 隧道未启动，无法获取下载链接")
                return
            from urllib.parse import quote
            dl_url = f"{base_url}/d{quote(full_path)}"
            
            success, msg = add_aria2_task(dl_url)
            safe_name = escape_md(item['name'])
            # msg 通常是 safe 的，但为了保险起见，如果 msg 也是动态的，最好也处理一下，这里暂且保留
            await query.message.reply_text(f"📥 *请求下载:*\n`{safe_name}`\n\n{msg}", parse_mode=ParseMode.MARKDOWN)

async def browser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """入口命令 /ls"""
    if not check_auth(update.effective_user.id): return
    path = context.args[0] if context.args else "/"
    await render_browser(update, context, path, 0, False)

# --- 逻辑重构: 抽取推流逻辑供回调使用 ---

async def trigger_stream_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, path, key_alias=None):
    """复用推流核心逻辑"""
    base_rtmp = TG_RTMP_URL_ENV
    if not base_rtmp:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ 未配置 TG_RTMP_URL")
        return

    stream_key = None
    display_name = "默认"

    if key_alias:
        stream_key = get_key(key_alias)
        if not stream_key:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ 找不到密钥: {key_alias}")
            return
        display_name = key_alias
    else:
        default_name, default_key = get_default_key()
        if default_key:
            stream_key = default_key
            display_name = default_name
    
    target_rtmp = ""
    if stream_key:
        if not base_rtmp.endswith("/") and not stream_key.startswith("/"):
            base_rtmp += "/"
        target_rtmp = base_rtmp + stream_key
    else:
        target_rtmp = base_rtmp
        display_name = "System Env"

    base_url = get_public_url()
    if not base_url:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ 隧道未就绪")
        return

    success, msg, _ = trigger_stream_action(base_url, path, target_rtmp)
    # GitHub Action 返回的消息通常包含 URL，Markdown 解析需要小心，这里假设 msg 是安全的或由我们控制
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.MARKDOWN)


# --- 原始命令处理器 ---

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
    args = context.args
    if not args:
        await update.message.reply_text("用法: `/stream /path/movie.mp4 [key]`\n💡 建议使用「📂 文件」菜单进行浏览选择。", parse_mode=ParseMode.MARKDOWN)
        return
    path = args[0]
    key = args[1] if len(args) > 1 else None
    await trigger_stream_logic(update, context, path, key)

async def add_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("用法: `/addkey <名称> <密钥>`", parse_mode=ParseMode.MARKDOWN)
        return
    # key name 是用户输入的，可能包含 markdown 字符，这里不使用 markdown 格式返回以防万一
    if add_key(args[0], args[1]):
        await update.message.reply_text(f"✅ 已保存: {args[0]}")

async def del_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    if not context.args: return
    if delete_key(context.args[0]):
        await update.message.reply_text(f"🗑 已删除: {context.args[0]}")

async def list_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    keys = get_all_keys()
    base_rtmp = TG_RTMP_URL_ENV or "❌ 未配置"
    msg = f"📺 *推流配置:*\n🔗 Base: `{escape_md(base_rtmp)}`\n\n"
    if not keys: msg += "(空)"
    for k, v in keys.items(): 
        # 隐藏密钥部分，mask 处理
        mask_v = f"...{v[-4:]}" if len(v) > 4 else "***"
        msg += f"🔸 {escape_md(k)}: `{mask_v}`\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    text = update.message.text
    
    if text == "📂 文件": await browser_command(update, context)
    elif text == "📊 状态": await send_status(update, context)
    elif text == "📥 任务": await send_tasks(update, context)
    elif text == "☁️ 隧道": await send_tunnel(update, context)
    elif text == "⬇️ 下载": await send_download_help(update, context)
    elif text == "📺 推流设置": await show_stream_menu(update, context)
    elif text == "📝 日志": await send_logs(update, context)
    elif text == "⚙️ 管理": await show_admin_menu(update, context)
    elif text == "❓ 帮助": await send_help(update, context)
    elif text == "🔄 重启服务": await restart_services(update, context)
    elif text == "🔑 查看密码": await send_admin_pass(update, context)
    elif text == "📉 GitHub 用量": await send_usage_stats(update, context)
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
    await update.message.reply_text("用法: `/addkey 名称 密钥`", parse_mode=ParseMode.MARKDOWN)

async def send_del_key_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("用法: `/delkey 名称`", parse_mode=ParseMode.MARKDOWN)

async def send_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # get_system_stats 内部也是 markdown，通常是安全的，但如果 psutil 返回怪异字符可能会有问题
    # 暂时认为它是安全的
    await update.message.reply_text(get_system_stats(), parse_mode=ParseMode.MARKDOWN)

async def send_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_aria2_status(), parse_mode=ParseMode.MARKDOWN)

async def send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_file = get_log_file_path("alist")
    if os.path.exists(log_file):
        await update.message.reply_document(document=open(log_file, 'rb'))
    else: await update.message.reply_text("❌ 日志不存在")

async def send_tunnel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = get_public_url() or "未获取到"
    await update.message.reply_text(f"☁️ *URL:* `{url}`", parse_mode=ParseMode.MARKDOWN)

async def restart_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 重启中...")
    restart_pm2_services()

async def send_admin_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 密码放入代码块
    pwd = get_admin_pass() or "未知"
    await update.message.reply_text(f"🔑 `{escape_md(pwd)}`", parse_mode=ParseMode.MARKDOWN)

async def send_usage_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = get_all_usage_stats()
    msg = "📉 *GitHub:*\n" + ("\n".join(results) if results else "未配置")
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def send_download_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("发送 `/dl 链接` 下载，或使用「📂 文件」菜单。", parse_mode=ParseMode.MARKDOWN)

async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 *指南*\n1. 使用「📂 文件」浏览网盘\n2. 点击文件可直接推流或下载\n3. /stream 手动推流", parse_mode=ParseMode.MARKDOWN)
