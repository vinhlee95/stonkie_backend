"""Question handlers for different types of financial questions."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Generator, Iterable, List, Optional, Union

from langfuse import get_client, observe

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient
from connectors.company import CompanyConnector
from utils.conversation_format import format_conversation_context

from .context_builders.components import PromptComponents

logger = logging.getLogger(__name__)
langfuse = get_client()
_openrouter_client: Optional[OpenRouterClient] = None


def get_openrouter_client() -> Optional[OpenRouterClient]:
    """Lazy init so we only attempt OpenRouter when configured."""
    global _openrouter_client
    if _openrouter_client is not None:
        return _openrouter_client

    try:
        _openrouter_client = OpenRouterClient()
    except Exception as e:
        logger.warning(f"OpenRouter not available: {e}")
        _openrouter_client = None
    return _openrouter_client


SOURCE_START_TAG = "[SOURCES_JSON]"
SOURCE_END_TAG = "[/SOURCES_JSON]"


def _process_source_tags(
    chunks: Iterable[Union[str, dict]],
    filing_lookup: Optional[Dict[str, str]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Process a stream of text chunks, extracting [SOURCES_JSON] blocks into sources events.

    Handles tag splits across chunk boundaries and partial tag buffering.
    Yields dicts with type "answer" or "sources".

    Args:
        chunks: Iterable of str text chunks and/or dict annotations from OpenRouter
        filing_lookup: Optional name→URL mapping for enriching source citations
    """
    buffer = ""
    buffering_sources = False
    all_emitted_urls: set = set()
    web_citations: list = []

    def _parse_sources(raw_json: str):
        try:
            parsed = json.loads(raw_json)
            sources = parsed.get("sources", [])
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Failed to parse SOURCES_JSON block")
            return None
        if filing_lookup:
            for src in sources:
                if not src.get("url") and src.get("name") in filing_lookup:
                    src["url"] = filing_lookup[src["name"]]
        deduped = []
        for src in sources:
            url = src.get("url")
            if url and url in all_emitted_urls:
                continue
            if url:
                all_emitted_urls.add(url)
            deduped.append(src)
        if deduped:
            return {"type": "sources", "body": deduped}
        return None

    def _emit_completed_text(text: str):
        """Yield answer/sources events from text that may contain complete source blocks."""
        while SOURCE_START_TAG in text:
            before, rest = text.split(SOURCE_START_TAG, 1)
            if before.strip():
                yield {"type": "answer", "body": before}
            if SOURCE_END_TAG in rest:
                json_str, text = rest.split(SOURCE_END_TAG, 1)
                evt = _parse_sources(json_str)
                if evt:
                    yield evt
            else:
                # Incomplete block — shouldn't happen in completed text, emit as-is
                if rest.strip():
                    yield {"type": "answer", "body": SOURCE_START_TAG + rest}
                return
        if text.strip():
            yield {"type": "answer", "body": text}

    for chunk in chunks:
        # Collect url_citation dicts from OpenRouter
        if isinstance(chunk, dict) and chunk.get("type") == "url_citation":
            web_citations.append(chunk)
            continue

        text_chunk = chunk
        if not text_chunk:
            continue

        # --- State machine for [SOURCES_JSON]...[/SOURCES_JSON] ---
        if buffering_sources:
            buffer += text_chunk
            # Strip start tag if partial match reassembled it
            if SOURCE_START_TAG in buffer:
                before_tag, buffer = buffer.split(SOURCE_START_TAG, 1)
                if before_tag.strip():
                    yield {"type": "answer", "body": before_tag}
            if SOURCE_END_TAG in buffer:
                buffering_sources = False
                json_str, after = buffer.split(SOURCE_END_TAG, 1)
                buffer = ""
                evt = _parse_sources(json_str)
                if evt:
                    yield evt
                # after may contain more source blocks
                yield from _emit_completed_text(after)
            continue

        if SOURCE_START_TAG in text_chunk:
            before, rest = text_chunk.split(SOURCE_START_TAG, 1)
            if before.strip():
                yield {"type": "answer", "body": before}
            if SOURCE_END_TAG in rest:
                json_str, after = rest.split(SOURCE_END_TAG, 1)
                evt = _parse_sources(json_str)
                if evt:
                    yield evt
                # after may contain more source blocks
                yield from _emit_completed_text(after)
            else:
                buffering_sources = True
                buffer = rest
            continue

        # Partial tag detection: hold back if chunk ends with prefix of start tag
        partial_match = ""
        for i in range(1, min(len(SOURCE_START_TAG), len(text_chunk)) + 1):
            if SOURCE_START_TAG.startswith(text_chunk[-i:]):
                partial_match = text_chunk[-i:]
                break
        if partial_match:
            safe = text_chunk[: -len(partial_match)]
            if safe:
                yield {"type": "answer", "body": safe}
            buffer = partial_match
            buffering_sources = True
            continue

        yield {"type": "answer", "body": text_chunk}

    # Flush remaining buffer
    if buffering_sources:
        # Strip start tag if present (from partial match that completed)
        if SOURCE_START_TAG in buffer:
            before_tag, buffer = buffer.split(SOURCE_START_TAG, 1)
            if before_tag.strip():
                yield {"type": "answer", "body": before_tag}
        if SOURCE_END_TAG in buffer:
            json_str, after = buffer.split(SOURCE_END_TAG, 1)
            evt = _parse_sources(json_str)
            if evt:
                yield evt
            yield from _emit_completed_text(after)
        elif buffer.strip():
            yield {"type": "answer", "body": buffer}

    # Emit any web citations not already emitted inline
    remaining_web = []
    for cit in web_citations:
        url = cit.get("url")
        if url and url not in all_emitted_urls:
            all_emitted_urls.add(url)
            remaining_web.append({"name": cit.get("title") or url, "url": url})
    if remaining_web:
        yield {"type": "sources", "body": remaining_web}


