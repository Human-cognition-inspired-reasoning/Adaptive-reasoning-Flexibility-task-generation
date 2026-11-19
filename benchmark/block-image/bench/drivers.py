# -*- coding: utf-8 -*-
"""Native API adapters; each episode owns its API state.

ask(system, visible_messages, state) -> (visible_text, call_metadata)
Only the latest user message is read from visible_messages. Earlier assistant
turns are replayed from the provider's original blocks, including signatures,
encrypted reasoning and phase. Never rebuild API history from visible text.

Reasoning settings are model-specific, not comparable token budgets:
  claude-opus-5-5:              adaptive thinking, effort medium
  claude-sonnet-5-5:            adaptive thinking, effort medium
  claude-haiku-5-5:             adaptive thinking, effort medium
  claude-haiku-4-5-20251001:     manual thinking, budget 4000 (파일럿 재현용)
  gpt-6.1-sol:                  effort medium, context all_turns

API references checked 2026-10-07:
https://platform.claude.com/docs/en/models/opus-5-5/migration-guide
https://platform.claude.com/docs/en/build-with-claude/extended-thinking
https://developers.openai.com/api/docs/models/gpt-6.1-sol
https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls
Opaque signatures/encrypted items are preserved; they are not readable thoughts.
"""
import base64
from copy import deepcopy
from functools import lru_cache
import os
from pathlib import Path
import time


@lru_cache(maxsize=512)
def _b64(path):
    """같은 그림이 여러 턴·여러 에피소드에 다시 실린다. 한 번만 읽는다."""
    return base64.b64encode(Path(path).read_bytes()).decode()


def user_message(msg, provider):
    """러너가 낸 공급자 중립 메시지를 그 공급자의 블록으로 옮긴다.

    content 가 문자열이면 손대지 않는다 — 버튼전구·물길·물길-꽃조합이 그렇게 낸다.
    리스트면 {"type":"text"} 와 {"type":"image","path":...} 두 가지만 쓴다.

    옮긴 것을 history 에 넣는다. 다음 턴에 원본을 다시 옮기면 그림이 두 번 인코딩돼
    같은 대화가 공급자마다 다른 바이트로 남는다."""
    content = msg.get("content")
    if isinstance(content, str):
        return deepcopy(msg)
    out = []
    for part in content:
        if part["type"] == "text":
            out.append({"type": "text", "text": part["text"]} if provider == "anthropic"
                       else {"type": "input_text", "text": part["text"]})
        elif part["type"] == "image":
            data = _b64(part["path"])
            # 형식은 확장자로(.jpg · .jpeg 면 JPEG, 나머지는 예전처럼 PNG). 블록 투사도는 제출마다 새 사진을 대화에 쌓아
            # 요청이 가볍도록 JPEG 를 쓴다(2026-11-12. 한도는 128 MB · 그림 600 장, vision 문서).
            mt = "image/jpeg" if str(part["path"]).lower().endswith((".jpg", ".jpeg")) else "image/png"
            out.append({"type": "image",
                        "source": {"type": "base64", "media_type": mt,
                                   "data": data}}
                       if provider == "anthropic" else
                       {"type": "input_image", "detail": "auto",
                        "image_url": f"data:{mt};base64,{data}"})
        else:
            raise ValueError(f"알 수 없는 조각 {part['type']}")
    return {"role": msg["role"], "content": out}


