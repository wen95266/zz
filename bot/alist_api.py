
import requests
import logging
import json
import re
from .system import get_admin_pass

logger = logging.getLogger(__name__)

ALIST_API_URL = "http://127.0.0.1:5244"
_cached_token = None

def get_token():
    """获取或刷新 Alist Token"""
    global _cached_token
    if _cached_token: return _cached_token
    
    raw_output = get_admin_pass()
    if not raw_output or "失败" in raw_output:
        logger.error(f"无法获取 Alist 密码信息: {raw_output}")
        return None

    # 解析密码
    # `alist admin` 输出通常为 "admin: 123456"
    password = raw_output.strip()
    
    # 尝试匹配 "admin: xxxxx"
    match = re.search(r'admin:\s*(\S+)', raw_output)
    if match:
        password = match.group(1).strip()
    else:
        # 如果没有 admin: 前缀，尝试取最后一行非空内容 (兜底策略)
        lines = [l.strip() for l in raw_output.split('\n') if l.strip()]
        if lines:
            # 假设最后一行是密码
            password = lines[-1]

    if not password:
        logger.error("解析 Alist 密码为空")
        return None

    try:
        # 尝试登录获取 Token
        url = f"{ALIST_API_URL}/api/auth/login"
        payload = {"username": "admin", "password": password}
        
        r = requests.post(url, json=payload, timeout=5)
        data = r.json()
        
        if data.get("code") == 200:
            _cached_token = data["data"]["token"]
            return _cached_token
        else:
            # 记录更详细的错误，方便排查
            logger.error(f"Alist 登录失败: {data}")
            return None
    except Exception as e:
        logger.error(f"Alist API 连接失败: {e}")
        return None

def fetch_file_list(path="/", page=1, per_page=100):
    """获取文件列表"""
    global _cached_token
    
    # 第一次尝试
    token = get_token()
    if not token: 
        return None, "无法连接 Alist 或密码错误。\n请尝试运行 `./set_pass.sh 您的密码` 并确保 Alist 正在运行。"

    url = f"{ALIST_API_URL}/api/fs/list"
    headers = {"Authorization": token}
    payload = {
        "path": path,
        "page": page,
        "per_page": per_page,
        "refresh": False
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        data = r.json()
        
        # 成功
        if data.get("code") == 200:
            return data["data"]["content"], None
            
        # 如果是 401 或 Token 无效，尝试清除缓存重试一次
        if data.get("code") in [401, 403]:
            logger.info("Token 可能失效，尝试重新获取...")
            _cached_token = None
            token = get_token()
            if token:
                headers["Authorization"] = token
                r = requests.post(url, headers=headers, json=payload, timeout=10)
                data = r.json()
                if data.get("code") == 200:
                    return data["data"]["content"], None

        return None, f"API 错误: {data.get('message')}"
    except Exception as e:
        return None, str(e)

def get_file_info(path):
    """获取单个文件信息"""
    token = get_token()
    if not token: return None
    url = f"{ALIST_API_URL}/api/fs/get"
    headers = {"Authorization": token}
    try:
        r = requests.post(url, headers=headers, json={"path": path}, timeout=5)
        return r.json()
    except:
        return None
