from models.company_financial import CompanyFinancials
from connectors.database import SessionLocal

class CompanyFinancialConnector:
  def __init__(self) -> None:
    self.db = SessionLocal()

  def get_company_revenue_data(self, ticker: str):
    return self.db.query(CompanyFinancials).filter(CompanyFinancials.company_symbol == ticker.upper()).order_by(CompanyFinancials.year.desc())