# Haiku 4.5는 adaptive thinking을 거부하고(400) 이전 턴의 thinking 블록도 제거한다.
# Opus 5.5는 반대로 type:"enabled"를 거부한다. 둘은 같은 추론 조건에 놓을 수 없다.
# Sonnet 5.5는 Opus와 같은 adaptive 계열이라, 세대가 아니라 크기만 다른 비교가 된다.
PROFILES = {
    "claude-opus-5-5": ("anthropic", "adaptive", "medium"),
    "claude-opus-5": ("anthropic", "adaptive", "medium"),
    "claude-sonnet-5-5": ("anthropic", "adaptive", "medium"),
    "claude-sonnet-5": ("anthropic", "adaptive", "medium"),
    "claude-haiku-5-5": ("anthropic", "adaptive", "medium"),
    "gpt-6.1-sol": ("openai", "effort", "medium"),
    "gpt-5.6-sol": ("openai", "effort", "medium"),
    "gpt-5.6-terra": ("openai", "effort", "medium"),
    "gpt-5.6-luna": ("openai", "effort", "medium"),
    "claude-haiku-4-5-20251001": ("anthropic", "budget", "4000"),
}
# 로그 파일 이름이 겹치면 안 되므로 라벨을 모델마다 못박는다.
# "haiku"를 부분문자열로 뽑으면 4.5와 5.5가 같은 파일에 섞인다.
LABELS = {
    "claude-opus-5-5": "opus",
    "claude-opus-5": "opus5",
    "claude-sonnet-5-5": "sonnet",
    "claude-sonnet-5": "sonnet5",
    "claude-haiku-5-5": "haiku",
    "gpt-6.1-sol": "gpt61sol",
    "gpt-5.6-sol": "sol",
    "gpt-5.6-terra": "terra",
    "gpt-5.6-luna": "luna",
    "claude-haiku-4-5-20251001": "haiku45",
}
assert set(LABELS) == set(PROFILES) and len(set(LABELS.values())) == len(LABELS)
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def resolve_config(cfg):
    """Validate before opening a client; never silently enlarge budgets."""
    c = dict(cfg)
    model = c["model_id"]
    if model not in PROFILES:
        raise ValueError("지원하는 정확한 모델 ID: " + ", ".join(PROFILES))
    provider, mode, default = PROFILES[model]
    if c.get("provider") not in (None, provider):
        raise ValueError(f"{model}의 provider는 {provider}입니다.")
    c["provider"], c["reasoning_mode"] = provider, mode
    value = str(c.get("reasoning") or "on").strip().lower()
    value = default if value == "on" else value
    c.setdefault("max_tokens", 8000)
    c.setdefault("retries", 5)
    c.setdefault("timeout", 180.0)
    c.setdefault("chain", True)
    if c["max_tokens"] <= 0 or c["retries"] < 0 or c["timeout"] <= 0:
        raise ValueError("max_tokens와 timeout은 양수, retries는 0 이상이어야 합니다.")
    if c.get("temperature") is not None:
        raise ValueError("이 추론 실험에서는 temperature를 지정하지 않습니다.")
    if mode == "budget":
        if not value.isdigit() or int(value) < 1024:
            raise ValueError("Haiku 4.5의 --reasoning은 1024 이상의 토큰 수입니다.")
        if int(value) >= c["max_tokens"]:
            raise ValueError("Haiku thinking budget은 --max-tokens보다 작아야 합니다.")
    elif value not in EFFORTS:
        raise ValueError(f"{model}의 --reasoning: {', '.join(EFFORTS)} (off 불가)")
    c["reasoning"] = value
    c["chain"] = bool(c["chain"]) if provider == "openai" else False
    return c


def settings(cfg):
    """Effective settings; --dry-run needs neither SDKs nor keys."""
    c = resolve_config(cfg)
    common = dict(provider=c["provider"], model_id=c["model_id"],
                  max_tokens=c["max_tokens"], reasoning=c["reasoning"],
                  reasoning_mode=c["reasoning_mode"], temperature=None,
                  retries=c["retries"], timeout=c["timeout"], tools=[])
    if c["provider"] == "anthropic":
        common.update(endpoint="messages", turn_chaining="native_content_history",
                      thinking_blocks_replayed=True,
                      reasoning_preservation=("model supports preserved thinking"
                          if c["reasoning_mode"] == "adaptive"
                          else "Haiku service strips previous-turn thinking"))
    else:
        common.update(endpoint="responses", reasoning_context_requested="all_turns",
                      turn_chaining=("previous_response_id" if c["chain"]
                                     else "native_output_history"),
                      store=c["chain"])
    return common


