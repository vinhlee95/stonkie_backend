"""Example review-map spec: PR #61 (off-topic + duplicate web sources). Copy its shape, not its content."""

H = "services/question_analyzer/handlers_v2.py"
R = "services/analyze_retrieval/retrieval.py"
REL = "services/analyze_retrieval/relevance.py"
RANK = "services/analyze_retrieval/ranking.py"
OBS = "services/analyze_retrieval/observability.py"
CMP = "services/question_analyzer/comparison_handler_v2.py"
ECMP = "services/etf_question_analyzer/comparison_handler_v2.py"
T_REL = "tests/services/analyze_retrieval/test_relevance.py"
T_RET = "tests/services/analyze_retrieval/test_retrieval.py"
T_RANK = "tests/services/analyze_retrieval/test_ranking.py"
T_ROUTE = "tests/services/question_analyzer/test_handlers_v2_routing.py"
T_CMP = "tests/services/question_analyzer/test_comparison_handler_v2.py"
T_EFAKE = "tests/services/etf_question_analyzer/etf_fakes.py"

PR = {
    "number": 61,
    "title": "fix(analyze-v2): drop off-topic + duplicate web sources (Q5, WP9)",
    "url": "https://app.graphite.com/github/pr/vinhlee95/stonkie_backend/61",
    "branch": "fix/source-relevance",
    "commit": "9b8543b",
    "additions": 727,
    "deletions": 22,
    "files": 13,
    "problem": "Web sources shown with analyze-v2 answers included articles about other companies "
    "(a Meta transcript for AAPL, Coca-Cola Consolidated dividends for KO) and the same story twice "
    "under different URLs.",
    "results": [
        {"label": "Off-topic sources, live runs", "before": "7", "after": "1"},
        {"label": "Duplicate titles in sources event", "before": "4", "after": "0"},
        {"label": "Retrieval CPU per question", "before": "", "after": "+0.3 ms"},
        {"label": "Name lookup cost (hidden behind Brave)", "before": "1.87 s", "after": "1.90 s"},
    ],
    "causes_title": "Six root causes, and where each is fixed",
    "asks": [
        "What's the riskiest part of this PR?",
        "Which findings would you fix before merging?",
        "Explain the order of filters in retrieve_for_analyze.",
    ],
    "causes": [
        {
            "text": "No topic check: any candidate overlapping the question's words could be picked.",
            "node": "off_topic",
        },
        {
            "text": "Dedupe by canonical URL only, so syndicated copies at other URLs got through.",
            "node": "title_dedupe",
        },
        {"text": "Sibling companies (Coca-Cola Consolidated, NASDAQ: COKE) counted as KO.", "node": "siblings"},
        {"text": "The speculative-search path passed the ticker as company_name.", "node": "handler"},
        {"text": "Comparison handlers concatenated per-ticker sources with no dedupe.", "node": "comparison"},
        {"text": "Hard-coded apple / apples stop-words in passage ranking.", "node": "passage_rank"},
    ],
    "summary_for_claude": """PR #61 in the Stonkie backend (FastAPI, Python): fix(analyze-v2): drop off-topic + duplicate web sources.
Root causes: (1) no topic check on Brave candidates; (2) dedupe only by canonical URL, syndicated copies at other URLs listed twice; (3) sibling companies like "Coca-Cola Consolidated (NASDAQ: COKE)" filling KO sources; (4) the speculative-search path in CompanySpecificFinanceHandlerV2 passed the ticker as company_name; (5) comparison handlers flattened per-ticker sources without dedupe; (6) hard-coded apple/apples stop-words in ranking.py.
Fix, in services/analyze_retrieval (pure helpers in new relevance.py): filter_off_topic keeps a candidate when its title names the company (alias or ticker), its URL path has the ticker, or its snippets mention it 2+ times; sibling listings don't count; stocks only; falls back to unfiltered when fewer than 2 remain. dedupe_by_title on normalized title with publisher tag stripped, keeping the trusted/fuller copy. Passage ranking excludes company name/ticker words. Handler looks up the real name via CompanyConnector.get_by_ticker in parallel with Brave (resolve_company_name callback). Comparison handlers use merge_sources. log_retrieval adds title_duplicates_dropped and filtered_off_topic.
Measured: off-topic 7 -> 1 and duplicate titles 4 -> 0 in 27 live runs; latency unchanged; +0.3 ms CPU per question.
Known limitations: homonyms (Amazon rainforest), sibling companies with no exchange-qualified listing in snippets, brand names that differ from the legal name.
Open review findings: FUNC-1 aliases only from legal name; FUNC-2 ASCII-only normalization; FUNC-3 publisher-suffix regex strips any trailing " - X" <= 40 chars; TEST-1 no test of which copy _is_better_copy keeps; TEST-2 _company_name None/empty untested; FUNC-4 title dedupe before stale filter; FUNC-5 unique_candidates now counts after title dedupe; ARCH-1 thread pool + callback split across handler and library; ARCH-2 maybe split dedupe helpers into dedupe.py; TEST-3/4/5 _company_terms wiring, ticker edge cases, ETF comparison merge test.""",
}

