from io import BytesIO

from pydantic import BaseModel
from pypdf import PdfReader


class PageData(BaseModel):
    page_number: int
    text: str


def get_pdf_content_from_bytes(file_content: bytes) -> list[PageData]:
    """Extract text content from PDF bytes with page numbers.

    Returns:
        list[dict]: List of dictionaries containing page number and text content
    """
    reader = PdfReader(BytesIO(file_content))
    pages_data = []

    for page_num, page in enumerate(reader.pages, 1):
        text_content = page.extract_text()
        pages_data.append(PageData(page_number=page_num, text=text_content))

    return pages_data
