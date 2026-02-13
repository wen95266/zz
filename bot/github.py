
import requests
import urllib.parse
import re
from .config import get_next_github_account, get_account_count, GITHUB_POOL
from .alist_api import get_token, get_file_info

def escape_text(text):
    """转义 Markdown V1 特殊字符"""
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

def trigger_stream_action(base_url, raw_path, target_rtmp_url, extra_payload=None):
    """
    触发 GitHub Actions 进行推流
    Args:
        base_url: Alist 的公网地址 (Tunnel URL)
        raw_path: 视频文件路径 (标准模式用)
        target_rtmp_url: 目标 RTMP 推流地址
        extra_payload: 字典，Radio 模式下的额外参数
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

    # 获取 Alist Token
    alist_token = get_token() or ""
    video_url = ""
    
    # 构造 Payload
    client_payload = {
        "rtmp_url": target_rtmp_url,
        "alist_token": alist_token # 无论何种模式，都传递 Token 以备不时之需
    }

    # 处理模式差异
    if extra_payload and extra_payload.get("mode") == "radio":
        # Radio 模式
        client_payload.update(extra_payload)
        client_payload["video_url"] = "radio_placeholder" # 避免 Workflow 报错
        
        display_msg = "📻 *Radio 推流任务*\n"
        display_msg += f"🎵 音频源: `{escape_text(extra_payload.get('audio_path'))}`\n"
        display_msg += f"🖼 背景源: `{escape_text(extra_payload.get('image_path'))}`"
        
    else:
        # 标准视频模式
        try:
            # 1. 尝试通过 API 获取真实直链
            file_data = get_file_info(raw_path)
            if file_data and file_data.get("code") == 200:
                raw_url = file_data["data"].get("raw_url", "")
                if raw_url:
                    if raw_url.startswith("http"):
                        # 🚨 关键检查: 如果 Alist 返回的是本地 IP (127.0.0.1/192.168/localhost)
                        # 说明 Alist 没配置 Site URL。GitHub 无法访问本地 IP。
                        # 此时必须强制回退到使用 base_url (Tunnel) 的手动构造模式。
                        is_local = re.search(r'://(127\.|10\.|172\.(1[6-9]|2\d|3[0-1])\.|192\.168\.|localhost)', raw_url)
                        if not is_local:
                             video_url = raw_url
                        else:
                             print(f"⚠️ 检测到本地链接 {raw_url}，将使用 Tunnel 回退方案")
                    else:
                        # 相对路径，加上 Base URL (Tunnel)
                        video_url = f"{base_url}{raw_url}"
                        if alist_token:
                            sep = "&" if "?" in video_url else "?"
                            video_url += f"{sep}token={alist_token}"
        except Exception as e:
            print(f"获取文件信息失败: {e}")

        # 2. 回退方案: 手动构造 /d 下载链接 (最稳妥，走 Tunnel)
        if not video_url:
            if not raw_path.startswith("/"): raw_path = "/" + raw_path
            # 使用 quote 编码路径，确保空格和中文正常
            encoded_path = urllib.parse.quote(raw_path)
            # 修正: Alist 的 /d 链接通常是 /d/path/to/file
            # 注意: encoded_path 已经包含了开头的 /
            video_url = f"{base_url}/d{encoded_path}"
            if alist_token:
                video_url += f"?token={alist_token}"
        
        client_payload["video_url"] = video_url
        client_payload["mode"] = "standard"
        
        display_msg = f"📺 *视频推流任务*\n📄 文件: `{escape_text(raw_path)}`"

    # GitHub API 请求
    api_url = f"https://api.github.com/repos/{repo}/dispatches"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "event_type": "start_stream",
        "client_payload": client_payload
    }

    try:
        r = requests.post(api_url, headers=headers, json=data, timeout=10)
        safe_repo = escape_text(repo)

        if r.status_code == 204:
            msg = f"✅ *指令已发送* (账号池: {pool_size})\n"
            msg += f"👤 仓库: `{safe_repo}`\n\n"
            msg += display_msg
            return True, msg, video_url
        elif r.status_code == 404:
            return False, f"❌ 找不到仓库 `{safe_repo}` (404)\n可能原因: 仓库名填错 / Token 权限不足 / 仓库是私有的", video_url
        elif r.status_code == 401:
            return False, f"❌ Token 无效 (401)\n请检查 GITHUB_ACCOUNTS_LIST 配置", video_url
        else:
            return False, f"❌ GitHub 拒绝: {r.status_code}\n{escape_text(r.text)}", video_url
    except Exception as e:
        return False, f"❌ 网络请求失败: {escape_text(str(e))}", video_url