def _collect_paragraph_sources(
    events: Generator[Dict[str, Any], None, None],
) -> Generator[Dict[str, Any], None, None]:
    """Pass all events through unchanged while collecting paragraph-source associations.

    Tracks paragraph index via double-newline boundaries in answer text.
    After stream ends, yields an additional sources_grouped event with all
    sources mapped to their paragraph indices.

    Sources arriving after a paragraph boundary (\\n\\n) are associated with
    the paragraph that just ended, not the next one.
    """
    paragraph_index = 0
    has_content_in_current = False
    source_map: Dict[str, Dict] = {}  # url_or_name -> {name, url, paragraph_indices}

    for event in events:
        if event["type"] == "answer":
            text = event["body"]
            for char in text:
                if char == "\n":
                    continue
                if not has_content_in_current:
                    has_content_in_current = True
            # Check for paragraph boundaries
            if "\n\n" in text:
                paragraph_index += text.count("\n\n")
                has_content_in_current = not text.endswith("\n\n")
            yield event

        elif event["type"] == "sources":
            # Pass through for inline rendering, also buffer for grouped event
            yield event
            # If no content yet in current paragraph, source belongs to previous one
            src_para = paragraph_index if has_content_in_current else max(0, paragraph_index - 1)
            for src in event.get("body", []):
                key = src.get("url") or src.get("name", "")
                if not key:
                    continue
                if key in source_map:
                    if src_para not in source_map[key]["paragraph_indices"]:
                        source_map[key]["paragraph_indices"].append(src_para)
                else:
                    source_map[key] = {
                        "name": src.get("name", key),
                        "url": src.get("url"),
                        "paragraph_indices": [src_para],
                    }
        else:
            yield event

    if source_map:
        yield {
            "type": "sources_grouped",
            "body": {
                "sources": list(source_map.values()),
            },
        }


