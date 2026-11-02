import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Dict, Generator, Iterable, Union

from openai import OpenAI

from ai_models.chat_tool import ChatTool, ChatToolResult
from ai_models.model_name import ModelName

logger = logging.getLogger(__name__)

# OpenRouter-specific model name mappings
# OpenRouter requires 'google/' prefix for Gemini models
OPENROUTER_MODEL_MAP: Dict[ModelName, str] = {
    # Map generic Gemini names to OpenRouter format
    ModelName.Gemini25Flash: "google/gemini-2.5-flash",
    ModelName.Gemini31FlashLite: "google/gemini-3.1-flash-lite",
    ModelName.Gemini35Flash: "google/gemini-3.5-flash",
    # Anthropic Models
    ModelName.Sonnet46: "anthropic/claude-sonnet-4.6",
    # OpenRouter Auto Router for automatic model selection
    ModelName.Auto: "openrouter/auto",
    # Fastest model with :nitro variant for high-speed inference
    # Uses Gemini 3.1 Flash-Lite with nitro variant (throughput-sorted routing)
    # See: https://openrouter.ai/docs/guides/routing/model-variants/nitro
    ModelName.Fastest: "google/gemini-3.1-flash-lite:nitro",
    # OpenAI models
    ModelName.Gpt4Mini: "openai/gpt-4.1-mini",
}


# openrouter/auto price band (low|medium|high|xhigh|max). With no setting OpenRouter routes "roughly low";
# pinned so the band can't drift. See: https://openrouter.ai/docs/features/model-routing
AUTO_ROUTER_COST_TIER = "low"


def get_openrouter_model_name(model_name: ModelName) -> str:
    """Convert generic model name to OpenRouter-specific format.

    Args:
        model_name: Generic model name (e.g., 'gemini-2.5-flash')

    Returns:
        OpenRouter-specific model name (e.g., 'google/gemini-2.5-flash')
        Falls back to original name if no mapping exists.
    """
    return OPENROUTER_MODEL_MAP.get(model_name, model_name)


def _reasoning_text(delta_extra: dict) -> str:
    """Readable reasoning in a stream delta: `reasoning`, else text/summary reasoning_details (encrypted ones skipped)."""
    text = delta_extra.get("reasoning")
    if isinstance(text, str) and text:
        return text
    parts = []
    for detail in delta_extra.get("reasoning_details") or []:
        if not isinstance(detail, dict):
            continue
        if detail.get("type") == "reasoning.text" and isinstance(detail.get("text"), str):
            parts.append(detail["text"])
        elif detail.get("type") == "reasoning.summary" and isinstance(detail.get("summary"), str):
            parts.append(detail["summary"])
    return "".join(parts)


def _auto_router_plugin(model_name: str, allowed_models: list[str] | None = None) -> dict | None:
    """auto-router plugin for openrouter/auto: pinned cost tier, plus the candidate pool when given."""
    if model_name != OPENROUTER_MODEL_MAP[ModelName.Auto]:
        return None
    plugin: dict = {"id": "auto-router", "cost_tier": AUTO_ROUTER_COST_TIER}
    if allowed_models:
        plugin["allowed_models"] = list(allowed_models)
    return plugin


def _usage_event(served_model: str, usage) -> dict:
    data = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage or {})
    details = data.get("completion_tokens_details") or {}
    return {
        "type": "usage",
        "model": served_model,
        "cost": data.get("cost"),
        "prompt_tokens": data.get("prompt_tokens"),
        "completion_tokens": data.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
    }


# Tool loop limits: parallel calls per round, the round's shared call timeout, and how much text a tools round may produce before
# it is treated as the answer (streamed) rather than a preamble to tool calls (dropped).
MAX_TOOL_CALLS_PER_ROUND = 3
MAX_TOOL_CALLS_PER_STREAM = 8  # whatever max_tool_rounds says: bounds searches and re-sent context
TOOL_CALL_TIMEOUT_S = 15
TOOL_ROUND_TEXT_FLUSH_CHARS = 80
TOOL_FAILED_CONTENT = "search failed"


def _sum_usage(served_model: str, rounds: list[dict]) -> dict:
    """One usage dict for all rounds of a stream: numeric fields summed, None when no round reported them."""
    total: dict = {"type": "usage", "model": served_model}
    for key in ("cost", "prompt_tokens", "completion_tokens", "reasoning_tokens"):
        values = [r[key] for r in rounds if isinstance(r.get(key), (int, float))]
        total[key] = sum(values) if values else None
    return total


