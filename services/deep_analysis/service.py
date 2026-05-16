"""DeepAnalysisService — orchestrates agent-based deep analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator

from connectors.agent_connector import AgentConnector, AgentEventType, AgentTool
from services.analysis_progress import AnalysisPhase, thinking_status
from services.analyze_retrieval.citation_index import build_sources_event
from services.analyze_retrieval.schemas import AnalyzeSource
from services.deep_analysis.prompts import build_system_prompt
from services.deep_analysis.tools import brave_search, get_company_profile, get_financial_data, read_url

if TYPE_CHECKING:
    from connectors.brave_client import BraveClient
    from connectors.company import CompanyConnector
    from connectors.company_financial import CompanyFinancialConnector

BUDGET_CAP = 10
MODEL_NAME = "claude-sonnet-4-6"

_TOOL_PHASE_MAP: dict[str, AnalysisPhase] = {
    "brave_search": AnalysisPhase.SEARCH,
    "read_url": AnalysisPhase.SEARCH,
    "get_financial_data": AnalysisPhase.DATA_FETCH,
    "get_company_profile": AnalysisPhase.DATA_FETCH,
}

_TOOL_STATUS_MSG: dict[str, str] = {
    "brave_search": "Searching the web...",
    "read_url": "Reading URL content...",
    "get_financial_data": "Pulling financial statements...",
    "get_company_profile": "Fetching company profile...",
}


class DeepAnalysisService:
    def __init__(
        self,
        *,
        agent_connector: AgentConnector,
        company_connector: CompanyConnector,
        company_financial_connector: CompanyFinancialConnector,
        brave_client: BraveClient,
    ):
        self._agent_connector = agent_connector
        self._company_connector = company_connector
        self._company_financial_connector = company_financial_connector
        self._brave_client = brave_client

    async def analyze(
        self,
        *,
        ticker: str,
        question: str,
        company_name: str,
        conversation_messages: list[dict] | None = None,
        extracted_url: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        yield thinking_status("Analyzing question...", phase=AnalysisPhase.CLASSIFY, step=1)

        has_url = extracted_url is not None
        system_prompt = build_system_prompt(ticker, company_name, has_url)
        tools = self._build_tools(ticker, company_name, question, extracted_url)
        messages = self._build_messages(conversation_messages, question)

        accumulated_sources: list[AnalyzeSource] = []
        seen_urls: set[str] = set()
        tool_call_count = 0
        step_counter = 2

        async for event in self._agent_connector.run_stream(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            max_turns=BUDGET_CAP,
        ):
            if event.type == AgentEventType.TOOL_USE_START:
                if tool_call_count >= BUDGET_CAP:
                    break
                tool_call_count += 1
                phase = _TOOL_PHASE_MAP.get(event.tool_name or "", AnalysisPhase.ANALYZE)
                msg = _TOOL_STATUS_MSG.get(event.tool_name or "", "Processing...")
                yield thinking_status(msg, phase=phase, step=step_counter)
                step_counter += 1

            elif event.type == AgentEventType.TOOL_RESULT:
                if event.tool_name == "brave_search" and isinstance(event.tool_output, dict):
                    for src in event.tool_output.get("analyze_sources", []):
                        if isinstance(src, AnalyzeSource) and src.url not in seen_urls:
                            accumulated_sources.append(src)
                            seen_urls.add(src.url)
                elif event.tool_name == "read_url" and isinstance(event.tool_output, dict):
                    src = event.tool_output.get("analyze_source")
                    if isinstance(src, AnalyzeSource) and src.url not in seen_urls:
                        accumulated_sources.append(src)
                        seen_urls.add(src.url)

            elif event.type == AgentEventType.TEXT_DELTA:
                if event.text:
                    yield {"type": "answer", "body": event.text}

            elif event.type == AgentEventType.RUN_COMPLETE:
                break

        if accumulated_sources:
            yield build_sources_event(accumulated_sources)

        yield {"type": "model_used", "body": MODEL_NAME}

    def _build_tools(
        self,
        ticker: str,
        company_name: str,
        question: str,
        extracted_url: str | None,
    ) -> list[AgentTool]:
        async def _brave_search(query: str, market: str = "GLOBAL") -> dict:
            return await brave_search(
                query=query,
                brave_client=self._brave_client,
                ticker=ticker,
                company_name=company_name,
                market=market,
            )

        async def _get_financial_data(
            statement_type: str = "all",
            period_type: str = "annual",
            num_periods: int = 3,
        ) -> list[dict]:
            return await get_financial_data(
                ticker=ticker,
                connector=self._company_financial_connector,
                statement_type=statement_type,
                period_type=period_type,
                num_periods=num_periods,
            )

        async def _get_company_profile() -> dict | None:
            return await get_company_profile(
                ticker=ticker,
                connector=self._company_connector,
            )

        async def _read_url(url: str) -> dict:
            return await read_url(url=url, question=question)

        tools = [
            AgentTool(
                name="brave_search",
                description="Search the web for current news, analysis, and market data.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "market": {"type": "string", "description": "Market context", "default": "GLOBAL"},
                    },
                    "required": ["query"],
                },
                fn=_brave_search,
            ),
            AgentTool(
                name="get_financial_data",
                description="Pull income statements, balance sheets, and cash flow statements.",
                parameters={
                    "type": "object",
                    "properties": {
                        "statement_type": {"type": "string", "default": "all"},
                        "period_type": {"type": "string", "default": "annual"},
                        "num_periods": {"type": "integer", "default": 3},
                    },
                },
                fn=_get_financial_data,
            ),
            AgentTool(
                name="get_company_profile",
                description="Get company fundamentals: sector, market cap, PE ratio, etc.",
                parameters={"type": "object", "properties": {}},
                fn=_get_company_profile,
            ),
            AgentTool(
                name="read_url",
                description="Read and extract content from a URL or PDF.",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to read"},
                    },
                    "required": ["url"],
                },
                fn=_read_url,
            ),
        ]
        return tools

    def _build_messages(
        self,
        conversation_messages: list[dict] | None,
        question: str,
    ) -> list[dict]:
        messages: list[dict] = []
        if conversation_messages:
            messages.extend(conversation_messages)
        messages.append({"role": "user", "content": question})
        return messages
