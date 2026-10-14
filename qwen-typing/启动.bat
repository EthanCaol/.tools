@echo off
cd /d "%~dp0"
title 语音输入 启动

set PY=%~dp0.venv\Scripts\python.exe
set AHK=%LOCALAPPDATA%\Programs\AutoHotkey\v2\AutoHotkey64.exe

if not exist "%PY%" (
    echo [错误] 未找到虚拟环境 %PY%
    echo 请先双击 tools\安装.bat 创建环境并安装依赖。
    pause
    exit /b 1
)

if not exist "%AHK%" (
    echo [错误] 未找到 AutoHotkey v2: %AHK%
    echo 请先安装 AutoHotkey v2。
    pause
    exit /b 1
)

rem 已有实例在运行时先退出,避免端口冲突
"%PY%" "%~dp0tools\停止全部.py" >nul 2>&1

echo 正在启动转写服务(首次启动要加载模型,请稍候)...
start "语音输入(Qwen) server" /min "%PY%" "%~dp0server.py"

echo 等待模型加载完成...
"%PY%" "%~dp0tools\等待就绪.py"
if errorlevel 1 (
    echo.
    echo [错误] 服务未能在超时时间内就绪。
    echo 请查看 logs\server.log 末尾的错误信息。
    pause
    exit /b 1
)

echo 注册热键并启动听写脚本...
start "" "%AHK%" "%~dp0dictate.ahk"

rem 热键名从 config.ini 读,避免提示语与配置不一致
set HK=
for /f "tokens=2 delims==" %%k in ('findstr /b /c:"key=" "%~dp0config.ini"') do set HK=%%k

echo.
echo 启动完成。按住 %HK% 说话,松手后文字会插入当前光标处。
echo 退出请右键点击右下角托盘图标,选「退出(并停止服务)」。
timeout /t 8 >nul
