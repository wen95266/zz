
import fs from 'fs';
import path from 'path';
import os from 'os';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const HOME = os.homedir();

// 1. 获取 Python 解释器路径
let pythonExec = "python3";
try {
  const check = execSync("command -v python3").toString().trim();
  if (check) {
    pythonExec = check;
  }
} catch (e) {
  const termuxPy = "/data/data/com.termux/files/usr/bin/python3";
  if (fs.existsSync(termuxPy)) {
    pythonExec = termuxPy;
  }
}

console.log(`ℹ️ Python 路径: ${pythonExec}`);

// 2. 检测 termux-chroot
let termuxChrootPath = "";
let useProot = false;
try {
  termuxChrootPath = execSync("command -v termux-chroot").toString().trim();
  if (termuxChrootPath) {
      useProot = true;
      console.log(`ℹ️ 检测到 Termux 环境: 将启用 termux-chroot (${termuxChrootPath})`);
  }
} catch (e) {
  console.log("ℹ️ 未检测到 termux-chroot，将直接运行");
}

// 3. 准备目录
const alistDataDir = path.join(HOME, 'alist-data');
if (!fs.existsSync(alistDataDir)) {
    try {
        fs.mkdirSync(alistDataDir, { recursive: true });
        console.log(`✅ 创建 Alist 数据目录: ${alistDataDir}`);
    } catch (e) {
        console.error("❌ 无法创建数据目录:", e);
    }
}

// 4. 定义 Tunnel 参数 (强制 Quick Tunnel)
// 优化: 使用 localhost，并设置为 auto 协议
const tunnelArgs = [
    'tunnel', 
    '--url', 'http://localhost:5244', 
    '--no-autoupdate', 
    '--protocol', 'auto', 
    '--edge-ip-version', '4', 
    '--metrics', '127.0.0.1:49500'
];

console.log(`ℹ️ 配置模式: 强制使用 Quick Tunnel (临时随机域名)`);

// 5. 定义 App 配置
// Alist
const alistApp = {
  name: "alist",
  script: path.join(HOME, "bin/alist"),
  args: ["server", "--data", alistDataDir],
  cwd: alistDataDir,
  interpreter: "none",
  autorestart: true,
  restart_delay: 5000,
  max_restarts: 10,
};

// Cloudflared
const cloudflaredApp = {
    name: "tunnel",
    script: path.join(HOME, "bin/cloudflared"),
    args: tunnelArgs,
    interpreter: "none",
    autorestart: true,
    restart_delay: 5000,
    max_restarts: 10
};

// 如果在 Termux 下，使用 termux-chroot 启动
if (useProot) {
    // ⚠️ 关键修复: 
    // Alist 保持在原生环境运行，不使用 termux-chroot。
    // 这样它能正确绑定到 0.0.0.0/localhost，避免 proot 网络隔离导致的连接拒绝 (Error 1033/530)。
    
    // Cloudflared 必须使用 termux-chroot，否则无法解析 DNS 连接 Cloudflare 边缘节点。
    cloudflaredApp.script = termuxChrootPath;
    cloudflaredApp.interpreter = "bash";
    cloudflaredApp.args = [path.join(HOME, "bin/cloudflared"), ...tunnelArgs];
}

// 6. 最终 PM2 配置
const config = {
  apps: [
    alistApp,
    {
      name: "aria2",
      script: "aria2c",
      args: [`--conf-path=${path.join(HOME, ".aria2/aria2.conf")}`],
      interpreter: "none", 
      autorestart: true,
      restart_delay: 5000,
    },
    {
      name: "bot",
      script: pythonExec,
      args: ["-u", "-m", "bot.main"],
      cwd: __dirname,
      interpreter: "none",
      autorestart: true,
      restart_delay: 3000,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1"
      }
    },
    cloudflaredApp
  ]
};

// 7. 写入文件
const outputPath = path.join(__dirname, 'ecosystem.config.json');
try {
  fs.writeFileSync(outputPath, JSON.stringify(config, null, 2));
  console.log(`✅ 已生成 PM2 配置文件: ${outputPath}`);
} catch (err) {
  console.error("❌ 生成配置文件失败:", err);
  process.exit(1);
}
