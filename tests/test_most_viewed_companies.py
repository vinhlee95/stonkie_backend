from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_get_most_viewed_companies_success():
    """Test successful retrieval of most viewed companies"""
    response = client.get("/api/companies/most-viewed")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "data" in data
    assert isinstance(data["data"], list)


def test_get_most_viewed_companies_has_industry_field():
    """Test that each company has an industry field"""
    response = client.get("/api/companies/most-viewed")

    assert response.status_code == 200
    data = response.json()
    companies = data["data"]

    # Check that each company has the industry field
    for company in companies:
        assert "industry" in company
        assert isinstance(company["industry"], str)


def test_get_most_viewed_companies_has_required_fields():
    """Test that each company has all required fields"""
    response = client.get("/api/companies/most-viewed")

    assert response.status_code == 200
    data = response.json()
    companies = data["data"]

    # Check that each company has required fields
    for company in companies:
        assert "name" in company
        assert "ticker" in company
        assert "logo_url" in company
        assert "industry" in company
        assert isinstance(company["name"], str)
        assert isinstance(company["ticker"], str)
        assert isinstance(company["logo_url"], str)
        assert isinstance(company["industry"], str)
