
import requests
import urllib.parse
from .config import get_next_github_account, get_account_count, GITHUB_POOL

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
    encoded_path = urllib.parse.quote(raw_path)
    video_url = f"{base_url}/d{encoded_path}"

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
            "rtmp_url": target_rtmp_url
        }
    }

    try:
        r = requests.post(api_url, headers=headers, json=data)
        if r.status_code == 204:
            # 简单的混淆显示 Token
            mask_repo = repo.split('/')[0] + "/..."
            return True, f"✅ 已发送至 Runner (池: {pool_size})\n👤 账号: `{mask_repo}`", video_url
        else:
            return False, f"❌ GitHub API 错误 ({repo}): {r.status_code}\n{r.text}", video_url
    except Exception as e:
        return False, f"❌ 网络请求失败: {str(e)}", video_url

def get_single_usage(repo, token):
    """查询单个账号的额度使用情况"""
    try:
        # 从 repo (username/repo) 提取 username
        username = repo.split('/')[0]
        url = f"https://api.github.com/users/{username}/settings/billing/actions"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        r = requests.get(url, headers=headers, timeout=5)
        
        if r.status_code == 200:
            data = r.json()
            used = data.get("total_minutes_used", 0)
            limit = data.get("included_minutes", 2000)
            return True, {"used": used, "limit": limit}
        elif r.status_code == 403:
            return False, "权限不足 (缺少 repo 或 user scope)"
        elif r.status_code == 404:
            return False, "找不到用户 (Token 错误?)"
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
        
        # 简单遮罩处理
        user = repo.split('/')[0]
        mask_name = user[:3] + "***" if len(user) > 3 else user
        
        if success:
            percent = 0
            if info['limit'] > 0:
                percent = round((info['used'] / info['limit']) * 100, 1)
            
            icon = "🟢"
            if percent > 80: icon = "🟡"
            if percent > 95: icon = "🔴"
            
            results.append(f"{icon} *{mask_name}*: `{info['used']}` / `{info['limit']}` ({percent}%)")
        else:
            results.append(f"⚪ *{mask_name}*: ⚠️ {info}")
            
    return results
