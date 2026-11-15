"""Extensible interview structure, show presets, and multi-speaker blueprints."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import threading
from typing import Dict, List


@dataclass(frozen=True)
class InterviewPreset:
    """Declarative show format preset for dual/multi-host interviews."""

    id: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    host_a_role: str
    host_b_role: str
    guest_role: str
    pacing_instruction: str


INTERVIEW_PRESETS: Dict[str, InterviewPreset] = {
    "noodle_chat": InterviewPreset(
        id="noodle_chat",
        name_zh="🍜 深夜麵攤閒聊風",
        name_en="🍜 Late-Night Noodle Stand Chat",
        description_zh="輕鬆接地氣、生活化比喻多，適合通勤收聽",
        description_en="Casual, relatable analogies, great for commuting",
        host_a_role="好奇心強、務實的全端工程師，代表聽眾問出白話問題並適度幽默吐槽",
        host_b_role="資深系統架構師，擅長用日常比喻拆解底層原理",
        guest_role="受邀來麵攤吃宵夜的技術專家／作者視角，分享第一手實戰細節",
        pacing_instruction="語氣像老朋友在麵攤邊吃麵邊聊技術，多用生活比喻、自然插話與笑點。",
    ),
    "deep_dive": InterviewPreset(
        id="deep_dive",
        name_zh="🎙️ 硬核技術深潛風",
        name_en="🎙️ Hardcore Tech Deep Dive",
        description_zh="直擊架構設計、Trade-offs 與工程踩坑實戰",
        description_en="Focuses on system architecture, trade-offs, and war stories",
        host_a_role="技術總編型主持人，問題精準犀利，會不斷追問效能數據與實作細節",
        host_b_role="首席架構師，精準剖析系統瓶頸、記憶體與延遲權衡",
        guest_role="核心開發者／領域專家，負責回應深度架構設計決策",
        pacing_instruction="保持專業節奏，深入剖析為什麼這樣設計、跟傳統方案的差異與真實代價。",
    ),
    "debate": InterviewPreset(
        id="debate",
        name_zh="⚔️ 正反辯論擂台風",
        name_en="⚔️ Tech Debate Arena",
        description_zh="樂觀派 vs 懷疑派正面交鋒，激盪技術優缺點",
        description_en="Optimist vs Skeptic debating pros, cons, and risks",
        host_a_role="技術樂觀派，看好這項新技術帶來的突破與未來場景",
        host_b_role="務實懷疑派（魔鬼代言人），專門挑戰落地成本、相容性與潛在風險",
        guest_role="中立裁判與實務顧問，負責補充關鍵事實並收斂共識",
        pacing_instruction="雙方必須有明確觀點碰撞與禮貌交鋒，不許一面倒吹捧。",
    ),
    "quick_brief": InterviewPreset(
        id="quick_brief",
        name_zh="⚡ 3 分鐘科技快報風",
        name_en="⚡ 3-Min Tech Briefing",
        description_zh="節奏明快、直奔重點與業界影響評估",
        description_en="Fast-paced briefing focused on key takeaways and impact",
        host_a_role="科技新聞主播，負責快速拋出核心事件與聽眾最關心的問題",
        host_b_role="產業分析師，用精煉短句點出關鍵技術突破與影響",
        guest_role="現場連線專家，補充一句關鍵實務洞察",
        pacing_instruction="節奏緊湊俐落，每輪台詞精簡有力，快速帶過核心亮點與結論。",
    ),
}

DEFAULT_INTERVIEW_FORMAT = "noodle_chat"
BUILTIN_PRESET_IDS = frozenset(INTERVIEW_PRESETS.keys())
MAX_CUSTOM_PRESETS = 50
_PRESETS_LOCK = threading.RLock()

CHAPTER_LABELS: Dict[str, Dict[str, str]] = {
    "hook": {"zh-TW": "破冰開場", "en-US": "Hook"},
    "deep_dive": {"zh-TW": "核心拆解", "en-US": "Deep Dive"},
    "challenge": {"zh-TW": "靈魂拷問", "en-US": "Challenge"},
    "takeaway": {"zh-TW": "麵攤總結", "en-US": "Takeaway"},
}


def get_preset(format_id: str) -> InterviewPreset:
    """Return an InterviewPreset by id, falling back to default."""
    with _PRESETS_LOCK:
        return INTERVIEW_PRESETS.get(format_id, INTERVIEW_PRESETS[DEFAULT_INTERVIEW_FORMAT])


def list_presets_meta(lang: str = "zh-TW") -> List[Dict[str, str]]:
    """Serialize interview presets for UI dropdowns and /api/meta."""
    is_zh = lang.startswith("zh")
    with _PRESETS_LOCK:
        presets = list(INTERVIEW_PRESETS.values())
    return [
        {
            "id": p.id,
            "name": p.name_zh if is_zh else p.name_en,
            "description": p.description_zh if is_zh else p.description_en,
        }
        for p in presets
    ]


def _get_custom_presets_path() -> Path:
    """Resolve the JSON file path used to persist custom interview presets."""
    override = os.environ.get("VIBEPODCAST_PRESETS_FILE")
    if override:
        return Path(override)
    episodes_dir = os.environ.get("VIBEPODCAST_EPISODES_DIR")
    if episodes_dir:
        return Path(episodes_dir) / "_custom_presets.json"
    return Path(__file__).resolve().parent.parent / "episodes" / "_custom_presets.json"


def _save_persisted_custom_presets() -> None:
    """Persist all non-built-in presets to disk atomically (caller holds _PRESETS_LOCK)."""
    import tempfile

    path = _get_custom_presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    custom = [
        asdict(p) for pid, p in INTERVIEW_PRESETS.items() if pid not in BUILTIN_PRESET_IDS
    ]
    payload = json.dumps(custom, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".presets_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _load_persisted_custom_presets() -> None:
    """Load persisted custom presets from disk on startup."""
    path = _get_custom_presets_path()
    if not path.is_file():
        return
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(items, list):
        return
    with _PRESETS_LOCK:
        for item in items:
            if not isinstance(item, dict):
                continue
            pid = item.get("id")
            if not pid or pid in BUILTIN_PRESET_IDS:
                continue
            try:
                INTERVIEW_PRESETS[pid] = InterviewPreset(**item)
            except TypeError:
                continue


def register_custom_preset(preset: InterviewPreset) -> InterviewPreset:
    """Register a custom InterviewPreset at runtime and persist it to disk."""
    if preset.id in BUILTIN_PRESET_IDS:
        raise ValueError(f"Cannot overwrite built-in preset: {preset.id}")
    with _PRESETS_LOCK:
        custom_count = sum(1 for k in INTERVIEW_PRESETS if k not in BUILTIN_PRESET_IDS)
        if preset.id not in INTERVIEW_PRESETS and custom_count >= MAX_CUSTOM_PRESETS:
            raise ValueError("Custom preset limit reached")
        INTERVIEW_PRESETS[preset.id] = preset
        _save_persisted_custom_presets()
    return preset


_load_persisted_custom_presets()
