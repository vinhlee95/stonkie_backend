# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

In all interactions and commit messages, be extremely concise. Sacrifice grammar for the sake of concision.

## Plans
At the end of each plan, give me a list of unresolved questions to answer, if any.
Make the questions extremely concise. Sacrifice grammar for the sake of concision.

## Project Overview

Stonkie is an AI-driven financial analysis platform that provides automated company insights, financial statement analysis, and intelligent reporting. The backend is built with FastAPI, PostgreSQL, and integrates multiple AI models (OpenAI, Google Gemini, OpenRouter) for natural language processing and financial analysis.

## Development Commands

### Running the Application

```bash
# Start the FastAPI application with auto-reload
hypercorn main:app --bind localhost:8080 --reload
```

### Database Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "description"

# Rollback to previous migration
alembic downgrade -1
```

### Code Quality

```bash
# Run linting and formatting
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Format code
ruff format .
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_conversation_store.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=. --cov-report=html
```

### Background Workers (Celery)

```bash
# Start Celery worker for background tasks
celery -A celery_app worker --loglevel=info

# Run with single worker (for memory optimization)
celery -A celery_app worker --pool=solo --loglevel=info
```

### Utility Scripts

Scripts in `scripts/` directory handle data ingestion and reporting:

```bash
# Export financial reports
python scripts/export_annual_financial_report.py

# Generate financial statement exports
python scripts/export_financial_report.py

# Check OpenRouter API latency
python scripts/openrouter_latency_check.py
```

## Change Verification Workflow

**IMPORTANT: Always verify changes before committing.** Follow this workflow for any code changes:

### 1. Run Integration Tests

**ALWAYS run the healthcheck test before committing any changes** to ensure the API is working:

```bash
# Activate virtual environment
source venv/bin/activate

# Run healthcheck integration test
python -m pytest tests/test_healthcheck.py -v

# Or run all tests
python -m pytest tests/ -v
```

This ensures:
- FastAPI app imports and initializes correctly
- No breaking changes to core dependencies
- API endpoints remain accessible

### 2. Syntax Verification

```bash
# Verify Python syntax
python -m py_compile <file_path>

# Or use ruff for comprehensive checks
ruff check <file_path>
```

### 3. Local Testing (when possible)

**For API endpoint changes:**

```bash
# Start the development server
hypercorn main:app --bind localhost:8080 --reload

# In another terminal, test the endpoint
curl -X GET http://localhost:8080/api/<endpoint>

