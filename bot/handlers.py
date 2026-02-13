
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

# --- 辅助函数 ---

def escape_md(text):
    if not text: return ""
    return str(text).replace("`", "'")

def escape_text(text):
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

# --- 推流核心逻辑 (提前定义以供调用) ---

async def trigger_stream_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, path, key_alias=None, mode="standard"):
    """复用推流核心逻辑"""
    base_rtmp = TG_RTMP_URL_ENV
    chat_id = update.effective_chat.id
    
    if not base_rtmp:
        await context.bot.send_message(chat_id=chat_id, text="❌ 未配置 TG_RTMP_URL")
        return

    stream_key = None
    
    if key_alias:
        stream_key = get_key(key_alias)
        if not stream_key:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ 找不到密钥: {key_alias}")
            return
    else:
        _, default_key = get_default_key()
        if default_key:
            stream_key = default_key
    
    target_rtmp = ""
    if stream_key:
        if not base_rtmp.endswith("/") and not stream_key.startswith("/"):
            base_rtmp += "/"
        target_rtmp = base_rtmp + stream_key
    else:
        target_rtmp = base_rtmp

    base_url = get_public_url()
    if not base_url:
        await context.bot.send_message(chat_id=chat_id, text="❌ 隧道未就绪 (Cloudflared 正在启动或重连中，请稍后再试)")
        return

    # Radio 模式参数处理
    extra_payload = {}
    if mode == "radio":
        radio_sel = context.user_data.get('radio_selection', {})
        audio_path = radio_sel.get('audio')
        image_path = radio_sel.get('image')
        if not audio_path or not image_path:
            await context.bot.send_message(chat_id=chat_id, text="❌ Radio 模式参数不全 (需音频+背景)")
            return
        extra_payload = {
            "mode": "radio",
            "audio_path": audio_path,
            "image_path": image_path,
            "base_url": base_url 
        }
        path = "Radio Mode" # 占位符

    # 发送状态提示
    status_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ 正在请求 GitHub Action...")
    
    success, msg, _ = trigger_stream_action(base_url, path, target_rtmp, extra_payload)
    
    # 删除状态提示，发送最终结果
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)
    except: pass
    
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)

# --- 全局错误处理 ---

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if ADMIN_ID:
        try:
            err_msg = str(context.error)[:500]
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"🚨 Bot 内部错误: {err_msg}")
        except: pass

# --- 文件浏览器 ---

ITEMS_PER_PAGE = 10

async def render_browser(update: Update, context: ContextTypes.DEFAULT_TYPE, path="/", page=0, edit_msg=False):
    try:
        files, err = fetch_file_list(path, page=1, per_page=200) 
        
        if err:
            safe_path = escape_md(path)
            safe_err = escape_md(str(err))
            text = f"❌ *读取失败*: `{safe_path}`\n\n🔻 *原因*:\n```\n{safe_err}\n```"
            if edit_msg: 
                await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
            else: 
                await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return

        # ⚡️ 防御性编程: 确保 files 是列表
        if files is None: files = []
        
        files.sort(key=lambda x: (not x.get('is_dir', False), x.get('name', '')))

        total_items = len(files)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
        if page >= total_pages: page = max(0, total_pages - 1)
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
            name = f.get('name', '未命名')
            keyboard.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"br:clk:{idx}")])

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

        # Radio 状态
        radio_sel = context.user_data.get('radio_selection', {})
        audio_path = radio_sel.get('audio')
        image_path = radio_sel.get('image')
        
        status_text = ""
        if audio_path or image_path:
            status_text += "\n\n📻 *Radio 待命:*"
            if audio_path: status_text += f"\n🎵 音频: `{escape_md(os.path.basename(audio_path))}`"
            if image_path: status_text += f"\n🖼 背景: `{escape_md(os.path.basename(image_path))}`"
            
            if audio_path and image_path:
                keyboard.insert(0, [InlineKeyboardButton("🚀 启动 Radio 推流", callback_data="br:start_radio")])
            else:
                keyboard.insert(0, [InlineKeyboardButton("⚠️ 需选音频+图片", callback_data="br:noop")])

        markup = InlineKeyboardMarkup(keyboard)
        safe_path = escape_md(path)
        text = f"📂 *当前路径:* `{safe_path}`\n📄 共 {total_items} 项 (第 {page+1}/{total_pages or 1} 页){status_text}"

        if edit_msg:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Render browser error: {e}")
        err_text = f"❌ 渲染界面出错: {str(e)}"
        try:
            if edit_msg:
                await update.callback_query.edit_message_text(err_text)
            else:
                await update.message.reply_text(err_text)
        except: pass

