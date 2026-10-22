"""视觉理解：把关键帧交给视觉大模型，拿回结构化标签。

走的是和各服务商通用的 OpenAI 兼容多模态格式：
`messages[].content = [{type:text}, {type:image_url, image_url:{url:"data:image/jpeg;base64,..."}}]`
通义千问 VL、豆包视觉、GLM-4V、OpenAI 都认这套。
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Optional

from ..services import llm_service

SYSTEM_PROMPT = "你是短视频素材分析助手。只输出 JSON，不要解释。"

USER_PROMPT = """看这张短视频画面，按下面的字段输出 JSON：
{
  "description": "不超过 20 个字，描述画面在拍什么",
  "tags": ["从这些里选 1-3 个：产品特写、人物口播、使用演示、包装展示、环境空镜、字幕卡、对比展示、其他"],
  "onscreen_text": "画面里出现的所有文字，用空格分隔；没有就填空字符串",
  "has_person": true 或 false,
  "product_visible": true 或 false,
  "hook_score": 0 到 10 的整数，表示"这段拿来当带货视频开头 3 秒够不够抓人"，
  "usage": "hook 或 body 或 unusable"
}

判断 hook_score 时考虑：有没有人脸、动作是否明确、画面是否干净、有没有能一眼看懂的信息。
只输出这个 JSON。"""

VALID_TAGS = {
    "产品特写", "人物口播", "使用演示", "包装展示",
    "环境空镜", "字幕卡", "对比展示", "其他",
}


def _encode_image(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    suffix = path.suffix.lower()
    mime = "png" if suffix == ".png" else "jpeg"
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _normalize(data: dict) -> dict:
    tags = []
    for tag in data.get("tags") or []:
        text = str(tag).strip()
        if text and text not in tags:
            tags.append(text)
    try:
        hook_score = int(round(float(data.get("hook_score", 5))))
    except (TypeError, ValueError):
        hook_score = 5
    usage = str(data.get("usage") or "body").strip().lower()
    if usage not in ("hook", "body", "unusable"):
        usage = "body"
    return {
        "description": str(data.get("description") or "").strip()[:60],
        "tags": tags,
        "onscreen_text": str(data.get("onscreen_text") or "").strip()[:120],
        "has_person": bool(data.get("has_person")),
        "product_visible": bool(data.get("product_visible")),
        "hook_score": max(0, min(10, hook_score)),
        "usage": usage,
    }


def analyze_frame(image_path: str, settings: Optional[dict] = None,
                  timeout: float = 60.0) -> Optional[dict]:
    """分析一张关键帧。失败返回 None，由调用方决定是否降级。"""
    settings = settings or llm_service.load_settings()
    api_key = settings.get("api_key") or ""
    model = settings.get("vision_model") or ""
    if not api_key or not model:
        return None

    image_url = _encode_image(Path(image_path))
    if not image_url:
        return None

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": USER_PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]},
        ],
    }

    last_error = None
    for attempt in range(2):
        try:
            response = llm_service._post_json(  # noqa: SLF001 - 复用同一套请求与错误处理
                llm_service._endpoint(settings.get("base_url", "")),
                payload, api_key, timeout=timeout,
            )
            content = response["choices"][0]["message"]["content"]
            if isinstance(content, list):
                # 少数服务商把 content 返回成数组
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            parsed = _parse_json(content)
            if parsed:
                return _normalize(parsed)
            last_error = "模型没有返回可解析的 JSON"
        except Exception as exc:  # noqa: BLE001 - 单帧失败不该中断整批分析
            last_error = str(exc)
            if attempt == 1:
                break
    if last_error:
        return None
    return None


def describe_settings_problem() -> str:
    settings = llm_service.load_settings()
    if not settings.get("api_key"):
        return "还没配置 API Key"
    if not settings.get("vision_model"):
        return "还没配置视觉模型名"
    return ""
