"""停止转写服务与听写脚本。

由 启动.bat 与 tools/静默启动.vbs 调用,清理可能残留的旧实例;
托盘菜单的「退出」也用它。

这里按命令行扫描进程,而不是按 PID 文件结束进程:venv 的 pythonw.exe 是一个
launcher,它会 spawn 真正的解释器,一个服务在进程表里对应父子两项,而 PID 文件
里只记录了其中一个。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 本文件在 tools/ 下
PID_PATH = ROOT / "logs" / "server.pid"
PROJECT_NAME = ROOT.name  # 用于在命令行里识别属于本工具的进程


def run_powershell(script):
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True,
    )


def stop_by_commandline(name_test, commandline_marker, label):
    """结束命令行同时满足两个条件的进程。"""
    script = (
        "Get-CimInstance Win32_Process "
        f"| Where-Object {{ ({name_test}) -and $_.CommandLine -like '*{commandline_marker}*' }} "
        "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    result = run_powershell(script)
    if result.returncode != 0:
        print(f"结束{label}时出错: {result.stderr.strip()}")
        return False
    print(f"已结束{label}")
    return True


def stop_server():
    ok = stop_by_commandline(
        "$_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe'",
        f"{PROJECT_NAME}*server.py",
        "转写服务",
    )
    if PID_PATH.exists():
        PID_PATH.unlink(missing_ok=True)
    return ok


def stop_ahk():
    # 用完整脚本路径而不是文件名做匹配:旧项目 whisper-typing 里的听写脚本
    # 也叫 dictate.ahk,只按文件名匹配会把那边的脚本一起杀掉。
    return stop_by_commandline(
        "$_.Name -eq 'AutoHotkey64.exe'",
        str(ROOT / "dictate.ahk"),
        "听写脚本",
    )


def main():
    # 从托盘菜单「退出」调用时带 --no-ahk:听写脚本自己会 ExitApp,
    # 这里再强杀它会让调用方在 RunWait 等待中直接消失。
    if "--no-ahk" not in sys.argv[1:]:
        stop_ahk()
    stop_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
