from enum import StrEnum
from models.company_insight import CompanyInsight
from connectors.database import SessionLocal
from datetime import datetime
from typing import Any
from sqlalchemy.inspection import inspect
from dataclasses import dataclass

class InsightType(StrEnum):
    GROWTH = "growth"
    EARNINGS = "earnings"
    CASH_FLOW = "cash_flow"

@dataclass(frozen=True)
class CompanyInsightDto:
    id: int
    company_symbol: str
    slug: str
    insight_type: str
    title: str
    content: str
    created_at: datetime
    thumbnail_url: str

@dataclass(frozen=True)
class CreateCompanyInsightDto:
    company_symbol: str
    slug: str
    insight_type: str
    title: str
    content: str
    thumbnail_url: str

class CompanyInsightConnector:
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

    def get_all_by_ticker(self, ticker: str) -> list[CompanyInsightDto]:
        with SessionLocal() as db:
            insights = db.query(CompanyInsight).filter(CompanyInsight.company_symbol == ticker).all()
            return [CompanyInsightDto(**self._to_dict(insight)) for insight in insights]

    def get_by_type(self, ticker: str, insight_type: InsightType) -> list[CompanyInsightDto]:
        with SessionLocal() as db:
            insights = db.query(CompanyInsight).filter(CompanyInsight.company_symbol == ticker, CompanyInsight.insight_type == insight_type).all()
            return [CompanyInsightDto(**self._to_dict(insight)) for insight in insights]

    def create_one(self, data: CreateCompanyInsightDto) -> CompanyInsightDto:
        with SessionLocal() as db:
            company_insight = CompanyInsight(**data.__dict__)
            db.add(company_insight)
            db.commit()
            db.refresh(company_insight)

            return CompanyInsightDto(**self._to_dict(company_insight))

    def get_by_slug(self, slug: str) -> CompanyInsightDto | None:
        with SessionLocal() as db:
            company_insight = db.query(CompanyInsight).filter(CompanyInsight.slug == slug).first()
            return CompanyInsightDto(**self._to_dict(company_insight)) if company_insight else None
