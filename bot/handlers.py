
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
from .github import trigger_stream_action
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
    """
    Markdown V1 代码块转义
    """
    if not text: return ""
    return str(text).replace("`", "'")

def escape_text(text):
    """
    Markdown V1 普通文本转义
    """
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

async def render_browser(update: Update, context: ContextTypes.DEFAULT_TYPE, path="/", page=0, edit_msg=False):
    """核心渲染函数：渲染文件列表按钮"""
    
    files, err = fetch_file_list(path, page=1, per_page=200) 
    
    if err:
        safe_path = escape_md(path)
        safe_err = escape_md(str(err))
        text = f"❌ *读取失败*: `{safe_path}`\n\n🔻 *原因*:\n```\n{safe_err}\n```"
        if edit_msg: await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    files.sort(key=lambda x: (not x['is_dir'], x['name']))

    total_items = len(files)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_files = files[start_idx:end_idx]

    context.user_data['browser'] = {
        'path': path,
        'page': page,
        'files': current_files 
    }

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

    # --- 广播状态显示 ---
    radio_sel = context.user_data.get('radio_selection', {})
    audio_path = radio_sel.get('audio')
    image_path = radio_sel.get('image')
    
    status_text = ""
    if audio_path or image_path:
        status_text += "\n\n📻 *Radio 待命:*"
        if audio_path: status_text += f"\n🎵 音频: `{escape_md(os.path.basename(audio_path))}`"
        if image_path: status_text += f"\n🖼 背景: `{escape_md(os.path.basename(image_path))}`"
        
        # 只有当音频和图片都就绪时，才显示“开始广播”按钮
        if audio_path and image_path:
            keyboard.insert(0, [InlineKeyboardButton("🚀 启动 Radio 推流 (Start Radio)", callback_data="br:start_radio")])
        else:
            keyboard.insert(0, [InlineKeyboardButton("⚠️ 需同时选择音频和图片才能启动", callback_data="br:noop")])

    markup = InlineKeyboardMarkup(keyboard)
    safe_path = escape_md(path)
    text = f"📂 *当前路径:* `{safe_path}`\n📄 共 {total_items} 项 (第 {page+1}/{total_pages or 1} 页){status_text}"

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
    
    if action == "noop":
        await query.answer("请继续选择缺少的资源 (音频或图片)", show_alert=True)
        return

    if action == "start_radio":
        radio_sel = context.user_data.get('radio_selection', {})
        if not radio_sel.get('audio') or not radio_sel.get('image'):
             await query.answer("未就绪", show_alert=True)
             return
        
        await query.message.reply_text("🚀 正在启动广播模式...\n这需要一些时间来解析文件列表，请稍候。", parse_mode=ParseMode.MARKDOWN)
        await trigger_stream_logic(update, context, None, mode="radio")
        # 清除选择
        context.user_data['radio_selection'] = {}
        await render_browser(update, context, current_path, current_page, True)
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
        item_path = os.path.join(current_path, item['name']).replace("\\", "/")
        
        safe_name = escape_md(item['name'])
        
        if item['is_dir']:
            # 文件夹操作菜单
            keyboard = [
                [InlineKeyboardButton("📂 进入目录", callback_data=f"br:enter:{idx}")],
                [InlineKeyboardButton("📻 设为广播音频源 (整个文件夹)", callback_data=f"br:set_audio:{idx}")],
                [InlineKeyboardButton("🖼 设为广播背景 (整个文件夹)", callback_data=f"br:set_image:{idx}")],
                [InlineKeyboardButton("🔙 返回", callback_data="br:act:back")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            msg = f"📂 *已选中目录:*\n`{safe_name}`\n\n请选择操作："
            await query.edit_message_text(msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        else:
            # 文件操作菜单
            keyboard = [
                [InlineKeyboardButton("📺 视频推流 (Video Stream)", callback_data=f"br:act:stream:{idx}")],
                [InlineKeyboardButton("📻 设为广播音频 (Radio Audio)", callback_data=f"br:set_audio:{idx}")],
                [InlineKeyboardButton("🖼 设为广播背景 (Radio BG)", callback_data=f"br:set_image:{idx}")],
                [InlineKeyboardButton("⬇️ 下载 (Download)", callback_data=f"br:act:dl:{idx}")],
                [InlineKeyboardButton("🔙 返回列表", callback_data="br:act:back")]
            ]
            markup = InlineKeyboardMarkup(keyboard)
            size_mb = round(item.get('size', 0) / (1024*1024), 2)
            safe_path = escape_md(item_path)
            
            msg = f"📄 *已选中文件:*\n`{safe_name}`\n\n📏 大小: {size_mb} MB\n🔗 路径: `{safe_path}`"
            await query.edit_message_text(msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        return
    
    if action == "enter":
        idx = int(parts[2])
        if idx >= len(current_files): return
        item = current_files[idx]
        new_path = os.path.join(current_path, item['name']).replace("\\", "/")
        await render_browser(update, context, new_path, 0, True)
        return

    if action == "set_audio":
        idx = int(parts[2])
        item = current_files[idx]
        full_path = os.path.join(current_path, item['name']).replace("\\", "/")
        
        if 'radio_selection' not in context.user_data: context.user_data['radio_selection'] = {}
        context.user_data['radio_selection']['audio'] = full_path
        
        await query.answer("✅ 已设置为广播音频源", show_alert=False)
        await render_browser(update, context, current_path, current_page, True)
        return

    if action == "set_image":
        idx = int(parts[2])
        item = current_files[idx]
        full_path = os.path.join(current_path, item['name']).replace("\\", "/")
        
        if 'radio_selection' not in context.user_data: context.user_data['radio_selection'] = {}
        context.user_data['radio_selection']['image'] = full_path
        
        await query.answer("✅ 已设置为广播背景源", show_alert=False)
        await render_browser(update, context, current_path, current_page, True)
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
            
            if not success: msg = escape_text(msg)
            await query.message.reply_text(f"📥 *请求下载:*\n`{safe_name}`\n\n{msg}", parse_mode=ParseMode.MARKDOWN)

async def browser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """入口命令 /ls"""
    if not check_auth(update.effective_user.id): return
    path = context.args[0] if context.args else "/"
    await render_browser(update, context, path, 0, False)

# --- 逻辑重构: 抽取推流逻辑供回调使用 ---

async def trigger_stream_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, path, key_alias=None, mode="standard"):
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

    # Radio 模式需要从 user_data 获取参数
    extra_payload = {}
    if mode == "radio":
        radio_sel = context.user_data.get('radio_selection', {})
        audio_path = radio_sel.get('audio')
        image_path = radio_sel.get('image')
        if not audio_path or not image_path:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Radio 模式参数不全")
            return
        extra_payload = {
            "mode": "radio",
            "audio_path": audio_path,
            "image_path": image_path,
            "base_url": base_url # 必须传 Base URL 供 GitHub 脚本调用 API
        }
        path = "Radio Mode" # 占位符

    success, msg, _ = trigger_stream_action(base_url, path, target_rtmp, extra_payload)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.MARKDOWN)

# ... (其余代码保持不变)
