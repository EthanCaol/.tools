@echo off
cd /d "%~dp0"
title whisper-typing 关闭开机自启

set LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\语音输入-Qwen.lnk

if exist "%LNK%" (
    del "%LNK%"
    echo 已关闭开机自启。下次登录不再自动启动。
) else (
    echo 开机自启本来就是关闭的,没有改动。
)

echo.
echo 已经在运行的服务不受影响。
echo 要立即退出,右键点击右下角托盘图标选「退出(并停止服务)」。
pause
