"""
One-off repair: rescale quarterly statements stored in full units / billions back to thousands.

Dry run by default; pass --apply to write.
    python scripts/repair_quarterly_statement_units.py [--ticker AAPL] [--apply]
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from connectors.database import SessionLocal  # noqa: E402
from core.financial_statement_type import FinancialStatementType  # noqa: E402
from core.statement_units import rescale, scale_ratio, unit_multiplier  # noqa: E402
from models.company_financial_statement import CompanyFinancialStatement  # noqa: E402
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        quarterly = db.query(CompanyQuarterlyFinancialStatement)
        annual = db.query(CompanyFinancialStatement)
        if args.ticker:
            quarterly = quarterly.filter(CompanyQuarterlyFinancialStatement.company_symbol == args.ticker.upper())
            annual = annual.filter(CompanyFinancialStatement.company_symbol == args.ticker.upper())
        quarters_by_ticker, annual_by_ticker = defaultdict(list), defaultdict(list)
        for row in quarterly.all():
            quarters_by_ticker[row.company_symbol].append(row)
        for row in annual.all():
            annual_by_ticker[row.company_symbol].append(row)

        fixes = []
        for ticker, rows in sorted(quarters_by_ticker.items()):
            for st in FinancialStatementType:
                field = st.value
                annual_refs = [v for r in annual_by_ticker[ticker] if isinstance(v := getattr(r, field), dict)]
                for row in rows:
                    metrics = getattr(row, field)
                    if not isinstance(metrics, dict):
                        continue
                    refs = [*annual_refs, *(getattr(o, field) for o in rows if o is not row)]
                    refs = [r for r in refs if isinstance(r, dict)]
                    multiplier = unit_multiplier(metrics, refs)
                    if multiplier != 1:
                        fixes.append((row, field, rescale(metrics, multiplier)))
                        print(
                            f"{ticker:10} {row.period_end_quarter:11} {field:17} "
                            f"ratio={scale_ratio(metrics, refs):<10.3g} x{multiplier:g}"
                        )

        print(f"{len(fixes)} mis-scaled statement(s)")
        if not args.apply:
            print("Dry run; pass --apply to write.")
            return
        for row, field, fixed in fixes:
            setattr(row, field, fixed)
        db.commit()
        print("Applied.")


if __name__ == "__main__":
    main()
