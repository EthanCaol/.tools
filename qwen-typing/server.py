"""常驻语音转写服务,Qwen3-ASR 版。

对外提供四个接口,与 whisper-typing 版保持一致,便于 dictate.ahk 直接复用:
    GET  /health      健康检查,用于等待模型就绪
    POST /start       开始录音
    POST /stop        停止录音并转写,返回识别文本
    POST /cancel      丢弃本次录音

与 whisper-typing 版的差异:
    - 模型换成 Qwen3-ASR,走 torch + transformers,不再用 faster-whisper
    - 没有 Whisper 内置的 Silero VAD,改成简单能量门限裁剪首尾静音
    - 不需要注册 CUDA DLL 目录:cu132 的 torch wheel 自带 CUDA 运行时
    - Qwen3-ASR 在纯静音上输出空字符串,不存在 Whisper 那种字幕水印幻觉,
      所以裁剪只为了省算力,不裁剪也不会出错

模型与录音设备在进程启动时初始化一次,之后常驻。

用法:
    .venv\\Scripts\\python.exe server.py
"""

import configparser
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# huggingface_hub 在 import 时读取这些变量,必须在导入 transformers 之前设置。
# 未显式指定 HF_ENDPOINT 时走国内镜像,否则模型权重通常无法下载。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import sounddevice as sd
import torch
from transformers import AutoModelForMultimodalLM, AutoProcessor

CFG = configparser.ConfigParser()
CFG.read(ROOT / "config.ini", encoding="utf-8")


def cfg(section, key, fallback=""):
    return CFG.get(section, key, fallback=fallback).strip()


HOST = cfg("server", "host", "127.0.0.1")
PORT = CFG.getint("server", "port", fallback=8757)

MODEL_ID = cfg("model", "name", "Qwen/Qwen3-ASR-1.7B-hf")
MODEL_DEVICE = cfg("model", "device", "auto")
MODEL_PROMPT = cfg("model", "prompt", "")
MAX_NEW_TOKENS = CFG.getint("model", "max_new_tokens", fallback=256)
STATIC_CACHE_WANTED = CFG.getboolean("model", "static_cache", fallback=True)

# 静态 KV cache 要求输入形状固定才能走编译路径,而音频长度每次都不同,
# 所以必须用两个不同长度预热:PyTorch 在前两个形状各编译一次之后会自动
# 切换到动态形状,此后任意长度都不再重编译。少于两个,第一个真实请求
# 就要现编译,会卡好几秒。
#
# 这两个长度同时是 tools/预热.wav 的长度下限:音频不够长会被零补齐,而补零等于
# 静音输入 —— 模型只输出起止标记就停,解码循环跑不起来,编译不触发。改大这里的
# 值,要确认 wav 还够长(它现在是 16 秒,启动时会检查)。
STATIC_WARMUP_SECONDS = (5, 15)
# 预热后的验证长度,故意取一个没预热过的值。同样不能超过 wav 时长。
STATIC_PROBE_SECONDS = 9
# 探针耗时上限。亚秒级正常;超过这个值说明动态形状没切过去,应当回退。
STATIC_PROBE_LIMIT = 3.0

SAMPLE_RATE = CFG.getint("capture", "sample_rate", fallback=16000)
MIN_SECONDS = CFG.getfloat("capture", "min_duration_ms", fallback=300.0) / 1000.0
MAX_SECONDS = CFG.getfloat("capture", "max_duration_ms", fallback=120000.0) / 1000.0
DEVICE_SPEC = cfg("capture", "device", "")

TRIM_SILENCE = CFG.getboolean("capture", "trim_silence", fallback=True)
TRIM_DBFS = CFG.getfloat("capture", "trim_threshold_dbfs", fallback=-45.0)
TRIM_MARGIN_MS = CFG.getint("capture", "trim_margin_ms", fallback=300)

# 是否把每次识别出的全文写进 logs/transcripts.log。关掉后该文件不再增长。
SAVE_TEXT = CFG.getboolean("log", "save_text", fallback=True)