# Or use httpie for better formatting
http GET localhost:8080/api/<endpoint>
```

**For service layer changes:**
- Run relevant unit tests: `pytest tests/test_<module>.py`
- Test integration flows if applicable

**For database changes:**
- Verify migration: `alembic upgrade head`
- Test rollback: `alembic downgrade -1 && alembic upgrade head`

### 4. Dependencies Check

If the worktree lacks dependencies, you can:
- Install in worktree: `pip install -r requirements.txt`
- Or verify syntax and logic, then test in main directory

### 5. Commit Only After Verification

Only commit changes after:
- **Integration tests pass** (especially `tests/test_healthcheck.py`)
- Syntax verification passes
- Local testing succeeds (or is verified as infeasible)
- Code follows project conventions
- No breaking changes to existing functionality

## High-Level Architecture

### 3-Layer Architecture Pattern

The codebase follows a clean separation of concerns with three distinct layers:

1. **Presentation Layer** (`main.py`)
   - FastAPI REST endpoints with streaming support
   - Request validation, CORS, middleware
   - Routes delegate to service layer

2. **Business Logic Layer** (`services/`, `agent/`)
   - Core financial analysis logic
   - Question classification and routing
   - AI model orchestration
   - Independent of data source and presentation

3. **Data Access Layer** (`connectors/`, `models/`)
   - Database operations (PostgreSQL via SQLAlchemy)
   - External API integrations (Finnhub, vector stores)
   - Data transformation and caching

### Key Architectural Components

#### Question Analysis Pipeline (`services/financial_analyzer.py`)

The `FinancialAnalyzer` is the main entry point for all financial questions. It orchestrates:

1. **Question Classification** (`services/question_analyzer/classifier.py`)
   - Determines question type: GENERAL_FINANCE, COMPANY_GENERAL, or COMPANY_SPECIFIC_FINANCE
   - Classifies data requirements: NONE, BASIC, or DETAILED
   - Classifies period requirements: ANNUAL, QUARTERLY, or BOTH

2. **Handler Routing**
   - Routes questions to specialized handlers based on classification
   - `GeneralFinanceHandler`: General market/finance questions
   - `CompanyGeneralHandler`: Company overview questions
   - `CompanySpecificFinanceHandler`: Financial statement analysis (most complex)

3. **Dynamic Section Generation** (`services/question_analyzer/company_specific_finance_handler.py`)
   - AI analyzes each question to generate 2 relevant section titles
   - Runs in parallel with data fetching for performance
   - Falls back to generic sections if AI generation fails/times out
   - See `services/question_analyzer/COMPANY_SPECIFIC_HANDLER_FLOW.md` for detailed flow diagrams

#### AI Model Abstraction (`agent/agent.py`)

The `Agent` class provides a unified interface across different AI providers:

```python
# Initialize agent with specific model
agent = Agent(model_type="gemini", model_name="gemini-2.5-flash")
openai_agent = Agent(model_type="openai")

# Generate content with streaming
async for chunk in agent.generate_content(prompt):
    yield chunk
```

- Abstracts OpenAI, Gemini, and OpenRouter clients
- Supports streaming responses for real-time UI updates
- Handles embeddings for vector search
- Model selection via `ai_models/model_mapper.py`

#### Conversation Management (`connectors/conversation_store.py`)

Conversations are tracked using Redis for context-aware responses:

- `generate_conversation_id()`: Creates unique conversation IDs
- `append_user_message()` / `append_assistant_message()`: Store message history
- `get_conversation_history_for_prompt()`: Retrieve context for prompts
- Metadata storage for tracking last ticker, data requirement, etc.

#### Data Optimization (`services/question_analyzer/data_optimizer.py`)

Intelligently fetches only required financial data based on question classification:

- **NONE**: No financial data fetched
- **BASIC**: Company fundamentals only (key stats)
- **DETAILED**: Full financial statements (annual/quarterly) based on period classification

This tiered approach reduces latency and API costs.

## Database Schema

### Core Models (`models/`)

- **`CompanyFinancialStatement`**: Annual financial statements (10-K)
  - Stores: balance_sheet, income_statement, cash_flow as JSON
  - Unique constraint: company_symbol + period_end_year

- **`CompanyQuarterlyFinancialStatement`**: Quarterly financial data (10-Q)
  - Similar structure to annual statements

- **`CompanyFinancials`**: Revenue breakdown and historical metrics
  - JSON field for revenue_breakdown by year

- **`CompanyInsight`**: AI-generated insights and analysis
  - Fields: title, content, insight_type, thumbnail_url, slug

- **`CompanyFundamental`**: Basic company information from external APIs
  - Caches fundamental data from Finnhub

### Database Session Management

Always use context managers for database sessions:

```python
from connectors.database import SessionLocal

with SessionLocal() as db:
    result = db.query(Company).filter(Company.symbol == ticker).first()
    return result
```

## API Patterns and Conventions

### RESTful Resource Naming

Follow these established patterns:

```python
# Company-specific resources
/api/companies/{ticker}/insights
/api/companies/{ticker}/financials/statements
/api/companies/{ticker}/fundamentals

# Collection endpoints
/api/insights
/api/companies

