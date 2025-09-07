from typing import List, Dict, Optional
from dataclasses import dataclass
from connectors.database import SessionLocal
from models.company_financial_statement import CompanyFinancialStatement
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement
from scripts.fetch_sec_filings import get_sec_filings
import logging

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class FilingDto:
    url: str
    period_end_year: int

@dataclass(frozen=True)
class CompanyFilingsConnector:
    
    def get_filings_by_period(self, ticker: str, period: str) -> List[FilingDto]:
        """
        Get 10K filings for a company by period (annual or quarter)
        
        Args:
            ticker: Stock ticker symbol
            period: "annual" or "quarter"
            
        Returns:
            List of FilingDto objects containing URL and period_end_year
        """
        try:
            # Validate period
            if period not in ["annual", "quarter"]:
                raise ValueError(f"Invalid period: {period}. Must be 'annual' or 'quarter'")
            
            # Try to get from database first
            db_filings = self._get_filings_from_database(ticker, period)
            if db_filings:
                logger.info(f"Found {len(db_filings)} filings in database for {ticker} ({period})")
                return db_filings
            
            # If not found in database, fetch from SEC
            logger.info(f"No filings found in database for {ticker} ({period}), fetching from SEC")
            return self._get_filings_from_sec(ticker, period)
            
        except Exception as e:
            logger.error(f"Error getting filings for {ticker} ({period}): {e}")
            return []
    
    def _get_filings_from_database(self, ticker: str, period: str) -> List[FilingDto]:
        """Get filings from database tables"""
        session = SessionLocal()
        filings = []
        
        try:
            if period == "annual":
                # Query annual filings from CompanyFinancialStatement
                records = session.query(CompanyFinancialStatement).filter(
                    CompanyFinancialStatement.company_symbol == ticker.upper(),
                    CompanyFinancialStatement.filing_10k_url.isnot(None),
                    CompanyFinancialStatement.filing_10k_url != ""
                ).order_by(CompanyFinancialStatement.period_end_year.desc()).limit(10).all()
                
                for record in records:
                    filings.append(FilingDto(
                        url=record.filing_10k_url,
                        period_end_year=record.period_end_year
                    ))
            
            elif period == "quarter":
                # For quarterly data, we need to extract year from period_end_quarter
                # Assuming period_end_quarter format is like "Q1 2023", "Q2 2023", etc.
                records = session.query(CompanyQuarterlyFinancialStatement).filter(
                    CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper()
                ).order_by(CompanyQuarterlyFinancialStatement.period_end_quarter.desc()).limit(10).all()
                
                for record in records:
                    # Extract year from period_end_quarter string
                    try:
                        # Assuming format like "Q1 2023" or "2023-Q1"
                        quarter_str = record.period_end_quarter
                        if " " in quarter_str:
                            year = int(quarter_str.split(" ")[-1])
                        elif "-" in quarter_str:
                            year = int(quarter_str.split("-")[0])
                        else:
                            # Try to extract 4-digit year
                            import re
                            year_match = re.search(r'\d{4}', quarter_str)
                            year = int(year_match.group()) if year_match else 0
                        
                        # For quarterly data, we don't have filing URLs in the database yet
                        # So this will return empty list and fall back to SEC API
                        # This is a placeholder for when we add filing URLs to quarterly table
                        
                    except (ValueError, AttributeError):
                        logger.warning(f"Could not extract year from period: {quarter_str}")
                        continue
            
        except Exception as e:
            logger.error(f"Database error getting filings for {ticker}: {e}")
        finally:
            session.close()
        
        return filings
    
    def _get_filings_from_sec(self, ticker: str, period: str) -> List[FilingDto]:
        """Get filings from SEC API"""
        try:
            # Map period to SEC API format
            period_type = "annually" if period == "annual" else "quarterly"
            
            # Fetch filings from SEC
            sec_filings = get_sec_filings(ticker, period_type, 10)
            
            if not sec_filings:
                logger.warning(f"No SEC filings found for {ticker} ({period_type})")
                return []
            
            filings = []
            for filing in sec_filings:
                try:
                    # Extract year from the time field (ISO format: "YYYY-MM-DD...")
                    time_str = filing.get('time', '')
                    if time_str:
                        year = int(time_str[:4])
                        url = filing.get('URL', '')
                        if url:
                            filings.append(FilingDto(
                                url=url,
                                period_end_year=year
                            ))
                except (ValueError, TypeError):
                    logger.warning(f"Could not extract year from filing time: {filing.get('time')}")
                    continue
            
            logger.info(f"Fetched {len(filings)} filings from SEC for {ticker} ({period_type})")
            return filings
            
        except Exception as e:
            logger.error(f"Error fetching from SEC for {ticker} ({period}): {e}")
            return []
