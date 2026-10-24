"""字幕断句与排版。

导入的 SRT 常见问题是「一条字幕几十个字」，烧进竖屏后会被 libass 折成
三四行，读起来很挤。这里做三件事：

1. 按句末标点、逗号把长句切成短句，单条不超过 max_chars 个字；
2. 按字数比例把原字幕的时间段重新分配给这些短句，保持整体口型同步；
3. 相邻两条都太短时合并，避免「嗯」「对」这种一闪而过的碎条。

可以选择让 LLM 做语义断句（避免把「增压花洒」切成「增压花」+「洒」），
AI 结果会做一致性校验，拼接后和原文不符就自动回退规则断句。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Sequence

# 句末标点：优先在这里断开
SENTENCE_END = "。！？!?…～~"
# 次级断点：句末切完还太长时按这些切
SOFT_BREAK = "，,、；;：:"

TIME_LINE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
# 字幕里的样式标签，断句时先剥掉，避免把 <i> 切成两半
TAG_BODY = re.compile(r"<[^>]{1,20}>|\{[^}]{0,40}\}")
PUNCT_ALL = re.compile(r"[\s，,。．.！!？?、；;：:…\-—~～\"'“”‘’（）()【】\[\]《》<>]+")

DEFAULT_MAX_CHARS = 15
DEFAULT_MIN_CHARS = 4
MAX_PARTS_PER_CUE = 12


# ── 时间 ────────────────────────────────────────────────────────────────
def _to_ms(match: re.Match, offset: int) -> int:
    hour = int(match.group(offset))
    minute = int(match.group(offset + 1))
    second = int(match.group(offset + 2))
    fraction = match.group(offset + 3).ljust(3, "0")[:3]
    return ((hour * 60 + minute) * 60 + second) * 1000 + int(fraction)


def _fmt_time(ms: int) -> str:
    ms = max(0, int(ms))
    hour, rest = divmod(ms, 3600_000)
    minute, rest = divmod(rest, 60_000)
    second, milli = divmod(rest, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{milli:03d}"


# ── 宽度 ────────────────────────────────────────────────────────────────
def char_width(text: str) -> float:
    """按显示宽度估算字数：中文 1，ASCII（英文/数字/半角符号）约 0.55。"""
    total = 0.0
    for char in text:
        total += 0.55 if ord(char) < 0x2E80 else 1.0
    return total


def strip_tags(text: str) -> str:
    return TAG_BODY.sub("", text or "").strip()


def _plain(text: str) -> str:
    """去掉所有标签、标点、空白，用来做 AI 结果的一致性校验。"""
    return PUNCT_ALL.sub("", strip_tags(text))


# ── 解析 / 输出 ─────────────────────────────────────────────────────────
def parse_srt(content: str) -> List[dict]:
    """宽容地解析 SRT：允许缺序号、多余空行、CRLF、半角逗号时间码。"""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    cues: List[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        index = 1 if re.fullmatch(r"\d+", lines[0].strip()) and len(lines) > 1 else 0
        if index >= len(lines):
            continue
        match = TIME_LINE.search(lines[index])
        if not match:
            continue
        body = strip_tags("\n".join(lines[index + 1:]))
        body = re.sub(r"\s*\n\s*", " ", body).strip()
        if not body:
            continue
        start, end = _to_ms(match, 1), _to_ms(match, 5)
        if end <= start:
            end = start + 800
        cues.append({"start": start, "end": end, "text": body})
    return cues


def build_srt(cues: Sequence[dict]) -> str:
    blocks = [
        f"{index}\n{_fmt_time(cue['start'])} --> {_fmt_time(cue['end'])}\n{cue['text']}"
        for index, cue in enumerate(cues, 1)
    ]
    return "\n\n".join(blocks) + "\n\n"


# ── 断句 ────────────────────────────────────────────────────────────────
def _hard_split(text: str, max_chars: float) -> List[str]:
    """没有标点的长句：按字数硬切，尽量不切断英文单词和数字。

    中文没有词边界，靠两个小规则减少「都能用不 / 需要」这种断法：
    · 切点前一个字是「不 / 没 / 就 / 都」这类黏前字 → 把它让到下一段开头；
    · 切点后一个字是「的 / 了 / 吗」这类黏后字 → 把它并到上一段结尾。
    """
    # 通常要和后面的字连用，不适合留在上一段末尾
    attach_next = set("不没未别很太更最就都也还才又再把被让给对从向和与及或但而所将能会要想该可")
    # 通常不该出现在一段开头
    no_lead = set("的了着过吗呢吧啊呀哦嘛")

    parts: List[str] = []
    buffer = ""
    for char in text:
        candidate = buffer + char
        if char_width(candidate) > max_chars and buffer:
            # 英文/数字中间不断开
            if buffer[-1].isascii() and buffer[-1].isalnum() and char.isascii() and char.isalnum():
                parts.append(buffer + char)
                buffer = ""
                continue
            if buffer[-1] in attach_next and len(buffer) > 1:
                carry = buffer[-1]
                parts.append(buffer[:-1])
                buffer = carry + char
            elif char in no_lead:
                parts.append(buffer + char)
                buffer = ""
            else:
                parts.append(buffer)
                buffer = char
        else:
            buffer = candidate
    if buffer:
        parts.append(buffer)
    return parts


def _split_by_soft(chunk: str, max_chars: float) -> List[str]:
    pieces = [piece for piece in re.split(rf"(?<=[{SOFT_BREAK}])\s*", chunk) if piece.strip()]
    merged: List[str] = []
    buffer = ""
    for piece in pieces:
        if not buffer:
            buffer = piece
        elif char_width(buffer) + char_width(piece) <= max_chars:
            buffer += piece
        else:
            merged.append(buffer)
            buffer = piece
    if buffer:
        merged.append(buffer)

    result: List[str] = []
    for piece in merged:
        if char_width(piece) <= max_chars:
            result.append(piece)
        else:
            result.extend(_hard_split(piece, max_chars))
    return result


def _merge_parts(parts: List[str], max_chars: float) -> List[str]:
    """把切得太碎的片段（比如单独一个「对，」）合并回上一条。"""
    merged: List[str] = []
    for part in parts:
        if merged and char_width(merged[-1]) + char_width(part) <= max_chars:
            merged[-1] += part
        else:
            merged.append(part)
    return [item.strip() for item in merged if item.strip()]


def split_sentence(text: str, max_chars: float = DEFAULT_MAX_CHARS) -> List[str]:
    """把一条字幕切成若干不超长的短句。"""
    clean = strip_tags(text)
    if not clean:
        return []
    if char_width(clean) <= max_chars:
        return [clean]

    chunks = [c for c in re.split(rf"(?<=[{SENTENCE_END}])\s*", clean) if c.strip()]
    parts: List[str] = []
    for chunk in chunks:
        if char_width(chunk) <= max_chars:
            parts.append(chunk)
        else:
            parts.extend(_split_by_soft(chunk, max_chars))

    parts = [p for p in parts if p.strip()]
    if not parts:
        return [clean]
    # 保留原句末标点的收尾感：切碎后每段都不带标点时补一个逗号意义不大，
    # 这里只做长度合并，不动文字内容。
    return _merge_parts(parts, max_chars)


# ── 时间重分配 / 合并 ───────────────────────────────────────────────────
def reflow_cue(cue: dict, parts: Sequence[str]) -> List[dict]:
    """把一条字幕的时间段按字数比例分给它的各个短句。"""
    parts = [part for part in parts if part.strip()]
    if not parts:
        return []
    if len(parts) == 1:
        return [{"start": cue["start"], "end": cue["end"], "text": parts[0]}]

    span = max(0, cue["end"] - cue["start"])
    weights = [max(0.1, char_width(part)) for part in parts]
    total = sum(weights)

    out: List[dict] = []
    cursor = cue["start"]
    for index, (part, weight) in enumerate(zip(parts, weights)):
        if index == len(parts) - 1:
            end = cue["end"]
        else:
            end = cursor + int(round(span * weight / total))
            end = min(max(end, cursor + 120), cue["end"])
        out.append({"start": cursor, "end": end, "text": part})
        cursor = end
    return out


def merge_short(cues: Sequence[dict], min_chars: float = DEFAULT_MIN_CHARS,
                 max_chars: float = DEFAULT_MAX_CHARS, max_gap_ms: int = 350) -> List[dict]:
    """合并过短的相邻字幕，避免「嗯」「对」这种一闪而过的碎条。"""
    merged: List[dict] = []
    for cue in cues:
        if merged:
            prev = merged[-1]
            gap = cue["start"] - prev["end"]
            joined = prev["text"] + cue["text"]
            short = char_width(prev["text"]) < min_chars or char_width(cue["text"]) < min_chars
            if short and gap <= max_gap_ms and char_width(joined) <= max_chars:
                prev["text"] = joined
                prev["end"] = cue["end"]
                continue
        merged.append(dict(cue))
    return merged


# ── AI 结果校验 ─────────────────────────────────────────────────────────
def sanitize_ai_parts(original: str, parts: Optional[Sequence[str]],
                      max_chars: float) -> Optional[List[str]]:
    """校验 LLM 的断句结果：不能改字、不能漏字、不能超长。"""
    if not parts:
        return None
    cleaned = [strip_tags(str(part)).strip() for part in parts]
    cleaned = [part for part in cleaned if part]
    if not cleaned or len(cleaned) > MAX_PARTS_PER_CUE:
        return None
    if _plain("".join(cleaned)) != _plain(original):
        return None
    if any(char_width(part) > max_chars * 1.6 for part in cleaned):
        return None
    return cleaned


def normalize(cues: Sequence[dict], max_chars: float = DEFAULT_MAX_CHARS,
              min_chars: float = DEFAULT_MIN_CHARS,
              ai_parts: Optional[Sequence[Optional[Sequence[str]]]] = None) -> List[dict]:
    """主流程：断句 → 重分配时间 → 合并碎条。"""
    if not cues:
        return []

    out: List[dict] = []
    for index, cue in enumerate(cues):
        parts = None
        if ai_parts and index < len(ai_parts):
            parts = sanitize_ai_parts(cue["text"], ai_parts[index], max_chars)
        if not parts:
            parts = split_sentence(cue["text"], max_chars)
        out.extend(reflow_cue(cue, parts))
    return merge_short(out, min_chars, max_chars)


# ── 结果缓存（AI 断句按文件内容缓存，避免每条成片都调一次接口） ────────
def _cache_dir() -> Path:
    root = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "VideoMatrix" / "subtitle_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_key(content: str, max_chars: float, min_chars: float, mode: str) -> str:
    payload = f"{mode}|{max_chars:g}|{min_chars:g}|{content}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def load_cached(key: str) -> Optional[List[dict]]:
    path = _cache_dir() / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return data
    except (OSError, ValueError):
        pass
    return None


def save_cached(key: str, cues: Sequence[dict]) -> None:
    try:
        path = _cache_dir() / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(cues), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
