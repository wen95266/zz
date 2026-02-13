
import requests
import logging
import json
import re
from .system import get_admin_pass
from .config import ALIST_PASSWORD, ALIST_TOKEN

logger = logging.getLogger(__name__)

ALIST_API_URL = "http://127.0.0.1:5244"
_cached_token = None

def get_token():
    """获取或刷新 Alist Token"""
    global _cached_token
    
    # 策略 0: 直接使用环境变量配置的 Token (最高优先级)
    if ALIST_TOKEN:
        return ALIST_TOKEN

    if _cached_token: return _cached_token
    
    password = ALIST_PASSWORD
    
    # 策略 1: 如果环境变量没配密码，尝试从系统/文件自动获取 (兼容旧模式)
    if not password:
        raw_output = get_admin_pass()
        if raw_output and "失败" not in raw_output:
            # 尝试简单解析
            if ":" in raw_output:
                # 可能是 "admin: 123" 这种格式
                parts = raw_output.split(":")
                if len(parts) > 1:
                    password = parts[-1].strip()
            if not password:
                # 可能是纯密码
                password = raw_output.strip()
    
    if not password:
        logger.error("❌ 未配置 ALIST_PASSWORD 且无法自动获取密码，请在 ~/.env 中配置。")
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
            logger.error(f"Alist 登录失败: {data} (User: admin, Pass: {password})")
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
        return None, "❌ 认证失败: 无法获取 Token。\n建议: 编辑 ~/.env 文件，直接填入 ALIST_PASSWORD=你的密码"

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
            
        # 如果是 401 或 Token 无效，尝试清除缓存重试一次 (仅当没有硬编码 Token 时)
        if data.get("code") in [401, 403] and not ALIST_TOKEN:
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
        return None, f"网络错误: {str(e)}"

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
