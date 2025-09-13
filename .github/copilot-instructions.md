# Stonkie Backend - GitHub Copilot Instructions

## Project Overview

Stonkie is an AI-driven financial analysis platform that provides automated company insights, financial statement analysis, and intelligent reporting capabilities. The backend is built with FastAPI, PostgreSQL, and integrates multiple AI models (OpenAI GPT, Google Gemini) for natural language processing and financial analysis.

## Architecture

### 3-Layer Architecture

The project follows a clean 3-layer architecture pattern:

1. **Presentation Layer** (`main.py`)
   - FastAPI application with REST endpoints
   - Request/response handling, validation, and serialization
   - CORS middleware and logging

2. **Business Logic Layer** (`services/`, `agent/`, `analyzer.py`)
   - Core business logic and domain services
   - AI model orchestration and question classification
   - Financial data analysis and insight generation

3. **Data Access Layer** (`connectors/`, `models/`)
   - Database connections and ORM models
   - External API integrations (financial data, vector stores)
   - Data transformation and persistence

### Key Components

- **AI Models** (`ai_models/`): Abstractions for OpenAI and Gemini models
- **Agent** (`agent/`): Unified wrapper for different AI model implementations
- **Connectors** (`connectors/`): Data access layer for databases, APIs, and external services
- **Models** (`models/`): SQLAlchemy ORM models representing database entities
- **Services** (`services/`): Business logic for specific domains (companies, insights, reports)

## Coding Guidelines

### Type Annotations

**ALWAYS use comprehensive type hints for all functions, methods, and variables:**

```python
from typing import Optional, List, Dict, Any, AsyncGenerator, Union, Literal
from datetime import datetime
from enum import Enum, StrEnum
from dataclasses import dataclass

# Function signatures
async def get_company_data(ticker: str, period: Optional[str] = None) -> CompanyDataDto | None:
    pass

# Class methods
def process_financial_data(self, data: Dict[str, Any]) -> List[FinancialMetric]:
    pass

# Async generators
async def stream_insights(ticker: str) -> AsyncGenerator[Dict[str, Any], None]:
    yield {"data": "example"}

# Union types for API responses
def get_analysis_result() -> Union[SuccessResponse, ErrorResponse]:
    pass
```

### Enums and Data Classes

**Use StrEnum for string-based enums and frozen dataclasses for DTOs:**

```python
from enum import StrEnum
from dataclasses import dataclass

class InsightType(StrEnum):
    GROWTH = "growth"
    EARNINGS = "earnings"
    CASH_FLOW = "cash_flow"

@dataclass(frozen=True)
class CompanyInsightDto:
    id: int
    company_symbol: str
    insight_type: str
    title: str
    content: str
    created_at: datetime
```

### Error Handling

**Always include proper error handling with logging:**

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

### Async/Await Patterns

**Use async/await for I/O operations and streaming responses:**

```python
# Async generators for streaming
async def generate_response() -> AsyncGenerator[str, None]:
    async for chunk in data_stream:
        yield f"data: {chunk}\n\n"

# Database operations
async def get_company_data(ticker: str) -> Optional[CompanyData]:
    with SessionLocal() as db:
        return db.query(Company).filter(Company.symbol == ticker).first()
```

## Data Models

### Database Models (`models/`)

Each model represents a database table with specific responsibilities:

- **`CompanyFinancialStatement`**: Annual financial statements retrieved from external services
  - Stores: balance_sheet, income_statement, cash_flow (JSON fields)
  - Unique constraint on company_symbol + period_end_year

- **`CompanyQuarterlyFinancialStatement`**: Quarterly financial data
  - Similar structure to annual statements but for quarterly periods

- **`CompanyFinancials`**: Revenue breakdown and financial metrics
  - Stores: revenue_breakdown (JSON field) by year

- **`CompanyInsight`**: AI-generated insights and analysis
  - Stores: title, content, insight_type, thumbnail_url, slug

- **`CompanyFundamental`**: Basic company information and key statistics
  - Stores: fundamental company data from external APIs

### Data Transfer Objects (DTOs)

Use frozen dataclasses for data transfer between layers, especially between connector & service layer:

```python
@dataclass(frozen=True)
class CreateCompanyInsightDto:
    company_symbol: str
    insight_type: str
    title: str
    content: str
    thumbnail_url: str
```

## AI Model Integration

### Agent Pattern

Use the `Agent` class as a unified interface for different AI models:

```python
from agent.agent import Agent, SupportedModel

# Initialize agent with specific model
agent = Agent(model_type="gemini", model_name="gemini-2.5-flash")
openai_agent = Agent(model_type="openai")

# Generate content with streaming
async for chunk in agent.generate_content_and_normalize_results(prompt):
    yield chunk

# Generate embeddings
embedding = agent.generate_embedding("financial analysis text")
```

### Model Selection Guidelines

- **Gemini 2.5 Flash**: General financial analysis, content generation
- **Gemini 2.5 Flash Lite**: Quick classification tasks, simple queries
- **OpenAI GPT-4**: Complex reasoning, structured data extraction
- **OpenAI Embeddings**: Vector similarity search, document retrieval

## Database Patterns

### Connection Management

```python
from connectors.database import SessionLocal

# Use context managers for database sessions
with SessionLocal() as db:
    result = db.query(Model).filter(...).first()
    return result
```

### Migrations

- Use Alembic for database migrations
- All models must be imported in `alembic/env.py`
- Run migrations with: `alembic upgrade head`

## API Patterns

### API Path Naming Conventions

**Use RESTful naming patterns with nouns and consistent resource naming:**

