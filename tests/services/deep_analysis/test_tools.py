from unittest.mock import MagicMock, patch

import pytest

from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeRetrievalResult, AnalyzeSource


@pytest.fixture
def mock_brave_client():
    client = MagicMock()
    return client


@pytest.fixture
def mock_company_financial_connector():
    connector = MagicMock()
    return connector


@pytest.fixture
def mock_company_connector():
    connector = MagicMock()
    return connector


class TestBraveSearch:
    @pytest.mark.asyncio
    async def test_returns_structured_results(self, mock_brave_client):
        from services.deep_analysis.tools import brave_search

        source = AnalyzeSource(
            id="src1",
            url="https://example.com/article",
            title="Test Article",
            publisher="Example",
            published_at=None,
            is_trusted=True,
            raw_content="Some content about AAPL",
        )
        passage = AnalyzePassage(
            source_id="src1",
            url="https://example.com/article",
            title="Test Article",
            publisher="Example",
            published_at=None,
            is_trusted=True,
            passage_index=0,
            content="Apple revenue grew 15% year over year",
        )
        retrieval_result = AnalyzeRetrievalResult(
            sources=[source],
            selected_passages=[passage],
            query="AAPL revenue growth",
            market="GLOBAL",
            request_id="req-123",
        )

        with patch("services.deep_analysis.tools.retrieve_for_analyze", return_value=retrieval_result):
            result = await brave_search(
                query="AAPL revenue growth",
                brave_client=mock_brave_client,
                ticker="AAPL",
                company_name="Apple Inc.",
            )

        assert isinstance(result, dict)
        assert "sources" in result
        assert "passages" in result
        assert len(result["sources"]) == 1
        assert result["sources"][0]["url"] == "https://example.com/article"
        assert result["sources"][0]["title"] == "Test Article"
        assert result["sources"][0]["is_trusted"] is True

    @pytest.mark.asyncio
    async def test_returns_sources_for_accumulation(self, mock_brave_client):
        from services.deep_analysis.tools import brave_search

        source = AnalyzeSource(
            id="src1",
            url="https://example.com/article",
            title="Test Article",
            publisher="Example",
            is_trusted=True,
            raw_content="Content",
        )
        retrieval_result = AnalyzeRetrievalResult(
            sources=[source],
            selected_passages=[],
            query="test",
            market="GLOBAL",
            request_id="req-123",
        )

        with patch("services.deep_analysis.tools.retrieve_for_analyze", return_value=retrieval_result):
            result = await brave_search(
                query="test query",
                brave_client=mock_brave_client,
                ticker="AAPL",
                company_name="Apple Inc.",
            )

        assert "analyze_sources" in result
        assert len(result["analyze_sources"]) == 1
        assert isinstance(result["analyze_sources"][0], AnalyzeSource)

    @pytest.mark.asyncio
    async def test_passes_market_and_freshness(self, mock_brave_client):
        from services.deep_analysis.tools import brave_search

        retrieval_result = AnalyzeRetrievalResult(
            sources=[],
            selected_passages=[],
            query="test",
            market="VN",
            request_id="req-123",
        )

        with patch("services.deep_analysis.tools.retrieve_for_analyze", return_value=retrieval_result) as mock_retrieve:
            await brave_search(
                query="test query",
                brave_client=mock_brave_client,
                ticker="VNM",
                company_name="Vinamilk",
                market="VN",
            )

        mock_retrieve.assert_called_once()
        call_kwargs = mock_retrieve.call_args.kwargs
        assert call_kwargs["market"] == "VN"