NODES = [
    {
        "id": "handler",
        "title": "Company question starts a search",
        "where": "CompanySpecificFinanceHandlerV2.handle",
        "tag": "changed",
        "summary": "When the router decides a web search is needed, the handler starts Brave in a background "
        "thread before the DB fetch. Before this PR it passed the ticker as `company_name`, so retrieval never "
        "had the real name to filter on. It now also passes `company_name_lookup`, a callback that reads the "
        "name with `CompanyConnector.get_by_ticker`.",
        "checks": [
            "`ticker_norm` is lower-case. `get_by_ticker` upper-cases its input (connectors/company.py:251), so the lookup still matches.",
            "`company_name=ticker_norm.upper()` is still passed as the fallback. `company_aliases` treats a name equal to the ticker as unknown, so if the lookup fails the filter switches itself off.",
            "TEST-2: `_company_name` returning None for a missing company or an empty name has no test.",
            "Comparison handlers are not touched here: they already pass a real `company_name` from their DB fetch.",
        ],
        "asks": [
            "What happens if get_by_ticker raises a DB error?",
            "Why pass a lambda instead of the name itself?",
        ],
        "code_refs": [
            ("hunk", H, [4], "handle(): pass the lookup callback"),
            ("hunk", H, [3], "_company_name(): new helper"),
        ],
        "test_refs": [("new", T_ROUTE, ["test_company_specific_search_looks_up_the_company_name_for_relevance"])],
    },
    {
        "id": "retrieve_routed",
        "title": "Name lookup runs beside Brave",
        "where": "_retrieve_routed",
        "tag": "changed",
        "summary": "`_retrieve_routed` opens a one-worker thread pool, submits the lookup, and hands the "
        "future's `.result` to retrieval as `resolve_company_name`. Retrieval calls it only after the Brave "
        "searches return, so the DB read overlaps the network wait instead of adding to it.",
        "checks": [
            "Error path: if Brave raises, leaving the `with ThreadPoolExecutor(...)` block waits for the lookup to finish (`shutdown(wait=True)`). A failed search can take up to the lookup time longer (0.2 to 1 s in the PR's measurements).",
            "A lookup exception is re-raised by `.result()` inside retrieval, where `_resolved_company_name` catches it.",
            "ARCH-1 (low): the pool lives in the handler while the callback contract lives in the library.",
            "`finished.set()` still runs in `finally`, so the thinking-status timing is unaffected.",
        ],
        "asks": [
            "Does the with-block really block when Brave fails?",
            "Would asyncio.gather be simpler here?",
        ],
        "code_refs": [("hunk", H, [0, 1, 2], "_retrieve_routed(): pool + callback")],
        "test_refs": [],
    },
    {
        "id": "brave",
        "title": "Brave searches",
        "where": "_execute_searches",
        "tag": "context",
        "summary": "Runs each router query against Brave and pools the candidates. Unchanged. Brave takes 1.8 "
        "to 2.8 s, which is what hides the name lookup.",
        "checks": [],
        "asks": [],
        "code_refs": [],
        "test_refs": [],
    },
    {
        "id": "resolve",
        "title": "Swap in the real company name",
        "where": "retrieve_for_analyze → _resolved_company_name",
        "tag": "changed",
        "summary": "Once the searches return, retrieval calls `resolve_company_name()`. A non-empty name "
        "replaces `company_name`. A failure is logged as a warning and the given name is kept. This is also "
        "where `company_scoped` is set: true for stocks, false for ETFs.",
        "checks": [
            "The resolved name only feeds the off-topic filter and passage ranking. The Brave queries were already built from the router's queries, so they don't change.",
            "`_resolved_company_name` catches every `Exception`. Check that breadth is what you want.",
            "The new parameter is optional and defaults to None, so every other caller behaves as before.",
        ],
        "asks": ["Which callers of retrieve_for_analyze pass resolve_company_name?"],
        "code_refs": [
            ("hunk", R, [0, 1], "imports"),
            ("hunk", R, [2, 3], "retrieve_for_analyze(): new parameter, resolve after search"),
            ("new", R, ["_resolved_company_name"]),
        ],
        "test_refs": [
            (
                "new",
                T_RET,
                [
                    "test_retrieve_for_analyze_resolves_the_company_name_for_the_filter",
                    "test_retrieve_for_analyze_keeps_the_given_name_when_the_lookup_fails",
                ],
            )
        ],
    },
    {
        "id": "url_dedupe",
        "title": "Dedupe by canonical URL",
        "where": "best_candidate_by_canonical_url",
        "tag": "context",
        "summary": "Existing step: one candidate per canonical URL, preferring the canonical copy. Syndicated "
        "copies at different URLs got past it (root cause 2), which is why title dedupe now follows.",
        "checks": [],
        "asks": [],
        "code_refs": [],
        "test_refs": [],
    },
    {
        "id": "title_dedupe",
        "title": "Dedupe by title",
        "where": "dedupe_by_title · _is_better_copy",
        "tag": "new",
        "summary": "Candidates with the same normalized title collapse into one. The title is lower-cased, "
        'punctuation is dropped, and a trailing publisher tag is removed (" - Bloomberg", " | Reuters", '
        '" By Investing.com"). The survivor keeps the first copy\'s position. A later copy replaces it if it is '
        "trusted and the first isn't, or, at equal trust, if it has more text.",
        "checks": [
            'FUNC-3: the suffix regex strips any trailing " - X" up to 40 characters, so "Q3 earnings - Apple" and "Q3 earnings - Meta" get the same key.',
            "FUNC-4: this runs before the stale filter. A stale trusted copy can beat a fresh mirror and then be dropped as stale, losing both.",
            "TEST-1: no test checks which copy `_is_better_copy` keeps.",
            "A title that normalizes to empty is kept as-is and never deduped.",
        ],
        "asks": [
            "Give me two real-looking titles that would wrongly collapse.",
            "How would I fix FUNC-4 with the smallest change?",
        ],
        "code_refs": [
            ("new", REL, ["_NON_ALNUM_RE"], "_NON_ALNUM_RE"),
            ("new", REL, ["_TITLE_PUBLISHER_SUFFIX_RE", "_normalize"]),
            ("new", REL, ["normalize_title", "dedupe_by_title"]),
            ("new", R, ["_is_better_copy"]),
            ("hunk", R, [4], "retrieve_for_analyze(): wiring (title dedupe, then stale, then off-topic)"),
        ],
        "test_refs": [
            (
                "new",
                T_REL,
                [
                    "test_normalize_title_strips_publisher_tags_and_punctuation",
                    "test_dedupe_by_title_keeps_one_copy_in_place_and_swaps_in_the_better_one",
                ],
            ),
            (
                "new",
                T_RET,
                [
                    "_titled",
                    "_company_candidates",
                    "test_retrieve_for_analyze_drops_off_topic_and_duplicate_title_sources",
                ],
            ),
            ("hunk", T_RET, [0], "_candidate(): titles made unique so old tests don't collapse"),
        ],
    },
    {
        "id": "stale",
        "title": "Drop stale results",
        "where": "_filter_stale_candidates",
        "tag": "context",
        "summary": "Existing freshness filter, unchanged. Note it now runs after title dedupe (see FUNC-4).",
        "checks": [],
        "asks": [],
        "code_refs": [],
        "test_refs": [],
    },
    {
        "id": "off_topic",
        "title": "Drop off-topic candidates",
        "where": "filter_off_topic · is_about_company",
        "tag": "new",
        "note": "stocks only",
        "summary": "Keeps a candidate when its title names the company (an alias or the ticker), its URL path "
        "contains the ticker, or its snippets mention the company at least twice. If fewer than 2 candidates "
        "would survive, the full list is returned unfiltered. ETF searches skip this step because their "
        "answers often come from index articles that never name the fund.",
        "checks": [
            'Title needs 1 mention, body needs 2 (`_MIN_BODY_MENTIONS`). The JPM "Turkey cuts interest rates" article, which cites JPMorgan once, is why.',
            "`_ticker_symbol` drops symbols shorter than 2 characters, so Ford (F) is matched by alias only, never by ticker.",
            "Ticker matching in text is case-sensitive on the upper-case symbol. Common-word tickers (ALL, NOW, IT) could match ordinary capitalized words in titles.",
            "The URL check needs a symbol of 3+ characters, so KO never matches on URL.",
            "The under-2 fallback is all-or-nothing: with 1 on-topic result, every off-topic one stays in.",
            "Known misses: homonyms (Amazon rainforest) and brand names that differ from the legal name (FUNC-1).",
        ],
        "asks": [
            "Walk me through is_about_company for a Reuters article titled 'Turkey cuts rates'.",
            "Is the all-or-nothing fallback the right call?",
        ],
        "code_refs": [
            ("lines", REL, 1, 13, "relevance.py: new module, imports", "+"),
            ("new", REL, ["_MIN_BODY_MENTIONS", "_MIN_ON_TOPIC"]),
            ("new", REL, ["_ticker_symbol", "_alias_pattern"]),
            ("new", REL, ["_count_mentions", "_url_has_ticker", "is_about_company", "filter_off_topic"]),
            ("hunk", R, [4], "retrieve_for_analyze(): stocks only"),
        ],
        "test_refs": [
            ("lines", T_REL, 1, 13, "test_relevance.py: imports", "+"),
            (
                "new",
                T_REL,
                [
                    "_candidate",
                    "test_filter_off_topic_drops_articles_about_other_companies",
                    "test_filter_off_topic_needs_two_body_mentions_without_a_title_mention",
                    "test_filter_off_topic_keeps_filings_named_by_ticker_in_the_url",
                    "test_filter_off_topic_keeps_everything_when_fewer_than_two_would_remain",
                    "test_filter_off_topic_is_a_no_op_without_a_real_company_name",
                ],
            ),
            ("new", T_RET, ["test_retrieve_for_analyze_does_not_topic_filter_etf_searches"]),
        ],
    },
    {
        "id": "aliases",
        "title": "Company aliases",
        "where": "company_aliases",
        "tag": "new",
        "summary": 'Turns the legal name into the strings the filter looks for. It drops a leading "The" and '
        "trailing legal-form words (Inc, Corp, Holdings, Class A), then keeps the core name plus each "
        'distinctive word of 3+ letters that isn\'t on the generic-word list. "Ford Motor Company" gives '
        '{"ford motor", "ford"}. An empty set (no name, or the name is just the ticker) turns the filter off.',
        "checks": [
            'FUNC-2: `_normalize` keeps only a-z and 0-9, so "Wärtsilä" becomes "w rtsil" and accented article text won\'t match.',
            'FUNC-1: brand names (Google, TSMC, "JP Morgan") are never aliases.',
            '"Bank of America Corporation" gives only {"bank of america"}: every single word is generic. Check that\'s acceptable.',
            "`_NAME_SUFFIXES` contains single letters a, b, c for share classes. They are only stripped from the end, and the last remaining word is never stripped.",
        ],
        "asks": [
            "What aliases would 'Alphabet Inc. Class A' produce?",
            "Which S&P 500 names would end up with weak aliases?",
        ],
        "code_refs": [
            ("new", REL, ["_NAME_SUFFIXES", "_GENERIC_NAME_WORDS"]),
            ("new", REL, ["company_aliases"]),
        ],
        "test_refs": [("new", T_REL, ["test_company_aliases"])],
    },
    {
        "id": "siblings",
        "title": "Ignore sibling listings",
        "where": "_sibling_words · _LISTING_RE",
        "tag": "new",
        "summary": '"Coca-Cola Consolidated (NASDAQ: COKE)" contains "Coca-Cola", so it would count as a KO '
        "mention. `_sibling_words` finds exchange-qualified listings for another symbol, looks up to 80 "
        "characters back for one of our aliases followed by a capitalized word, and records that word "
        '("consolidated"). `_count_mentions` then skips alias matches followed by a sibling word. '
        '"Alphabet Inc. (NASDAQ: GOOG)" adds nothing, because the next word (Inc) is a legal suffix.',
        "checks": [
            'Only the exchanges in `_LISTING_RE` count. A sibling with no "(EXCH: SYM)" in its snippet slips through (listed limitation).',
            "Sibling words are collected per candidate across title and body together, then applied to both.",
            'Plain "Coca-Cola" mentions in the same article still count. A COKE article that also says "Coca-Cola" twice on its own passes.',
            "The regex in `_sibling_words` is dense. Try it on your own example in the Ask box.",
        ],
        "asks": [
            "Trace _sibling_words on: 'Coca-Cola FEMSA, S.A.B. de C.V. (NYSE: KOF) reported…'",
            "Could this regex be slow on long snippets?",
        ],
        "code_refs": [
            ("new", REL, ["_LISTING_RE"]),
            ("new", REL, ["_sibling_words"]),
        ],
        "test_refs": [
            (
                "new",
                T_REL,
                [
                    "test_filter_off_topic_drops_a_sibling_company_sharing_the_name",
                    "test_filter_off_topic_treats_another_share_class_as_the_same_company",
                ],
            )
        ],
    },
    {
        "id": "rank_select",
        "title": "Rank and pick sources",
        "where": "rank_for_chat · _select_candidates",
        "tag": "context",
        "summary": "Existing trust and domain ranking, unchanged. It now ranks the filtered list, and "
        "`ranked_urls` in the log reflects the list after the off-topic filter.",
        "checks": [],
        "asks": [],
        "code_refs": [],
        "test_refs": [],
    },
    {
        "id": "passage_rank",
        "title": "Rank passages without the name",
        "where": "rank_passages_for_chat · _company_terms",
        "tag": "changed",
        "summary": "Passages are scored by word overlap with the question. On a company search every passage "
        'contains the company name, so those words dominated the score. The hard-coded "apple" / "apples" '
        "stop-words patched that for one company. Now the alias words and ticker (`_company_terms`) are removed "
        "from the question's terms, for stocks only.",
        "checks": [
            "If the company name is the question's only meaningful word (\"Tell me about Apple\"), `query_terms` is empty. Every overlap is then 0 and passages fall back to trusted, source order, then length. That's the same as before for other companies.",
            "TEST-3: the `_company_terms` wiring inside `retrieve_for_analyze` has no direct test.",
            '`_company_terms` adds `ticker.lower()` without the `_ticker_symbol` cleaning, so "BRK.B" adds "brk.b", which never matches a token.',
        ],
        "asks": ["Show me a question where excluding the name makes ranking worse."],
        "code_refs": [
            ("hunk", RANK, [0, 1], "ranking.py: drop apple stop-words, add excluded_terms"),
            ("new", R, ["_company_terms"]),
            ("hunk", R, [5], "retrieve_for_analyze(): pass excluded_terms"),
        ],
        "test_refs": [
            ("hunk", T_RANK, [0], "imports"),
            ("new", T_RANK, ["_analyze_source", "test_rank_passages_for_chat_ignores_excluded_company_terms"]),
        ],
    },
    {
        "id": "log",
        "title": "Log the new counts",
        "where": "log_retrieval",
        "tag": "changed",
        "summary": "`log_retrieval` takes two new optional fields, `title_duplicates_dropped` and "
        "`filtered_off_topic`. The PR's per-request numbers (NVDA 3, KO 4 to 5, F 44 to 64) come from these.",
        "checks": [
            "FUNC-5: `unique_candidates` now counts after title dedupe, so any dashboard comparing it across this deploy will see a step.",
            "Both fields are only added to the payload when not None, matching the existing pattern.",
        ],
        "asks": [],
        "code_refs": [
            ("hunk", OBS, [0, 1], "observability.py"),
            ("hunk", R, [6], "retrieve_for_analyze(): pass the counts"),
        ],
        "test_refs": [("hunk", T_RET, [1], "expected log payload")],
    },
    {
        "id": "comparison",
        "title": "Merge per-ticker sources",
        "where": "merge_sources",
        "tag": "new",
        "summary": "Comparison questions (TSLA vs F) run retrieval once per ticker. The results used to be "
        "concatenated, so one article found for both tickers was listed twice. `merge_sources` keeps ticker "
        "order, skips a source whose id or normalized title was already seen, and keeps passages only for kept "
        "sources, each once. The same change is made in the stock and the ETF comparison handlers.",
        "checks": [
            "Passages of a dropped title-duplicate are dropped too (its id isn't in `seen_ids`). The model only sees the first ticker's copy.",
            "Each per-ticker retrieval also runs the off-topic filter for its own ticker. That's why F as the second ticker dropped 44 to 64 Tesla-only articles.",
            "TEST-5: no test covers the ETF comparison merge.",
        ],
        "asks": ["Could merge_sources drop the only source for the second ticker?"],
        "code_refs": [
            ("new", REL, ["merge_sources"]),
            ("hunk", CMP, [0, 1], "stock comparison handler"),
            ("hunk", ECMP, [0, 1], "ETF comparison handler"),
        ],
        "test_refs": [
            ("new", T_CMP, ["test_an_article_found_for_both_tickers_is_listed_once"]),
            (
                "new",
                T_REL,
                ["_source", "_passage", "test_merge_sources_lists_an_article_found_for_several_tickers_once"],
            ),
            ("hunk", T_EFAKE, [0], "etf_fakes: unique titles so ETF tests don't collapse"),
        ],
    },
]

# Diagram: frames of rows; a row holds one or more node ids side by side ("|" = runs in parallel).
FLOWS = [
    {
        "title": "Company question",
        "frames": [
            {"label": "handlers_v2.py", "rows": [["handler"], ["brave", "retrieve_routed"]], "parallel_row": 1},
            {
                "label": "retrieve_for_analyze()  ·  retrieval.py",
                "rows": [
                    ["resolve"],
                    ["url_dedupe"],
                    ["title_dedupe"],
                    ["stale"],
                    ["off_topic"],
                    ["rank_select"],
                    ["passage_rank"],
                    ["log"],
                ],
                "children": {"off_topic": ["aliases", "siblings"]},
            },
        ],
    },
    {
        "title": "Comparison question (stock and ETF)",
        "frames": [{"label": "comparison_handler_v2.py ×2", "rows": [["comparison"]]}],
        "note": "Calls retrieve_for_analyze once per ticker, then merges.",
    },
]

TOUR = [
    "handler",
    "retrieve_routed",
    "resolve",
    "title_dedupe",
    "aliases",
    "off_topic",
    "siblings",
    "passage_rank",
    "log",
    "comparison",
]
