"""FFmpeg／ffprobe 工具：檢測、MP4 索引位置、轉檔指令、四項自動檢查（第 11 章）。"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from typing import Callable, Optional

from istudy_common.util import TZ


class ToolError(Exception):
    pass


def _run(cmd: list[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    except FileNotFoundError as e:
        raise ToolError(f"找不到 {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"{cmd[0]} 執行逾時") from e


def ffprobe(path: str) -> dict:
    p = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path], timeout=120)
    if p.returncode != 0:
        raise ToolError("ffprobe 無法讀取影片：" + (p.stderr.strip().splitlines() or ["未知錯誤"])[-1][:300])
    return json.loads(p.stdout or "{}")


def mp4_faststart(path: str) -> Optional[bool]:
    """MP4／MOV：moov 在 mdat 之前＝索引在檔頭。非 ISO BMFF 檔回傳 None。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            seen_ftyp = False
            for _ in range(64):
                if pos + 8 > size:
                    return None
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                box_size, box_type = struct.unpack(">I4s", hdr)
                if box_size == 1:
                    box_size = struct.unpack(">Q", f.read(8))[0]
                elif box_size == 0:
                    box_size = size - pos
                t = box_type.decode("latin-1")
                if pos == 0 and t != "ftyp":
                    return None
                seen_ftyp = seen_ftyp or t == "ftyp"
                if t == "moov":
                    return True
                if t == "mdat":
                    return False
                if box_size < 8:
                    return None
                pos += box_size
    except OSError:
        return None
    return None


def _fps(rate: Optional[str]) -> Optional[float]:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        if "/" in rate:
            n, d = rate.split("/")
            return round(float(n) / float(d), 3) if float(d) else None
        return round(float(rate), 3)
    except ValueError:
        return None


def idet(path: str, frames: int = 500) -> dict:
    """抽樣約 500 格畫面判斷是否交錯掃描。"""
    p = _run(["ffmpeg", "-hide_banner", "-nostdin", "-i", path, "-map", "0:v:0", "-vf", "idet",
              "-frames:v", str(frames), "-an", "-f", "null", "-"], timeout=600)
    m = re.findall(r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)\s*Undetermined:\s*(\d+)", p.stderr)
    if not m:
        return {"tff": 0, "bff": 0, "progressive": 0, "undetermined": 0, "interlaced": None}
    tff, bff, prog, und = map(int, m[-1])
    return {"tff": tff, "bff": bff, "progressive": prog, "undetermined": und, "interlaced": (tff + bff) > prog}


def recorded_time(path: str, fmt: dict) -> Optional[_dt.datetime]:
    """錄影時間：ffprobe creation_time，否則用 exiftool 讀 MTS 內的 DateTimeOriginal（有安裝時）。"""
    tags = (fmt.get("format") or {}).get("tags") or {}
    ct = tags.get("creation_time")
    if ct:
        try:
            return _dt.datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone(TZ)
        except ValueError:
            pass
    if shutil.which("exiftool"):
        p = _run(["exiftool", "-j", "-DateTimeOriginal", "-api", "LargeFileSupport=1", path], timeout=60)
        try:
            v = json.loads(p.stdout)[0].get("DateTimeOriginal")
        except (ValueError, IndexError, AttributeError):
            v = None
        if v:
            m = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})(?:([+-]\d{2}):?(\d{2}))?", v)
            if m:
                y, mo, d, h, mi, s = map(int, m.groups()[:6])
                tz = TZ
                if m.group(7):
                    sign = 1 if m.group(7)[0] == "+" else -1
                    tz = _dt.timezone(sign * _dt.timedelta(hours=abs(int(m.group(7))), minutes=int(m.group(8) or 0)))
                return _dt.datetime(y, mo, d, h, mi, s, tzinfo=tz)
    return None


