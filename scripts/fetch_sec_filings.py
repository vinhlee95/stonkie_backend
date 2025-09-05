#!/usr/bin/env python3
"""
SEC Filings Fetcher

Simplified script with a single function to fetch SEC filings.

Usage:
    from scripts.fetch_sec_filings import get_sec_filings
    filings = get_sec_filings("AAPL", "annually", 3)
"""

import requests
import json
import time
from typing import Dict, List, Optional
from datetime import datetime


def get_sec_filings(ticker: str, period_type: str, limit: int) -> List[Dict]:
    """
    Get SEC filings for a given ticker symbol.
    
    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL')
        period_type: "annually" or "quarterly"
        limit: Maximum number of filings to return
        
    Returns:
        List of filing objects with structure:
        - time: timestamp of the report (ISO 8601 format)
        - period: "annually" or "quarterly"
        - type: "10K" for all filings
        - URL: the first AI-readable HTML URL
    """
    fetcher = _SECFilingsFetcher()
    
    # Map period_type to filing type
    filing_type = "10-K" if period_type == "annually" else "10-Q"
    
    return fetcher.get_filings_list(ticker, filing_type, limit) or []


class _SECFilingsFetcher:
    """Fetches SEC filings for public companies"""
    
    def __init__(self):
        # SEC requires a User-Agent header with contact information
        self.headers = {
            'User-Agent': 'Stock Analysis Tool contact@example.com',
            'Accept-Encoding': 'gzip, deflate',
            'Host': 'data.sec.gov'
        }
        self.base_url = 'https://data.sec.gov'
        
    def get_company_cik(self, ticker: str) -> Optional[str]:
        """
        Get the CIK (Central Index Key) for a given ticker symbol
        
        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            
        Returns:
            CIK string with leading zeros, or None if not found
        """
        # Known mappings for major companies (fallback)
        known_ciks = {
            'AAPL': '0000320193',
            'MSFT': '0000789019', 
            'GOOGL': '0001652044',
            'AMZN': '0001018724',
            'TSLA': '0001318605',
            'META': '0001326801',
            'NVDA': '0001045810',
            'NFLX': '0001065280',
            'DIS': '0001001039',
            'JPM': '0000019617'
        }
        
        ticker_upper = ticker.upper()
        
        # Check known mappings first
        if ticker_upper in known_ciks:
            print(f"Found CIK {known_ciks[ticker_upper]} for ticker {ticker} (known mapping)")
            return known_ciks[ticker_upper]
        
        try:
            # Try the company tickers endpoint
            url = f'{self.base_url}/files/company_tickers_exchange.json'
            response = requests.get(url, headers=self.headers)
            
            if response.status_code == 200:
                data = response.json()
                
                # The data structure might be different - check the actual structure
                if 'data' in data:
                    for exchange, companies in data['data'].items():
                        for company_data in companies:
                            if company_data.get('ticker', '').upper() == ticker_upper:
                                cik = str(company_data['cik']).zfill(10)
                                print(f"Found CIK {cik} for ticker {ticker} on exchange {exchange}")
                                return cik
                else:
                    # Alternative structure
                    for key, company_data in data.items():
                        if isinstance(company_data, dict) and company_data.get('ticker', '').upper() == ticker_upper:
                            cik = str(company_data.get('cik_str', company_data.get('cik', ''))).zfill(10)
                            if cik != '0000000000':
                                print(f"Found CIK {cik} for ticker {ticker}")
                                return cik
            
            # Fallback: try the original endpoint
            url = f'{self.base_url}/files/company_tickers.json'
            response = requests.get(url, headers=self.headers)
            
            if response.status_code == 200:
                companies = response.json()
                
                # Search for the ticker
                for company_data in companies.values():
                    if company_data.get('ticker', '').upper() == ticker_upper:
                        cik = str(company_data['cik_str']).zfill(10)
                        print(f"Found CIK {cik} for ticker {ticker}")
                        return cik
                    
            print(f"Ticker {ticker} not found in SEC database")
            return None
            
        except requests.RequestException as e:
            print(f"Error fetching company CIK: {e}")
            print(f"Checking known mappings for {ticker}...")
            
            # Return known mapping if available
            if ticker_upper in known_ciks:
                print(f"Using known CIK {known_ciks[ticker_upper]} for ticker {ticker}")
                return known_ciks[ticker_upper]
            
            return None
    
    def get_filings_metadata(self, cik: str) -> Optional[Dict]:
        """
        Get all filings metadata for a given CIK
        
        Args:
            cik: 10-digit CIK string with leading zeros
            
        Returns:
            Dictionary containing filing metadata, or None if error
        """
        try:
            url = f'{self.base_url}/submissions/CIK{cik}.json'
            print(f"Fetching filings from: {url}")
            
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            return response.json()
            
        except requests.RequestException as e:
            print(f"Error fetching filings metadata: {e}")
            return None
    
    def filter_filings(self, filings_data: Dict, filing_type: Optional[str] = None, 
                      limit: Optional[int] = None) -> List[Dict]:
        """
        Filter and format filings data
        
        Args:
            filings_data: Raw filings metadata from SEC API
            filing_type: Filter by filing type (e.g., '10-K', '10-Q', '8-K')
            limit: Maximum number of filings to return
            
        Returns:
            List of filtered filing dictionaries
        """
        if 'filings' not in filings_data:
            return []
            
        recent_filings = filings_data['filings']['recent']
        
        # Create list of filing dictionaries
        filings = []
        for i in range(len(recent_filings.get('form', []))):
            filing = {
                'form': recent_filings['form'][i],
                'filingDate': recent_filings['filingDate'][i],
                'reportDate': recent_filings['reportDate'][i],
                'acceptanceDateTime': recent_filings['acceptanceDateTime'][i],
                'accessionNumber': recent_filings['accessionNumber'][i],
                'primaryDocument': recent_filings['primaryDocument'][i],
                'primaryDocDescription': recent_filings['primaryDocDescription'][i]
            }
            filings.append(filing)
        
        # Filter by filing type if specified
        if filing_type:
            filings = [f for f in filings if f['form'].upper() == filing_type.upper()]
        
        # Apply limit if specified
        if limit:
            filings = filings[:limit]
            
        return filings
    
    def get_accessible_filing_urls(self, accession_number: str, primary_document: str, cik: str) -> Dict[str, str]:
        """
        Get accessible URLs for SEC filings based on SEC API documentation
        
        Args:
            accession_number: SEC accession number (format: 0000000000-00-000000)
            primary_document: Primary document filename
            cik: Company CIK (10 digits with leading zeros)
            
        Returns:
            Dictionary with accessible URLs for different formats
        """
        # Remove dashes from accession number for directory structure
        accession_clean = accession_number.replace('-', '')
        cik_int = int(cik)  # Remove leading zeros for URL path
        
        # Base paths
        edgar_base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_clean}"
        data_base = f"https://data.sec.gov/Archives/edgar/data/{cik_int}/{accession_clean}"
        
        urls = {}
        
        # 1. Primary document (usually HTML for 10-K, 10-Q)
        if primary_document:
            urls['primary_html'] = f"{edgar_base}/{primary_document}"
            urls['primary_data'] = f"{data_base}/{primary_document}"
        
        # 2. Complete submission text file (most reliable for AI reading)
        urls['complete_text'] = f"{edgar_base}/{accession_number}.txt"
        urls['complete_text_data'] = f"{data_base}/{accession_number}.txt"
        
        # 3. Filing summary (R1.htm) - often contains main content
        urls['filing_summary'] = f"{edgar_base}/R1.htm"
        
        # 4. Document index
        urls['document_index'] = f"{edgar_base}/{accession_number}-index.htm"
        
        # 5. Interactive data viewer (for human viewing)
        urls['interactive_viewer'] = f"https://www.sec.gov/cgi-bin/viewer?action=view&cik={cik}&accession_number={accession_number.replace('-', '')}"
        
        # 6. XBRL files (if available) - for structured data
        urls['xbrl_instance'] = f"{edgar_base}/{primary_document.replace('.htm', '_htm.xml')}" if primary_document and primary_document.endswith('.htm') else None
        
        return urls

    def get_best_readable_urls(self, filing: Dict) -> List[str]:
        """
        Get the best URLs for AI model reading, prioritizing .htm files
        
        Args:
            filing: Filing dictionary with accessible URLs
            
        Returns:
            List of URLs ordered by best for AI reading (prioritizes .htm)
        """
        if 'accessibleUrls' not in filing:
            return [filing.get('documentUrl', '')]
        
        urls = filing['accessibleUrls']
        readable_urls = []
        
        # Priority order for AI reading:
        # 1. Primary HTML document (.htm files) - BEST for AI
        # 2. Filing summary (R1.htm)
        # 3. Complete text file (fallback)
        
        # First, add .htm files (primary HTML documents)
        for key in ['primary_html', 'primary_data']:
            if key in urls and urls[key] and urls[key].endswith('.htm'):
                readable_urls.append(urls[key])
        
        # Then add filing summary (.htm)
        if 'filing_summary' in urls and urls['filing_summary']:
            readable_urls.append(urls['filing_summary'])
        
        # Finally, add text files as fallback
        for key in ['complete_text', 'complete_text_data']:
            if key in urls and urls[key]:
                readable_urls.append(urls[key])
        
        return readable_urls

    def get_htm_urls_only(self, filing: Dict) -> List[str]:
        """
        Get only the .htm URLs that are suitable for AI reading
        
        Args:
            filing: Filing dictionary with accessible URLs
            
        Returns:
            List of .htm URLs only
        """
        if 'accessibleUrls' not in filing:
            return []
        
        urls = filing['accessibleUrls']
        htm_urls = []
        
        # Collect all .htm URLs
        for key, url in urls.items():
            if url and url.endswith('.htm'):
                htm_urls.append(url)
        
        return htm_urls

    def filter_filings_with_htm(self, filings: List[Dict]) -> List[Dict]:
        """
        Filter filings to only include those with accessible .htm documents
        
        Args:
            filings: List of filing dictionaries
            
        Returns:
            Filtered list containing only filings with .htm documents
        """
        htm_filings = []
        
        for filing in filings:
            htm_urls = self.get_htm_urls_only(filing)
            if htm_urls:
                filing['htmUrls'] = htm_urls
                filing['bestHtmUrl'] = htm_urls[0]  # Use the first .htm URL as the best
                htm_filings.append(filing)
        
        return htm_filings

    def validate_url_accessibility(self, url: str) -> bool:
        """
        Check if a URL is accessible (returns 200 status)
        
        Args:
            url: URL to check
            
        Returns:
            True if accessible, False otherwise
        """
        try:
            response = requests.head(url, headers=self.headers, timeout=10)
            return response.status_code == 200
        except:
            return False

    def get_accessible_urls_only(self, filing: Dict) -> List[str]:
        """
        Get only the URLs that are actually accessible
        
        Args:
            filing: Filing dictionary
            
        Returns:
            List of verified accessible URLs
        """
        if 'accessibleUrls' not in filing:
            return []
        
        accessible = []
        best_urls = self.get_best_readable_urls(filing)
        
        print(f"  Checking URL accessibility for {filing['form']}...")
        
        for url in best_urls[:3]:  # Check top 3 URLs to avoid too many requests
            if self.validate_url_accessibility(url):
                accessible.append(url)
                print(f"    ✓ Accessible: {url}")
            else:
                print(f"    ✗ Not accessible: {url}")
        
        return accessible
    
    def fetch_filings_for_ticker(self, ticker: str, filing_type: Optional[str] = None, 
                                limit: int = 10) -> Optional[List[Dict]]:
        """
        Complete workflow to fetch filings for a ticker
        
        Args:
            ticker: Stock ticker symbol
            filing_type: Filter by filing type (optional)
            limit: Maximum number of filings to return
            
        Returns:
            List of filing dictionaries with URLs, or None if error
        """
        print(f"Fetching SEC filings for ticker: {ticker}")
        
        # Get CIK for ticker
        cik = self.get_company_cik(ticker)
        if not cik:
            return None
        
        # Add rate limiting to be respectful to SEC servers
        time.sleep(0.1)
        
        # Get filings metadata
        filings_data = self.get_filings_metadata(cik)
        if not filings_data:
            return None
        
        # Filter and format filings
        filings = self.filter_filings(filings_data, filing_type, limit)
        
        # Add accessible URLs for each filing
        for filing in filings:
            filing['accessibleUrls'] = self.get_accessible_filing_urls(
                filing['accessionNumber'], 
                filing['primaryDocument'], 
                cik
            )
            # Get the best readable URLs (prioritizing .htm)
            filing['readableUrls'] = self.get_best_readable_urls(filing)
            # Get only .htm URLs
            filing['htmUrls'] = self.get_htm_urls_only(filing)
            # Set the best URL (prefer .htm)
            filing['documentUrl'] = filing['htmUrls'][0] if filing['htmUrls'] else (filing['readableUrls'][0] if filing['readableUrls'] else '')
        
        # Optionally filter to only include filings with .htm documents
        # filings = self.filter_filings_with_htm(filings)
        
        return filings
    
    def get_recent_10k_filings(self, ticker: str, limit: int = 3) -> Optional[List[Dict]]:
        """
        Get the most recent 10-K filings for a ticker
        
        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of 10-K filings to return (default: 3)
            
        Returns:
            List of most recent 10-K filing dictionaries with metadata and URLs
        """
        print(f"Fetching most recent 10-K filings for {ticker.upper()}...")
        return self.fetch_filings_for_ticker(ticker, filing_type='10-K', limit=limit)
    
    def get_recent_10k_simple(self, ticker: str, limit: int = 3) -> Optional[List[Dict]]:
        """
        Get the most recent 10-K filings for a ticker in simplified format
        
        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of 10-K filings to return (default: 3)
            
        Returns:
            List of simplified filing objects with time, period, type, and URL
        """
        filings = self.get_recent_10k_filings(ticker, limit)
        if not filings:
            return None
        
        simplified_filings = []
        for filing in filings:
            # Get the first AI-readable HTML URL
            html_url = ""
            if 'htmUrls' in filing and filing['htmUrls']:
                html_url = filing['htmUrls'][0]
            elif 'readableUrls' in filing and filing['readableUrls']:
                # Fallback to first readable URL if no HTML URLs
                html_url = filing['readableUrls'][0]
            
            # Convert reportDate to timestamp format (ISO 8601)
            report_date = filing['reportDate']
            try:
                # Convert YYYY-MM-DD to ISO 8601 timestamp
                from datetime import datetime
                dt = datetime.strptime(report_date, '%Y-%m-%d')
                timestamp = dt.isoformat() + 'Z'
            except:
                # Fallback to original date string if parsing fails
                timestamp = report_date
            
            simplified_filing = {
                "time": timestamp,
                "period": "annually",  # 10-K is always annual
                "type": "10K",
                "URL": html_url
            }
            simplified_filings.append(simplified_filing)
        
        return simplified_filings
    
    def get_filings_list(self, ticker: str, filing_type: str = "10-K", limit: int = 3) -> Optional[List[Dict]]:
        """
        Get filings for a ticker and return as a Python list of objects with specified properties
        
        Args:
            ticker: Stock ticker symbol
            filing_type: Type of filing to fetch (default: "10-K")
            limit: Maximum number of filings to return (default: 3)
            
        Returns:
            Python list of objects, each with properties:
            - time: timestamp of the report (ISO 8601 format)
            - period: "annually" for 10-K, "quarterly" for 10-Q  
            - type: "10K" for all filings for now
            - URL: the first AI-readable HTML URL
        """
        # Fetch the filings
        filings = self.fetch_filings_for_ticker(ticker, filing_type, limit)
        if not filings:
            return None
        
        result_list = []
        for filing in filings:
            # Get the first AI-readable HTML URL
            html_url = ""
            if 'htmUrls' in filing and filing['htmUrls']:
                html_url = filing['htmUrls'][0]
            elif 'readableUrls' in filing and filing['readableUrls']:
                # Fallback to first readable URL if no HTML URLs
                html_url = filing['readableUrls'][0]
            
            # Convert reportDate to timestamp format (ISO 8601)
            report_date = filing['reportDate']
            try:
                # Convert YYYY-MM-DD to ISO 8601 timestamp
                from datetime import datetime
                dt = datetime.strptime(report_date, '%Y-%m-%d')
                timestamp = dt.isoformat() + 'Z'
            except:
                # Fallback to original date string if parsing fails
                timestamp = report_date
            
            # Determine period based on filing type
            if filing_type.upper() == "10-K":
                period = "annually"
            elif filing_type.upper() == "10-Q":
                period = "quarterly"
            else:
                # Default based on original form type from SEC
                period = "annually" if filing['form'].upper() == "10-K" else "quarterly"
            
            filing_obj = {
                "time": timestamp,
                "period": period,
                "type": "10K",  # As requested, "10K" for all for now
                "URL": html_url
            }
            result_list.append(filing_obj)
        
        return result_list
    
    def save_filings_to_file(self, filings: List[Dict], filename: str):
        """Save filings data to JSON file"""
        with open(filename, 'w') as f:
            json.dump(filings, f, indent=2)
        print(f"Saved {len(filings)} filings to {filename}")
    
    def get_filing_content(self, document_url: str, format_type: str = 'text') -> Optional[str]:
        """
        Fetch the actual content of a filing document
        
        Args:
            document_url: URL to the filing document
            format_type: 'text', 'html', or 'raw'
            
        Returns:
            Document content as string, or None if error
        """
        try:
            response = requests.get(document_url, headers=self.headers)
            response.raise_for_status()
            
            content = response.text
            
            if format_type == 'text':
                # Basic HTML tag removal for text extraction
                import re
                # Remove HTML tags
                content = re.sub(r'<[^>]+>', '', content)
                # Clean up whitespace
                content = re.sub(r'\s+', ' ', content).strip()
                
            elif format_type == 'html':
                # Return HTML as-is
                pass
            elif format_type == 'raw':
                # Return raw content
                pass
                
            return content
            
        except requests.RequestException as e:
            print(f"Error fetching document content: {e}")
            return None

    def get_best_readable_url_single(self, filing: Dict) -> str:
        """
        Get the single best URL for AI model reading
        
        Args:
            filing: Filing dictionary with accessibleUrls
            
        Returns:
            Best single URL for reading
        """
        readable_urls = self.get_best_readable_urls(filing)
        return readable_urls[0] if readable_urls else filing.get('documentUrl', '')

# Example usage:
# from scripts.fetch_sec_filings import get_sec_filings
# filings = get_sec_filings("AAPL", "annually", 3)
# print(filings)
