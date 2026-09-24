from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Collection

from study_app.ai.providers import LLM_FEATURES, LLMSettings, estimate_tokens, load_llm_settings


class LLMNotConfiguredError(RuntimeError):
    pass


LLM_HTTP_TIMEOUT_SECONDS = 75


def _normalize_message_content(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif item.get("type") == "text" and isinstance(item.get("content"), str):
                    parts.append(str(item.get("content")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part.strip() for part in parts if str(part).strip()).strip()
    return str(content or "").strip()


def _extract_json_candidate(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if text.startswith("{") or text.startswith("["):
        return text

    first_object = text.find("{")
    first_array = text.find("[")
    indices = [index for index in (first_object, first_array) if index >= 0]
    if not indices:
        return text
    return text[min(indices):].strip()


def _slice_first_balanced_json(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    opener = None
    closer = None
    start_index = -1
    for index, char in enumerate(text):
        if char == "{":
            opener, closer, start_index = "{", "}", index
            break
        if char == "[":
            opener, closer, start_index = "[", "]", index
            break
    if start_index < 0 or opener is None or closer is None:
        return text

    depth = 0
    in_string = False
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]
    return text[start_index:]


def _escape_control_chars_in_strings(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                result.append(char)
                escaped = False
                continue
            if char == "\\":
                result.append(char)
                escaped = True
                continue
            if char == '"':
                result.append(char)
                in_string = False
                continue
            if char == "\n":
                result.append("\\n")
                continue
            if char == "\r":
                result.append("\\r")
                continue
            if char == "\t":
                result.append("\\t")
                continue
            result.append(char)
            continue
        result.append(char)
        if char == '"':
            in_string = True
    return "".join(result)


def _coerce_json_content(content: object) -> str:
    normalized = _normalize_message_content(content)
    candidate = _slice_first_balanced_json(_extract_json_candidate(normalized))
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        repaired = _escape_control_chars_in_strings(candidate)
        json.loads(repaired)
        return repaired


def assert_can_call_llm(prompt: str, settings: LLMSettings | None = None) -> LLMSettings:
    settings = settings or load_llm_settings()
    if not settings.is_cloud_enabled:
        raise LLMNotConfiguredError("LLM 增强解析未启用。")
    if not settings.base_url:
        raise LLMNotConfiguredError("当前后端没有可用 API 地址。")
    if not settings.api_key:
        raise LLMNotConfiguredError("尚未填写 API Key。")
    if not settings.model:
        raise LLMNotConfiguredError("尚未填写模型名称。")
    tokens = estimate_tokens(prompt)
    if tokens > settings.single_call_token_limit:
        raise LLMNotConfiguredError(
            f"估算 tokens {tokens} 超过单次上限 {settings.single_call_token_limit}。"
        )
    return settings


def parse_record_with_llm(payload: dict, *, settings: LLMSettings | None = None) -> dict:
    from study_app.ai.audit import audited_chat_completion_json, summarize_payload

    class AuditedPayload(dict):
        pass

    settings = settings or load_llm_settings()
    prompt = build_record_parse_prompt(payload, settings.enabled_features)
    content = audited_chat_completion_json(
        "record_parser",
        prompt,
        summarize_payload(payload),
        settings=settings,
    )
    result = AuditedPayload(json.loads(content))
    result.audit_id = content.audit_id
    return result


def build_record_parse_prompt(
    payload: dict,
    enabled_features: Collection[str] | None = None,
) -> str:
    features = set(LLM_FEATURES if enabled_features is None else enabled_features)
    problem_schema = {
        "title": "题号或简短标题",
        "statement": "题面或题面摘要",
        "correctness": 0.0,
        "error_cause": "错因，没有则为空字符串",
        "statement_source": "llm_text",
    }
    constraints = ["correctness 必须是 0 到 1"]
    if "error_classifier" in features:
        problem_schema["error_category"] = (
            "concept_forgetting|condition_misjudgment|calculation_error|"
            "modeling_error|boundary_omission|method_gap|time_management|none"
        )
    if "knowledge_mapping" in features:
        problem_schema["related_topics"] = ["知识点"]
        constraints.append("related_topics 要尽量具体，优先使用课程知识点而不是笼统学科名")
    if "difficulty_calibration" in features:
        problem_schema["difficulty_score"] = 0
        problem_schema["difficulty"] = "easy|medium|hard"
        constraints.append("difficulty_score 必须是 0 到 100")
    return (
        "你是学习记录结构化解析器。请只输出 JSON，不要输出解释。\n"
        "任务：根据用户的自然语言学习记录、错因、附件 OCR/PDF 文本，识别题目级作答证据。\n"
        "输出 JSON schema：\n"
        + json.dumps({"problems": [problem_schema]}, ensure_ascii=False, indent=2)
        + "\n约束："
        + "；".join(constraints)
        + "；不要输出 schema 以外的字段；"
        + "不要编造附件中不存在的具体题面，无法确定时写摘要。\n\n"
        "输入：\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )


def chat_completion_json(prompt: str, settings: LLMSettings, timeout_seconds: int | None = None) -> str:
    if settings.api_protocol == "anthropic":
        return anthropic_messages_json(prompt, settings, timeout_seconds=timeout_seconds)
    return openai_chat_completion_json(prompt, settings, timeout_seconds=timeout_seconds)


def openai_chat_completion_json(
    prompt: str,
    settings: LLMSettings,
    timeout_seconds: int | None = None,
) -> str:
    url = f"{settings.base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    payload = _post_json(
        url,
        body,
        {
            "Authorization": f"Bearer {settings.api_key}",
        },
        timeout_seconds=timeout_seconds,
    )
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMNotConfiguredError("LLM 返回格式异常，未找到 message.content。") from error
    return _coerce_or_raise(content)


def anthropic_messages_json(
    prompt: str,
    settings: LLMSettings,
    timeout_seconds: int | None = None,
) -> str:
    url = f"{settings.base_url.rstrip('/')}/v1/messages"
    body = {
        "model": settings.model,
        "system": "You output strict JSON only. Do not include markdown.",
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "max_tokens": min(8192, max(2048, settings.single_call_token_limit)),
        "temperature": 0.1,
    }
    payload = _post_json(
        url,
        body,
        {
            "x-api-key": settings.api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout_seconds=timeout_seconds,
    )
    try:
        content = payload["content"]
    except (KeyError, TypeError) as error:
        raise LLMNotConfiguredError("LLM 返回格式异常，未找到 Anthropic content。") from error
    return _coerce_or_raise(content)


def _post_json(
    url: str,
    body: dict,
    extra_headers: dict[str, str],
    timeout_seconds: int | None = None,
) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        **extra_headers,
    }
    timeout = int(timeout_seconds or LLM_HTTP_TIMEOUT_SECONDS)
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise LLMNotConfiguredError(f"LLM 调用失败：HTTP {error.code} {detail[:500]}") from error
    except (TimeoutError, socket.timeout) as error:
        raise LLMNotConfiguredError(f"LLM 调用超过 {timeout} 秒未返回。") from error
    except urllib.error.URLError as error:
        raise LLMNotConfiguredError(f"LLM 网络连接失败：{error.reason}") from error

    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise LLMNotConfiguredError(f"LLM 返回内容不是合法 JSON：{raw[:500]}") from error


def _coerce_or_raise(content: object) -> str:
    try:
        return _coerce_json_content(content)
    except json.JSONDecodeError as error:
        preview = _normalize_message_content(content)[:500]
        raise LLMNotConfiguredError(f"LLM message.content 不是合法 JSON：{preview}") from error
