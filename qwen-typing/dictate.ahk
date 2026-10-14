; 按住热键说话,松手后由 server.py 转写,文本逐字插入当前光标位置。
; 需要 AutoHotkey v2 与已启动的 server.py。详见 README.md

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; ---------------------------------------------------------------- 配置

CFG_FILE := A_ScriptDir . "\config.ini"

HOTKEY_NAME := IniRead(CFG_FILE, "hotkey", "key", "RAlt")
SRV_HOST    := IniRead(CFG_FILE, "server", "host", "127.0.0.1")
SRV_PORT    := IniRead(CFG_FILE, "server", "port", "8756")
CHAR_DELAY  := Integer(IniRead(CFG_FILE, "typing", "char_delay_ms", "8"))

; 托盘菜单的「退出」用它结束常驻服务
PY_EXE   := A_ScriptDir . "\.venv\Scripts\python.exe"
STOP_ALL := A_ScriptDir . "\tools\停止全部.py"

BASE_URL := "http://" . SRV_HOST . ":" . SRV_PORT
LOG_FILE := A_ScriptDir . "\logs\ahk.log"

; ---------------------------------------------------------------- 状态

Recording := false

; ---------------------------------------------------------------- 工具函数

LogMsg(msg) {
    global LOG_FILE
    try {
        DirCreate(A_ScriptDir . "\logs")
        FileAppend(FormatTime(, "yyyy-MM-dd HH:mm:ss") . "  " . msg . "`n", LOG_FILE, "UTF-8")
    }
}

ShowTip(text) {
    ToolTip(text, A_ScreenWidth // 2 - 60, A_ScreenHeight - 160)
}

HideTip() {
    ToolTip()
}

; 向本地服务发一个 POST,返回 {status, body}。连接失败时 status 为 0。
HttpPost(path, timeoutMs) {
    global BASE_URL
    try {
        req := ComObject("MSXML2.ServerXMLHTTP.6.0")
        req.open("POST", BASE_URL . path, false)
        req.setTimeouts(timeoutMs, timeoutMs, timeoutMs, timeoutMs)
        req.send()
        return {status: req.status, body: req.responseText}
    } catch as err {
        LogMsg("HTTP " . path . " 失败: " . err.Message)
        return {status: 0, body: ""}
    }
}

ServerReady() {
    global BASE_URL
    try {
        req := ComObject("MSXML2.ServerXMLHTTP.6.0")
        req.open("GET", BASE_URL . "/health", false)
        req.setTimeouts(1000, 1000, 1000, 1000)
        req.send()
        return req.status = 200
    } catch {
        return false
    }
}

; 逐字发送,避免长文本一次性注入时部分程序丢字。
; SendText 不解释 {} + ^ ! # 等特殊字符,也不需要剪贴板。
TypeText(text) {
    global CHAR_DELAY
    for ch in StrSplit(text) {
        SendText(ch)
        if (CHAR_DELAY > 0)
            Sleep(CHAR_DELAY)
    }
}

; ---------------------------------------------------------------- 热键处理

OnHotkeyDown(*) {
    global Recording

    if (Recording)
        return

    ; 服务端打开录音设备的上限是 6 秒,这里留出余量。
    resp := HttpPost("/start", 10000)
    if (resp.status != 200) {
        if (resp.status = 0) {
            ShowTip("✕ 连不上转写服务,请双击 启动.bat")
        } else {
            ShowTip("✕ 开始录音失败:" . resp.body)
        }
        SetTimer(HideTip, -5000)
        LogMsg("开始录音失败,status=" . resp.status . " body=" . resp.body)
        return
    }

    Recording := true
    ShowTip("● 录音中,松手结束")
    LogMsg("开始录音")
}

OnHotkeyUp(*) {
    global Recording

    if (!Recording)
        return

    Recording := false
    ShowTip("○ 转写中…")

    resp := HttpPost("/stop", 60000)
    HideTip()

    if (resp.status != 200) {
        LogMsg("转写失败,status=" . resp.status . " body=" . resp.body)
        ShowTip("✕ 转写失败:" . resp.body)
        SetTimer(HideTip, -4000)
        return
    }

    text := Trim(resp.body)
    if (text = "") {
        LogMsg("本次无识别结果")
        return
    }

    ; 等修饰键彻底释放,否则注入的首字符可能被当作组合键。
    KeyWait(HOTKEY_NAME)
    TypeText(text)
    LogMsg("已输入 " . StrLen(text) . " 字:" . SubStr(text, 1, 40))
}

; ---------------------------------------------------------------- 托盘

; 托盘菜单「退出」:先结束 Python 服务,再退出本脚本。
; 传 --no-ahk 是因为强杀 AutoHotkey 会让本进程在 RunWait 中途消失,
; 由脚本自己 ExitApp 收尾更干净。
QuitAll(*) {
    global PY_EXE, STOP_ALL
    ShowTip("正在退出…")
    try RunWait('"' . PY_EXE . '" "' . STOP_ALL . '" --no-ahk', , "Hide")
    ExitApp()
}

; ---------------------------------------------------------------- 启动

if (!ServerReady()) {
    MsgBox("转写服务没有响应 (" . BASE_URL . ")。`n`n请先双击 启动.bat 启动服务,"
        . "等它显示就绪后再运行本脚本。", "语音输入", "Icon!")
}

Hotkey(HOTKEY_NAME, OnHotkeyDown)
Hotkey(HOTKEY_NAME . " up", OnHotkeyUp)

; 只留一行文本。传了 Title 会多出一行,而 Windows 在这之上还会自己加一行
; 应用名(AutoHotkey 64-bit),那一行来自 exe 的资源信息,脚本改不掉。
TrayTip("语音输入已就绪")

; 默认托盘菜单里的 Exit / Pause / Reload 都会绕过服务清理,留下没人管的
; Python 进程,所以整份删除后只留一个「退出」。
A_IconTip := "语音输入"
A_TrayMenu.Delete()
A_TrayMenu.Add("退出(并停止服务)", QuitAll)
A_TrayMenu.Default := "退出(并停止服务)"
LogMsg("热键注册: " . HOTKEY_NAME . " 服务地址: " . BASE_URL)
