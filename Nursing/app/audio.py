import subprocess
import threading
import queue
import os
import sys
import re

# ✅ Safe import for SAPI
try:
    import win32com.client
except Exception:
    win32com = None


_audio_q = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()

_DIGITS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

def _ps_escape(s: str) -> str:
    return s.replace("'", "''")

_sapi_voice = None
_sapi_lock = threading.Lock()

def _init_sapi_voice():
    global _sapi_voice
    if _sapi_voice is not None:
        return True
    if win32com is None:
        return False

    try:
        _sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")
        _sapi_voice.Rate = 1
        _sapi_voice.Volume = 100

        target = "zira"
        for v in _sapi_voice.GetVoices():
            name = v.GetDescription().lower()
            if target in name:
                _sapi_voice.Voice = v
                break
        return True
    except Exception:
        _sapi_voice = None
        return False

def _run_powershell_blocking(ps_cmd: str):
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000
    )

def _tts_blocking(text: str):
    if _init_sapi_voice():
        with _sapi_lock:
            _sapi_voice.Volume = 100
            _sapi_voice.Rate = 0

            # ✅ IMPORTANT: 0 = synchronous (blocking)
            _sapi_voice.Speak(text, 0)

            # ✅ extra safety: wait until speech is fully done
            _sapi_voice.WaitUntilDone(-1)
        return

    # fallback PowerShell (already blocking)
    text_ps = _ps_escape(text)
    ps_cmd = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Volume = 100; "
        "$s.Rate = 0; "
        f"$s.Speak('{text_ps}');"
    )
    _run_powershell_blocking(ps_cmd)

def _play_audio_blocking(path: str):
    p = os.path.abspath(path)
    p_ps = _ps_escape(p)
    ext = os.path.splitext(p)[1].lower()

    if ext == ".wav":
        ps_cmd = (
            "Add-Type -AssemblyName System.Media; "
            f"$p = '{p_ps}'; "
            "if (Test-Path $p) { "
            "$sp = New-Object System.Media.SoundPlayer($p); "
            "$sp.PlaySync(); "
            "} "
        )
        _run_powershell_blocking(ps_cmd)
        return

    ps_cmd = (
        f"$p = '{p_ps}'; "
        "if (Test-Path $p) { "
        "$w = New-Object -ComObject WMPlayer.OCX; "
        "$w.settings.autoStart = $true; "
        "$w.URL = $p; "
        "$w.controls.play(); "
        "while ($w.playState -ne 1) { Start-Sleep -Milliseconds 80 } "
        "} "
    )
    _run_powershell_blocking(ps_cmd)

def _audio_worker():
    while True:
        item = _audio_q.get()
        try:
            if isinstance(item, tuple) and item[0] == "TTS":
                _tts_blocking(item[1])
            else:
                _play_audio_blocking(item)
        except Exception as e:
            print(f"[AUDIO-ERROR] {e}")
        finally:
            _audio_q.task_done()

def _start_worker_once():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_audio_worker, daemon=True).start()
        _worker_started = True

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

AUDIO_DIR = os.path.join(app_dir(), "audios")
DING_WAV = os.path.join(AUDIO_DIR, "ding.wav")
INTRO_WAV = os.path.join(AUDIO_DIR, "intro.wav")
COUNTER1_WAV = os.path.join(AUDIO_DIR, "Counter1.wav")
COUNTER2_WAV = os.path.join(AUDIO_DIR, "Counter2.wav")
COUNTER3_WAV = os.path.join(AUDIO_DIR, "Counter3.wav")
COUNTER4_WAV = os.path.join(AUDIO_DIR, "Counter4.wav")
NURSING_WAV = os.path.join(AUDIO_DIR, "Nursing.wav")

TOKEN_START = 1001
TOKEN_END   = 1090

def _wrap_system_token(n: int) -> int:
    if n > TOKEN_END:
        return TOKEN_START
    if n < TOKEN_START:
        return TOKEN_START
    return n

def _pick_counter_audio(counter: str) -> str:
    c = (counter or "").strip().lower()
    if "nursing" in c or "nurse" in c:
        return NURSING_WAV
    if "1" in c:
        return COUNTER1_WAV
    if "2" in c:
        return COUNTER2_WAV
    if "3" in c:
        return COUNTER3_WAV
    if "4" in c:
        return COUNTER4_WAV

    return COUNTER1_WAV  # safe default


def announce_token(use_tts: bool, token_no: int, counter: str):
    _start_worker_once()

    counter_audio = _pick_counter_audio(counter)

    # Always ding first
    _audio_q.put(DING_WAV)

    if use_tts:
        # "Token Number" + TTS digits + counter
        digit_words = ", ".join(_DIGITS[d] for d in str(int(token_no)))
        _audio_q.put(INTRO_WAV)
        _audio_q.put(("TTS", digit_words))
        _audio_q.put(counter_audio)
    else:
        # ding + pre-recorded token wav + counter
        system_token = _wrap_system_token(int(token_no))
        token_wav = os.path.join(AUDIO_DIR, f"{system_token}.wav")
        _audio_q.put(token_wav)
        _audio_q.put(counter_audio)

    # print("AUDIO announce_token called:", use_tts, token_no, counte