# CompanySpecificFinanceHandler Flow Documentation

## Overview

The `CompanySpecificFinanceHandler` processes company-specific financial questions with AI-driven dynamic section generation and optimized data fetching.

## Main Flow

```mermaid
flowchart TD
    Start([User Question]) --> Handle[handle method]
    Handle --> ClassifyData[Classify Data Requirement]
    
    ClassifyData --> CheckReq{Data Requirement?}
    CheckReq -->|NONE| FetchBasic[Fetch No Data]
    CheckReq -->|BASIC| FetchBasic[Fetch Fundamental Only]
    CheckReq -->|DETAILED| ClassifyPeriod[Classify Period Requirement]
    
    ClassifyPeriod --> Parallel[Execute in Parallel]
    
    Parallel --> DimAnalysis[Analyze Question Dimensions]
    Parallel --> FetchData[Fetch Optimized Financial Data]
    
    DimAnalysis --> ParseJSON[Parse Dimension JSON]
    ParseJSON --> Validate[Validate Section Titles]
    Validate -->|Valid| UseDynamic[Use AI-Generated Sections]
    Validate -->|Invalid| UseFallback[Use Fallback Sections]
    
    FetchBasic --> BuildContext
    UseDynamic --> BuildContext[Build Financial Context]
    UseFallback --> BuildContext
    FetchData --> BuildContext
    
    BuildContext --> GenerateAnswer[Generate LLM Answer]
    GenerateAnswer --> Stream[Stream Response Chunks]
    Stream --> RelatedQ[Generate Related Questions]
    RelatedQ --> End([Complete])
```

## Data Requirement Classification

```mermaid
flowchart LR
    Question[Question Input] --> Classifier{Question Classifier}
    
    Classifier -->|General Info| NONE[FinancialDataRequirement.NONE]
    Classifier -->|Basic Metrics| BASIC[FinancialDataRequirement.BASIC]
    Classifier -->|Detailed Analysis| DETAILED[FinancialDataRequirement.DETAILED]
    
    NONE --> NoData[No financial data fetched]
    BASIC --> FundData[Fetch fundamental data only]
    DETAILED --> PeriodClass[Period Requirement Classifier]
    
    PeriodClass --> Annual[Annual Statements]
    PeriodClass --> Quarterly[Quarterly Statements]
    PeriodClass --> Both[Both Annual & Quarterly]
```

## Dimension Analysis Flow (AI-Generated Sections)

```mermaid
flowchart TD
    Start[_analyze_question_dimensions] --> BuildPrompt[Build Dimension Prompt]
    BuildPrompt --> Examples[Include Few-Shot Examples]
    Examples --> CallAI[Call Gemini 2.5 Flash Lite]
    CallAI -->|3s timeout| Collect[Collect Response Chunks]
    
    Collect --> Parse[_parse_dimension_json]
    
    Parse --> TryDirect{Try json.loads}
    TryDirect -->|Success| ReturnJSON[Return Parsed Data]
    TryDirect -->|Fail| TryMarkdown{Try Markdown Extraction}
    
    TryMarkdown -->|Success| ReturnJSON
    TryMarkdown -->|Fail| TryAny{Try Any JSON Pattern}
    
    TryAny -->|Success| ReturnJSON
    TryAny -->|Fail| ReturnNone[Return None]
    
    ReturnJSON --> Validate[_validate_section_titles]
    
    Validate --> CheckCount{Exactly 2 sections?}
    CheckCount -->|No| Invalid[Return False]
    CheckCount -->|Yes| CheckTitles{Valid titles?}
    
    CheckTitles -->|Max 6 words| CheckChars{Valid characters?}
    CheckTitles -->|Too long| Invalid
    
    CheckChars -->|Only a-zA-Z0-9 &-| CheckPoints{Valid focus points?}
    CheckChars -->|Invalid chars| Invalid
    
    CheckPoints -->|2-4 points, non-empty| Valid[Return True]
    CheckPoints -->|Invalid| Invalid
    
    Valid --> UseSections[Use AI-Generated Sections]
    Invalid --> Fallback[Use Fallback Sections]
    ReturnNone --> Fallback
```

## Parallel Execution Pattern (DETAILED queries only)

