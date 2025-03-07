from models.company_financial import CompanyFinancials
from connectors.database import SessionLocal
from typing import List

class CompanyFinancialConnector:
    def get_company_revenue_data(self, ticker: str) -> List[CompanyFinancials]:
        """Get company revenue data using a fresh session for each request"""
        with SessionLocal() as db:
            return db.query(CompanyFinancials)\
                .filter(CompanyFinancials.company_symbol == ticker.upper())\
                .order_by(CompanyFinancials.year.desc())