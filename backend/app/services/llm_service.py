"""LLM 文案生成（OpenAI 兼容接口）+ 转 SRT。

配置存在 %APPDATA%/VideoMatrix/llm_settings.json，API Key 不出本机。
兼容 DeepSeek / 通义千问 / 豆包 / Kimi / OpenAI 等所有 OpenAI 格式的接口，
用户自己填 Base URL、Key、模型名即可。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

SETTINGS_DEFAULTS = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "vision_model": "qwen-vl-plus",
    "temperature": 0.9,
}

# 中文口播大致语速，生成 SRT 时用来估时间轴
DEFAULT_CHARS_PER_SECOND = 4.5


def settings_dir() -> Path:
    root = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "VideoMatrix"
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_file() -> Path:
    return settings_dir() / "llm_settings.json"


def load_settings() -> dict:
    settings = dict(SETTINGS_DEFAULTS)
    path = settings_file()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                settings.update({k: v for k, v in saved.items() if k in SETTINGS_DEFAULTS})
        except (OSError, ValueError):
            pass
    return settings


def save_settings(payload: dict) -> dict:
    settings = load_settings()
    for key in ("base_url", "model", "vision_model"):
        if payload.get(key) is not None:
            settings[key] = str(payload[key]).strip()
    if payload.get("temperature") is not None:
        try:
            settings["temperature"] = max(0.0, min(2.0, float(payload["temperature"])))
        except (TypeError, ValueError):
            pass
    # 留空表示保持原 Key，避免前端拿到明文来回传
    if payload.get("api_key"):
        settings["api_key"] = str(payload["api_key"]).strip()
    if payload.get("clear_key"):
        settings["api_key"] = ""

    path = settings_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return settings


def public_settings() -> dict:
    settings = load_settings()
    return {
        "base_url": settings["base_url"],
        "model": settings["model"],
        "vision_model": settings["vision_model"],
        "temperature": settings["temperature"],
        "has_key": bool(settings["api_key"]),
    }


def _endpoint(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("请先填写接口地址（Base URL）")
    if url.endswith("/chat/completions"):
        return url
    if not url.endswith("/v1"):
        url += "/v1"
    return url + "/chat/completions"


def _post_json(url: str, payload: dict, api_key: str, timeout: float = 90.0) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="ignore"))
            message = detail.get("error", {}).get("message") or detail.get("message") or str(detail)
        except Exception:  # noqa: BLE001
            message = f"HTTP {exc.code}"
        raise RuntimeError(f"接口返回 {exc.code}：{message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连不上接口：{exc.reason}") from exc


def _build_prompt(brief: str, count: int, style: str, duration: float) -> str:
    words = max(20, int(duration * DEFAULT_CHARS_PER_SECOND))
    return (
        "你是短视频带货的口播文案编剧，服务信息流投放。\n"
        f"产品/卖点信息：{brief}\n"
        f"风格要求：{style or '真人口语化，像朋友聊天，有感染力'}\n"
        f"每条时长约 {duration:.0f} 秒（约 {words} 个字）。\n\n"
        "要求：\n"
        "1. 开头 3 秒必须有钩子，直接抛出痛点或反常识结论；\n"
        "2. 全篇口语，不要书面广告腔，不要“大家好”“今天给大家”这类开场；\n"
        f"3. 写 {count} 条，每条角度不同，可以换钩子、换场景、换人群；\n"
        "4. 每条结尾有明确行动指令（点击下方/评论区/看小黄车等，按产品调性来）；\n"
        "5. 只输出 JSON 数组，不要解释、不要 Markdown，格式："
        '[{"text": "第一条文案"}, {"text": "第二条文案"}]'
    )


def _extract_items(content: str) -> List[str]:
    text = (content or "").strip()
    # 有些模型仍会包 ```json
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or []
        if isinstance(data, list):
            items = []
            for entry in data:
                if isinstance(entry, str):
                    items.append(entry.strip())
                elif isinstance(entry, dict):
                    value = entry.get("text") or entry.get("content") or entry.get("文案")
                    if value:
                        items.append(str(value).strip())
            if items:
                return [item for item in items if item]
    except ValueError:
        pass
    # 兜底：按空行/行号切
    lines = [re.sub(r"^\s*[\d]+[.、)]\s*", "", line).strip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("{")]


def generate(payload: dict) -> dict:
    settings = load_settings()
    api_key = settings.get("api_key") or ""
    if not api_key:
        raise ValueError("还没配置 API Key，请先在上面填好并保存")

    brief = str(payload.get("brief") or "").strip()
    if not brief:
        raise ValueError("请先填写产品信息或卖点")
    try:
        count = max(1, min(10, int(payload.get("count") or 3)))
    except (TypeError, ValueError):
        count = 3
    try:
        duration = max(5.0, min(180.0, float(payload.get("duration") or 30)))
    except (TypeError, ValueError):
        duration = 30.0
    style = str(payload.get("style") or "").strip()

    request_payload = {
        "model": settings.get("model") or SETTINGS_DEFAULTS["model"],
        "temperature": settings.get("temperature", 0.9),
        "messages": [
            {"role": "system", "content": "你是专业短视频口播文案编剧，只输出要求的 JSON。"},
            {"role": "user", "content": _build_prompt(brief, count, style, duration)},
        ],
    }
    response = _post_json(_endpoint(settings.get("base_url", "")), request_payload, api_key)
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("接口返回格式不认识，请确认填的是 OpenAI 兼容地址") from exc

    items = _extract_items(content)
    if not items:
        raise RuntimeError("模型没有返回可用文案，换个模型或降低要求再试")
    return {"items": items[:count], "model": request_payload["model"]}


# ── SRT ──────────────────────────────────────────────────────────────────
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def split_sentences(text: str, max_chars: int = 18) -> List[str]:
    """按标点断句；过长的句子再按逗号拆，保证单行字幕不至于太挤。"""
    sentences: List[str] = []
    for chunk in _SENTENCE_SPLIT.split(text.replace("\r", "").strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) <= max_chars:
            sentences.append(chunk)
            continue
        buffer = ""
        for piece in re.split(r"(?<=[，,、])", chunk):
            if not piece:
                continue
            if len(buffer) + len(piece) <= max_chars:
                buffer += piece
            else:
                if buffer:
                    sentences.append(buffer)
                buffer = piece
        if buffer:
            sentences.append(buffer)
    return sentences or [text.strip()]


def _fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds * 1000)))
    hours, rest = divmod(total, 3600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def text_to_cues(text: str, chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
                 gap: float = 0.12) -> List[dict]:
    cues: List[dict] = []
    cursor = 0.0
    cps = max(1.0, float(chars_per_second or DEFAULT_CHARS_PER_SECOND))
    for sentence in split_sentences(text):
        length = max(2, len(re.sub(r"\s", "", sentence)))
        duration = max(0.8, min(8.0, length / cps))
        cues.append({"start": cursor, "duration": duration, "text": sentence})
        cursor += duration + gap
    return cues


def write_srt(text: str, directory: str, filename: str = "",
              chars_per_second: float = DEFAULT_CHARS_PER_SECOND) -> dict:
    target_dir = Path(str(directory or "").strip())
    if not target_dir.is_dir():
        raise ValueError("字幕目录不存在，请先在「字幕」卡片里选一个目录")

    cues = text_to_cues(text, chars_per_second)
    if not cues:
        raise ValueError("文案是空的")

    stem = (filename or "").strip()
    if not stem:
        stem = f"文案_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem).strip(" .") or f"文案_{uuid.uuid4().hex[:6]}"
    path = target_dir / f"{stem}.srt"
    if path.exists():
        path = target_dir / f"{stem}_{uuid.uuid4().hex[:4]}.srt"

    blocks = [
        f"{index}\n{_fmt_time(cue['start'])} --> {_fmt_time(cue['start'] + cue['duration'])}\n{cue['text']}"
        for index, cue in enumerate(cues, 1)
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "cues": len(cues),
        "duration": round(cues[-1]["start"] + cues[-1]["duration"], 2),
    }
