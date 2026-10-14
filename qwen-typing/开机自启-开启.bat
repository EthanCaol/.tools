@echo off
cd /d "%~dp0"
title whisper-typing 开启开机自启

set VBS=%~dp0tools\静默启动.vbs
set LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\语音输入-Qwen.lnk

if not exist "%VBS%" (
    echo [错误] 未找到 %VBS%
    pause
    exit /b 1
)

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%LNK%'); $sc.TargetPath = '%VBS%'; $sc.WorkingDirectory = '%~dp0'; $sc.Description = '语音输入'; $sc.Save()"

if errorlevel 1 (
    echo [错误] 创建快捷方式失败
    pause
    exit /b 1
)

echo 已开启开机自启。
echo 快捷方式: %LNK%
echo.
echo 下次登录时会自动启动转写服务与听写脚本。
echo 想立即生效又不弹窗口,双击 tools\静默启动.vbs。
pause
