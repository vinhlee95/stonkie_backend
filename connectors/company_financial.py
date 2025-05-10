from copy import deepcopy
from models.company_financial import CompanyFinancials
from models.company_financial_statement import CompanyFinancialStatement
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement
from connectors.database import SessionLocal
from typing import Any, List
from sqlalchemy.inspection import inspect
from datetime import datetime

class CompanyFinancialConnector:
    @classmethod
    def to_dict(cls, model_instance) -> dict[str, Any]:
        """Convert SQLAlchemy model to dictionary, handling datetime fields"""
        result = {}
        for c in inspect(model_instance).mapper.column_attrs:
            value = getattr(model_instance, c.key)
            # Convert datetime objects to ISO format strings
            if isinstance(value, datetime):
                value = value.isoformat()
            result[c.key] = value
        return result

    def _to_dict(self, model_instance) -> dict[str, Any]:
        """Convert SQLAlchemy model to dictionary, handling datetime fields"""
        result = {}
        for c in inspect(model_instance).mapper.column_attrs:
            value = getattr(model_instance, c.key)
            # Convert datetime objects to ISO format strings
            if isinstance(value, datetime):
                value = value.isoformat()
            result[c.key] = value
        return result

    def get_company_statement_by_type(self, financial_statement_dict: dict[str, Any], statement_type: 'str') -> dict[str, Any]:
        # Get intended income statement data
        # Deep copy the income statement data
        data = deepcopy(financial_statement_dict)
        
        if statement_type == 'income_statement':
            data.pop('balance_sheet', None)
            data.pop('cash_flow', None)
        elif statement_type == 'balance_sheet':
            data.pop('income_statement', None)
            data.pop('cash_flow', None)
        elif statement_type == 'cash_flow':
            data.pop('income_statement', None)
            data.pop('balance_sheet', None)
            
        return data


    def get_company_revenue_data(self, ticker: str) -> List[CompanyFinancials]:
        """Get company revenue data using a fresh session for each request"""
        with SessionLocal() as db:
            return db.query(CompanyFinancials)\
                .filter(CompanyFinancials.company_symbol == ticker.upper())\
                .order_by(CompanyFinancials.year.desc())

    def get_company_financial_statements(self, ticker: str) -> List[CompanyFinancialStatement]:
        with SessionLocal() as db:
            return db.query(CompanyFinancialStatement)\
                .filter(CompanyFinancialStatement.company_symbol == ticker.upper())\
                .order_by(CompanyFinancialStatement.period_end_year.desc()).all()

    def get_company_quarterly_financial_statements(self, ticker: str) -> List[CompanyQuarterlyFinancialStatement]:
        with SessionLocal() as db:
            return db.query(CompanyQuarterlyFinancialStatement)\
                .filter(CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper())\
                .all()

    def get_annual_income_statements(self, ticker: str) -> list[dict[str, Any]]:
            annual_financial_statements = self.get_company_financial_statements(ticker)
            results = []
            for item in annual_financial_statements:
                data = self.get_company_statement_by_type(self._to_dict(item), 'income_statement')
                results.append(data)

            return results

    def get_quarterly_income_statements(self, ticker: str) -> list[dict[str, Any]]:
            quarterly_financial_statements = self.get_company_quarterly_financial_statements(ticker)

            results = []
            for item in quarterly_financial_statements:
                data = self.get_company_statement_by_type(self._to_dict(item), 'income_statement')
                results.append(data)

            return results

    def get_company_tickers_having_financial_data(self) -> List[str]:
        with SessionLocal() as db:
            return [row[0] for row in db.query(CompanyFinancialStatement.company_symbol).distinct().all()]