def as_dict(response):
    if isinstance(response, dict):
        return deepcopy(response)
    return response.model_dump(mode="json", exclude_unset=True)


class CallError(Exception):
    """An API/transport failure, never a puzzle failure."""
    def __init__(self, meta):
        self.meta = meta
        super().__init__(meta["error"]["message"])



QUOTA_MARKERS = ("insufficient_quota", "credit_balance_exhausted",
                 "billing_hard_limit_reached", "credit balance is too low",
                 "no credits remaining")


def permanent_quota_error(exc):
    """429 중 크레딧·한도 소진처럼 재시도로 풀리지 않는 것을 가려낸다."""
    parts = [str(getattr(exc, a, "") or "") for a in ("code", "type", "message")]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        parts += [str(body.get(k) or "") for k in ("code", "type", "message")]
    blob = " ".join(parts).lower()
    return any(m in blob for m in QUOTA_MARKERS)


class Driver:
    name = ""

    def __init__(self, cfg, client=None, error_types=(), sleep=time.sleep):
        self.cfg = resolve_config(cfg)
        self.sleep = sleep
        self.connection_errors = (ConnectionError, TimeoutError)
        if client is None:
            try:
                sdk = __import__(self.name)
            except ImportError as e:
                raise ValueError(f"{self.name} SDK가 없습니다. requirements-api.txt를 설치하세요.") from e
            key_name = "ANTHROPIC_API_KEY" if self.name == "anthropic" else "OPENAI_API_KEY"
            if not os.environ.get(key_name):
                raise ValueError(f"{key_name} 가 없습니다. export {key_name}=... 로 주세요.")
            factory = sdk.Anthropic if self.name == "anthropic" else sdk.OpenAI
            client = factory(max_retries=0, timeout=self.cfg["timeout"])
            error_types = (sdk.APIError,)
            self.connection_errors += (sdk.APIConnectionError, sdk.APITimeoutError)
            self.sdk_version = sdk.__version__
        else:
            self.sdk_version = "injected-client"
        self.client, self.error_types = client, error_types

    def meta(self):
        return dict(settings(self.cfg), sdk=self.sdk_version)

    def _request(self, request, call):
        start, history = time.monotonic(), []
        for attempt in range(self.cfg["retries"] + 1):
            t0 = time.monotonic()
            try:
                response = call(**deepcopy(request))
            except self.error_types as e:
                status = getattr(e, "status_code", None)
                retryable = (status in (408, 409, 429) or
                             (status is not None and status >= 500) or
                             isinstance(e, self.connection_errors))
                if retryable and permanent_quota_error(e):
                    retryable = False      # 크레딧 소진은 429지만 기다려도 안 풀린다
                error = dict(type=type(e).__name__, message=str(e),
                             status_code=status, request_id=getattr(e, "request_id", None),
                             retryable=retryable)
                history.append(dict(attempt=attempt + 1, error=error,
                                    secs=time.monotonic() - t0))
                if not retryable or attempt == self.cfg["retries"]:
                    raise CallError(dict(request=deepcopy(request), response=None,
                                         error=error, attempts=len(history),
                                         attempt_history=history,
                                         secs=time.monotonic() - start)) from e
                delay = min(2 ** (attempt + 1), 30)
                history[-1]["retry_delay"] = delay
                self.sleep(delay)
                continue
            raw = as_dict(response)
            history.append(dict(attempt=attempt + 1, secs=time.monotonic() - t0))
            return raw, dict(request=deepcopy(request), response=raw,
                             response_id=raw.get("id"),
                             request_id=getattr(response, "_request_id", None),
                             model_served=raw.get("model"), attempts=len(history),
                             attempt_history=history, secs=time.monotonic() - start)

    def close(self):
        self.client.close()


