from models.company_financial import CompanyFinancials
from models.company_financial_statement import CompanyFinancialStatement
from connectors.database import SessionLocal
from typing import List

class CompanyFinancialConnector:
    def get_company_revenue_data(self, ticker: str) -> List[CompanyFinancials]:
        """Get company revenue data using a fresh session for each request"""
        with SessionLocal() as db:
            return db.query(CompanyFinancials)\
                .filter(CompanyFinancials.company_symbol == ticker.upper())\
                .order_by(CompanyFinancials.year.desc())

    def get_company_financial_statements(self, ticker: str) -> List[CompanyFinancialStatement]:
        """Get company financial statements using a fresh session for each request"""
        with SessionLocal() as db:
            return db.query(CompanyFinancialStatement)\
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())\
                .order_by(CompanyFinancialStatement.period_end_year.desc()).all()

    def get_company_tickers_having_financial_data(self) -> List[str]:
        """Get company tickers having financial data using a fresh session for each request"""
        with SessionLocal() as db:
            return [row[0] for row in db.query(CompanyFinancialStatement.company_symbol).distinct().all()]
