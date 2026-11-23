"""OpenAI and Anthropic chat backends used by maze.loop_session: one request per session, sessions in parallel threads,
transient errors retried and counted, per-call wall time (api_seconds), refusals and OpenAI invalid_prompt blocks
returned as stop_reason "refusal"."""
import time


class OpenAIChat:
    """OpenAI chat completions, one request per session, sessions in parallel threads. No fallback models: the
    request names one model and the served model is recorded. Sampling: temperature as configured, max_completion_tokens
    = the stage's output cap (reasoning tokens count toward it; finish_reason 'length' = cut off). Reasoning effort is
    left at the API default and recorded as such."""

    def __init__(self, model_id: str, temperature: float, max_new_tokens: int, effort: str | None = None):
        import openai
        self.client, self.model_id = openai.OpenAI(), model_id
        self.temperature, self.max_new_tokens, self.effort = temperature, max_new_tokens, effort
        self.last_meta = {}

    def _one(self, msgs):
        import openai
        # api_seconds (2026-10-23): wall time of this call, transient-error retries and their waits included (retries counted)
        t0 = time.monotonic()
        kw = ({} if self.temperature is None else {"temperature": self.temperature}) | ({"reasoning_effort": self.effort} if self.effort else {})
        for attempt in range(6):   # transient errors only (rate limit, connection, server); never the output limit
            try:
                r = self.client.chat.completions.create(model=self.model_id, messages=msgs,
                                                        max_completion_tokens=self.max_new_tokens, **kw)
                break
            except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError, openai.APITimeoutError):
                if attempt == 5:
                    raise
                time.sleep(2 ** attempt * 5)
            except openai.BadRequestError as e:
                if getattr(e, "code", None) == "invalid_prompt" or "invalid_prompt" in str(e):
                    # decision 2026-10-26: a request blocked as "invalid_prompt" (HTTP 400, flagged before the model answers)
                    # is handled like an Anthropic refusal: no answer, recorded as stop_reason "refusal" with
                    # refusal_kind "invalid_prompt" so maze.loop_session re-asks the same attempt; nothing is billed
                    return {"text": "", "finish_reason": "invalid_prompt", "stop_reason": "refusal", "refusal_kind": "invalid_prompt",
                            "served_model": None, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                            "cached_tokens": 0, "error": str(e)[:300], "api_seconds": round(time.monotonic() - t0, 3),
                            "retries": attempt, "reasoning_tokens_reported": True}
                # The API sometimes rejects a request that hit the output cap instead of returning finish_reason
                # "length". Recorded as a truncation (empty answer = parse failure), no retry; usage is not reported,
                # so the billed output is taken as the full cap (upper bound) and flagged.
                if "max_tokens or model output limit was reached" not in str(e):
                    raise
                return {"text": "", "finish_reason": "length_error", "served_model": self.model_id, "prompt_tokens": 0,
                        "completion_tokens": self.max_new_tokens, "reasoning_tokens": self.max_new_tokens,
                        "cached_tokens": 0, "usage_estimated": True, "error": str(e)[:200],
                        "api_seconds": round(time.monotonic() - t0, 3), "retries": attempt, "reasoning_tokens_reported": False}
        u = r.usage
        det = getattr(u, "completion_tokens_details", None)
        pdet = getattr(u, "prompt_tokens_details", None)
        msg = r.choices[0].message
        out = {"text": msg.content or "", "finish_reason": r.choices[0].finish_reason,
               "served_model": r.model, "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
               "reasoning_tokens": getattr(det, "reasoning_tokens", None) or 0,
               "cached_tokens": getattr(pdet, "cached_tokens", None) or 0,
               "api_seconds": round(time.monotonic() - t0, 3), "retries": attempt, "reasoning_tokens_reported": True}
        # v30 refusal handling for OpenAI (2026-10-23): a refusal message or a content-filter stop is recorded as
        # stop_reason "refusal", the signal maze.loop_session already uses for Anthropic
        if getattr(msg, "refusal", None) or out["finish_reason"] == "content_filter":
            out["stop_reason"], out["refusal_text"] = "refusal", (getattr(msg, "refusal", None) or "")[:500]
        return out

    def __call__(self, prompts, metas, step):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(len(prompts)) as ex:
            res = list(ex.map(self._one, prompts))
        self.last_meta = {"seed": None, "api": [x | {"effort": self.effort or "api default"} for x in res]}
        return [x["text"] for x in res], [x["completion_tokens"] for x in res], [x["prompt_tokens"] for x in res]