def summarize_probe(path: str, raw: dict, run_idet: bool = True) -> dict:
    streams = raw.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = raw.get("format") or {}
    duration = None
    for src in (fmt.get("duration"), (v or {}).get("duration")):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue
    field_order = (v or {}).get("field_order")
    info = {
        "container": fmt.get("format_name"),
        "video_codec": (v or {}).get("codec_name"),
        "profile": (v or {}).get("profile"),
        "pix_fmt": (v or {}).get("pix_fmt"),
        "width": (v or {}).get("width"),
        "height": (v or {}).get("height"),
        "fps": _fps((v or {}).get("avg_frame_rate") or (v or {}).get("r_frame_rate")),
        "field_order": field_order,
        "audio_codec": (a or {}).get("codec_name"),
        "audio_channels": (a or {}).get("channels"),
        "duration": duration,
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "faststart": mp4_faststart(path),
        "has_video": v is not None,
        "has_audio": a is not None,
    }
    interlaced = None
    if field_order in ("tt", "bb", "tb", "bt"):
        interlaced = True
    if run_idet and v is not None:
        det = idet(path)
        info["idet"] = det
        if det["interlaced"] is not None:
            interlaced = det["interlaced"] or bool(interlaced and det["progressive"] == 0)
    if interlaced is None:
        interlaced = False
    info["interlaced"] = interlaced
    return info


MP4_CONTAINERS = ("mov", "mp4", "m4a", "3gp", "3g2", "mj2")


def decide_play_mode(info: dict, ext: str) -> str:
    """direct：直接播放原檔；remux：換封裝（影像複製）；transcode：去交錯／重新編碼。"""
    if not info.get("has_video"):
        raise ToolError("檔案沒有影像軌")
    h264_ok = info.get("video_codec") == "h264" and (info.get("pix_fmt") in ("yuv420p", "yuvj420p", None)) \
        and not info.get("interlaced")
    if not h264_ok:
        return "transcode"
    is_mp4 = ext.lower() in ("mp4", "m4v") and any(c in (info.get("container") or "") for c in MP4_CONTAINERS)
    audio_ok = (not info.get("has_audio")) or info.get("audio_codec") == "aac"
    if is_mp4 and audio_ok and info.get("faststart") is True:
        return "direct"
    return "remux"


# ---------- 轉檔 ----------

def build_command(mode: str, src: str, dst: str, info: dict, encoder: str) -> list[str]:
    base = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", "-ac", "2"] if info.get("has_audio") else []
    tail = ["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", dst]
    if mode == "remux":
        if info.get("audio_codec") == "aac" or not info.get("has_audio"):
            audio = ["-map", "0:a:0?", "-c:a", "copy"] if info.get("has_audio") else []
        return base + ["-i", src, "-map", "0:v:0", "-c:v", "copy"] + audio + tail
    pre, vf, enc = encoder_args(encoder, bool(info.get("interlaced")))
    return base + pre + ["-i", src, "-map", "0:v:0"] + vf + enc + audio + tail


def run_ffmpeg(cmd: list[str], duration: Optional[float], on_progress: Callable[[float], None],
               should_stop: Optional[Callable[[], bool]] = None) -> tuple[int, str]:
    """執行 ffmpeg，讀取 -progress 輸出回報百分比。回傳（結束碼、stderr 最後部分）。"""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    except FileNotFoundError as e:
        raise ToolError("找不到 ffmpeg") from e
    import threading
    err_tail: list[str] = []

    def read_err():
        for line in p.stderr:  # type: ignore[union-attr]
            err_tail.append(line)
            if len(err_tail) > 60:
                del err_tail[:20]

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    last = 0.0
    for line in p.stdout:  # type: ignore[union-attr]
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if duration:
                pct = max(0.0, min(99.9, us / 1_000_000 / duration * 100))
                if pct - last >= 1:
                    last = pct
                    on_progress(round(pct, 1))
        if should_stop and should_stop():
            p.terminate()
    p.wait()
    t.join(timeout=5)
    for stream in (p.stdout, p.stderr):
        if stream:
            stream.close()
    return p.returncode, "".join(err_tail)[-2000:]