```mermaid
sequenceDiagram
    participant Handler as CompanySpecificFinanceHandler
    participant Classifier as QuestionClassifier
    participant DimGen as Dimension Generator
    participant DataOpt as Data Optimizer
    participant LLM as Gemini 2.5 Flash Lite
    participant DB as Database/APIs
    
    Handler->>Classifier: classify_data_requirement()
    Classifier-->>Handler: DETAILED
    
    Handler->>Classifier: classify_period_requirement()
    Classifier-->>Handler: period_requirement
    
    par Parallel Execution
        Handler->>DimGen: _analyze_question_dimensions()
        DimGen->>LLM: Generate section titles
        LLM-->>DimGen: JSON response
        DimGen->>DimGen: Parse & Validate
        DimGen-->>Handler: dimension_sections
    and
        Handler->>DataOpt: fetch_optimized_data()
        DataOpt->>DB: Fetch statements
        DB-->>DataOpt: financial_data
        DataOpt-->>Handler: (fundamental, annual, quarterly)
    end
    
    Handler->>Handler: _build_financial_context()
    Handler->>Handler: Generate & Stream Answer
```

## Context Building Logic

```mermaid
flowchart TD
    Build[_build_financial_context] --> CheckReq{Data Requirement}
    
    CheckReq -->|NONE| NonePrompt["Base context only<br/>(~150 words)<br/>Use Google Search"]
    
    CheckReq -->|BASIC| BasicPrompt["Base context +<br/>Fundamental data<br/>(~150 words)"]
    
    CheckReq -->|DETAILED| CheckSections{Valid Dimension<br/>Sections?}
    
    CheckSections -->|Yes| BuildDynamic["Build Dynamic Sections<br/>Section 1 title + focus points<br/>Section 2 title + focus points"]
    CheckSections -->|No| BuildFallback["Use Fallback Sections<br/>Financial Performance<br/>Strategic Positioning"]
    
    BuildDynamic --> DetailedPrompt
    BuildFallback --> DetailedPrompt["Detailed Context:<br/>Summary (~80 words)<br/>Section 1 (~160 words)<br/>Section 2 (~160 words)<br/>Total: ~400 words"]
    
    NonePrompt --> Return[Return Prompt String]
    BasicPrompt --> Return
    DetailedPrompt --> Return
```

## Section Structure (DETAILED Analysis)

```mermaid
graph TB
    subgraph "3-Section Response Structure"
        Summary["**Executive Summary**<br/>(~80 words)<br/>Preview key findings"]
        
        Section1["**AI-Generated Title 1**<br/>(~160 words)<br/>Focus points:<br/>- Point 1<br/>- Point 2<br/>- Point 3"]
        
        Section2["**AI-Generated Title 2**<br/>(~160 words)<br/>Focus points:<br/>- Point 1<br/>- Point 2<br/>- Point 3"]
        
        Sources["Sources:<br/>Annual Report 2023, Quarterly Statement Q1 2024"]
    end
    
    Summary --> Section1
    Section1 --> Section2
    Section2 --> Sources
```

## Key Features

### 🎯 Dynamic Section Generation
- AI analyzes each question to generate 2 relevant section titles
- 3-second timeout with fallback to generic sections
- Validates title length (max 6 words) and structure

### ⚡ Parallel Execution
- Dimension analysis runs concurrently with data fetching
- Reduces latency by ~1-2 seconds for DETAILED queries

### 📊 Tiered Data Fetching
- **NONE**: No financial data
- **BASIC**: Fundamental data only
- **DETAILED**: Full statements (annual/quarterly/both)

### ✅ Robust JSON Parsing
1. Direct `json.loads()`
2. Markdown code block extraction
3. Regex pattern matching
4. Fallback to default sections

### 📝 Word Allocation
- Summary: 80 words
- Section 1: 160 words
- Section 2: 160 words
- **Total: ~400 words** (configurable)

## Error Handling

```mermaid
flowchart LR
    Error[Exception Occurs] --> CheckType{Exception Type}
    
    CheckType -->|Timeout| LogTimeout[Log timeout error]
    CheckType -->|Parse Fail| LogParse[Log parse error]
    CheckType -->|Validation Fail| LogValidation[Log validation error]
    CheckType -->|Data Fetch Fail| LogFetch[Log & raise error]
    CheckType -->|Other| LogGeneral[Log general error]
    
    LogTimeout --> Fallback[Use Fallback Sections]
    LogParse --> Fallback
    LogValidation --> Fallback
    LogFetch --> UserError[Return error message to user]
    LogGeneral --> UserError
    
    Fallback --> Continue[Continue with analysis]
```

## Performance Profiling Points

The handler tracks execution time at key stages:
- **Time to First Token (TTFT)**: Time from prompt to first response chunk
- **Model Generation**: Total LLM generation time
- **Related Questions**: Time to generate follow-up questions
- **Total Handler Time**: End-to-end execution time

All metrics are logged with `logger.info()` for observability.