class AnthropicChat:
    """Anthropic Messages API, one request per session, sessions in parallel threads. No temperature (Claude 5 models
    reject it), thinking and effort left at the API defaults (adaptive thinking on, effort high on claude-opus-5 and
    claude-sonnet-5), no server-side fallbacks. Truncation = stop_reason "max_tokens" (recorded as finish_reason
    "length"). An empty assistant turn (an earlier unparsable answer) is dropped because the API rejects empty content;
    the neighbouring user turns are then combined by the API, so the model sees the same information."""

    def __init__(self, model_id: str, max_new_tokens: int, effort: str | None = None):
        import anthropic
        # retries are done here, not inside the SDK, so that each call records how many it needed (2026-10-26; the SDK
        # default was max_retries=5, the loop below also allows 5)
        self.client, self.model_id, self.max_new_tokens = anthropic.Anthropic(max_retries=0), model_id, max_new_tokens
        self.effort = effort   # output_config.effort when given (decision 2026-10-26: medium for the v32 100-session run); else API default
        self.last_meta = {}

    def _one(self, msgs):
        import anthropic
        # api_seconds (2026-10-23): wall time of this call, transient-error retries and their waits included (retries counted)
        t0 = time.monotonic()
        msgs = [m for m in msgs if not (m["role"] == "assistant" and m["content"] == "")]
        kw = {"output_config": {"effort": self.effort}} if self.effort else {}
        for attempt in range(6):   # transient errors only (rate limit, connection, timeout, 5xx incl. overloaded)
            try:
                if self.max_new_tokens > 16000:
                    # large output caps: the SDK requires streaming to avoid HTTP timeouts (v24, v25); <= 16000 is unchanged (v22)
                    with self.client.messages.stream(model=self.model_id, max_tokens=self.max_new_tokens, messages=msgs, **kw) as stream:
                        r = stream.get_final_message()
                else:
                    r = self.client.messages.create(model=self.model_id, max_tokens=self.max_new_tokens, messages=msgs, **kw)
                break
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError, __import__("httpx").TransportError) as e:   # httpx: a read timeout inside a stream is not wrapped by the SDK (2026-10-27)
                if attempt == 5:
                    raise
                time.sleep(min(2 ** attempt * 2, 60))
            except anthropic.APIStatusError as e:
                # an error event inside a stream (e.g. overloaded_error) can arrive with a non-5xx status; retry it by type
                # (2026-10-28), with longer waits than other transient errors
                transient = e.status_code >= 500 or any(t in str(e) for t in ("overloaded_error", "api_error"))
                if not transient or attempt == 5:
                    raise
                time.sleep(min(2 ** attempt * 10, 300))
        text = "".join(b.text for b in r.content if b.type == "text")
        u = r.usage
        # the Anthropic usage object reports no separate thinking-token count (output_tokens includes thinking); the
        # characters of returned thinking blocks are kept as an auxiliary measure
        return {"text": text, "finish_reason": "length" if r.stop_reason == "max_tokens" else r.stop_reason,
                "stop_reason": r.stop_reason, "served_model": r.model, "prompt_tokens": u.input_tokens,
                "completion_tokens": u.output_tokens, "reasoning_tokens": None, "reasoning_tokens_reported": False,
                "thinking_chars": sum(len(getattr(b, "thinking", "") or "") for b in r.content if b.type == "thinking"),
                "cached_tokens": getattr(u, "cache_read_input_tokens", 0) or 0, "request_id": getattr(r, "_request_id", None),
                "api_seconds": round(time.monotonic() - t0, 3), "retries": attempt}

    def __call__(self, prompts, metas, step):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(len(prompts)) as ex:
            res = list(ex.map(self._one, prompts))
        self.last_meta = {"seed": None, "api": [x | {"effort": self.effort or "api default"} for x in res]}
        return [x["text"] for x in res], [x["completion_tokens"] for x in res], [x["prompt_tokens"] for x in res]