async def browser_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理浏览器按钮点击"""
    query = update.callback_query
    
    try:
        # 1. 立即响应，消除转圈 (必须在所有逻辑之前)
        await query.answer()
        
        data = query.data
        parts = data.split(':')
        if len(parts) < 2: return
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
            await query.message.reply_text("🚀 启动中...", parse_mode=ParseMode.MARKDOWN)
            await trigger_stream_logic(update, context, None, mode="radio")
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
            if idx >= len(current_files): 
                await query.answer("文件列表已过期，请刷新", show_alert=True)
                return
            
            item = current_files[idx]
            safe_name = escape_md(item['name'])
            
            if item['is_dir']:
                keyboard = [
                    [InlineKeyboardButton("📂 进入目录", callback_data=f"br:enter:{idx}")],
                    [InlineKeyboardButton("📻 设为广播音频源", callback_data=f"br:set_audio:{idx}")],
                    [InlineKeyboardButton("🖼 设为广播背景", callback_data=f"br:set_image:{idx}")],
                    [InlineKeyboardButton("🔙 返回", callback_data="br:act:back")]
                ]
                markup = InlineKeyboardMarkup(keyboard)
                msg = f"📂 *选中目录:*\n`{safe_name}`"
                await query.edit_message_text(msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
            else:
                keyboard = [
                    [InlineKeyboardButton("📺 视频推流", callback_data=f"br:act:stream:{idx}")],
                    [InlineKeyboardButton("📻 设为广播音频", callback_data=f"br:set_audio:{idx}")],
                    [InlineKeyboardButton("🖼 设为广播背景", callback_data=f"br:set_image:{idx}")],
                    [InlineKeyboardButton("⬇️ 下载", callback_data=f"br:act:dl:{idx}")],
                    [InlineKeyboardButton("🔙 返回", callback_data="br:act:back")]
                ]
                markup = InlineKeyboardMarkup(keyboard)
                size_mb = round(item.get('size', 0) / (1024*1024), 2)
                msg = f"📄 *选中文件:*\n`{safe_name}`\n📏 {size_mb} MB"
                await query.edit_message_text(msg, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
            return
        
        if action == "enter":
            idx = int(parts[2])
            if idx < len(current_files):
                item = current_files[idx]
                new_path = os.path.join(current_path, item['name']).replace("\\", "/")
                await render_browser(update, context, new_path, 0, True)
            return

        if action == "set_audio":
            idx = int(parts[2])
            if idx < len(current_files):
                item = current_files[idx]
                full_path = os.path.join(current_path, item['name']).replace("\\", "/")
                if 'radio_selection' not in context.user_data: context.user_data['radio_selection'] = {}
                context.user_data['radio_selection']['audio'] = full_path
                await query.answer("✅ 已设为音频源", show_alert=False)
                await render_browser(update, context, current_path, current_page, True)
            return

        if action == "set_image":
            idx = int(parts[2])
            if idx < len(current_files):
                item = current_files[idx]
                full_path = os.path.join(current_path, item['name']).replace("\\", "/")
                if 'radio_selection' not in context.user_data: context.user_data['radio_selection'] = {}
                context.user_data['radio_selection']['image'] = full_path
                await query.answer("✅ 已设为背景源", show_alert=False)
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
                await query.message.reply_text(f"🚀 准备推流: `{safe_name}`", parse_mode=ParseMode.MARKDOWN)
                await trigger_stream_logic(update, context, full_path)
                
            elif sub_act == "dl":
                base_url = get_public_url()
                if not base_url:
                    await query.message.reply_text("❌ 隧道未启动")
                    return
                from urllib.parse import quote
                dl_url = f"{base_url}/d{quote(full_path)}"
                success, msg = add_aria2_task(dl_url)
                if not success: msg = escape_text(msg)
                await query.message.reply_text(f"📥 下载任务:\n{msg}", parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        try:
            await query.answer("❌ 操作发生错误", show_alert=True)
            # 尝试发送错误详情
            await query.message.reply_text(f"❌ 错误: {str(e)[:100]}")
        except: pass

async def browser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    path = context.args[0] if context.args else "/"
    await render_browser(update, context, path, 0, False)

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
        await update.message.reply_text("用法: `/dl http://url`", parse_mode=ParseMode.MARKDOWN)
        return
    success, msg = add_aria2_task(context.args[0])
    if not success: msg = escape_text(msg)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def trigger_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("建议使用「📂 文件」菜单。", parse_mode=ParseMode.MARKDOWN)
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
    if add_key(args[0], args[1]):
        await update.message.reply_text(f"✅ 已保存: `{escape_md(args[0])}`", parse_mode=ParseMode.MARKDOWN)

async def del_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    if not context.args: return
    if delete_key(context.args[0]):
        await update.message.reply_text(f"🗑 已删除: `{escape_md(context.args[0])}`", parse_mode=ParseMode.MARKDOWN)

async def list_keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    keys = get_all_keys()
    base_rtmp = TG_RTMP_URL_ENV or "❌ 未配置"
    msg = f"📺 *推流配置:*\n🔗 Base: `{escape_md(base_rtmp)}`\n\n"
    if not keys: msg += "(空)"
    for k, v in keys.items(): 
        mask_v = f"...{v[-4:]}" if len(v) > 4 else "***"
        msg += f"🔸 `{escape_md(k)}`: `{mask_v}`\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update.effective_user.id): return
    text = update.message.text
    
    if text == "📂 文件": await browser_command(update, context)
    elif text == "📊 状态": await send_status(update, context)
    elif text == "📥 任务": await send_tasks(update, context)
    elif text == "⬇️ 下载": await send_download_help(update, context)
    elif text == "📺 推流设置": await show_stream_menu(update, context)
    elif text == "📝 日志": await send_logs(update, context)
    elif text == "⚙️ 管理": await show_admin_menu(update, context)
    elif text == "❓ 帮助": await send_help(update, context)
    elif text == "🔄 重启服务": await restart_services(update, context)
    elif text == "🔑 查看密码": await send_admin_pass(update, context)
    elif text == "👀 查看配置": await list_keys_command(update, context)
    elif text == "➕ 添加配置": await send_add_key_help(update, context)
    elif text == "🗑 删除配置": await send_del_key_help(update, context)
    elif text == "🔙 返回主菜单": await start(update, context)

# --- 辅助消息发送 ---

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
    await update.message.reply_text(get_system_stats(), parse_mode=ParseMode.MARKDOWN)

async def send_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_aria2_status(), parse_mode=ParseMode.MARKDOWN)

async def send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_file = get_log_file_path("alist")
    if os.path.exists(log_file):
        await update.message.reply_document(document=open(log_file, 'rb'))
    else: await update.message.reply_text("❌ 日志不存在")

async def restart_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ 重启中...")
    restart_pm2_services()

async def send_admin_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = get_admin_pass() or "未知"
    await update.message.reply_text(f"🔑 `{escape_md(pwd)}`", parse_mode=ParseMode.MARKDOWN)

async def send_download_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("发送 `/dl 链接` 下载，或使用「📂 文件」菜单。", parse_mode=ParseMode.MARKDOWN)

async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 *指南*\n1. 使用「📂 文件」浏览网盘\n2. 点击文件可直接推流或下载\n3. /stream 手动推流", parse_mode=ParseMode.MARKDOWN)

async def monitor_services_job(context: ContextTypes.DEFAULT_TYPE):
    # 简化的监控逻辑，防止阻塞
    pass
