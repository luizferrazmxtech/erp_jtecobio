import os
from datetime import date
from decimal import Decimal

import pytest

# Os testes sempre usam SQLite, mesmo que o .env aponte para o PostgreSQL.
os.environ.pop("POSTGRES_DB", None)
os.environ.pop("LEGADO_DB", None)


@pytest.fixture
def glicerina(db):
    from apps.cadastros.models import Produto
    return Produto.objects.create(codigo="GLIC80", nome="Glicerina 80%", tipo="GLIC80", densidade=Decimal("1.26"))


@pytest.fixture
def aroma(db):
    from apps.cadastros.models import Local
    return Local.objects.create(nome="Tanque Aroma", tipo="INDUSTR")


@pytest.fixture
def biobrotas(db):
    from apps.cadastros.models import Local
    return Local.objects.create(nome="Tanque Biobrotas", tipo="INDUSTR")


@pytest.fixture
def dia():
    return date(2026, 10, 1)
