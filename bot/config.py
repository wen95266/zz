
import os
import itertools
from dotenv import load_dotenv

# 加载环境变量
HOME = os.path.expanduser("~")
load_dotenv(os.path.join(HOME, ".env"))

# 机器人配置
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

# 推流基础地址 (Base URL)
TG_RTMP_URL_ENV = os.getenv("TG_RTMP_URL")

# --- GitHub 多账号逻辑 ---
_multi_accounts_str = os.getenv("GITHUB_ACCOUNTS_LIST", "")
GITHUB_POOL = []

if _multi_accounts_str:
    try:
        items = _multi_accounts_str.split(',')
        for item in items:
            item = item.strip()
            if '|' in item:
                r, t = item.split('|', 1)
                GITHUB_POOL.append({"repo": r.strip(), "token": t.strip()})
    except Exception as e:
        print(f"⚠️ 解析 GITHUB_ACCOUNTS_LIST 失败: {e}")

_account_cycle = itertools.cycle(GITHUB_POOL) if GITHUB_POOL else None

def get_next_github_account():
    if not _account_cycle: return None
    return next(_account_cycle)

def get_account_count():
    return len(GITHUB_POOL)

# --- 系统配置 ---
TUNNEL_MODE = os.getenv("TUNNEL_MODE", "quick")
CLOUDFLARE_TOKEN = os.getenv("CLOUDFLARE_TOKEN")
ALIST_DOMAIN = os.getenv("ALIST_DOMAIN")
ARIA2_RPC_SECRET = os.getenv("ARIA2_RPC_SECRET")

# Alist 认证配置 (新增)
ALIST_PASSWORD = os.getenv("ALIST_PASSWORD")
ALIST_TOKEN = os.getenv("ALIST_TOKEN")

HOME_DIR = HOME

# 主菜单布局 (已更新)
MAIN_MENU = [
    ["📂 文件", "📊 状态", "📥 任务"], # 新增 "文件" 入口
    ["⬇️ 下载", "☁️ 隧道", "⚙️ 管理"],
    ["📺 推流设置", "📝 日志", "❓ 帮助"]
]

# 管理子菜单
ADMIN_MENU = [
    ["📉 GitHub 用量", "🔄 重启服务"],
    ["🔑 查看密码", "🔙 返回主菜单"]
]

# 推流设置子菜单
STREAM_MENU = [
    ["👀 查看配置", "➕ 添加配置"],
    ["🗑 删除配置", "🔙 返回主菜单"]
]

def validate_config():
    if not BOT_TOKEN:
        print("❌ 错误: ~/.env 中缺少 BOT_TOKEN")
        exit(1)

def check_auth(user_id):
    if not ADMIN_ID: return True
    return str(user_id) == str(ADMIN_ID)