def _accumulate_tool_call(calls: dict[int, dict], tool_delta) -> None:
    """Merge one streamed tool_call delta (id/name first, arguments in pieces) into `calls` by index."""
    call = calls.setdefault(tool_delta.index, {"id": None, "name": "", "arguments": ""})
    call["id"] = call["id"] or getattr(tool_delta, "id", None)
    function = getattr(tool_delta, "function", None)
    if function is not None:
        call["name"] += function.name or ""
        call["arguments"] += function.arguments or ""


def _parse_arguments(raw: str) -> dict | None:
    try:
        arguments = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return arguments if isinstance(arguments, dict) else None


class _ToolMarkupFilter:
    """Drops tool calls a model writes as answer text (`<invoke>...</invoke>`) instead of sending a real tool call.

    Streaming-safe: holds back a tail that could be the start of a tag split across chunks. A block that is never
    closed ends at the first prose line, so a missing `</invoke>` cannot swallow the rest of the answer.
    """

    _OPEN = "<invoke"
    _CLOSE = "</invoke>"

    def __init__(self):
        self._pending = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._pending += text
        out: list[str] = []
        while True:
            if self._inside:
                end, newline = self._pending.find(self._CLOSE), self._pending.find("\n")
                if end >= 0 and (newline < 0 or end < newline):
                    self._pending = self._pending[end + len(self._CLOSE) :]
                    self._inside = False
                    continue
                if newline < 0:
                    break  # wait for the rest of the line
                if not self._is_markup_line(self._pending[:newline]):
                    self._inside = False  # prose again: the block was never closed
                    continue
                self._pending = self._pending[newline + 1 :]
                continue
            start, stray = self._pending.find(self._OPEN), self._pending.find(self._CLOSE)
            if stray >= 0 and (start < 0 or stray < start):  # a duplicate closing tag
                out.append(self._pending[:stray])
                self._pending = self._pending[stray + len(self._CLOSE) :]
                continue
            if start >= 0:
                out.append(self._pending[:start])
                self._pending = self._pending[start:]
                self._inside = True
                continue
            keep = self._partial_tag_len(self._pending)
            out.append(self._pending[: len(self._pending) - keep])
            self._pending = self._pending[len(self._pending) - keep :]
            break
        return "".join(out)

    def flush(self) -> str:
        tail, self._pending = self._pending, ""
        if self._inside and self._is_markup_line(tail):
            return ""
        return tail

    @staticmethod
    def _is_markup_line(line: str) -> bool:
        """Inside an unclosed block: blank lines and tag lines are markup; the first prose line ends the block."""
        return not line.strip() or line.lstrip().startswith("<")

    def _partial_tag_len(self, text: str) -> int:
        for n in range(min(len(text), len(self._CLOSE) - 1), 0, -1):
            if self._OPEN.startswith(text[-n:]) or self._CLOSE.startswith(text[-n:]):
                return n
        return 0


class OpenRouterClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model_name: ModelName | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required to use OpenRouterClient")

        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        # Convert generic model name to OpenRouter-specific format
        DEFAULT_MODEL_NAME = ModelName.Gemini35Flash
        generic_name = model_name or DEFAULT_MODEL_NAME
        self.model_name = get_openrouter_model_name(generic_name)
        # Model OpenRouter served on this client's latest stream (openrouter/auto resolves to a concrete one).
        # Reset when a stream starts and set from its first chunk, so read it only after draining the stream,
        # and only on a per-request client: a client shared across requests is overwritten by each stream.
        self.served_model: str | None = None
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=120.0, max_retries=2)

    def stream_chat(
        self,
        prompt: str,
        use_google_search: bool = False,
        max_tokens: int = 8192,
        reasoning: dict | None = None,
        include_usage: bool = False,
        auto_router_models: list[str] | None = None,
        tools: list[ChatTool] | None = None,
        max_tool_rounds: int = 0,
    ) -> Iterable[Union[str, dict]]:
        """
        Stream chat completions as plain text chunks and citation dicts.

        Args:
            prompt: The user prompt
            use_google_search: If True, appends ':online' to model name to enable web search
            reasoning: OpenRouter reasoning config (e.g. {"effort": "low"}); also turns on reasoning dicts
            include_usage: Request usage accounting and yield one usage dict after the stream
            auto_router_models: candidate pool for openrouter/auto (auto-router plugin allowed_models)
            tools: function tools the model may call; up to `max_tool_rounds` rounds of calls, then a final
                call without tools. Text of a round that ends in tool calls (a "let me search" preamble) is dropped.

        Yields:
            str for text chunks, dict for url_citation annotations; with `reasoning`,
            {"type": "reasoning", "text": ...}; with `include_usage`, a final {"type": "usage", ...};
            with `tools`, {"type": "tool_call", ...} / {"type": "tool_result", ...} per executed call and
            {"type": "tool_budget_exhausted"} before the forced final call.
        """
        # model_name is already in OpenRouter format from __init__
        chosen_model = self.model_name

        # OpenRouter enables web search by appending ':online' to the model name
        # Strip existing variants (e.g. :nitro) first — can't chain variants
        if use_google_search and not chosen_model.endswith(":online"):
            base_model = chosen_model.split(":")[0] if ":" in chosen_model else chosen_model
            chosen_model = f"{base_model}:online"

        extra_body: dict = {}
        plugins = []
        if use_google_search:
            plugins.append({"id": "web", "max_results": 3})
        auto_router = _auto_router_plugin(self.model_name, auto_router_models)
        if auto_router:
            plugins.append(auto_router)
        if plugins:
            extra_body["plugins"] = plugins
        if reasoning is not None:
            extra_body["reasoning"] = reasoning
        if include_usage:
            extra_body["usage"] = {"include": True}

        logger.info(
            f"OpenRouter stream_chat: model={chosen_model}, google_search={use_google_search}, reasoning={reasoning}, "
            f"tools={[t.name for t in tools or []]}"
        )

        messages: list[dict] = [{"role": "user", "content": prompt}]
        request = {"model": chosen_model, "max_tokens": max_tokens, "extra_body": extra_body}
        usage_totals: list[dict] = []
        self.served_model = None

        if tools:
            markup = _ToolMarkupFilter()
            for chunk in self._tool_loop(messages, request, reasoning, usage_totals, tools, max_tool_rounds):
                if isinstance(chunk, str):
                    chunk = markup.feed(chunk)
                    if not chunk:
                        continue
                yield chunk
            tail = markup.flush()
            if tail:
                yield tail
        else:
            yield from self._stream_round(self._create(messages, request), reasoning, usage_totals)

        if include_usage:
            usage_event = _sum_usage(self.served_model or chosen_model, usage_totals)
            logger.info(
                "OpenRouter usage: requested=%s served=%s cost=%s completion_tokens=%s reasoning_tokens=%s",
                chosen_model,
                usage_event["model"],
                usage_event["cost"],
                usage_event["completion_tokens"],
                usage_event["reasoning_tokens"],
            )
            yield usage_event

    def _create(
        self,
        messages: list[dict],
        request: dict,
        tools: list[ChatTool] | None = None,
        tool_choice: str | None = None,
    ):
        extra_body = request["extra_body"]
        try:
            return self.client.chat.completions.create(
                model=request["model"],
                messages=list(messages),
                max_tokens=request["max_tokens"],
                stream=True,
                **({"tools": [t.schema() for t in tools]} if tools else {}),
                **({"tool_choice": tool_choice} if tool_choice else {}),
                **({"extra_body": extra_body} if extra_body else {}),
            )
        except Exception as e:
            logger.error(f"OpenRouter API error (model={request['model']}): {e}")
            raise

    def _tool_loop(
        self,
        messages: list[dict],
        request: dict,
        reasoning: dict | None,
        usage_totals: list[dict],
        tools: list[ChatTool],
        max_tool_rounds: int,
    ) -> Generator[Union[str, dict], None, None]:
        tools_by_name = {t.name: t for t in tools}
        question = list(messages)  # the user prompt alone, for a plain answer if a tools request fails
        used_tools = False
        calls_left = MAX_TOOL_CALLS_PER_STREAM
        for _ in range(max_tool_rounds):
            if calls_left <= 0:
                break
            try:
                response = self._create(messages, request, tools)
            except Exception:
                logger.warning("OpenRouter tools request failed; answering without tools", exc_info=True)
                yield from self._stream_round(self._create(question, request), reasoning, usage_totals)
                return
            calls = yield from self._stream_round(response, reasoning, usage_totals, buffer_text=True)
            if not calls:
                return
            used_tools = True
            calls = calls[: min(MAX_TOOL_CALLS_PER_ROUND, calls_left)]
            calls_left -= len(calls)
            yield from self._run_tool_calls(calls, tools_by_name, messages)
        if not used_tools:
            yield from self._stream_round(self._create(messages, request), reasoning, usage_totals)
            return
        yield {"type": "tool_budget_exhausted"}
        try:
            # Tool history needs its tools declared; "none" makes the model answer from the results it has.
            response = self._create(messages, request, tools, tool_choice="none")
        except Exception:
            logger.warning("OpenRouter final call after tool rounds failed; answering without tools", exc_info=True)
            response = self._create(question, request)
        yield from self._stream_round(response, reasoning, usage_totals)

    def _stream_round(
        self, response, reasoning: dict | None, usage_totals: list[dict], buffer_text: bool = False
    ) -> Generator[Union[str, dict], None, list[dict]]:
        """Stream one completion; return its tool calls ([] when it answered).

        With `buffer_text`, text is held until the round ends or passes TOOL_ROUND_TEXT_FLUSH_CHARS: a round
        that ends in tool calls drops it (a "let me search" preamble). Tool calls after a flush still run.
        """
        round_model = None
        usage = None
        streamed: list[str] = []  # text the user already saw this round
        buffered: list[str] = []
        flushed = not buffer_text
        calls: dict[int, dict] = {}
        for event in response:
            round_model = round_model or getattr(event, "model", None)
            if getattr(event, "usage", None):
                usage = event.usage
            if not event.choices:
                continue  # the usage accounting chunk has no choices
            delta = event.choices[0].delta
            if reasoning is not None:
                reasoning_text = _reasoning_text(delta.model_extra or {})
                if reasoning_text:
                    yield {"type": "reasoning", "text": reasoning_text}
            if delta.content:
                if flushed:
                    streamed.append(delta.content)
                    yield delta.content
                else:
                    buffered.append(delta.content)
                    if not calls and sum(len(t) for t in buffered) > TOOL_ROUND_TEXT_FLUSH_CHARS:
                        flushed = True
                        streamed.append("".join(buffered))
                        yield streamed[-1]
                        buffered = []
            for tool_delta in getattr(delta, "tool_calls", None) or []:
                _accumulate_tool_call(calls, tool_delta)
            # OpenRouter extension: url_citation annotations in model_extra
            annotations = (delta.model_extra or {}).get("annotations", [])
            for ann in annotations:
                if ann.get("type") == "url_citation":
                    citation = ann.get("url_citation", {})
                    yield {
                        "type": "url_citation",
                        "url": citation.get("url", ""),
                        "title": citation.get("title"),
                        "content": citation.get("content"),
                    }
        self.served_model = round_model or self.served_model
        if usage is not None:
            usage_totals.append(_usage_event(round_model or "", usage))
        if calls:
            if buffer_text and flushed:
                yield "\n\n"  # a preamble already streamed: the answer after the tool round starts a new paragraph
            text = "".join(streamed) if buffer_text else ""
            return [{**calls[i], "id": calls[i]["id"] or f"call_{i}", "text": text} for i in sorted(calls)]
        if buffered:
            yield "".join(buffered)
        return []

    def _run_tool_calls(
        self, calls: list[dict], tools_by_name: dict[str, ChatTool], messages: list[dict]
    ) -> Generator[dict, None, None]:
        """Run one round's tool calls concurrently and append the assistant + tool messages for the next round."""
        parsed = [(call, _parse_arguments(call["arguments"])) for call in calls]
        for call, arguments in parsed:
            yield {"type": "tool_call", "name": call["name"], "arguments": arguments or {}}

        pool = ThreadPoolExecutor(max_workers=MAX_TOOL_CALLS_PER_ROUND, thread_name_prefix="chat-tool")
        futures = [
            pool.submit(tools_by_name[call["name"]].run, arguments)
            if arguments is not None and call["name"] in tools_by_name
            else None
            for call, arguments in parsed
        ]
        results: list[ChatToolResult | None] = []
        deadline = time.monotonic() + TOOL_CALL_TIMEOUT_S  # calls run in parallel: one deadline for the round
        for (call, _arguments), future in zip(parsed, futures):
            result = None
            if future is not None:
                try:
                    result = future.result(timeout=max(0.0, deadline - time.monotonic()))
                except FutureTimeoutError:
                    logger.warning("Tool call %s timed out", call["name"])
                except Exception:
                    logger.warning("Tool call %s failed", call["name"], exc_info=True)
            results.append(result)
        pool.shutdown(wait=False, cancel_futures=True)

        messages.append(
            {
                "role": "assistant",
                # Text already streamed this round, so the next round continues it instead of restarting.
                "content": (parsed[0][0].get("text") or None) if parsed else None,
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c, _ in parsed
                ],
            }
        )
        for (call, arguments), result in zip(parsed, results):
            content = result.content if result is not None else TOOL_FAILED_CONTENT
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            yield {
                "type": "tool_result",
                "name": call["name"],
                "arguments": arguments or {},
                "ok": result is not None,
                "meta": result.meta if result is not None else {},
            }

    # Add this new method to the OpenRouterClient class
    def stream_chat_with_pdf(
        self, prompt: str, pdf_content: bytes, filename: str = "document.pdf", pdf_engine: str = "pdf-text"
    ) -> Iterable[str]:
        """
        Stream chat completions with PDF file input as plain text chunks.

        Args:
            prompt: The user prompt/question about the PDF
            pdf_content: Raw bytes of the PDF file
            filename: Name of the PDF file (for context)
            pdf_engine: PDF parsing engine - "pdf-text" (default, faster) or "mistral-ocr" (slower, more accurate)

        Yields:
            String chunks from the streaming response

        Example:
            >>> client = OpenRouterClient()
            >>> with open("report.pdf", "rb") as f:
            >>>     pdf_bytes = f.read()
            >>> for chunk in client.stream_chat_with_pdf("Summarize this report", pdf_bytes):
            >>>     print(chunk, end="")
        """
        # Encode PDF to base64
        base64_pdf = base64.b64encode(pdf_content).decode("utf-8")
        data_url = f"data:application/pdf;base64,{base64_pdf}"

        # Prepare model name with optional online search
        chosen_model = self.model_name

        # Structure messages with both text and file
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "file", "file": {"filename": filename, "file_data": data_url}},
                ],
            }
        ]

        # Configure PDF processing engine
        plugins = [
            {
                "id": "file-parser",
                "pdf": {
                    "engine": pdf_engine  # "pdf-text" or "mistral-ocr"
                },
            }
        ]
        auto_router = _auto_router_plugin(self.model_name)
        if auto_router:
            plugins.append(auto_router)

        # Stream the response
        response = self.client.chat.completions.create(
            model=chosen_model,
            messages=messages,  # type: ignore
            extra_body={"plugins": plugins},  # OpenAI client passes extra params via extra_body
            stream=True,
        )

        self.served_model = None
        for event in response:
            self.served_model = self.served_model or getattr(event, "model", None)
            delta = event.choices[0].delta.content
            if delta:
                yield delta

    def stream_chat_with_pdf_url(
        self, prompt: str, pdf_url: str, filename: str = "document.pdf", pdf_engine: str = "pdf-text"
    ) -> Iterable[str]:
        """
        Stream chat completions with PDF from URL as plain text chunks.

        Args:
            prompt: The user prompt/question about the PDF
            pdf_url: URL pointing to the PDF file
            filename: Name of the PDF file (for context)
            pdf_engine: PDF parsing engine - "pdf-text" (default, faster) or "mistral-ocr" (slower, more accurate)

        Yields:
            String chunks from the streaming response

        Example:
            >>> client = OpenRouterClient()
            >>> url = "https://example.com/report.pdf"
            >>> for chunk in client.stream_chat_with_pdf_url("Summarize this report", url):
            >>>     print(chunk, end="")
        """
        # Prepare model name
        chosen_model = self.model_name

        # Structure messages with text and file URL
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "file", "file": {"filename": filename, "file_data": pdf_url}},
                ],
            }
        ]

        # Configure PDF processing engine
        plugins = [
            {
                "id": "file-parser",
                "pdf": {
                    "engine": pdf_engine  # "pdf-text" or "mistral-ocr"
                },
            }
        ]
        auto_router = _auto_router_plugin(self.model_name)
        if auto_router:
            plugins.append(auto_router)

        # Stream the response
        response = self.client.chat.completions.create(
            model=chosen_model,
            messages=messages,  # type: ignore
            extra_body={"plugins": plugins},  # OpenAI client passes extra params via extra_body
            stream=True,
        )

        self.served_model = None
        for event in response:
            self.served_model = self.served_model or getattr(event, "model", None)
            delta = event.choices[0].delta.content
            if delta:
                yield delta