class AnthropicDriver(Driver):
    name = "anthropic"

    def ask(self, system, messages, state):
        c = self.cfg
        history = (deepcopy(state.get("history", []))
                   + [user_message(messages[-1], "anthropic")])
        # 대화가 에피소드 안에서 계속 길어진다. 최상위 cache_control 을 주면
        # 마지막 캐시 가능 블록에 중단점이 자동으로 찍히고 대화가 자라면 따라 옮겨간다.
        # 보내는 내용은 한 글자도 바뀌지 않으므로 결과에 영향이 없다.
        # (캐시가 걸리는지는 usage.cache_read 가 0 을 벗어나는지로 확인한다.
        #  프롬프트가 짧은 초반 턴은 최소 길이에 못 미쳐 캐시되지 않는다.)
        request = dict(model=c["model_id"], system=system, messages=history,
                       max_tokens=c["max_tokens"],
                       cache_control={"type": "ephemeral"})
        if c["reasoning_mode"] == "adaptive":
            request["thinking"] = {"type": "adaptive"}
            request["output_config"] = {"effort": c["reasoning"]}
        else:
            request["thinking"] = {"type": "enabled", "budget_tokens": int(c["reasoning"])}
        raw, meta = self._request(request, self.client.messages.create)
        blocks = raw.get("content", [])
        state["history"] = history + [{"role": "assistant", "content": deepcopy(blocks)}]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        u = raw.get("usage") or {}
        details = u.get("output_tokens_details") or {}
        meta.update(stop_reason=raw.get("stop_reason"),
                    complete=raw.get("stop_reason") == "end_turn",
                    usage=dict(input=u.get("input_tokens"), output=u.get("output_tokens"),
                               reasoning=details.get("thinking_tokens"),
                               cache_read=u.get("cache_read_input_tokens"),
                               cache_creation=u.get("cache_creation_input_tokens")))
        return text, meta


class OpenAIDriver(Driver):
    name = "openai"

    def ask(self, system, messages, state):
        c = self.cfg
        user = user_message(messages[-1], "openai")
        history = deepcopy(state.get("history", [])) + [user]
        prev = state.get("prev_id")
        request = dict(model=c["model_id"], instructions=system,
                       input=[user] if c["chain"] and prev else history,
                       max_output_tokens=c["max_tokens"], store=c["chain"],
                       reasoning={"effort": c["reasoning"], "context": "all_turns"})
        if c["chain"] and prev:
            request["previous_response_id"] = prev
        if not c["chain"]:
            request["include"] = ["reasoning.encrypted_content"]
        # Never silently switch a failed chain to a different protocol.
        raw, meta = self._request(request, self.client.responses.create)
        output = raw.get("output", [])
        state["history"] = history + deepcopy(output)
        if c["chain"]:
            state["prev_id"] = raw.get("id")
        text = "".join(b.get("text", "") for item in output
                       if item.get("type") == "message"
                       for b in item.get("content", []) if b.get("type") == "output_text")
        u = raw.get("usage") or {}
        meta.update(stop_reason=raw.get("status"),
                    complete=raw.get("status") == "completed",
                    incomplete_details=raw.get("incomplete_details"),
                    reasoning_context_effective=(raw.get("reasoning") or {}).get("context"),
                    usage=dict(input=u.get("input_tokens"), output=u.get("output_tokens"),
                               reasoning=(u.get("output_tokens_details") or {}).get("reasoning_tokens"),
                               cache_read=(u.get("input_tokens_details") or {}).get("cached_tokens")))
        return text, meta


def pick(cfg, **kwargs):
    cfg = resolve_config(cfg)
    return {"anthropic": AnthropicDriver, "openai": OpenAIDriver}[cfg["provider"]](cfg, **kwargs)
