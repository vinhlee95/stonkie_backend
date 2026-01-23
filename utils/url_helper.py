"""URL extraction and validation utilities."""

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def extract_first_url(text: str) -> Optional[str]:
    """
    Extract the first URL from text.

    Args:
        text: Input text potentially containing URLs

    Returns:
        First URL found, or None if no URLs present
    """
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    matches = re.findall(url_pattern, text)
    return matches[0] if matches else None


def is_sec_filing_url(url: str) -> bool:
    """
    Check if a URL is from SEC.gov (Securities and Exchange Commission).

    SEC filing URLs often return 403 errors for automated requests
    but can be accessed via Google Search.

    Args:
        url: The URL to check

    Returns:
        True if URL is from sec.gov domain, False otherwise
    """
    if not url:
        return False
    return "sec.gov" in url.lower()


def validate_pdf_url(url: str) -> tuple[bool, str]:
    """
    Validate that a URL is accessible and points to a PDF document.

    Args:
        url: The URL to validate

    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if URL is valid and accessible PDF, False otherwise
        - error_message: Empty string if valid, descriptive error message if invalid
    """
    # Basic URL format validation
    if not url or not url.strip():
        return False, "URL is empty"

    if not url.startswith(("http://", "https://")):
        return False, "URL must use HTTP or HTTPS protocol"

    # Check if URL is accessible and returns PDF content
    try:
        # First try HEAD request (faster, doesn't download content)
        response = requests.head(url, timeout=10, allow_redirects=True)
        response.raise_for_status()

        # Check Content-Type header
        content_type = response.headers.get("content-type", "").lower()

        # If HEAD doesn't return content-type, try GET with stream
        if "application/pdf" not in content_type:
            response = requests.get(url, stream=True, timeout=10, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()

            # Close the stream immediately as we only need headers
            response.close()

            if "application/pdf" not in content_type:
                # Fallback: check URL extension
                if url.lower().endswith(".pdf"):
                    logger.warning(f"URL {url} has .pdf extension but Content-Type is {content_type}")
                    return True, ""  # Allow if extension is .pdf
                return False, "URL does not point to a PDF file"

        # Check Content-Length if available (20MB limit)
        content_length = response.headers.get("content-length")
        if content_length:
            size_mb = int(content_length) / (1024 * 1024)
            if size_mb > 20:
                return False, f"PDF file is too large ({size_mb:.1f}MB). Maximum size is 20MB"

        return True, ""

    except requests.Timeout:
        return False, "Request timed out while accessing URL"
    except requests.ConnectionError:
        return False, "Could not connect to URL. Please check the URL and try again"
    except requests.HTTPError as e:
        status_code = e.response.status_code
        if status_code == 404:
            return False, "URL not found (404)"
        elif status_code == 403:
            return False, "Access denied (403). The URL may require authentication"
        elif status_code >= 500:
            return False, f"Server error ({status_code}). Please try again later"
        else:
            return False, f"HTTP error: {status_code}"
    except Exception as e:
        logger.error(f"Error validating PDF URL {url}: {e}")
        return False, f"Error accessing URL: {str(e)}"


def strip_url_from_text(text: str, url: str) -> str:
    """
    Remove a URL from text, cleaning up extra whitespace.

    Args:
        text: Original text containing the URL
        url: URL to remove

    Returns:
        Text with URL removed and whitespace normalized
    """
    # Replace URL with empty string
    cleaned = text.replace(url, "")

    # Clean up multiple spaces
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Trim leading/trailing whitespace
    return cleaned.strip()
