
import requests
import urllib.parse
from .config import get_next_github_account, get_account_count, GITHUB_POOL
from .alist_api import get_token

def escape_text(text):
    """转义 Markdown V1 特殊字符"""
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

def trigger_stream_action(base_url, raw_path, target_rtmp_url):
    """
    触发 GitHub Actions 进行推流
    Args:
        base_url: Alist 的公网地址
        raw_path: 视频文件路径
        target_rtmp_url: 目标 RTMP 推流地址
    """
    if not target_rtmp_url:
        return False, "❌ 错误: 未提供 RTMP 推流地址", ""

    # 获取当前轮到的账号
    account = get_next_github_account()
    if not account:
        return False, "❌ 未配置 GitHub 账号！请在 `~/.env` 设置 GITHUB_ACCOUNTS_LIST", ""

    repo = account['repo']
    token = account['token']
    pool_size = get_account_count()

    # 路径处理与 URL 编码
    if not raw_path.startswith("/"): raw_path = "/" + raw_path
    
    # ⚡️ 修复: 保留路径中的斜杠 '/' 不被转义，只转义文件名中的特殊字符 (如空格)
    encoded_path = urllib.parse.quote(raw_path, safe='/')
    video_url = f"{base_url}/d{encoded_path}"

    # 获取 Alist Token 用于权限验证
    alist_token = get_token() or ""

    # GitHub API 请求
    api_url = f"https://api.github.com/repos/{repo}/dispatches"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "event_type": "start_stream",
        "client_payload": {
            "video_url": video_url,
            "rtmp_url": target_rtmp_url,
            "alist_token": alist_token  # 传递 Token 给 Action
        }
    }

    try:
        r = requests.post(api_url, headers=headers, json=data, timeout=10)
        
        # 移除遮罩，显示完整仓库名
        safe_repo = escape_text(repo)

        if r.status_code == 204:
            # 204 表示 GitHub 成功接收了请求
            msg = f"✅ *指令已发送* (账号池: {pool_size})\n"
            msg += f"👤 仓库: `{safe_repo}`\n\n"
            msg += "⚠️ *如果直播没开始:*\n"
            msg += "请检查你的 GitHub 仓库中是否存在 `.github/workflows/stream.yml` 文件。\n"
            msg += "👉 *Bot 只是发送指令，实际推流由 GitHub 运行你仓库里的文件。*"
            return True, msg, video_url
        elif r.status_code == 404:
            return False, f"❌ 找不到仓库 `{safe_repo}` (404)\n可能原因: 仓库名填错 / Token 权限不足 / 仓库是私有的", video_url
        elif r.status_code == 401:
            return False, f"❌ Token 无效 (401)\n请检查 GITHUB_ACCOUNTS_LIST 配置", video_url
        else:
            return False, f"❌ GitHub 拒绝: {r.status_code}\n{escape_text(r.text)}", video_url
    except Exception as e:
        return False, f"❌ 网络请求失败: {escape_text(str(e))}", video_url

def get_single_usage(repo, token):
    """查询单个账号的额度使用情况"""
    try:
        # 从 repo (username/repo) 提取 owner (可能是 User 也可能是 Org)
        owner = repo.split('/')[0]
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # 1. 检查账号类型 (User vs Organization)
        # 这一步非常重要，因为 billing API 的路径不同，且可以提前验证 Token 有效性
        type_url = f"https://api.github.com/users/{owner}"
        r_type = requests.get(type_url, headers=headers, timeout=5)

        if r_type.status_code == 401:
             return False, "Token 无效 (401)"
        elif r_type.status_code == 404:
             return False, "用户/组织不存在 (404)"
        elif r_type.status_code != 200:
             # 如果连用户信息都读不到，直接返回错误
             return False, f"API 错误 {r_type.status_code}"

        account_type = r_type.json().get("type", "User")

        # 2. 根据类型选择 Billing API 接口
        if account_type == "Organization":
            url = f"https://api.github.com/orgs/{owner}/settings/billing/actions"
        else:
            url = f"https://api.github.com/users/{owner}/settings/billing/actions"
            
        r = requests.get(url, headers=headers, timeout=5)
        
        if r.status_code == 200:
            data = r.json()
            used = data.get("total_minutes_used", 0)
            limit = data.get("included_minutes", 2000)
            return True, {"used": used, "limit": limit}
        elif r.status_code == 403:
            return False, "权限不足 (缺少 user 权限)"
        elif r.status_code == 404 or r.status_code == 410:
            # 404/410: Fine-grained Token 不支持 Billing，或者 API 对该类型账号不可用
            # 这不代表 Token 无法用于推流，因此标记为成功但 limit=-1
            return True, {"used": 0, "limit": -1}
        else:
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)

def get_all_usage_stats():
    """获取所有配置账号的统计信息"""
    results = []
    if not GITHUB_POOL:
        return []

    for acc in GITHUB_POOL:
        repo = acc['repo']
        success, info = get_single_usage(repo, acc['token'])
        
        # 移除遮罩，直接显示完整用户名
        user = repo.split('/')[0]
        safe_name = escape_text(user)
        
        if success:
            if info.get('limit') == -1:
                # 无法获取额度的情况 (Fine-grained token 等)
                results.append(f"🟢 *{safe_name}*: `额度未知` (API受限)")
            else:
                percent = 0
                if info['limit'] > 0:
                    percent = round((info['used'] / info['limit']) * 100, 1)
                
                icon = "🟢"
                if percent > 80: icon = "🟡"
                if percent > 95: icon = "🔴"
                
                results.append(f"{icon} *{safe_name}*: `{info['used']}` / `{info['limit']}` ({percent}%)")
        else:
            # 错误信息必须转义，否则包含 _ 等字符会报错
            safe_info = escape_text(info)
            results.append(f"⚪ *{safe_name}*: ⚠️ {safe_info}")
            
    return results
