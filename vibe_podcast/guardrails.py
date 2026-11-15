"""Content safety guardrails against discrimination, hate speech, and ableist slurs."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from vibe_podcast.scriptwriter import PodcastScript


class ContentSafetyError(ValueError):
    """Raised when input or generated content violates anti-discrimination guardrails."""


BLOCKED_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "身心障礙或精神疾病歧視",
        re.compile(r"(智障|低能兒|低能仔|腦殘|白痴|白癡|神經病|瘋子|殘廢|死殘障|玻璃心神經)"),
    ),
    (
        "性別或多元性別歧視",
        re.compile(
            r"(女拳|母豬|台女|死娘炮|死基佬|人妖|變態同性戀|女人懂什麼|女生不適合寫程式|男主外女主內)"
        ),
    ),
    (
        "種族、國籍或地域歧視",
        re.compile(r"(死阿陸|支那|死老外|黑鬼|尼哥|印傭素質|外勞都|番仔)"),
    ),
    (
        "弱勢或社經歧視",
        re.compile(r"(底層賤民|窮鬼活該|乞丐命|低端人口|流浪漢都是)"),
    ),
    (
        "英文歧視或仇恨用語",
        re.compile(r"\b(retarded|retard|faggot|tranny|nigger|chink)\b", re.IGNORECASE),
    ),
]

SOFT_REPLACEMENTS: Dict[str, str] = {
    "精神分裂": "邏輯打架",
    "雙重人格": "前後矛盾",
    "強迫症": "極度講究",
}

SAFETY_PROMPT_RULES = """
【內容安全與反歧視紅線（必須嚴格遵守）】
- 嚴禁任何形式的歧視、貶抑、刻板印象或人身攻擊，包含但不限於：
  1. 身心與精神狀態歧視（禁止使用「神經病」、「精神分裂」、「白痴」、「智障」、「腦殘」等詞彙，即使是比喻系統混亂也請改用「邏輯打架」、「徹底錯亂」、「當機」）。
  2. 性別與多元性別歧視（禁止任何性別刻板印象、貶低特定性別或 LGBTQ+ 群體的言論）。
  3. 種族、國籍、文化與地域歧視。
  4. 弱勢族群、身心障礙者或社經地位歧視。
- 主持人吐槽只能針對「技術設計、系統架構或程式碼本身」，保持友善、尊重與專業。
""".strip()

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff]+")
_STRIP_OBFUSCATION_RE = re.compile(r"[\s\u200b-\u200d\ufeff.\-_*~·]+")

_INJECTION_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?", re.IGNORECASE),
    re.compile(r"(override|bypass|disregard)\s+(system|safety|all)\s+(rules?|instructions?|prompt)", re.IGNORECASE),
    re.compile(r"</?\s*source_document", re.IGNORECASE),
    re.compile(r"(忽略|無視|覆寫|繞過)(以上|先前|所有|系統)(指令|規則|設定|提示)"),
]


def check_prompt_injection(text: str, context: str = "輸入內容") -> None:
    """Reject obvious prompt-override or tag-escape payloads in user-supplied presets."""
    cleaned = _ZERO_WIDTH_RE.sub("", text)
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise ContentSafetyError(
                f"偵測到疑似提示詞注入（Prompt Injection）指令，已封鎖{context}。"
            )


def _apply_soft_replacements(text: str) -> str:
    for bad, safe in SOFT_REPLACEMENTS.items():
        text = text.replace(bad, safe)
    return text


def check_text_safety(text: str, context: str = "輸入內容") -> None:
    """Raise ContentSafetyError if text contains discriminatory or hateful expressions."""
    no_zw = _ZERO_WIDTH_RE.sub("", text)
    compact = _STRIP_OBFUSCATION_RE.sub("", no_zw)
    for category, pattern in BLOCKED_PATTERNS:
        targets = (no_zw, compact) if category == "英文歧視或仇恨用語" else (compact,)
        for target in targets:
            match = pattern.search(target)
            if match:
                raise ContentSafetyError(
                    f"觸發內容安全護欄（{category}）：偵測到不當用語「{match.group(0)}」，於{context}。"
                )


def enforce_script_guardrails(script: PodcastScript) -> PodcastScript:
    """Sanitize mild metaphors and block any discriminatory content in a PodcastScript."""
    cleaned_host_a = _apply_soft_replacements(script.host_a)
    cleaned_host_b = _apply_soft_replacements(script.host_b)
    cleaned_guest = _apply_soft_replacements(script.guest_name) if script.guest_name else None

    check_text_safety(cleaned_host_a, context="Host A 名稱")
    check_text_safety(cleaned_host_b, context="Host B 名稱")
    if cleaned_guest:
        check_text_safety(cleaned_guest, context="特別來賓名稱")

    cleaned_title = _apply_soft_replacements(script.title)
    cleaned_summary = _apply_soft_replacements(script.summary)

    check_text_safety(cleaned_title, context="單集標題")
    check_text_safety(cleaned_summary, context="單集摘要")

    cleaned_turns = []
    for idx, turn in enumerate(script.turns):
        cleaned_speaker_name = _apply_soft_replacements(turn.speaker_name)
        check_text_safety(cleaned_speaker_name, context=f"第 {idx + 1} 輪說話者名稱")
        text = _apply_soft_replacements(turn.text)
        check_text_safety(text, context=f"第 {idx + 1} 輪對話")
        cleaned_turns.append(
            turn.model_copy(update={"speaker_name": cleaned_speaker_name, "text": text})
        )

    return script.model_copy(
        update={
            "host_a": cleaned_host_a,
            "host_b": cleaned_host_b,
            "guest_name": cleaned_guest,
            "title": cleaned_title,
            "summary": cleaned_summary,
            "turns": cleaned_turns,
        }
    )
