"""Model calls for judges and LLM subjects: any provider, any model, by config.

A judge (configs/judge_panel.toml) or an LLM subject (project.yaml `subject:`)
names a `provider`. A provider is one of the built-ins below or one defined in
config, and each provider speaks one of four API styles. Adding a model, or a
whole new vendor, is a config change, not a code change (docs/CHOOSING_MODELS.md):

  openai_responses         OpenAI and xAI (POST /responses)
  anthropic_messages       Anthropic (POST /messages)
  google_generate_content  Google Gemini (POST /models/{model}:generateContent)
  openai_chat_completions  any OpenAI-compatible server: DeepSeek, Mistral,
                           OpenRouter, Together, Fireworks, Azure-style gateways,
                           and local open-weight servers (vLLM, Ollama, llama.cpp)

Outcomes are deliberately distinguished:
- transport_failure: timeout/network failure after retries;
- provider_failure: HTTP/API failure after retries;
- model_output_failure: successful API response with no usable model text;
- ok: successful API response with non-empty model text.

No failure is silently converted into a clinical model answer, and no fallback to
a different model is ever requested: the model evaluated is the model configured.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

API_STYLES = (
    "openai_responses", "anthropic_messages", "google_generate_content", "openai_chat_completions",
)

BUILTIN_PROVIDERS: dict[str, dict] = {
    "openai": {"api": "openai_responses", "base_url": "https://api.openai.com/v1",
               "api_key_env": "OPENAI_API_KEY", "vendor": "openai"},
    "xai": {"api": "openai_responses", "base_url": "https://api.x.ai/v1",
            "api_key_env": "XAI_API_KEY", "vendor": "xai"},
    "anthropic": {"api": "anthropic_messages", "base_url": "https://api.anthropic.com/v1",
                  "api_key_env": "ANTHROPIC_API_KEY", "vendor": "anthropic"},
    "google": {"api": "google_generate_content",
               "base_url": "https://generativelanguage.googleapis.com/v1beta",
               "api_key_env": "GOOGLE_API_KEY", "vendor": "google"},
}
# Backward-compatible view used by older callers.
KEY_NAMES = {name: spec["api_key_env"] for name, spec in BUILTIN_PROVIDERS.items()}

# Reasoning-effort values each API style accepts. "provider_default" sends nothing,
# so the provider's own default applies; that default can differ by model (for
# example Claude Opus 5.5 defaults to medium), so a frozen study should set it.
EFFORTS_BY_API = {
    "openai_responses": {"provider_default", "minimal", "low", "medium", "high", "xhigh"},
    "anthropic_messages": {"provider_default", "low", "medium", "high", "xhigh", "max"},
    "google_generate_content": {"provider_default", "low", "medium", "high"},
    "openai_chat_completions": {"provider_default", "minimal", "low", "medium", "high"},
}
EFFORTS = set().union(*EFFORTS_BY_API.values())
RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 529}
COMPLETE_FINISH = {"end_turn", "stop_sequence", "STOP", "completed", "stop", ""}

class TransportError(RuntimeError):
    pass


class ProviderConfigError(ValueError):
    pass


class MissingKeyError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------------
# Provider registry
# --------------------------------------------------------------------------
def provider_registry(cfg: dict | None = None) -> dict[str, dict]:
    """Built-ins merged with the panel's `providers:` block.

    A panel entry with a built-in name overrides individual fields (for example a
    different base_url for a regional or proxied endpoint); a new name defines a
    new provider and must give `api` and `base_url`. An empty `api_key_env` means
    the provider needs no key (e.g. a local server).
    """
    registry = {name: dict(spec) for name, spec in BUILTIN_PROVIDERS.items()}
    for name, spec in ((cfg or {}).get("providers") or {}).items():
        spec = {k: (None if v == "" and k in ("api_key_env", "effort_param") else v)
                for k, v in (spec or {}).items()}
        merged = {**registry.get(name, {}), **spec}
        merged.setdefault("vendor", name)
        registry[name] = merged
    for name, spec in registry.items():
        if spec.get("api") not in API_STYLES:
            raise ProviderConfigError(
                f"provider {name!r}: api must be one of {list(API_STYLES)}, got {spec.get('api')!r}")
        if not str(spec.get("base_url", "")).startswith(("https://", "http://")):
            raise ProviderConfigError(f"provider {name!r}: base_url must be an http(s) URL")
        spec["base_url"] = str(spec["base_url"]).rstrip("/")
    return registry


def resolve_provider(provider: str, registry: dict[str, dict] | None = None) -> dict:
    registry = registry or provider_registry()
    if provider not in registry:
        raise ProviderConfigError(
            f"unknown provider {provider!r}; built-ins are {sorted(BUILTIN_PROVIDERS)} and "
            "others can be defined under `providers:` in the model panel")
    return registry[provider]


def entry_vendor(entry: dict, registry: dict[str, dict]) -> str:
    """Who built the model, as opposed to whose API served it. Used for the
    same-developer judge audit and for target-panel diversity, so a model reached
    through an aggregator is still attributed to its developer."""
    return str(entry.get("vendor") or resolve_provider(entry["provider"], registry).get("vendor")
               or entry["provider"])


def load_keys(path: str | Path | None = None, registry: dict[str, dict] | None = None) -> dict[str, str]:
    """Keys from environment variables, then from a git-ignored keys file if one
    exists (`NAME_API_KEY = value` lines; the file wins where both define a key).
    A missing file is not an error: a missing key fails only when it is needed."""
    names = {spec.get("api_key_env") for spec in (registry or provider_registry()).values()}
    names = {n for n in names if n} | set(KEY_NAMES.values())
    keys = {name: os.environ[name] for name in names if os.environ.get(name)}
    resolved = Path(path) if path else None
    if resolved is None and os.environ.get("MEDROBUST_KEYS_PATH"):
        resolved = Path(os.environ["MEDROBUST_KEYS_PATH"])
    if resolved is not None and resolved.exists():
        txt = resolved.read_text(encoding="utf-8")
        for m in re.finditer(r"^([A-Z0-9_]+_(?:API_)?KEY)\s*=\s*(\S+)", txt, re.M):
            keys[m.group(1)] = m.group(2).strip()
    return keys


def require_key(provider: str, keys: dict[str, str], registry: dict[str, dict] | None = None) -> str:
    spec = resolve_provider(provider, registry)
    name = spec.get("api_key_env")
    if not name:            # e.g. a local server that needs no key
        return ""
    if not keys.get(name):
        raise MissingKeyError(
            f"missing {name}: set it as an environment variable, or in the git-ignored keys file "
            "(API_KEYS.local.md, or the path in MEDROBUST_KEYS_PATH)")
    return keys[name]


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
def _http_json(
    url: str,
    headers: dict[str, str],
    payload: dict | None,
    timeout_seconds: int,
) -> tuple[int, dict]:
    """POST `payload` as JSON (or GET when payload is None) and return (status, body)."""
    req = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("provider returned a non-object JSON body")
            return int(response.status), data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"error": raw[:1000]}
        return int(exc.code), data if isinstance(data, dict) else {"error": data}
    except Exception as exc:
        raise TransportError(repr(exc)) from exc


# --------------------------------------------------------------------------
# API styles: request shape, text extraction, finish reason, usage
# --------------------------------------------------------------------------
def _responses_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return str(data["output_text"])
    parts = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                parts.append(str(block.get("text", "")))
    return "".join(parts)


def _anthropic_text(data: dict) -> str:
    return "".join(
        str(block.get("text", ""))
        for block in data.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _google_text(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except Exception:
        return ""
    # Gemini marks its reasoning summary parts with thought=true; only the answer counts.
    return "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and not p.get("thought"))


def _chat_text(data: dict) -> str:
    try:
        content = data["choices"][0]["message"].get("content")
    except Exception:
        return ""
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


def _finish_reason(api: str, data: dict) -> str:
    """Why generation stopped, in the provider's own vocabulary.

    Recorded because a 200 with text is not necessarily a complete answer: a
    response cut off at the output cap, or declined, still arrives as text.
    """
    if api in BUILTIN_PROVIDERS:          # accept a provider name from older callers
        api = BUILTIN_PROVIDERS[api]["api"]
    try:
        if api == "anthropic_messages":
            return str(data.get("stop_reason") or "")
        if api == "google_generate_content":
            return str(data["candidates"][0].get("finishReason") or "")
        if api == "openai_chat_completions":
            return str(data["choices"][0].get("finish_reason") or "")
        status = str(data.get("status") or "")
        detail = (data.get("incomplete_details") or {}).get("reason")
        return f"{status}:{detail}" if detail else status
    except Exception:
        return ""


def _usage(api: str, data: dict) -> dict:
    if api == "google_generate_content":
        return data.get("usageMetadata", {}) or {}
    return data.get("usage", {}) or {}


def _refusal_detail(api: str, data: dict) -> dict:
    if api == "anthropic_messages" and data.get("stop_reason") == "refusal":
        return data.get("stop_details") or {"type": "refusal"}
    return {}


def _build_request(
    api: str, base_url: str, model: str, system: str, user: str, key: str,
    effort: str, max_output_tokens: int, spec: dict,
) -> tuple[str, dict[str, str], dict, Callable[[dict], str]]:
    allowed = EFFORTS_BY_API[api]
    if effort not in allowed:
        raise ValueError(
            f"reasoning_effort {effort!r} is not supported by the {api} API style "
            f"(allowed: {sorted(allowed)})")
    extra = dict(spec.get("extra_headers") or {})

    if api == "openai_responses":
        payload = {
            "model": model,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_output_tokens": int(max_output_tokens),
        }
        if effort != "provider_default":
            payload["reasoning"] = {"effort": effort}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **extra}
        return f"{base_url}/responses", headers, payload, _responses_text

    if api == "anthropic_messages":
        payload = {
            "model": model,
            "max_tokens": int(max_output_tokens),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if effort != "provider_default":
            # Adaptive thinking is the only thinking mode current Claude models
            # accept; depth is controlled by effort. No fallbacks are requested:
            # a refusal is recorded as the configured model's outcome.
            payload["thinking"] = {"type": "adaptive"}
            payload["output_config"] = {"effort": effort}
        headers = {
            "x-api-key": key,
            "anthropic-version": str(spec.get("anthropic_version", "2023-06-01")),
            "Content-Type": "application/json",
            **extra,
        }
        return f"{base_url}/messages", headers, payload, _anthropic_text

    if api == "google_generate_content":
        # The key travels in a header, never in the URL, so it cannot reach a log
        # line, an exception message or the recorded endpoint.
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": int(max_output_tokens)},
        }
        if effort != "provider_default":
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": effort}
        headers = {"x-goog-api-key": key, "Content-Type": "application/json", **extra}
        return f"{base_url}/models/{model}:generateContent", headers, payload, _google_text

    # openai_chat_completions
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        str(spec.get("max_tokens_field", "max_tokens")): int(max_output_tokens),
    }
    effort_param = spec.get("effort_param", "reasoning_effort")
    if effort != "provider_default":
        if not effort_param:
            raise ValueError(
                f"provider {spec.get('name', '?')!r} declares no effort_param, so reasoning_effort "
                "must be provider_default")
        payload[str(effort_param)] = effort
    headers = {"Content-Type": "application/json", **extra}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return f"{base_url}/chat/completions", headers, payload, _chat_text


def _request_for_provider(
    provider: str,
    model: str,
    system: str,
    user: str,
    key: str,
    reasoning_effort: str,
    max_output_tokens: int,
    registry: dict[str, dict] | None = None,
):
    spec = {**resolve_provider(provider, registry), "name": provider}
    effort = str(reasoning_effort or "provider_default").lower()
    return _build_request(spec["api"], spec["base_url"], model, system, user, key, effort,
                          max_output_tokens, spec)


def model_matches(configured: str, resolved: str | None) -> bool:
    """Providers report dated or prefixed IDs (`gpt-x-2026-09-01`, `models/gemini-x`).
    Anything else, e.g. a silently substituted model, is flagged."""
    if not resolved:
        return True
    c, r = configured.lower().removeprefix("models/"), str(resolved).lower().removeprefix("models/")
    return r == c or r.startswith(c + "-") or r.startswith(c + "@") or r.startswith(c + "_")


# --------------------------------------------------------------------------
# The one call path every study script uses
# --------------------------------------------------------------------------
def call_provider(
    provider: str,
    model: str,
    system: str,
    user: str,
    keys: dict[str, str],
    *,
    reasoning_effort: str = "provider_default",
    max_output_tokens: int = 3500,
    max_attempts: int = 4,
    retry_backoff_seconds: float = 1.0,
    timeout_seconds: int = 180,
    sleep_fn=time.sleep,
    registry: dict[str, dict] | None = None,
) -> tuple[str, str, dict]:
    """Return (text, status, metadata) without throwing for ordinary API failures."""
    spec = resolve_provider(provider, registry)
    api = spec["api"]
    key = require_key(provider, keys, registry)
    endpoint, headers, payload, parser = _request_for_provider(
        provider, model, system, user, key, reasoning_effort, max_output_tokens, registry
    )
    request_hash = canonical_hash({
        "endpoint": endpoint,
        "provider": provider,
        "api": api,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": int(max_output_tokens),
        "payload": payload,
    })
    started = utcnow()

    def meta(attempt: int, http_status, data: dict, **extra) -> dict:
        resolved = data.get("model") or data.get("modelVersion") if data else None
        return {
            "attempts": attempt,
            "http_status": http_status,
            "provider": provider,
            "api": api,
            "configured_model": model,
            "resolved_model": resolved,
            "resolved_model_matches_configured": model_matches(model, resolved),
            "endpoint": endpoint,
            "reasoning_effort": reasoning_effort,
            "max_output_tokens": int(max_output_tokens),
            "request_sha256": request_hash,
            "started_at_utc": started,
            "finished_at_utc": utcnow(),
            "usage": _usage(api, data or {}),
            **extra,
        }

    last_http: int | None = None
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            http_status, data = _http_json(endpoint, headers, payload, int(timeout_seconds))
            last_http = http_status
        except TransportError as exc:
            if attempt < max_attempts:
                sleep_fn(float(retry_backoff_seconds) * (2 ** (attempt - 1)))
                continue
            return "", "transport_failure", meta(
                attempt, None, {}, error_type="transport_failure", error_detail=str(exc)[:1000])

        if 200 <= http_status < 300:
            text = parser(data).strip()
            finish = _finish_reason(api, data)
            m = meta(attempt, http_status, data, error_type=None, error_detail="",
                     finish_reason=finish, output_complete=finish in COMPLETE_FINISH,
                     refusal=_refusal_detail(api, data))
            if not m["resolved_model"]:
                m["resolved_model"] = model
            if text:
                return text, "ok", m
            m["error_type"] = "model_output_failure"
            m["error_detail"] = (
                f"the model declined ({m['refusal']})" if m["refusal"]
                else "successful API response contained no usable model text")
            return "", "model_output_failure", m

        if http_status in RETRYABLE_HTTP and attempt < max_attempts:
            sleep_fn(float(retry_backoff_seconds) * (2 ** (attempt - 1)))
            continue
        return "", "provider_failure", meta(
            attempt, http_status, data, error_type="provider_failure",
            error_detail=json.dumps(data, ensure_ascii=False)[:1000])

    raise AssertionError(f"unreachable provider loop; last_http={last_http}")


def list_models(provider: str, keys: dict[str, str], registry: dict[str, dict] | None = None,
                timeout_seconds: int = 60) -> list[str]:
    """Model IDs the provider says this key can use. A listing call generates no
    tokens and is not billed by the providers built in here."""
    spec = resolve_provider(provider, registry)
    api, base = spec["api"], spec["base_url"]
    key = require_key(provider, keys, registry)
    extra = dict(spec.get("extra_headers") or {})
    if api == "anthropic_messages":
        headers = {"x-api-key": key, "anthropic-version": str(spec.get("anthropic_version", "2023-06-01"))}
    elif api == "google_generate_content":
        headers = {"x-goog-api-key": key}
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    ids: list[str] = []
    url = f"{base}/models" + ("?limit=1000" if api == "anthropic_messages" else "")
    status, data = _http_json(url, {**headers, **extra}, None, timeout_seconds)
    if not 200 <= status < 300:
        raise RuntimeError(f"{provider}: model listing failed with HTTP {status}: {str(data)[:300]}")
    for item in data.get("data") or data.get("models") or []:
        if isinstance(item, dict):
            ids.append(str(item.get("id") or item.get("name", "")).removeprefix("models/"))
    return sorted(i for i in ids if i)