# Query parameters for options
/api/companies/{ticker}/insights?type=growth&stream=true
```

**Key conventions:**
- Use `ticker` parameter for company symbols (not `symbol` or `code`)
- Plural resource names (`/insights` not `/insight`)
- Query parameters for filtering and options (`?stream=true`, `?type=growth`)

### Streaming Responses

Many endpoints support streaming for real-time updates:

```python
from fastapi.responses import StreamingResponse

async def stream_analysis():
    async for chunk in financial_analyzer.analyze_question(ticker, question):
        yield f"data: {json.dumps(chunk)}\n\n"

return StreamingResponse(stream_analysis(), media_type="text/event-stream")
```

## Coding Standards

### Type Annotations

**ALWAYS use comprehensive type hints:**

```python
from typing import Optional, List, Dict, Any, AsyncGenerator

async def get_company_data(
    ticker: str,
    period: Optional[str] = None
) -> CompanyDataDto | None:
    pass

async def stream_insights(ticker: str) -> AsyncGenerator[Dict[str, Any], None]:
    yield {"data": "example"}
```

### Enums and Data Classes

Use `StrEnum` for string-based enums and frozen dataclasses for DTOs:

```python
from enum import StrEnum
from dataclasses import dataclass

class InsightType(StrEnum):
    GROWTH = "growth"
    EARNINGS = "earnings"

@dataclass(frozen=True)
class CompanyInsightDto:
    company_symbol: str
    title: str
    content: str
```

### Error Handling

Always include proper error handling with logging:

```python
import logging

logger = logging.getLogger(__name__)

try:
    result = await process_data()
    return result
except Exception as e:
    logger.error(f"Error processing data: {e}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

## Environment Configuration

### Required Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/stonkie

# AI Models
OPENAI_API_KEY=your_openai_key
GEMINI_API_KEY=your_gemini_key
OPENROUTER_API_KEY=your_openrouter_key

# Vector Store
PINECONE_API_KEY=your_pinecone_key

# Background Jobs
REDIS_URL=redis://localhost:6379/0

# Application
ENV=local|production
LOG_LEVEL=INFO|DEBUG|WARNING
```

## AI Model Selection Guidelines

Different models are used for different tasks:

- **Gemini 2.5 Flash**: General financial analysis, content generation
- **Gemini 2.5 Flash Lite**: Quick classification tasks, simple queries
- **OpenAI GPT-4**: Complex reasoning, structured data extraction
- **OpenAI Embeddings**: Vector similarity search, document retrieval
- **OpenRouter Auto Router**: Automatic model selection based on task

Model selection is handled by `ai_models/model_mapper.py` and can be overridden per request via `preferred_model` parameter.

## Performance Considerations

### Parallel Execution

For DETAILED financial queries, dimension analysis and data fetching run in parallel:

```python
# Both operations start simultaneously
dimension_task = asyncio.create_task(self._analyze_question_dimensions(...))
data_task = asyncio.create_task(self.data_optimizer.fetch_optimized_data(...))

dimension_sections, (fundamental, annual, quarterly) = await asyncio.gather(
    dimension_task, data_task
)
```

### Caching Strategy

- Redis used for conversation context (15-minute TTL)
- Database caching via `connectors/cache.py` for financial data
- Connection pooling for PostgreSQL

### Memory Management (Celery)

Celery workers use aggressive memory optimization:
- `worker_max_tasks_per_child=1`: Restart after each task (important for Playwright)
- `worker_pool="solo"`: Single process for minimal overhead
- `worker_prefetch_multiplier=1`: Process one task at a time

## Code Organization Philosophy

**Avoid over-engineering:**
- Only make changes directly requested or clearly necessary
- Don't add features, refactoring, or "improvements" beyond the task scope
- Don't add docstrings, comments, or type annotations to unchanged code
- Only validate at system boundaries (user input, external APIs)
- Don't create helpers or abstractions for one-time operations
- Delete unused code completely (no `// removed` comments or `_unused` variables)

**When editing files:**
- ALWAYS read the file first before making changes
- Prefer editing existing files over creating new ones
- Maintain consistency with existing patterns in the file
