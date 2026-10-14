' 语音输入(Qwen) 静默启动
' 根目录的 启动.bat 和开机自启的快捷方式都指向本文件。
' 隐藏启动转写服务,等就绪后拉起听写脚本;就绪后由听写脚本弹一条托盘提示。

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

' 这套启动方式不创建任何窗口,失败时屏幕上什么都不会出现,所以必须弹窗说一声,
' 否则双击之后没反应,无从判断是启动失败还是还在加载。
Sub Fail(msg)
    MsgBox msg & vbCrLf & vbCrLf & "详情见 logs\server.log", 16, "语音输入"
    WScript.Quit 1
End Sub

If Not fso.FileExists(py) Then
    Fail "未找到虚拟环境:" & vbCrLf & py & vbCrLf & _
         "项目目录可能被移动过,或依赖还没装好。"
End If
If Not fso.FileExists(ahk) Then
    Fail "未找到 AutoHotkey v2:" & vbCrLf & ahk
End If

' 服务已在运行时跳过启动。2 秒超时探一下,已就绪就不动它,避免白重启一次。
rc = sh.Run(q & py & q & " " & q & base & "\tools\等待就绪.py" & q & " 2", 0, True)

If rc <> 0 Then
    ' pythonw 无窗口;服务启动后由 等待就绪.py 确认就绪
    sh.Run q & pyw & q & " " & q & base & "\server.py" & q, 0, False
    rc = sh.Run(q & py & q & " " & q & base & "\tools\等待就绪.py" & q & " 300", 0, True)
    If rc <> 0 Then
        Fail "转写服务在 300 秒内没有就绪。" & vbCrLf & _
             "常见原因:显存不足、模型需要重新下载,或端口被别的程序占用。"
    End If
End If

' dictate.ahk 带 #SingleInstance Force,重复调用安全
sh.Run q & ahk & q & " " & q & base & "\dictate.ahk" & q, 0, False

WScript.Quit 0
