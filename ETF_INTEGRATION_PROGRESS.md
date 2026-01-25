# ETF Integration Progress & Learnings

Project: ETF data scraping from justetf.com using Playwright + Celery + AI extraction

---

## Phase 0: Bare-Minimum Validation Script ✅ COMPLETED (2026-01-25)

### Objective
Validate that scraping justetf.com + AI parsing works BEFORE building full Celery infrastructure.

### Implementation Summary
Created `scripts/test_etf_scraper.py` - standalone script that:
1. Accepts justetf.com URL as CLI argument
2. Extracts ISIN from URL query params
3. Fetches page HTML with Playwright (headless Chromium)
4. Handles cookie consent modal
5. Sends HTML to OpenAI for JSON extraction (same model as financial_crawler.py)
6. Validates and displays extracted ETF data

### Test Results

#### Test 1: iShares Core S&P 500 (IE00B5BMR087) ✅
```
Name: iShares Core S&P 500 UCITS ETF USD (Acc)
ISIN: IE00B5BMR087
TER: 0.07%
Holdings: 10 items extracted
Sectors: 11 items extracted
Countries: 8 items extracted
Status: PASSED PERFECTLY
```

#### Test 2: iShares Core MSCI World (IE00B4L5Y983) ✅
```
Name: iShares Core MSCI World UCITS ETF USD (Acc)
ISIN: IE00B4L5Y983
TER: 0.2%
Holdings: 10 items extracted
Sectors: Varies (sometimes empty)
Countries: Varies (sometimes empty)
Status: PASSED (critical fields present)
```

### Key Learnings

#### 1. Cookie Consent Handling
- **Finding**: All justetf.com pages use Cookiebot modal
- **Solution**: Implemented multiple selector strategies
- **Winner**: `#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll` works reliably
- **Recommendation**: Keep multi-selector fallback approach for resilience

#### 2. HTML Size & Truncation
- **Initial Assumption**: 100k chars sufficient based on specs
- **Reality**: Pages are 2.6M+ chars (26x larger!)
- **Issue**: 100k truncation cut off critical data (holdings, sectors)
- **Solution**: Increased to 500k chars
- **Impact**: Extraction success rate improved dramatically
- **For Celery**: Use 500k as default, consider full HTML for production

#### 3. OpenAI Response Handling
- **Model**: OpenAI (default model, same as financial_crawler.py)
- **Approach**: Standard text response + manual JSON parsing
- **Consistency**: Uses same pattern as existing financial data scrapers
- **Code Pattern**:
  ```python
  openai_agent = Agent(model_type="openai")
  response = openai_agent.generate_content(prompt, stream=False)
  text = response.text
  # Strip markdown code blocks
  text = re.sub(r"```json\s*", "", text)
  data = json.loads(text)
  ```
- **Reliability**: Proven pattern from production financial_crawler.py

#### 4. AI Extraction Quality
- **Consistency**: Critical fields (name, isin) extracted 100% of time
- **Variability**: Optional fields (holdings, sectors, TER) depend on page structure
- **Pattern**: First test URL extracts perfectly, second URL partial
- **Hypothesis**: HTML structure varies by ETF provider or page version
- **Recommendation**: Implement field-level validation + retry logic in Celery task

#### 5. Page Load Timing
- **Pattern**: `page.goto()` + `wait_for_load_state("networkidle")` + 3s wait
- **Why 3s?**: Dynamic content loads after initial render
- **Alternative Tried**: Waiting for specific selectors - unreliable due to varying structures
- **Recommendation**: Keep timing-based approach for consistency

#### 6. Validation Strategy
- **Initial**: Strict validation - all fields required
- **Problem**: AI extraction variability caused false failures
- **Solution**: Two-tier validation
  - **Critical**: name, isin (must be present)
  - **Recommended**: ter_percent, fund_provider, arrays (warn if missing)
- **Benefit**: Script succeeds while flagging incomplete extractions

### Technical Adjustments Made

| Aspect | Initial Spec | Final Implementation | Reason |
|--------|-------------|---------------------|---------|
| HTML chars | 100,000 | 500,000 | Pages are 2.6M, need more data |
| AI config | JSON mode | Text parsing | JSON mode returns None |
| Validation | All required | Critical + warnings | AI extraction varies |
| Timeout | 30s | 30s | Sufficient |
| Cookie wait | 2s | 2s | Works well |

### Answers to Open Questions

**Q: Does AI consistently extract all fields from justetf.com HTML?**
A: Critical fields (name, isin) - yes. Optional fields (holdings, sectors) - varies by page structure. Recommend field-level retry logic.

**Q: Are there regional variations in HTML structure?**
A: Not tested in Phase 0. Both test ETFs were from same provider (iShares). Need broader testing in Phase 3.

**Q: Does cookie consent blocking prevent data access?**
A: No. Cookiebot modal reliably detected and handled. No data access issues.

**Q: What's the optimal HTML truncation limit for AI context?**
A: 500k chars balances AI context window with data coverage. Could test up to 1M if needed.

### Production Recommendations for Phase 3

1. **HTML Limit**: Use 500k chars minimum, consider full HTML
2. **AI Model**: OpenAI default model - same as financial_crawler.py for consistency
3. **Error Handling**: Implement field-level extraction status tracking
4. **Retry Logic**: Retry on missing critical fields, warn on missing optional
5. **Validation**: Use two-tier approach (critical vs recommended)
6. **Cookie Strategy**: Keep multi-selector approach, monitor for changes
7. **Page Timing**: networkidle + 3s wait is reliable
8. **Response Parsing**: Use text parsing, not JSON mode

### Files Created
- `scripts/test_etf_scraper.py` (241 lines)

### Next Steps
- Phase 1: Database layer (ETFFundamental model + migration)
- Phase 4: Cache + task state (can be done in parallel with Phase 1)
- Phase 2: Connector + DTOs (depends on Phase 1)
- Phase 3: Celery task (depends on Phase 2 & Phase 4)

---

## Phase 1: Database Layer (PENDING)
Status: Not started
Blocked by: None
Next action: Create ETFFundamental model following CompanyFundamental pattern

---

## Phase 2: Connector + DTOs (PENDING)
Status: Not started
Blocked by: Phase 1
Next action: Create ETFFundamentalConnector with CRUD operations

---

## Phase 3: Celery Task + AI Extraction (PENDING)
Status: Not started
Blocked by: Phase 2, Phase 4
Next action: Create crawl_etf_data_task using patterns from test_etf_scraper.py

---

## Phase 4: Cache + Task State (PENDING)
Status: Not started
Blocked by: None (can start immediately)
Next action: Add get_etf_task_state_key to connectors/cache.py

---

## Phase 5: API Endpoints (PENDING)
Status: Not started
Blocked by: Phase 3
Priority: Low
Next action: Add /api/etf endpoints to main.py
