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


def _endpoint_candidates(base_url: str) -> List[str]:
    """按优先级给出候选请求地址。

    各家的 Base URL 版本段不一样：
      · OpenAI / DeepSeek / Kimi / 硅基流动 → 要 /v1
      · 火山方舟（豆包）→ /api/v3
      · 智谱 → /api/paas/v4
    所以不再猜着补 /v1：先按用户填的原样请求，只有 404/405 才换下一种写法。
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("请先填写接口地址（Base URL）")
    if url.endswith("/chat/completions"):
        return [url]
    candidates = [f"{url}/chat/completions"]
    # 末尾已经带版本段（/v1、/v3、/v4 …）时不要再补 /v1，否则必然 404
    if not re.search(r"/v\d+$", url):
        candidates.append(f"{url}/v1/chat/completions")
    return candidates


def _endpoint(base_url: str) -> str:
    """单个地址（兼容旧调用）；多候选场景请用 _endpoint_candidates。"""
    return _endpoint_candidates(base_url)[0]


def _is_missing_path(message: str) -> bool:
    return "接口返回 404" in message or "接口返回 405" in message


def _chat(base_url: str, api_key: str, model: str, messages: list,
          temperature: float = 0.3, timeout: float = 90.0) -> str:
    """调一次 chat/completions，返回正文。

    自动处理「Base URL 该不该带 /v1」：先按用户填的原样请求，
    只有 404/405 才回退到补 /v1 的写法。
    """
    payload = {"model": model, "temperature": temperature, "messages": messages}
    response = None
    tried: List[str] = []
    last_error: Optional[RuntimeError] = None
    for endpoint in _endpoint_candidates(base_url):
        tried.append(endpoint)
        try:
            response = _post_json(endpoint, payload, api_key, timeout=timeout)
            break
        except RuntimeError as exc:
            last_error = exc
            # 只有「路径不存在」才换下一种写法；鉴权、模型名、额度错误直接抛
            if not _is_missing_path(str(exc)):
                raise
    if response is None:
        raise RuntimeError(
            f"{last_error}。已尝试：{'、'.join(tried)}。"
            "请确认 Base URL 填到版本号这一级（火山方舟例：https://ark.cn-beijing.volces.com/api/v3）"
        )
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("接口返回格式不认识，请确认填的是 OpenAI 兼容地址") from exc


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

    model = settings.get("model") or SETTINGS_DEFAULTS["model"]
    messages = [
        {"role": "system", "content": "你是专业短视频口播文案编剧，只输出要求的 JSON。"},
        {"role": "user", "content": _build_prompt(brief, count, style, duration)},
    ]
    content = _chat(
        settings.get("base_url", ""), api_key, model, messages,
        temperature=settings.get("temperature", 0.9),
    )


    items = _extract_items(content)
    if not items:
        raise RuntimeError("模型没有返回可用文案，换个模型或降低要求再试")
    return {"items": items[:count], "model": model}


# ── 字幕断句 ────────────────────────────────────────────────────────────
_SUBTITLE_BATCH = 40


def _parse_split_json(content: str, expected: int) -> List[Optional[List[str]]]:
    text = (content or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except ValueError:
        return [None] * expected

    entries = data.get("items") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return [None] * expected

    result: List[Optional[List[str]]] = [None] * expected
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("i", position + 1)) - 1
        except (TypeError, ValueError):
            index = position
        if not 0 <= index < expected:
            continue
        parts = entry.get("parts")
        if isinstance(parts, list):
            cleaned = [str(part).strip() for part in parts if str(part).strip()]
            result[index] = cleaned or None
    return result


def split_subtitles(texts: List[str], max_chars: int = 15) -> Optional[List[Optional[List[str]]]]:
    """让模型按语义把每条字幕切成短句。

    返回与输入等长的列表（元素是分句数组，None 表示这条不切）。
    未配置 Key、接口失败、返回格式异常时返回 None，调用方回退规则断句。
    """
    if not texts:
        return []
    settings = load_settings()
    api_key = settings.get("api_key") or ""
    if not api_key:
        return None
    model = settings.get("model") or SETTINGS_DEFAULTS["model"]

    system_prompt = (
        "你是短视频字幕排版助手。只输出 JSON，不要解释。"
        "任务是把长字幕切成适合竖屏阅读的短句，绝对不允许改写、增删文字。"
    )
    results: List[Optional[List[str]]] = []

    for start in range(0, len(texts), _SUBTITLE_BATCH):
        batch = texts[start:start + _SUBTITLE_BATCH]
        numbered = "\n".join(f"{index}. {text}" for index, text in enumerate(batch, 1))
        user_prompt = (
            f"把下面的字幕逐条切成短句，每条不超过 {max_chars} 个字（英文数字按半个字算）。\n"
            "规则：\n"
            "1. 只在语义完整处断开，不要拆开词语、品牌名、价格、数字；\n"
            "2. 不改字、不增字、不删字，标点可以省略；\n"
            "3. 一条可以切成 1 到 6 段，本来就短的不要切；\n"
            '4. 只输出 JSON：{"items":[{"i":1,"parts":["第一段","第二段"]}]}\n\n'
            f"字幕：\n{numbered}"
        )
        try:
            content = _chat(
                settings.get("base_url", ""), api_key, model,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                timeout=120.0,
            )
        except RuntimeError:
            return None
        results.extend(_parse_split_json(content, len(batch)))

    return results if any(results) else None


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
