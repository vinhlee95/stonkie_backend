import importlib
from unittest.mock import patch

import services.company
from connectors.database import Base


def test_importing_company_service_does_not_create_tables():
    # Schema is owned by Alembic; import-time create_all created `users` before its migration ran (DuplicateTable).
    with patch.object(Base.metadata, "create_all") as create_all:
        importlib.reload(services.company)

    create_all.assert_not_called()