LOG_PATH = ROOT / "logs" / "server.log"
PID_PATH = ROOT / "logs" / "server.pid"
TEXT_LOG_PATH = ROOT / "logs" / "transcripts.log"
_log_lock = threading.Lock()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with _log_lock:
        # 开机自启走 pythonw.exe,此时 sys.stdout 为 None,不能直接 print。
        if sys.stdout is not None:
            print(line, flush=True)
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def log_text(text, duration, elapsed):
    """把一条识别结果追加到 logs/transcripts.log。

    单独一个文件而不是并进 server.log:这份记录的唯一用途是日后统计词频
    (挑出常说的专名和英文术语填进 config.ini 的 prompt),按行读、按制表符
    切就够了,不用再从日志里过滤出需要的行。

    一行一条,四个字段:时间 \\t 音频时长 \\t 转写耗时 \\t 文本。
    用制表符而不是逗号 —— 识别文本里本来就有逗号。
    """
    if not SAVE_TEXT or not text:
        return

    # 文本里若混进换行或制表符,会把一条记录拆成两行或多列,统一换成空格
    safe = text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t"
            f"{duration:.2f}\t{elapsed:.2f}\t{safe}")

    with _log_lock:
        try:
            TEXT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(TEXT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


# ---------------------------------------------------------------- 录音


def resolve_device(spec):
    """把配置里的设备写法解析成 sounddevice 接受的 device 参数。

    留空或 default 表示系统默认输入设备;纯数字表示设备索引;
    其余按名称子串匹配第一个输入设备。
    """
    spec = spec.strip()
    if not spec or spec.lower() == "default":
        return None
    if spec.isdigit():
        return int(spec)
    lowered = spec.lower()
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and lowered in dev["name"].lower():
            return idx
    raise RuntimeError(f"未找到名称包含 {spec!r} 的输入设备")


# 打开录音设备属于阻塞的底层调用,在个别情况下(设备被其他程序占用、
# 驱动异常)会长时间不返回。这里给每一步都设上限,避免整个服务卡死。
LOCK_WAIT_SECONDS = 3.0
OPEN_TIMEOUT_SECONDS = 6.0
CLOSE_TIMEOUT_SECONDS = 5.0


def empty_audio():
    return np.zeros(0, dtype=np.float32)


class Recorder:
    """单声道 int16 录音缓冲,start/stop 成对调用。

    关键约束:任何一步失败都必须释放锁并把状态复位。否则一个卡住的请求
    会让后续所有 /start 永久等待,服务只能靠重启恢复。
    """

    def __init__(self, device, rate):
        self.device = device
        self.rate = rate
        self._frames = []
        self._stream = None
        self._started_at = 0.0
        self._lock = threading.Lock()

    @property
    def active(self):
        return self._stream is not None

    @property
    def device_label(self):
        return self.device if self.device is not None else "default"

    def _callback(self, indata, frames, time_info, status):
        if status:
            log(f"录音状态提示: {status}")
        self._frames.append(indata.copy())

    def start(self):
        if not self._lock.acquire(timeout=LOCK_WAIT_SECONDS):
            raise RuntimeError("录音器忙(上一个操作未结束),请稍后重试")

        try:
            if self._stream is not None:
                log("已在录音中,忽略重复的开始请求")
                return False

            self._frames = []
            result = {}

            def open_stream():
                try:
                    stream = sd.InputStream(
                        samplerate=self.rate,
                        channels=1,
                        dtype="int16",
                        device=self.device,
                        callback=self._callback,
                    )
                    stream.start()
                    result["stream"] = stream
                    if result.get("abandoned"):
                        # 主线程已判定超时并放弃,这里自行收拾,避免流泄漏。
                        log("超时后设备才打开成功,已直接关闭")
                        try:
                            stream.stop()
                            stream.close()
                        except Exception as exc:  # noqa: BLE001
                            log(f"关闭遗留录音流出错: {exc}")
                        result["stream"] = None
                except Exception as exc:  # noqa: BLE001
                    result["error"] = exc

            worker = threading.Thread(target=open_stream, daemon=True, name="open-input-stream")
            worker.start()
            worker.join(timeout=OPEN_TIMEOUT_SECONDS)

            if worker.is_alive():
                result["abandoned"] = True
                raise RuntimeError(
                    f"打开录音设备超时({OPEN_TIMEOUT_SECONDS:.0f} 秒),"
                    "设备可能被其他程序占用"
                )

            if "error" in result:
                raise result["error"]

            self._stream = result["stream"]
            self._started_at = time.time()
            log(f"开始录音 (device={self.device_label})")
            return True
        finally:
            self._lock.release()

    def stop(self):
        """停止录音,返回 (float32 音频, 时长秒数)。未在录音时返回空。"""
        if not self._lock.acquire(timeout=LOCK_WAIT_SECONDS):
            log("停止录音失败:录音器被占用")
            return empty_audio(), 0.0

        try:
            if self._stream is None:
                log("当前没有在录音,忽略停止请求")
                return empty_audio(), 0.0

            stream, self._stream = self._stream, None
            frames, self._frames = self._frames, []
        finally:
            self._lock.release()

        # 关流放到锁外,并且限时 —— 它同样可能不返回。
        done = threading.Event()

        def close_stream():
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001
                log(f"关闭录音流时出错: {exc}")
            finally:
                done.set()

        worker = threading.Thread(target=close_stream, daemon=True, name="close-input-stream")
        worker.start()
        if not done.wait(timeout=CLOSE_TIMEOUT_SECONDS):
            log(f"关闭录音流超时({CLOSE_TIMEOUT_SECONDS:.0f} 秒),已放弃等待")

        if not frames:
            return empty_audio(), 0.0

        raw = np.concatenate(frames).reshape(-1)
        duration = len(raw) / self.rate
        if duration > MAX_SECONDS:
            log(f"录音 {duration:.1f}s 超过上限 {MAX_SECONDS:.0f}s,截断")
            raw = raw[: int(MAX_SECONDS * self.rate)]
            duration = MAX_SECONDS

        audio = raw.astype(np.float32) / 32768.0
        log(f"停止录音,共 {duration:.2f}s")
        return audio, duration


RECORDER = None


# ---------------------------------------------------------------- 静音裁剪


def trim_silence(audio, rate, threshold_dbfs, margin_ms, frame_ms=30):
    """裁掉首尾静音,保留 margin_ms 的余量。

    Whisper 版靠内置 Silero VAD 做这件事。这里用能量门限代替:按 30ms 分帧算
    均方根,找出高于门限的帧,从第一帧往前留一段余量、最后一帧往后留一段余量。
    全部低于门限时返回空数组,由调用方当作「无有效语音」处理。
    """
    frame = int(rate * frame_ms / 1000)
    count = len(audio) // frame
    if count == 0:
        return audio

    rms = np.sqrt(np.mean(audio[: count * frame].reshape(count, frame) ** 2, axis=1))
    loud = np.nonzero(rms > 10 ** (threshold_dbfs / 20))[0]
    if loud.size == 0:
        return empty_audio()

    margin = int(rate * margin_ms / 1000)
    start = max(0, int(loud[0]) * frame - margin)
    end = min(len(audio), (int(loud[-1]) + 1) * frame + margin)
    return audio[start:end]


# ---------------------------------------------------------------- 模型

MODEL = None
PROCESSOR = None
MODEL_LOCK = threading.Lock()
RESOLVED_DEVICE = "cpu"
USE_STATIC_CACHE = False  # 由 warmup() 探测后决定,探测失败自动保持 False


def resolve_torch_device():
    if MODEL_DEVICE == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return MODEL_DEVICE


def load_model():
    """加载 Qwen3-ASR。device=auto 时优先用 GPU,没有可用 GPU 就退回 CPU。"""
    global PROCESSOR, RESOLVED_DEVICE

    RESOLVED_DEVICE = resolve_torch_device()
    torch_dtype = torch.bfloat16 if RESOLVED_DEVICE == "cuda" else torch.float32

    log(f"加载模型 {MODEL_ID} (device={RESOLVED_DEVICE}, dtype={torch_dtype}) ...")
    t0 = time.time()
    PROCESSOR = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForMultimodalLM.from_pretrained(MODEL_ID, dtype=torch_dtype)
    model = model.to(RESOLVED_DEVICE)
    model.eval()
    log(f"模型加载完成,用时 {time.time() - t0:.1f}s")
    return model


def prepare_inputs(audio):
    """把 float32 音频交给处理器,并按模型 dtype 对齐。"""
    kwargs = {"prompt": MODEL_PROMPT} if MODEL_PROMPT else {}
    inputs = PROCESSOR.apply_transcription_request(audio=audio, **kwargs)
    inputs = inputs.to(RESOLVED_DEVICE)
    # 模型以 bf16 加载时,处理器产出的 float32 音频特征必须在进网络前统一 dtype,
    # 否则第一次卷积就报 Input type (float) and bias type (BFloat16) should be the same。
    # 只转浮点张量,input_ids 之类的整型不能碰。
    for key, value in inputs.items():
        if torch.is_floating_point(value):
            inputs[key] = value.to(MODEL.dtype)
    return inputs


def generate(inputs):
    """调用一次生成。USE_STATIC_CACHE 决定走哪条 cache 路径。"""
    kwargs = {"cache_implementation": "static"} if USE_STATIC_CACHE else {}
    with torch.no_grad():
        return MODEL.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, **kwargs)


