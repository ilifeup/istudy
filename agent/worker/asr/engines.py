"""字幕辨識引擎（dev.57）：同一個介面 recognize(批次音訊) → [{text, tokens, timestamps}]。

本機路線（4 個名稱）：
  paraformer-zh     FunASR Paraformer-zh（sherpa-onnx int8）＋ CT-Transformer 標點   ← 實作（預設）
  sensevoice-small  FunASR SenseVoiceSmall（sherpa-onnx int8，內建標點／ITN）        ← 實作
  qwen3-asr-0.6b    Qwen3-ASR-0.6B（＋ForcedAligner）                               ← 只預留名稱
  breeze-asr-25     Breeze-ASR-25（台灣口音，Whisper-large-v2 微調）                  ← 只預留名稱（要啟用時需另外加入對應的執行環境）
線上路線：online（只預留介面，尚未接任何服務；啟用後音檔會送出 Server）。

dev.59：移除 Whisper（faster-whisper）引擎。FunASR 已在實機驗收，「自動」只在 FunASR 之間選；
舊設定或舊工作若仍指定 whisper，會明確報錯（REMOVED），不會默默改用別的引擎。
歷史字幕的 subtitles.model／asr_report 若寫著 whisper-small，資料保留不動。
"""
from __future__ import annotations

import os
from typing import Optional

# 模型檔放在 /opt/istudy/models/asr/<資料夾>（Agent 容器內是 /models/asr/…）；由 istudyctl asr-models 下載
VAD_FILE = "asr/vad/silero_vad.onnx"
PUNCT_FILE = "asr/punct-ct-transformer/model.int8.onnx"

ENGINES: list[dict] = [
    {"id": "paraformer-zh", "name": "FunASR Paraformer-zh", "route": "local", "runtime": "sherpa-onnx",
     "stage": "ready", "files": ["asr/paraformer-zh/model.int8.onnx", "asr/paraformer-zh/tokens.txt", PUNCT_FILE],
     "note": "中文主力候選：非自回歸、CPU 快；標點由 CT-Transformer 補上；不支援熱詞（用修正表）"},
    {"id": "sensevoice-small", "name": "FunASR SenseVoiceSmall", "route": "local", "runtime": "sherpa-onnx",
     "stage": "ready", "files": ["asr/sensevoice-small/model.int8.onnx", "asr/sensevoice-small/tokens.txt"],
     "note": "速度基線：最快；內建標點；不支援熱詞（用修正表）"},
    {"id": "qwen3-asr-0.6b", "name": "Qwen3-ASR-0.6B（＋ForcedAligner）", "route": "local", "runtime": "",
     "stage": "reserved", "files": [], "note": "準確度挑戰者：尚未提供（盲測後再決定是否加入）"},
    {"id": "breeze-asr-25", "name": "Breeze-ASR-25", "route": "local", "runtime": "",
     "stage": "reserved", "files": [], "note": "台灣口音挑戰者：尚未提供（盲測後再決定是否加入）"},
    {"id": "online", "name": "線上 API", "route": "online", "runtime": "",
     "stage": "reserved", "files": [], "note": "只預留介面，尚未設定任何服務；啟用後音檔會送出 Server"},
]
BY_ID = {e["id"]: e for e in ENGINES}
AUTO_ORDER = ("paraformer-zh", "sensevoice-small")   # 「自動」時依序選第一個可用的
# 已移除的引擎：舊設定或佇列中的舊工作指定它們時，回覆這段說明（dev.59）
REMOVED = {"whisper": "Whisper（faster-whisper）已在 dev.59 移除：請到 Server App「字幕」把引擎改成「自動」或 FunASR 引擎"}


class EngineUnavailable(Exception):
    pass


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 — 原生模組載入失敗（例如缺 .so）也算不可用
        return False


def availability(eid: str, models_dir: str) -> tuple[bool, str]:
    """(可用, 白話原因)。只檢查套件與模型檔是否存在，不載入模型。"""
    e = BY_ID.get(eid)
    if not e:
        return False, REMOVED.get(eid, "沒有這個引擎")
    if e["stage"] == "reserved":
        return False, "尚未設定" if e["route"] == "online" else "尚未提供"
    if e["runtime"] == "sherpa-onnx":
        if not _has_module("sherpa_onnx"):
            return False, "Agent 映像沒有安裝 sherpa-onnx"
        missing = [f for f in e["files"] + [VAD_FILE] if not os.path.isfile(os.path.join(models_dir, f))]
        if missing:
            return False, "模型未安裝（sudo istudyctl asr-models --fetch " + eid + "）"
        return True, ""
    return False, "不支援"