```python
# Resource-based paths (PREFERRED)
/api/companies/{ticker}                          # GET - Get company details
/api/companies/{ticker}/insights                 # GET - List all insights for company
/api/companies/{ticker}/insights/{insight_id}    # GET - Get specific insight
/api/companies/{ticker}/insights/{type}          # GET - Get insights by type
/api/companies/{ticker}/financials               # GET - Get financial data
/api/companies/{ticker}/financials/statements    # GET - Get financial statements
/api/companies/{ticker}/fundamentals             # GET - Get fundamental data

# Collection endpoints
/api/insights                                    # GET - List all insights (with pagination)
/api/companies                                   # GET - List companies
/api/reports                                     # GET - List reports

# Nested resources for related data
/api/companies/{ticker}/reports                  # GET - Get reports for specific company
/api/companies/{ticker}/reports/{report_id}      # GET - Get specific report

# Query parameters for filtering and options
/api/companies/{ticker}/insights?type=growth     # Filter by insight type
/api/companies/{ticker}/insights?stream=true     # Enable streaming
/api/insights?company={ticker}&limit=10          # Pagination and filtering
```

### Existing Resource Patterns to Follow

Based on the current codebase, maintain consistency with these established patterns:

```python
# Companies as primary resource
/api/companies/{ticker}/*

# Insights as sub-resource and standalone
/api/companies/{ticker}/insights
/api/insights

# Financial data sub-resources
/api/companies/{ticker}/financials
/api/companies/{ticker}/fundamentals

# Reports as related resource
/api/companies/{ticker}/reports
/api/reports
```

### Naming Guidelines

1. **Use nouns, not verbs**: `/api/companies` not `/api/getCompanies`
2. **Plural resource names**: `/api/insights` not `/api/insight`
3. **Hierarchical relationships**: `/api/companies/{ticker}/insights` for company-specific insights
4. **Consistent parameter names**: Always use `ticker` for company symbols
5. **Query parameters for options**: `?stream=true`, `?type=growth`, `?limit=10`

### FastAPI Endpoints

```python
from fastapi import HTTPException, Query, Path
from typing import Optional, List
from enum import StrEnum

class InsightType(StrEnum):
    GROWTH = "growth"
    EARNINGS = "earnings"
    CASH_FLOW = "cash_flow"

# Resource-based endpoint following naming patterns
@app.get("/api/companies/{ticker}/insights")
async def get_company_insights(
    ticker: str = Path(..., description="Company ticker symbol"),
    type: Optional[InsightType] = Query(None, description="Filter by insight type"),
    limit: Optional[int] = Query(10, ge=1, le=100, description="Number of insights to return"),
    stream: Optional[bool] = Query(False, description="Enable streaming response")
) -> Union[StreamingResponse, List[CompanyInsightDto]]:
    """Get insights for a specific company."""
    # Implementation here

@app.get("/api/companies/{ticker}/insights/{insight_id}")
async def get_company_insight(
    ticker: str = Path(..., description="Company ticker symbol"),
    insight_id: int = Path(..., ge=1, description="Insight ID")
) -> CompanyInsightDto:
    """Get a specific insight for a company."""
    # Implementation here

@app.get("/api/companies/{ticker}/financials/statements")
async def get_financial_statements(
    ticker: str = Path(..., description="Company ticker symbol"),
    period: Optional[str] = Query(None, description="Annual or quarterly period"),
    year: Optional[int] = Query(None, description="Specific year filter")
) -> List[FinancialStatementDto]:
    """Get financial statements for a company."""
    # Implementation here

# Collection endpoints
@app.get("/api/insights")
async def list_insights(
    company: Optional[str] = Query(None, description="Filter by company ticker"),
    type: Optional[InsightType] = Query(None, description="Filter by insight type"),
    limit: Optional[int] = Query(10, ge=1, le=100),
    offset: Optional[int] = Query(0, ge=0)
) -> List[CompanyInsightDto]:
    """List insights across all companies with filtering."""
    # Implementation here
```

### URL Parameter Patterns

**Maintain consistency in parameter naming across endpoints:**

- `ticker` - Always use for company symbols (not `symbol`, `code`, etc.)
- `insight_id` - For specific insight identifiers
- `report_id` - For specific report identifiers
- `type` - For categorical filters (insight_type, report_type)
- `period` - For time-based filters (annual, quarterly)
- `year` - For year-specific data
- `limit` / `offset` - For pagination
- `stream` - For streaming responses

## Environment Configuration

### Required Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/stonkie

# AI Models
OPENAI_API_KEY=your_openai_key
GEMINI_API_KEY=your_gemini_key

# Vector Store
PINECONE_API_KEY=your_pinecone_key

# Application
ENV=local|production
LOG_LEVEL=INFO|DEBUG|WARNING
```

## Testing Guidelines

- Write unit tests for business logic in `services/`
- Mock external API calls and database connections
- Test both sync and async functions appropriately
- Use pytest for testing framework

## Performance Considerations

- Use connection pooling for database operations
- Implement caching for frequently accessed data
- Stream large responses to avoid memory issues
- Add request logging and performance monitoring

## Security Best Practices

- Validate all input parameters
- Use environment variables for sensitive configuration
- Implement proper CORS policies
- Add rate limiting for API endpoints
- Sanitize data before database operations

## Development Workflow

1. **Database Changes**: Create Alembic migration → Update models → Test locally
2. **New Features**: Create service → Add connector if needed → Implement API endpoint
3. **AI Integration**: Use Agent abstraction → Handle streaming responses → Add error handling
4. **Code Review**: Ensure type hints → Check error handling → Validate architecture compliance
