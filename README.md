# Tools

自用工具的集合,每个子目录一个独立工具,各自带自己的虚拟环境,互不干扰。

## qwen-typing

按住右 Ctrl 说话,松手后用本地 Qwen3-ASR 模型转写,文字逐字插入当前光标位置。
全程离线,音频和文本不离开本机。基于 `whisper-typing` 改写,把引擎从
faster-whisper 换成了 Qwen3-ASR。

| 操作 | 方法 |
|---|---|
| 启动(不弹窗) | 双击 `qwen-typing\tools\静默启动.vbs` |
| 启动(显示加载进度) | 双击 `qwen-typing\启动.bat` |
| 退出 | 右键托盘图标 → 「退出(并停止服务)」 |
| 开启开机自启 | 双击 `qwen-typing\开机自启-开启.bat` |
| 关闭开机自启 | 双击 `qwen-typing\开机自启-关闭.bat` |

首次启动要加载模型并预热,约 21 秒;之后常驻,单次转写 0.3~0.9 秒。

配置、排查和设计说明见 [qwen-typing/README.md](qwen-typing/README.md)。

## 约定

- 每个工具自带 `.venv`,依赖不共用。虚拟环境不要跨目录移动。
- 启动脚本按自身所在位置定位路径(`A_ScriptDir`、`WScript.ScriptFullName`、
  `Path(__file__)`),整个工具目录可以整体移动或改名。
- `.venv`、`logs`、模型权重和下载缓存不进版本库,规则见 `.gitignore`。