def transcribe(audio):
    """转写 float32 16kHz 单声道音频,返回 (文本, 耗时秒数)。"""
    duration = len(audio) / SAMPLE_RATE
    t0 = time.time()

    if TRIM_SILENCE:
        trimmed = trim_silence(audio, SAMPLE_RATE, TRIM_DBFS, TRIM_MARGIN_MS)
        if len(trimmed) / SAMPLE_RATE < MIN_SECONDS:
            log(f"裁剪后有效语音不足 {MIN_SECONDS * 1000:.0f}ms,忽略")
            return "", 0.0
        audio = trimmed

    inputs = prepare_inputs(audio)
    with MODEL_LOCK:
        output_ids = generate(inputs)
    generated = output_ids[:, inputs["input_ids"].shape[1]:]
    text = PROCESSOR.decode(generated, return_format="transcription_only")[0].strip()

    elapsed = time.time() - t0
    log(f"转写 {duration:.2f}s 音频,耗时 {elapsed:.2f}s,输出 {len(text)} 字")
    log_text(text, duration, elapsed)
    return text, elapsed


WARMUP_WAV = ROOT / "tools" / "预热.wav"
WARMUP_GEN = ROOT / "tools" / "生成预热语音.py"


def ensure_warmup_wav():
    """预热语音不存在时用系统语音合成生成一份。

    这段音频只要求「是真实语音、能生成足够多的 token」,用谁的声音都行,所以
    仓库里存生成脚本而不是音频本身 —— 音频是几百 KB 的二进制,脚本是纯文本,
    而且生成出来的不是录制者的声音,放公开仓库没有顾虑。
    """
    if WARMUP_WAV.exists() or not WARMUP_GEN.exists():
        return WARMUP_WAV.exists()

    log(f"未找到 {WARMUP_WAV.name},调用 {WARMUP_GEN.name} 生成 ...")
    try:
        proc = subprocess.run(
            [sys.executable, str(WARMUP_GEN)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"生成预热语音失败: {type(exc).__name__}: {exc}")
        return False

    if proc.returncode != 0 or not WARMUP_WAV.exists():
        detail = (proc.stdout or "") + (proc.stderr or "")
        log(f"生成预热语音失败:{detail.strip()[:200]}")
        return False

    log("预热语音已生成")
    return True


def load_warmup_audio():
    """读取预热用的真实语音。

    试过静音、白噪、门控噪声、交替方波、扫频,全部只产出 4 个 token 且文本为空
    —— 模型直接输出起止标记,解码循环没走完,编译不触发。只有真实语音才算数。
    内容是什么、谁说的都不重要,所以文件缺失时由 ensure_warmup_wav() 现生成一段。
    """
    import wave

    if not ensure_warmup_wav():
        return None
    with wave.open(str(WARMUP_WAV), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            return None
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def warmup():
    """预热:首次推理包含显存分配、算子选择和编译开销。"""
    global USE_STATIC_CACHE

    try:
        t0 = time.time()
        with MODEL_LOCK:
            generate(prepare_inputs(np.zeros(SAMPLE_RATE, dtype=np.float32)))
        log(f"基础预热完成,用时 {time.time() - t0:.2f}s")
    except Exception as exc:  # noqa: BLE001
        log(f"基础预热失败(不影响使用): {exc}")

    if not STATIC_CACHE_WANTED:
        log("静态 KV cache 未启用(config.ini 里 static_cache=false),服务就绪")
        return

    speech = load_warmup_audio()
    if speech is None:
        log(f"未找到 {WARMUP_WAV.name} 或格式不符,静态 KV cache 需要真实语音预热,"
            "本次跳过(服务按默认模式运行,功能不受影响)")
        return

    # 时长检查。短于需要的长度会被零补齐,而补零等价于静音输入:模型只输出起止
    # 标记就停,解码循环跑不起来,编译不触发 —— 也就是白预热一场。这个失败不报错,
    # 只表现为「每条都慢 3 倍」,所以在这里主动喊一声。
    longest = max(max(STATIC_WARMUP_SECONDS), STATIC_PROBE_SECONDS)
    have = len(speech) / SAMPLE_RATE
    if have < longest:
        log(f"警告:{WARMUP_WAV.name} 只有 {have:.1f}s,短于需要的 {longest}s,"
            "超出部分会用静音补齐,静态 cache 很可能不生效")

    # 必须在预热循环之前置位:generate() 读的就是这个标志,
    # 先预热后置位等于用默认 cache 预热,白跑。
    USE_STATIC_CACHE = True

    try:
        t0 = time.time()
        with MODEL_LOCK:
            for secs in STATIC_WARMUP_SECONDS:
                need = secs * SAMPLE_RATE
                clip = speech[:need] if len(speech) >= need else np.pad(
                    speech, (0, need - len(speech)))
                generate(prepare_inputs(clip))
        warmed = time.time() - t0

        # 验证探针:再用一个没预热过的长度跑一次。动态形状切换成功的话它是
        # 亚秒级;若切换失败,这次会现场重编译,耗时以秒计 —— 那就回退。
        # 没有这道检查,失败会表现为「每条都慢 3 倍」而不是报错,很难发现。
        need = STATIC_PROBE_SECONDS * SAMPLE_RATE
        probe_clip = speech[:need] if len(speech) >= need else np.pad(
            speech, (0, need - len(speech)))
        t0 = time.time()
        with MODEL_LOCK:
            generate(prepare_inputs(probe_clip))
        probe = time.time() - t0

        if probe > STATIC_PROBE_LIMIT:
            USE_STATIC_CACHE = False
            log(f"静态 cache 未生效(探针 {STATIC_PROBE_SECONDS}s 音频耗时 "
                f"{probe:.1f}s,超过 {STATIC_PROBE_LIMIT:.0f}s 上限),"
                "已回退默认模式,服务就绪")
            return

        log(f"静态 KV cache 已启用(预热 {warmed:.1f}s,探针 {probe:.2f}s),服务就绪")
    except Exception as exc:  # noqa: BLE001
        USE_STATIC_CACHE = False
        log(f"静态 KV cache 不可用,已回退默认模式: "
            f"{type(exc).__name__}: {str(exc)[:120]}")
        log("服务就绪(默认模式)")


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, code, body="", extra_headers=None):
        """body 以字节发送。调用方传字符串时按 UTF-8 编码。"""
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, json.dumps(
                {"ok": True, "model": MODEL_ID, "recording": RECORDER.active},
                ensure_ascii=False,
            ))
        else:
            self._reply(404, "not found")

    def do_POST(self):
        try:
            if self.path == "/start":
                RECORDER.start()
                self._reply(200, "ok")
            elif self.path == "/stop":
                audio, duration = RECORDER.stop()
                text, elapsed = transcribe(audio)
                # 识别文本直接作为响应体,避免调用方再做一次 JSON 解析。
                self._reply(200, text, {
                    "X-Audio-Duration": f"{duration:.2f}",
                    "X-Elapsed": f"{elapsed:.2f}",
                })
            elif self.path == "/cancel":
                RECORDER.stop()
                self._reply(200, "ok")
            else:
                self._reply(404, "not found")
        except Exception as exc:  # noqa: BLE001
            log(f"请求 {self.path} 处理异常: {type(exc).__name__}: {exc}")
            self._reply(500, f"{type(exc).__name__}: {exc}")

    def log_message(self, fmt, *args):
        pass  # 关闭逐请求的 stderr 输出,日志统一走 log()


def main():
    global MODEL, RECORDER

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    log("=" * 50)
    log(f"服务启动: model={MODEL_ID} port={PORT}")

    device = resolve_device(DEVICE_SPEC)
    try:
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        log(f"录音设备: [{device if device is not None else sd.default.device[0]}] {info['name']}")
    except Exception as exc:  # noqa: BLE001
        log(f"录音设备信息读取失败: {exc}")
    RECORDER = Recorder(device, SAMPLE_RATE)

    MODEL = load_model()
    warmup()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"监听 http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("服务停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
