
import json
import os
from .config import HOME_DIR

DATA_DIR = os.path.join(HOME_DIR, "bot", "data")
DATA_FILE = os.path.join(DATA_DIR, "stream_keys.json")

def _load_data():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    
    if not os.path.exists(DATA_FILE):
        return {}
    
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def _save_data(data):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_key(name, url):
    """添加或更新密钥"""
    data = _load_data()
    data[name] = url.strip()
    _save_data(data)

def delete_key(name):
    """删除密钥"""
    data = _load_data()
    if name in data:
        del data[name]
        _save_data(data)
        return True
    return False

def get_key(name):
    """获取指定名称的密钥"""
    data = _load_data()
    return data.get(name)

def get_all_keys():
    """获取所有密钥"""
    return _load_data()

def get_default_key():
    """获取第一个密钥作为默认值"""
    data = _load_data()
    if data:
        # 返回 (名称, URL)
        key = next(iter(data))
        return key, data[key]
    return None, None