def status_all(models_dir: str) -> dict:
    """Agent 心跳回報：每個引擎是否可用，以及「自動」目前會選哪一個。"""
    st = {}
    for e in ENGINES:
        ok, why = availability(e["id"], models_dir)
        st[e["id"]] = {"ok": ok, "reason": why}
    auto = next((i for i in AUTO_ORDER if st[i]["ok"]), None)
    return {"engines": st, "auto": auto, "vad": os.path.isfile(os.path.join(models_dir, VAD_FILE))}


def resolve(choice: Optional[str], models_dir: str) -> str:
    """設定值（auto 或引擎代號）→ 實際要用的引擎；不可用時丟出白話錯誤，不默默改用別的引擎。"""
    choice = (choice or "auto").strip()
    if choice == "auto":
        for i in AUTO_ORDER:
            if availability(i, models_dir)[0]:
                return i
        raise EngineUnavailable("沒有可用的字幕辨識引擎：請先下載模型（sudo istudyctl asr-models --fetch all）")
    if choice in REMOVED:
        raise EngineUnavailable(REMOVED[choice])
    ok, why = availability(choice, models_dir)
    if not ok:
        name = BY_ID.get(choice, {}).get("name", choice)
        raise EngineUnavailable(f"{name}：{why}")
    return choice


# ---------------------------------------------------------------- 引擎實作 ----

class SherpaEngine:
    """FunASR 模型（Paraformer-zh／SenseVoiceSmall）以 sherpa-onnx（ONNX Runtime，CPU int8）執行。"""

    def __init__(self, eid: str, models_dir: str, threads: int):
        import sherpa_onnx  # type: ignore
        self.id = eid
        self.punct = None
        d = os.path.join(models_dir, "asr", eid)
        tokens = os.path.join(d, "tokens.txt")
        model = os.path.join(d, "model.int8.onnx")
        if eid == "paraformer-zh":
            self.rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=model, tokens=tokens, num_threads=threads, sample_rate=16000, feature_dim=80,
                decoding_method="greedy_search")
            pc = sherpa_onnx.OfflinePunctuationConfig(
                model=sherpa_onnx.OfflinePunctuationModelConfig(ct_transformer=os.path.join(models_dir, PUNCT_FILE),
                                                                num_threads=max(1, min(threads, 2))))
            self.punct = sherpa_onnx.OfflinePunctuation(pc)
        else:
            self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model, tokens=tokens, num_threads=threads, sample_rate=16000, feature_dim=80,
                decoding_method="greedy_search", language="zh", use_itn=True)
        self.version = getattr(sherpa_onnx, "__version__", "")

    def recognize(self, batch: list) -> list[dict]:
        streams = []
        for samples in batch:
            s = self.rec.create_stream()
            s.accept_waveform(16000, samples)
            streams.append(s)
        self.rec.decode_streams(streams)
        out = []
        for s in streams:
            r = s.result
            text = (r.text or "").strip()
            tokens = list(getattr(r, "tokens", []) or [])
            times = list(getattr(r, "timestamps", []) or [])
            if self.punct and text:
                text = self.punct.add_punctuation(text)
            out.append({"text": text, "tokens": tokens, "timestamps": times})
        return out


class OnlineEngine:
    """線上路線（只預留介面）。

    之後要接服務時，實作 recognize(batch) 即可：輸入 16 kHz float32 片段，回傳 [{text, tokens, timestamps}]。
    設定會放在 server_config 的 asr.online（服務名稱、網址、模型）；金鑰不寫進報告、不回傳給畫面。
    """

    def __init__(self, *_a, **_k):
        raise EngineUnavailable("線上路線尚未設定任何服務")


def create(eid: str, models_dir: str, threads: int):
    if eid in ("paraformer-zh", "sensevoice-small"):
        return SherpaEngine(eid, models_dir, threads)
    if eid in REMOVED:
        raise EngineUnavailable(REMOVED[eid])
    if eid == "online":
        return OnlineEngine()
    raise EngineUnavailable(f"{BY_ID.get(eid, {}).get('name', eid)}：尚未提供")