class BaseQuestionHandler:
    """Base class for question handlers."""

    def __init__(self, agent: Optional[Agent] = None, company_connector: Optional[CompanyConnector] = None):
        """
        Initialize the handler.

        Args:
            agent: AI agent for generating responses
            company_connector: Connector for company data
        """
        self.agent = agent or Agent(model_type="gemini")
        self.company_connector = company_connector or CompanyConnector()

    @observe(name="generate_related_questions")
    async def _generate_related_questions(
        self, original_question: str, preferred_model: ModelName = ModelName.Auto
    ) -> AsyncGenerator[Dict[str, str], None]:
        """
        Generate related follow-up questions using MultiAgent with streaming.

        Buffers streaming chunks to yield complete questions one at a time.

        Args:
            original_question: The original question asked
            preferred_model: Preferred model to use for question generation

        Yields:
            Dictionary with type "related_question" and body containing the complete question
        """
        try:
            prompt = f"""
                Based on this original question: "{original_question}"

                Generate exactly 3 high-quality follow-up questions that a curious investor might naturally ask next.

                Requirements:
                - Each question should explore a DIFFERENT dimension:
                * Question 1: Go deeper into the same topic (more specific/detailed)
                * Question 2: Compare or contrast with a related concept, company, or time period
                * Question 3: Explore a related but adjacent topic (e.g., if original was about revenue, ask about profitability or cash flow)
                - Keep questions between 8-15 words
                - Make them actionable and specific (avoid vague questions like "What else should I know?")
                - Frame questions naturally, as a user would ask them
                - Ensure questions are relevant to the original context (financial analysis, company performance, market trends)
                - Do NOT number the questions or add any prefixes
                - Put EACH question on its OWN LINE

                Output format (one question per line):
                How does Apple's gross margin compare to its competitors?
                What was the main driver behind revenue growth last quarter?
                Is the current valuation sustainable given industry trends?
            """

            agent = MultiAgent(model_name=preferred_model)

            # Stream complete questions one at a time
            for question in agent.generate_content_by_lines(
                prompt=prompt,
                use_google_search=False,
                max_lines=3,
                min_line_length=10,
                strip_numbering=True,
                strip_markdown=True,
            ):
                yield {"type": "related_question", "body": question}

        except Exception as e:
            logger.error(f"Error generating related questions with MultiAgent: {e}")
            # Silently fail - related questions are non-critical


class GeneralFinanceHandler(BaseQuestionHandler):
    """Handles general financial concept questions."""

    async def handle(
        self,
        question: str,
        use_google_search: bool,
        use_url_context: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle general finance questions.

        Args:
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context
            preferred_model: Preferred model to use for answer generation
            conversation_messages: Optional list of previous conversation messages for context (not typically used for general finance)

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        try:
            yield {"type": "thinking_status", "body": "Structuring the answer..."}

            # Conversation context (important for follow-ups like "then?", "so?", "based on that?")
            conversation_context = ""
            if conversation_messages:
                # Use last 1–2 Q/A pairs to keep prompt small and focused
                recent_messages = (
                    conversation_messages[-4:] if len(conversation_messages) >= 4 else conversation_messages
                )
                conversation_lines: list[str] = []
                for msg in recent_messages:
                    role = (msg.get("role") or "").upper()
                    content = (msg.get("content") or "").strip()
                    if content:
                        conversation_lines.append(f"{role}: {content}")

                if conversation_lines:
                    conversation_context = "\n\nPrevious conversation:\n" + "\n".join(conversation_lines) + "\n"
                    num_pairs = len(conversation_messages) // 2
                    logger.info(
                        f"💬 Injected {num_pairs} Q/A pair(s) of conversation context into GeneralFinanceHandler prompt"
                    )

            prompt = f"""
                Please explain this financial concept or answer this question:

                {question}.

                Give a short answer in less than 150 words.
                Break the answer into different paragraphs for better readability.
                In the last paragraph, give an example of how this concept is used in a real-world situation

                IMPORTANT:
                - If the question is a follow-up (e.g., contains words like "then", "so", "based on that"), use the numbers/facts from the previous conversation to answer.
                - If the previous conversation includes a worked example with specific numbers, reuse those numbers and show the calculation briefly.
                - If the previous conversation does not contain enough information to compute something, ask ONE clarifying question.

                {conversation_context}
            """

            t_model = time.perf_counter()
            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="general-finance-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "use_google_search": use_google_search,
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                completion_start_time = None
                output_tokens = 0
                full_output = []

                raw_chunks = agent.generate_content(prompt=prompt, use_google_search=use_google_search)
                for event in _process_source_tags(raw_chunks):
                    if event["type"] == "answer":
                        if not first_chunk_received:
                            completion_start_time = datetime.now(timezone.utc)
                            t_first_chunk = time.perf_counter()
                            ttft = t_first_chunk - t_model
                            logger.info(f"Profiling GeneralFinanceHandler time_to_first_token: {ttft:.4f}s")
                            gen.update(completion_start_time=completion_start_time)
                            first_chunk_received = True

                        full_output.append(event["body"])
                        output_tokens += len(event["body"].split())

                    yield event

                # Update generation with output and usage
                gen.update(
                    output="".join(full_output),
                    usage_details={"output_tokens": output_tokens},
                    metadata={
                        "use_google_search": use_google_search,
                        "use_url_context": use_url_context,
                        "model": model_used,
                    },
                )

            t_model_end = time.perf_counter()
            logger.info(f"Profiling GeneralFinanceHandler model_generate_content: {t_model_end - t_model:.4f}s")

            # Yield the model used for answer
            yield {"type": "model_used", "body": model_used}

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question, preferred_model):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling GeneralFinanceHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling GeneralFinanceHandler total: {t_related_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"❌ Error generating explanation: {e}")
            yield {"type": "answer", "body": "❌ Error generating explanation. Please try again later."}


