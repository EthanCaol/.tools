' 语音输入(Qwen) 静默启动
' 开机自启的快捷方式指向本文件:隐藏启动转写服务,等就绪后拉起听写脚本。
' 直接双击等价于 启动.bat,区别只是不显示控制台窗口。

Option Explicit

Dim sh, fso, base, py, pyw, ahk, q, rc

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 本文件在 tools\ 下,项目根目录是它的上一级
base = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
py   = base & "\.venv\Scripts\python.exe"
pyw  = base & "\.venv\Scripts\pythonw.exe"
ahk  = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\AutoHotkey\v2\AutoHotkey64.exe"
q    = Chr(34)

If Not fso.FileExists(py) Then WScript.Quit 1
If Not fso.FileExists(ahk) Then WScript.Quit 1

' 服务已在运行时跳过启动
rc = sh.Run(q & py & q & " " & q & base & "\tools\等待就绪.py" & q & " 2", 0, True)

If rc <> 0 Then
    ' pythonw 无窗口;服务启动后由 等待就绪.py 确认就绪
    sh.Run q & pyw & q & " " & q & base & "\server.py" & q, 0, False
    rc = sh.Run(q & py & q & " " & q & base & "\tools\等待就绪.py" & q & " 300", 0, True)
    If rc <> 0 Then WScript.Quit 1
End If

' dictate.ahk 带 #SingleInstance Force,重复调用安全
sh.Run q & ahk & q & " " & q & base & "\dictate.ahk" & q, 0, False

WScript.Quit 0