VAAPI_DEVICE = os.environ.get("ISTUDY_VAAPI_DEVICE", "/dev/dri/renderD128")
ENCODER_ORDER = ("qsv", "vaapi", "cpu")  # dev.55 HW-01：QSV → VA-API → CPU
_TEST_SRC = ["-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=25"]


def _stderr_tail(text: str, lines: int = 12, limit: int = 1500) -> str:
    """保留 FFmpeg 錯誤輸出的最後幾行（診斷用；不含任何密碼或路徑以外的資料）。"""
    rows = [ln.rstrip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join(rows[-lines:])[-limit:]


def encoder_args(encoder: str, interlaced: bool) -> tuple[list[str], list[str], list[str]]:
    """回傳（輸入前參數、濾鏡、編碼參數）。偵測與正式轉檔用同一組參數，偵測結果才有意義。"""
    if encoder == "qsv":
        vf = ["-vf", "vpp_qsv=deinterlace=2"] if interlaced else []
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"], vf, \
            ["-c:v", "h264_qsv", "-global_quality", "23", "-profile:v", "high"]
    if encoder == "vaapi":
        # CPU 解碼（任何來源格式都能讀）→ 上傳到 GPU → h264_vaapi 編碼；交錯影片先在 CPU 用 bwdif 去交錯
        chain = ("bwdif," if interlaced else "") + "format=nv12,hwupload"
        return ["-vaapi_device", VAAPI_DEVICE], ["-vf", chain], \
            ["-c:v", "h264_vaapi", "-qp", os.environ.get("ISTUDY_VAAPI_QP", "23"), "-profile:v", "high"]
    vf = ["-vf", "bwdif,format=yuv420p" if interlaced else "format=yuv420p"]
    return [], vf, ["-c:v", "libx264", "-preset", os.environ.get("ISTUDY_X264_PRESET", "medium"), "-crf", "21", "-profile:v", "high"]


QSV_TEST = {"seconds": 2, "width": 1280, "height": 720, "fps": 30}   # dev.59：與主機上驗證 QSV 的條件相同


def _qsv_file_test() -> dict:
    """dev.59：h264_qsv 實際編碼 2 秒 1280×720 30fps 的測試影片到暫存 mp4，再用 ffprobe 確認是 H.264、尺寸與 60 幀。"""
    t = QSV_TEST
    frames = t["seconds"] * t["fps"]
    d = tempfile.mkdtemp(prefix="qsv-test-")
    out = os.path.join(d, "qsv.mp4")
    try:
        p = _run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
                  "-i", f"testsrc=duration={t['seconds']}:size={t['width']}x{t['height']}:rate={t['fps']}",
                  "-vf", "format=nv12", "-c:v", "h264_qsv", "-global_quality", "23", out], timeout=90)
        if p.returncode != 0:
            tail = _stderr_tail(p.stderr)
            last = tail.splitlines()[-1] if tail else "未知錯誤"
            return {"ok": False, "message": f"h264_qsv 測試編碼失敗：{last[:200]}", "stderr": tail}
        q = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                  "stream=codec_name,width,height,nb_read_frames", "-of", "json", out], timeout=60)
        try:
            st = (json.loads(q.stdout or "{}").get("streams") or [{}])[0]
        except ValueError:
            st = {}
        got = (st.get("codec_name"), st.get("width"), st.get("height"), int(st.get("nb_read_frames") or 0))
        if got != ("h264", t["width"], t["height"], frames):
            return {"ok": False, "message": f"h264_qsv 輸出不正確（ffprobe：{got[0]} {got[1]}×{got[2]}、{got[3]} 幀，"
                                            f"預期 h264 {t['width']}×{t['height']}、{frames} 幀）", "stderr": _stderr_tail(q.stderr)}
        return {"ok": True, "message": f"實測 {t['seconds']} 秒 {t['width']}×{t['height']}：{frames} 幀 H.264（ffprobe 確認）", "stderr": ""}
    except ToolError as e:
        return {"ok": False, "message": str(e), "stderr": ""}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def encoder_test(encoder: str) -> dict:
    """實際編碼測試畫面。回傳 {ok, message, stderr}；stderr 保留最後幾行供 Super 後台查看。
    QSV：2 秒 1280×720 寫成檔案並用 ffprobe 驗證（dev.59）；VA-API：維持 1 秒 320×240 的既有檢測（已實機驗證，不更動）。"""
    if encoder == "cpu":
        return {"ok": True, "message": "libx264（最後備援）", "stderr": ""}
    name = {"qsv": "h264_qsv", "vaapi": "h264_vaapi"}[encoder]
    if not os.path.exists(VAAPI_DEVICE):
        return {"ok": False, "message": f"找不到 {VAAPI_DEVICE}", "stderr": ""}
    try:
        p = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=30)
        if name not in p.stdout:
            return {"ok": False, "message": f"FFmpeg 沒有 {name} 編碼器", "stderr": ""}
        if encoder == "qsv":
            return _qsv_file_test()   # 測試畫面在系統記憶體，不經 QSV 解碼
        pre, vf, enc = encoder_args(encoder, False)
        p = _run(["ffmpeg", "-hide_banner", "-nostdin"] + pre + _TEST_SRC + vf + enc + ["-f", "null", "-"], timeout=60)
    except ToolError as e:
        return {"ok": False, "message": str(e), "stderr": ""}
    if p.returncode != 0:
        tail = _stderr_tail(p.stderr)
        last = tail.splitlines()[-1] if tail else "未知錯誤"
        return {"ok": False, "message": f"{name} 測試編碼失敗：{last[:200]}", "stderr": tail}
    return {"ok": True, "message": "", "stderr": ""}


