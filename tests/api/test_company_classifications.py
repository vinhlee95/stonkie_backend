import pytest
from sqlalchemy.orm import sessionmaker

from connectors import company as company_connector_module
from connectors.company import CompanyClassificationDto, CompanyConnector
from models.company_fundamental import CompanyFundamental


@pytest.fixture()
def connector(test_engine, monkeypatch):
    # The test DB is stamped past the migration that creates company_fundamental.
    CompanyFundamental.__table__.create(test_engine, checkfirst=True)
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(company_connector_module, "SessionLocal", session_local)
    with session_local() as db:
        db.add_all(
            [
                CompanyFundamental(company_symbol="AAPL", data={"sector": "TECHNOLOGY", "country": "USA"}),
                CompanyFundamental(company_symbol="NODATA", data=None),
                CompanyFundamental(company_symbol="PARTIAL", data={"name": "Partial Inc"}),
            ]
        )
        db.commit()
    yield CompanyConnector()
    CompanyFundamental.__table__.drop(test_engine)


def test_get_classifications_maps_rows_and_omits_unknown_tickers(connector):
    result = connector.get_classifications(["AAPL", "NODATA", "PARTIAL", "MISSING"])

    assert result == {
        "AAPL": CompanyClassificationDto(sector="TECHNOLOGY", country="USA"),
        "NODATA": CompanyClassificationDto(sector="", country=""),
        "PARTIAL": CompanyClassificationDto(sector="", country=""),
    }


def test_get_classifications_empty_list_skips_query(connector):
    assert connector.get_classifications([]) == {}
