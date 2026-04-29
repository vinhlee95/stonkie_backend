from __future__ import annotations

from services.analyze_retrieval.source_policy import (
    DISCARDS,
    FI_EXTENSION_TIER_1,
    FI_EXTENSION_TIER_2,
    GLOBAL_TIER_1,
    GLOBAL_TIER_2,
    VN_TIER_1,
    VN_TIER_2,
    Market,
)


def build_chat_goggle(market: Market) -> str:
    if market == "VN":
        tier_1 = set(VN_TIER_1)
        tier_2 = set(VN_TIER_2)
    elif market == "FI":
        tier_1 = set(GLOBAL_TIER_1) | set(FI_EXTENSION_TIER_1)
        tier_2 = set(GLOBAL_TIER_2) | set(FI_EXTENSION_TIER_2)
    else:
        tier_1 = set(GLOBAL_TIER_1)
        tier_2 = set(GLOBAL_TIER_2)

    tier_1_clean = sorted(domain for domain in tier_1 if not domain.startswith("*."))
    tier_2_clean = sorted(domain for domain in tier_2 if domain not in tier_1)

    lines = [f"$boost=4,site={domain}" for domain in tier_1_clean]
    lines.extend(f"$boost=2,site={domain}" for domain in tier_2_clean)
    lines.extend(f"$discard={domain}" for domain in sorted(DISCARDS))
    return "\n".join(lines)