def probe_encoders() -> dict:
    """三層分開檢測（Agent 啟動時執行一次）：{"qsv": {...}, "vaapi": {...}, "cpu": {...}}。"""
    return {e: encoder_test(e) for e in ENCODER_ORDER}


def qsv_available() -> tuple[bool, str]:
    """相容舊呼叫：只回報 Quick Sync。"""
    r = encoder_test("qsv")
    return r["ok"], r["message"]


# ---------- 四項自動檢查 ----------

def verify_output(path: str, expected_duration: Optional[float], expect_audio: bool) -> dict:
    checks = []
    try:
        raw = ffprobe(path)
        info = summarize_probe(path, raw, run_idet=False)
    except ToolError as e:
        return {"passed": False, "checks": [{"name": "讀取 MP4", "ok": False, "message": str(e)}]}
    dur = info.get("duration")
    if expected_duration:
        ok = dur is not None and abs(dur - expected_duration) <= 1.0
        checks.append({"name": "長度與原檔相差不超過 1 秒", "ok": ok,
                       "message": f"原檔 {expected_duration:.2f} 秒、MP4 {dur if dur is None else round(dur, 2)} 秒"})
    else:
        checks.append({"name": "長度與原檔相差不超過 1 秒", "ok": dur is not None and dur > 0, "message": "原檔長度不明，只確認 MP4 有長度"})
    streams_ok = info.get("video_codec") == "h264" and info.get("pix_fmt") in ("yuv420p", "yuvj420p") \
        and (info.get("audio_codec") == "aac" if expect_audio else True) \
        and any(c in (info.get("container") or "") for c in MP4_CONTAINERS)
    checks.append({"name": "影像軌、音訊軌存在且符合輸出規格", "ok": bool(streams_ok),
                   "message": f"{info.get('video_codec')}/{info.get('pix_fmt')}，音訊 {info.get('audio_codec') or '無'}"})
    p = _run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-f", "null", "-"], timeout=None)
    decode_ok = p.returncode == 0 and not p.stderr.strip()
    checks.append({"name": "完整解碼一次無錯誤", "ok": decode_ok, "message": "" if decode_ok else p.stderr.strip()[-500:]})
    fs = mp4_faststart(path)
    checks.append({"name": "索引資訊在檔頭（可直接拖曳播放）", "ok": fs is True, "message": "" if fs else "moov 不在檔頭"})
    return {"passed": all(c["ok"] for c in checks), "checks": checks, "duration": dur}