class CompanyGeneralHandler(BaseQuestionHandler):
    """Handles general questions about companies."""

    async def handle(
        self,
        ticker: str,
        question: str,
        use_google_search: bool,
        use_url_context: bool,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle company general questions.

        Args:
            ticker: Company ticker symbol
            question: The question to answer
            use_google_search: Whether to use Google Search
            use_url_context: Whether to use URL context
            preferred_model: Preferred model to use for answer generation
            conversation_messages: Optional list of previous conversation messages for context

        Yields:
            Dictionary chunks with analysis results
        """
        t_start = time.perf_counter()

        company = self.company_connector.get_by_ticker(ticker)
        company_name = company.name if company else ""

        yield {
            "type": "thinking_status",
            "body": f"Analyzing general information about {company_name} (ticker: {ticker}) and preparing a concise, insightful answer...",
        }

        try:
            source_instructions = PromptComponents.source_instructions()

            # Format conversation context if available
            conversation_context = ""
            if conversation_messages:
                num_pairs = len(conversation_messages) // 2
                conversation_context = format_conversation_context(conversation_messages, ticker, company_name)
                conversation_context = f"\n\n{conversation_context}\n"
                logger.info(
                    f"💬 Injected {num_pairs} Q/A pair(s) of conversation context into CompanyGeneralHandler prompt "
                    f"(ticker: {ticker.upper()}, company: {company_name})"
                )
            else:
                logger.debug(f"💬 No conversation context to inject (CompanyGeneralHandler, ticker: {ticker.upper()})")

            prompt = f"""
                You are an expert about a business. Answer the following question about {company_name} (ticker: {ticker}):
                {question}.
{conversation_context}
                Keep the response concise in under 200 words. Do not repeat points or facts. Connect the facts to a compelling story.
                Break the answer into different paragraphs and bullet points for better readability.
                
                {source_instructions}
            """

            t_model = time.perf_counter()
            agent = MultiAgent(model_name=preferred_model)
            model_used = agent.model_name

            with langfuse.start_as_current_observation(
                as_type="generation", name="company-general-llm-call", model=model_used
            ) as gen:
                gen.update(
                    input={
                        "prompt": prompt,
                        "ticker": ticker,
                        "company_name": company_name,
                        "use_google_search": use_google_search,
                        "model": model_used,
                    }
                )

                first_chunk_received = False
                completion_start_time = None
                output_tokens = 0
                full_output = []

                raw_chunks = agent.generate_content(prompt=prompt, use_google_search=use_google_search)
                for event in _collect_paragraph_sources(_process_source_tags(raw_chunks)):
                    if event["type"] == "answer":
                        if not first_chunk_received:
                            completion_start_time = datetime.now(timezone.utc)
                            t_first_chunk = time.perf_counter()
                            ttft = t_first_chunk - t_model
                            logger.info(f"Profiling CompanyGeneralHandler time_to_first_token: {ttft:.4f}s")
                            gen.update(completion_start_time=completion_start_time)
                            first_chunk_received = True

                        full_output.append(event["body"])
                        output_tokens += len(event["body"].split())

                    yield event

                # Update generation with output and usage
                gen.update(
                    output="".join(full_output),
                    usage_details={"output_tokens": output_tokens},
                    metadata={
                        "ticker": ticker,
                        "use_google_search": use_google_search,
                        "use_url_context": use_url_context,
                        "model": model_used,
                    },
                )

            t_model_end = time.perf_counter()
            logger.info(f"Profiling CompanyGeneralHandler model_generate_content: {t_model_end - t_model:.4f}s")

            # Yield the model used for answer
            yield {"type": "model_used", "body": model_used}

            t_related = time.perf_counter()
            async for related_q in self._generate_related_questions(question, preferred_model):
                yield related_q
            t_related_end = time.perf_counter()
            logger.info(f"Profiling CompanyGeneralHandler related_questions: {t_related_end - t_related:.4f}s")
            logger.info(f"Profiling CompanyGeneralHandler total: {t_related_end - t_start:.4f}s")

        except Exception as e:
            logger.error(f"Error generating answer: {str(e)}")
            yield {"type": "answer", "body": "❌ Error generating answer."}
