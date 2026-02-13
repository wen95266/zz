
import os
import subprocess
import psutil
import re
import requests
import json
import logging
import socket
from .config import HOME_DIR, TUNNEL_MODE, ARIA2_RPC_SECRET, ALIST_DOMAIN

logger = logging.getLogger(__name__)

def check_port(port):
    """检查本地端口是否开放"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        result = sock.connect_ex(('127.0.0.1', int(port)))
        sock.close()
        return result == 0
    except:
        return False

def check_services_health():
    # Termux 中 psutil 经常拿不到进程列表，改为检测端口
    # Alist: 5244
    # Aria2: 6800
    # Tunnel: 49500 (在 generate-config.js 中配置了 --metrics localhost:49500)
    status = {
        'alist': check_port(5244),
        'aria2c': check_port(6800),
        'cloudflared': check_port(49500)
    }
    
    # 如果端口没通，尝试兜底用进程名查一次 (兼容部分特殊情况)
    if not all(status.values()):
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                name = proc.info['name'] or ""
                cmdline = " ".join(proc.info['cmdline'] or [])
                if not status['alist'] and ('alist' in name or 'alist' in cmdline): status['alist'] = True
                if not status['aria2c'] and ('aria2c' in name or 'aria2c' in cmdline): status['aria2c'] = True
                if not status['cloudflared'] and ('cloudflared' in name or 'cloudflared' in cmdline): status['cloudflared'] = True
            except (psutil.NoSuchProcess, psutil.AccessDenied): continue
            
    return status

def get_public_url():
    if ALIST_DOMAIN:
        url = ALIST_DOMAIN.strip()
        if not url.startswith("http"): url = "https://" + url
        return url
    if TUNNEL_MODE == "quick":
        # Cloudflared 的 Quick Tunnel URL 通常打印在 stderr 中 (tunnel-error.log)
        # 我们同时检查 error 和 out 日志
        log_files = ["tunnel-error.log", "tunnel-out.log"]
        
        for log_file in log_files:
            try:
                log_path = os.path.join(HOME_DIR, ".pm2", "logs", log_file)
                if os.path.exists(log_path):
                    # 读取最后 4KB 内容
                    with open(log_path, 'rb') as f:
                        try:
                            f.seek(-4096, 2)
                        except OSError:
                            f.seek(0)
                        logs = f.read().decode('utf-8', errors='ignore')
                        
                    urls = re.findall(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', logs)
                    if urls: return urls[-1]
            except Exception: pass
    return None

def get_disk_usage():
    """获取磁盘使用情况"""
    try:
        du = psutil.disk_usage(HOME_DIR)
        total = round(du.total / (1024**3), 1)
        used = round(du.used / (1024**3), 1)
        free = round(du.free / (1024**3), 1)
        percent = du.percent
        return f"{used}GB / {total}GB ({percent}%)", percent
    except Exception:
        return "未知", 0

def get_system_stats():
    msg = "*📊 系统状态:*"
    health = check_services_health()
    msg += f"\n{'✅' if health['alist'] else '❌'} `alist`"
    msg += f"\n{'✅' if health['aria2c'] else '❌'} `aria2c`"
    msg += f"\n{'✅' if health['cloudflared'] else '❌'} `tunnel`"
    
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk_str, disk_percent = get_disk_usage()
    
    msg += f"\n\n🔥 CPU: `{cpu}%`"
    msg += f"\n🧠 RAM: `{ram}%`"
    msg += f"\n💾 Disk: `{disk_str}`"
    
    if disk_percent > 90:
        msg += "\n⚠️ *警告: 磁盘空间即将耗尽！*"
        
    return msg

def get_log_file_path(service="alist"):
    """返回日志文件的绝对路径"""
    return os.path.join(HOME_DIR, ".pm2", "logs", f"{service}-out.log")

def restart_pm2_services():
    try:
        subprocess.run(["pm2", "restart", "all"], check=True)
        return True, "✅ 重启指令已发送"
    except Exception as e: return False, f"❌ 失败: {str(e)}"

def get_admin_pass():
    try:
        # 指定数据目录查询密码
        data_dir = os.path.join(HOME_DIR, "alist-data")
        cmd = [os.path.join(HOME_DIR, "bin", "alist"), "admin", "--data", data_dir]
        
        # 增加超时限制，防止卡死
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10).decode('utf-8').strip()
        
        # ⚠️ 关键修复: 去除 ANSI 颜色代码 (Termux 环境常见)
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_output = ansi_escape.sub('', output)
        
        return clean_output
    except subprocess.TimeoutExpired:
        return "获取失败: 命令超时"
    except Exception as e: 
        return f"获取失败: {str(e)}"

# --- Aria2 相关 ---

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {power_labels[n]}B"

def get_aria2_status():
    rpc_url = "http://127.0.0.1:6800/jsonrpc"
    # 获取全局统计
    payload_global = {"jsonrpc": "2.0", "method": "aria2.getGlobalStat", "id": "stat"}
    # 获取活跃任务
    payload_active = {"jsonrpc": "2.0", "method": "aria2.tellActive", "id": "list", "params": [["gid", "status", "totalLength", "completedLength", "downloadSpeed", "files"]]}
    
    if ARIA2_RPC_SECRET:
        token_param = f"token:{ARIA2_RPC_SECRET}"
        payload_global.setdefault("params", []).insert(0, token_param)
        payload_active["params"].insert(0, token_param)

    try:
        # 查询全局
        r_g = requests.post(rpc_url, json=payload_global, timeout=3).json()
        g_stat = r_g.get("result", {})
        speed_down = format_bytes(int(g_stat.get("downloadSpeed", 0)))
        speed_up = format_bytes(int(g_stat.get("uploadSpeed", 0)))
        
        # 查询任务
        r_a = requests.post(rpc_url, json=payload_active, timeout=3).json()
        tasks = r_a.get("result", [])
        
        msg = f"📉 *Aria2 概览*\n⬇️ {speed_down}/s  ⬆️ {speed_up}/s\n"
        msg += f"活动: {g_stat.get('numActive')}  等待: {g_stat.get('numWaiting')}  停止: {g_stat.get('numStopped')}\n\n"
        
        if not tasks:
            msg += "💤 当前没有正在下载的任务"
        else:
            for t in tasks:
                try:
                    total = int(t['totalLength'])
                    done = int(t['completedLength'])
                    speed = int(t['downloadSpeed'])
                    percent = round((done/total)*100, 1) if total > 0 else 0
                    
                    # 获取文件名
                    file_path = t['files'][0]['path']
                    file_name = os.path.basename(file_path) if file_path else "未知文件"
                    
                    msg += f"📄 `{file_name}`\n"
                    msg += f"└ {percent}% ({format_bytes(speed)}/s)\n"
                except:
                    msg += "📄 解析任务详情失败\n"
                    
        return msg
    except Exception as e:
        return f"❌ 无法连接 Aria2 RPC: {str(e)}"

def add_aria2_task(url):
    rpc_url = "http://127.0.0.1:6800/jsonrpc"
    payload = {"jsonrpc": "2.0", "method": "aria2.addUri", "id": "bot", "params": [[url]]}
    if ARIA2_RPC_SECRET: payload["params"].insert(0, f"token:{ARIA2_RPC_SECRET}")
    try:
        r = requests.post(rpc_url, json=payload, timeout=5)
        res = r.json()
        if "error" in res: return False, f"Aria2 报错: {res['error']['message']}"
        return True, f"✅ 任务已添加 GID: `{res.get('result')}`"
    except Exception as e: return False, f"❌ 无法连接 Aria2: {str(e)}"