class TestGetFinancialData:
    @pytest.mark.asyncio
    async def test_annual_data(self, mock_company_financial_connector):
        from services.deep_analysis.tools import get_financial_data

        mock_statement = MagicMock()
        mock_statement.company_symbol = "AAPL"
        mock_statement.period_end_year = 2024
        mock_company_financial_connector.get_company_financial_statements_recent.return_value = [mock_statement]
        mock_company_financial_connector.to_dict.return_value = {
            "company_symbol": "AAPL",
            "period_end_year": 2024,
            "income_statement": {"revenue": 400000000000},
        }

        result = await get_financial_data(
            ticker="AAPL",
            connector=mock_company_financial_connector,
            period_type="annual",
            num_periods=3,
        )

        assert isinstance(result, list)
        assert len(result) == 1
        mock_company_financial_connector.get_company_financial_statements_recent.assert_called_once_with("AAPL", 3)

    @pytest.mark.asyncio
    async def test_quarterly_data(self, mock_company_financial_connector):
        from services.deep_analysis.tools import get_financial_data

        mock_statement = MagicMock()
        mock_company_financial_connector.get_company_quarterly_financial_statements_recent.return_value = [
            mock_statement
        ]
        mock_company_financial_connector.to_dict.return_value = {
            "company_symbol": "AAPL",
            "period_end_quarter": "2024-Q4",
            "income_statement": {"revenue": 100000000000},
        }

        result = await get_financial_data(
            ticker="AAPL",
            connector=mock_company_financial_connector,
            period_type="quarterly",
            num_periods=4,
        )

        assert isinstance(result, list)
        assert len(result) == 1
        mock_company_financial_connector.get_company_quarterly_financial_statements_recent.assert_called_once_with(
            "AAPL", 4
        )

    @pytest.mark.asyncio
    async def test_filters_statement_type(self, mock_company_financial_connector):
        from services.deep_analysis.tools import get_financial_data

        mock_statement = MagicMock()
        mock_company_financial_connector.get_company_financial_statements_recent.return_value = [mock_statement]
        mock_company_financial_connector.to_dict.return_value = {
            "company_symbol": "AAPL",
            "income_statement": {"revenue": 400000000000},
            "balance_sheet": {"total_assets": 350000000000},
            "cash_flow": {"operating_cash_flow": 100000000000},
        }
        mock_company_financial_connector.get_company_statement_by_type.return_value = {
            "income_statement": {"revenue": 400000000000},
        }

        result = await get_financial_data(
            ticker="AAPL",
            connector=mock_company_financial_connector,
            statement_type="income_statement",
            period_type="annual",
            num_periods=3,
        )

        assert isinstance(result, list)
        assert len(result) == 1
        mock_company_financial_connector.get_company_statement_by_type.assert_called_once()


class TestGetCompanyProfile:
    @pytest.mark.asyncio
    async def test_returns_fundamentals(self, mock_company_connector):
        from connectors.company import CompanyFundamentalDto
        from services.deep_analysis.tools import get_company_profile

        mock_company_connector.get_fundamental_data.return_value = CompanyFundamentalDto(
            name="Apple Inc.",
            market_cap=3000000000000,
            pe_ratio=28.5,
            revenue=400000000000,
            net_income=100000000000,
            basic_eps=6.5,
            sector="Technology",
            industry="Consumer Electronics",
            description="Apple designs consumer electronics",
            country="US",
            exchange="NASDAQ",
            dividend_yield=0.5,
            logo_url="https://logo.com/aapl.png",
            currency="USD",
        )

        result = await get_company_profile(ticker="AAPL", connector=mock_company_connector)

        assert isinstance(result, dict)
        assert result["name"] == "Apple Inc."
        assert result["market_cap"] == 3000000000000
        assert result["sector"] == "Technology"
        mock_company_connector.get_fundamental_data.assert_called_once_with("AAPL")

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_ticker(self, mock_company_connector):
        from services.deep_analysis.tools import get_company_profile

        mock_company_connector.get_fundamental_data.return_value = None

        result = await get_company_profile(ticker="XXXXX", connector=mock_company_connector)

        assert result is None


class TestReadUrl:
    @pytest.mark.asyncio
    async def test_returns_text(self):
        from services.deep_analysis.tools import read_url

        source = AnalyzeSource(
            id="src1",
            url="https://sec.gov/filing.htm",
            title="SEC Filing",
            publisher="SEC",
            is_trusted=True,
        )
        passage = AnalyzePassage(
            source_id="src1",
            url="https://sec.gov/filing.htm",
            title="SEC Filing",
            publisher="SEC",
            is_trusted=True,
            passage_index=0,
            content="The company reported revenue of $50B in Q4 2024.",
        )
        from connectors.tavily_extract_client import UrlIngestResult

        ingest_result = UrlIngestResult(
            source=source,
            selected_passages=[passage],
        )

        with patch("services.deep_analysis.tools.ingest_url", return_value=ingest_result):
            result = await read_url(url="https://sec.gov/filing.htm", question="What was Q4 revenue?")

        assert isinstance(result, dict)
        assert "content" in result
        assert "Q4 2024" in result["content"]
        assert "source" in result
        assert result["source"]["url"] == "https://sec.gov/filing.htm"
