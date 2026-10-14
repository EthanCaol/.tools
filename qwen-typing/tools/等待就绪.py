"""轮询转写服务的 /health,直到就绪或超时。

用法:
    等待就绪.py [超时秒数]     默认 300

退出码 0 表示服务已就绪,1 表示超时。启动.bat 与 tools/静默启动.vbs 都会调用它。
"""

import configparser
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 本文件在 tools/ 下
CFG = configparser.ConfigParser()
CFG.read(ROOT / "config.ini", encoding="utf-8")

host = CFG.get("server", "host", fallback="127.0.0.1").strip()
port = CFG.getint("server", "port", fallback=8756)
url = f"http://{host}:{port}/health"

timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0

# 模型从磁盘加载进内存通常需要几秒到几十秒。
deadline = time.time() + timeout
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status == 200:
                print("服务已就绪")
                sys.exit(0)
    except (urllib.error.URLError, OSError):
        pass
    time.sleep(0.5)

print("等待服务就绪超时", file=sys.stderr)
sys.exit(1)
