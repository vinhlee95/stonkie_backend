from sqlalchemy import inspect


def test_users_table_created_by_migration(test_engine):
    inspector = inspect(test_engine)

    assert "users" in inspector.get_table_names()
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert set(columns) == {"id", "google_sub", "email", "name", "avatar_url", "created_at", "last_login_at"}
    assert columns["google_sub"]["nullable"] is False
    assert columns["email"]["nullable"] is False
    assert columns["name"]["nullable"] is True
    assert columns["avatar_url"]["nullable"] is True
    uniques = inspector.get_unique_constraints("users")
    assert {"name": "uq_users_google_sub", "column_names": ["google_sub"]} in [
        {"name": u["name"], "column_names": u["column_names"]} for u in uniques
    ]
