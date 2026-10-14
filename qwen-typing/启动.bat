@echo off
rem 启动语音输入。等同于双击 tools\静默启动.vbs —— 全程不弹窗口,
rem 服务就绪后右下角会弹一条「语音输入已就绪」。
rem
rem 这里只做转发,不自己启动:双击 .bat 时 Windows 必然先开一个控制台窗口,
rem 这一点从 bat 内部消除不掉,所以让它一闪就关。真正的启动逻辑只有一份,
rem 放在 vbs 里,开机自启的快捷方式也指向它,避免两处实现各自漂移。
rem
rem 启动失败时 vbs 会弹窗提示;想看加载进度,看 logs\server.log。

if not exist "%~dp0tools\静默启动.vbs" (
    echo [错误] 未找到 tools\静默启动.vbs
    pause
    exit /b 1
)

start "" wscript.exe "%~dp0tools\静默启动.vbs"
