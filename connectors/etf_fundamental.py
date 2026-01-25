import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from connectors.database import SessionLocal
from models.etf_fundamental import ETFFundamental

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ETFHoldingDto:
    name: str
    weight_percent: float


@dataclass(frozen=True)
class ETFSectorAllocationDto:
    sector: str
    weight_percent: float


@dataclass(frozen=True)
class ETFCountryAllocationDto:
    country: str
    weight_percent: float


@dataclass(frozen=True)
class ETFFundamentalDto:
    isin: str
    ticker: str | None
    name: str
    fund_provider: str
    fund_size_millions: float | None
    ter_percent: float
    replication_method: str
    distribution_policy: str
    fund_currency: str
    domicile: str
    launch_date: str | None  # ISO format YYYY-MM-DD
    index_tracked: str
    holdings: list[ETFHoldingDto]
    sector_allocation: list[ETFSectorAllocationDto]
    country_allocation: list[ETFCountryAllocationDto]
    source_url: str | None = None
    updated_at: str | None = None  # ISO datetime string
    created_at: str | None = None  # ISO datetime string


class ETFFundamentalConnector:
    """Connector for ETF fundamental data operations."""

    def _to_dict(self, model_instance: ETFFundamental) -> dict[str, Any]:
        """Convert SQLAlchemy model to dictionary, handling datetime fields."""
        result = {}
        for c in inspect(model_instance).mapper.column_attrs:
            value = getattr(model_instance, c.key)
            if isinstance(value, datetime):
                value = value.isoformat()
            result[c.key] = value
        return result

    def _model_to_dto(self, etf: ETFFundamental) -> ETFFundamentalDto:
        """Convert ETFFundamental model to ETFFundamentalDto."""
        metadata = etf.core_metadata or {}

        # Convert holdings array
        holdings = [ETFHoldingDto(name=h["name"], weight_percent=h["weight_percent"]) for h in (etf.holdings or [])]

        # Convert sector allocation array
        sector_allocation = [
            ETFSectorAllocationDto(sector=s["sector"], weight_percent=s["weight_percent"])
            for s in (etf.sector_allocation or [])
        ]

        # Convert country allocation array
        country_allocation = [
            ETFCountryAllocationDto(country=c["country"], weight_percent=c["weight_percent"])
            for c in (etf.country_allocation or [])
        ]

        return ETFFundamentalDto(
            isin=etf.isin,
            ticker=etf.ticker,
            name=metadata.get("name"),
            fund_provider=etf.fund_provider,
            fund_size_millions=metadata.get("fund_size_millions"),
            ter_percent=metadata.get("ter_percent"),
            replication_method=metadata.get("replication_method"),
            distribution_policy=metadata.get("distribution_policy"),
            fund_currency=metadata.get("fund_currency"),
            domicile=metadata.get("domicile"),
            launch_date=metadata.get("launch_date"),
            index_tracked=metadata.get("index_tracked"),
            holdings=holdings,
            sector_allocation=sector_allocation,
            country_allocation=country_allocation,
            source_url=etf.source_url,
            updated_at=etf.updated_at.isoformat() if etf.updated_at else None,
            created_at=etf.created_at.isoformat() if etf.created_at else None,
        )

    def get_by_isin(self, isin: str) -> ETFFundamentalDto | None:
        """Retrieve ETF fundamental data by ISIN."""
        with SessionLocal() as db:
            etf = db.query(ETFFundamental).filter(ETFFundamental.isin == isin).first()
            if not etf:
                return None
            return self._model_to_dto(etf)

    def get_by_ticker(self, ticker: str) -> ETFFundamentalDto | None:
        """Retrieve ETF fundamental data by ticker symbol."""
        with SessionLocal() as db:
            etf = db.query(ETFFundamental).filter(ETFFundamental.ticker == ticker).first()
            if not etf:
                return None
            return self._model_to_dto(etf)

    def get_by_provider(self, provider: str) -> list[ETFFundamentalDto]:
        """Retrieve all ETFs by fund provider (e.g., 'iShares')."""
        with SessionLocal() as db:
            etfs = db.query(ETFFundamental).filter(ETFFundamental.fund_provider == provider).all()
            return [self._model_to_dto(etf) for etf in etfs]

    def get_all(self) -> list[ETFFundamentalDto]:
        """Retrieve all ETF fundamental data."""
        with SessionLocal() as db:
            etfs = db.query(ETFFundamental).all()
            return [self._model_to_dto(etf) for etf in etfs]

    def upsert(self, data: dict[str, Any]) -> ETFFundamentalDto:
        """
        Insert or update ETF fundamental data.

        Args:
            data: Dictionary with ETF data matching test_etf_scraper.py output format
                  {
                      "isin": "IE00B5BMR087",
                      "ticker": "SXR8",
                      "name": "iShares Core S&P 500 UCITS ETF (Acc)",
                      "fund_provider": "iShares",
                      "fund_size_millions": 55570,
                      "ter_percent": 0.07,
                      "replication_method": "Physical (Full replication)",
                      "distribution_policy": "Accumulating",
                      "fund_currency": "USD",
                      "domicile": "IE",
                      "launch_date": "2010-05-17",
                      "index_tracked": "S&P 500",
                      "holdings": [...],
                      "sector_allocation": [...],
                      "country_allocation": [...],
                      "source_url": "https://www.justetf.com/..."
                  }

        Returns:
            ETFFundamentalDto: The created/updated ETF data
        """
        isin = data["isin"]

        # Extract fund_provider for indexed column
        fund_provider = data.get("fund_provider", "Unknown")

        # Build metadata JSON (exclude arrays and indexed fields)
        metadata = {
            "name": data.get("name"),
            "fund_size_millions": data.get("fund_size_millions"),
            "ter_percent": data.get("ter_percent"),
            "replication_method": data.get("replication_method"),
            "distribution_policy": data.get("distribution_policy"),
            "fund_currency": data.get("fund_currency"),
            "domicile": data.get("domicile"),
            "launch_date": data.get("launch_date"),
            "index_tracked": data.get("index_tracked"),
        }

        # Extract arrays
        holdings = data.get("holdings", [])
        sector_allocation = data.get("sector_allocation", [])
        country_allocation = data.get("country_allocation", [])

        with SessionLocal() as db:
            try:
                # Check if record exists
                existing_etf = (
                    db.query(ETFFundamental).filter(ETFFundamental.isin == isin).with_for_update(nowait=False).first()
                )

                if existing_etf:
                    # Update existing record
                    existing_etf.ticker = data.get("ticker")
                    existing_etf.fund_provider = fund_provider
                    existing_etf.core_metadata = metadata
                    existing_etf.holdings = holdings
                    existing_etf.sector_allocation = sector_allocation
                    existing_etf.country_allocation = country_allocation
                    existing_etf.source_url = data.get("source_url")
                    logger.info(f"Updated ETF fundamental data for ISIN {isin}")
                else:
                    # Create new record
                    new_etf = ETFFundamental(
                        isin=isin,
                        ticker=data.get("ticker"),
                        fund_provider=fund_provider,
                        core_metadata=metadata,
                        holdings=holdings,
                        sector_allocation=sector_allocation,
                        country_allocation=country_allocation,
                        source_url=data.get("source_url"),
                    )
                    db.add(new_etf)
                    logger.info(f"Created new ETF fundamental data for ISIN {isin}")

                db.commit()

                # Retrieve and return as DTO
                etf = db.query(ETFFundamental).filter(ETFFundamental.isin == isin).first()
                return self._model_to_dto(etf)

            except IntegrityError as e:
                db.rollback()
                logger.error(f"IntegrityError upserting ETF {isin}: {e}")
                raise

    def delete_by_isin(self, isin: str) -> bool:
        """Delete ETF fundamental data by ISIN. Returns True if deleted, False if not found."""
        with SessionLocal() as db:
            etf = db.query(ETFFundamental).filter(ETFFundamental.isin == isin).first()
            if not etf:
                return False
            db.delete(etf)
            db.commit()
            logger.info(f"Deleted ETF fundamental data for ISIN {isin}")
            return True
