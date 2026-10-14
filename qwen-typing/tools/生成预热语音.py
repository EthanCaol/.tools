"""生成静态 KV cache 预热用的语音。

为什么要专门生成一段:预热必须用真实语音。静音、白噪、门控噪声、交替方波、扫频
都试过,模型一律只产出 4 个 token 且文本为空 —— 解码循环没走完,编译不触发。
但用什么声音无所谓,所以这里用 Windows 自带的语音合成生成,不必把录制者的声音
放进仓库。

输出 tools\\预热.wav:16 kHz / 单声道 / 16 bit。服务器启动时若发现该文件不存在,
会自动调用本脚本;也可以手动运行。

用法:
    .venv\\Scripts\\python.exe tools\\生成预热语音.py
"""

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "预热.wav"

# 必须覆盖 server.py 里最长的预热长度和探针长度(当前是 15 秒和 9 秒)。短了会被
# 零补齐,而补零等价于静音输入,预热白做。server.py 启动时也会检查长度并告警。
TARGET_SECONDS = 16.0
RATE = 16000

ZH_TEXT = (
    "语音识别服务在启动的时候需要一段真实的语音输入。"
    "这段话由系统自带的语音合成生成,用来触发编译,让静态缓存生效。"
    "合成的声音和真人录音在预热效果上没有区别,因为预热只要求模型产生足够多的输出。"
)

EN_TEXT = (
    "The speech recognition service needs a real speech sample during startup. "
    "This passage is produced by the built in speech synthesizer to trigger "
    "compilation so that the static cache takes effect. A synthesized voice works "
    "just as well as a recording, because the warmup only needs the model to emit "
    "enough output tokens."
)

# 用 Windows PowerShell 5.1,不是 pwsh:System.Speech 在 PowerShell 7 里需要额外装包。
# 脚本写成文件再 -File 调用,避免中文文本在命令行上传参时的引号转义问题。
PS_SCRIPT = """$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$zh = {zh}
$en = {en}

# 优先选中文语音,没有就用默认的英文语音配英文文本
$picked = $null
foreach ($v in $s.GetInstalledVoices()) {{
    if ($v.Enabled -and $v.VoiceInfo.Culture.Name -like 'zh*') {{ $picked = $v; break }}
}}
if ($picked) {{
    $s.SelectVoice($picked.VoiceInfo.Name)
    $text = $zh
    Write-Output ("voice=" + $picked.VoiceInfo.Name)
}} else {{
    $text = $en
    Write-Output "voice=default(en)"
}}

$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile({out}, $fmt)
# 说两遍,保证合成结果一定长于 TARGET_SECONDS,再多截断
$s.Speak($text)
$s.Speak($text)
$s.Dispose()
Write-Output "done"
"""


def quote_ps(text):
    """转成 PowerShell 单引号字面量:单引号本身要写两遍"""
    return "'" + text.replace("'", "''") + "'"


def synthesize(raw_wav):
    script = PS_SCRIPT.format(
        zh=quote_ps(ZH_TEXT),
        en=quote_ps(EN_TEXT),
        out=quote_ps(str(raw_wav).replace("/", "\\")),
    )
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8-sig") as f:
        f.write(script)
        script_path = f.name

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    finally:
        Path(script_path).unlink(missing_ok=True)


def trim_to(src, dst, seconds):
    """截取前 seconds 秒。合成的语音一开头就是正文,不像录音带前导静音"""
    with wave.open(str(src), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise RuntimeError(f"合成结果格式意外: {w.getnchannels()} 声道 / "
                               f"{w.getsampwidth() * 8} bit")
        rate = w.getframerate()
        need = int(seconds * rate)
        data = w.readframes(min(w.getnframes(), need))
    with wave.open(str(dst), "wb") as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(rate)
        o.writeframes(data)
    return len(data) / 2 / rate, rate


def main():
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.wav"
        code, output = synthesize(raw)
        note = output.strip().replace("\n", " ")
        if code != 0 or not raw.exists():
            print(f"语音合成失败(退出码 {code})")
            print(note)
            return 1

        try:
            length, rate = trim_to(raw, OUT, TARGET_SECONDS)
        except Exception as exc:  # noqa: BLE001
            print(f"处理合成结果失败: {exc}")
            return 1

    size_kb = OUT.stat().st_size / 1024
    print(f"已生成 {OUT}")
    print(f"  {note}")
    print(f"  {length:.2f}s / {rate} Hz / 单声道 16 bit / {size_kb:.0f} KB")
    if length < TARGET_SECONDS - 0.5:
        print(f"  警告:短于目标 {TARGET_SECONDS}s,预热可能不生效")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
