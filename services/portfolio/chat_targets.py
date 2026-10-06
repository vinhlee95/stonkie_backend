"""Which holdings a Portfolio chat question is about, for focusing the news search."""

import re
from typing import Literal

# Holdings searched per question: the focus holding(s), or the biggest movers.
MAX_SEARCH_HOLDINGS = 2
# First words of company names that are ordinary words ("General Motors"), so not a mention.
GENERIC_NAME_WORDS = {
    "advanced", "alpha", "american", "applied", "bank", "british", "canadian", "china", "digital",
    "eastern", "energy", "first", "general", "global", "international", "national", "new", "northern",
    "public", "royal", "southern", "the", "united", "western",
}  # fmt: skip


def base_symbol(ticker: str) -> str:
    return ticker.split(".")[0]


def _brands(name: str | None) -> set[str]:
    """Distinctive forms of a company name's first word: "Coca-Cola" → {"CocaCola", "Coca", "Cola"},
    "Amazon.com," → {"Amazoncom", "Amazon"}. Generic words and short pieces are dropped."""
    first = (name or "").split(" ")[0]
    candidates = {re.sub(r"\W", "", first), *re.split(r"\W+", first)}
    return {c for c in candidates if len(c) >= 4 and c.lower() not in GENERIC_NAME_WORDS}


def mentioned_holdings(question: str, rows: list[dict]) -> list[dict]:
    """Holdings named in the question: by ticker (case-sensitive, with or without exchange suffix;
    1-2 letter tickers only as "$A") or by a distinctive first word of the company name
    ("Tesla, Inc." → "tesla", "Coca-Cola" → "cocacola"; case-insensitive)."""

    def found(term: str, text: str, flags: int = 0) -> bool:
        return re.search(rf"(?<![\w.]){re.escape(term)}(?!\w)", text, flags) is not None

    def found_brand(brand: str) -> bool:
        # Optional possessive: "Nokia's" names Nokia.
        pattern = rf"(?<![\w.]){re.escape(brand)}(?:['’]s)?(?!\w)"
        return re.search(pattern, joined, re.IGNORECASE) is not None

    # Join hyphenated words so "Coca-Cola" in the question matches the "CocaCola" brand.
    joined = re.sub(r"(?<=\w)-(?=\w)", "", question)
    matches = []
    for row in rows:
        tickers = {row["ticker"], base_symbol(row["ticker"])}
        by_ticker = any(found(t, question) if len(t) > 2 else found(f"${t}", question) for t in tickers)
        by_name = any(found_brand(brand) for brand in _brands(row.get("name")))
        if by_ticker or by_name:
            matches.append(row)
    return matches


SearchMode = Literal["named", "movers", "question"]

# Questions about price moves; anything else unnamed (macro, rates, sectors) is searched as asked.
_MOVE_WORDS = re.compile(
    r"\b(mov(e|es|ed|ing)|up|down|drop(s|ped)?|fell|fall(s|ing)?|rose|gain(s|ed)?|lost|loss(es)?|jump(s|ed)?"
    r"|slid|rall(y|ied)|plung(e|ed)|surg(e|ed)|tank(ed)?|today|this week)\b",
    re.IGNORECASE,
)


def search_targets(question: str, rows: list[dict], scope_ticker: str | None) -> tuple[list[dict], SearchMode]:
    """What to search news for: the focus holding or holdings named in the question; for an unnamed
    question about moves, the biggest movers; otherwise ([], "question") to search the question itself."""
    if scope_ticker:
        return [r for r in rows if r["ticker"] == scope_ticker], "named"
    named = mentioned_holdings(question, rows)
    if named:
        return named[:MAX_SEARCH_HOLDINGS], "named"
    if not _MOVE_WORDS.search(question):
        return [], "question"
    movers = sorted(
        (r for r in rows if r.get("day_change") is not None), key=lambda r: abs(r["day_change"]), reverse=True
    )
    return movers[:MAX_SEARCH_HOLDINGS], "movers"
