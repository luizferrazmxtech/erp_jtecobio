from decimal import Decimal

import pytest
from django.contrib.auth.models import User

from apps.estoque import services


@pytest.fixture
def admin_client(client, db):
    user = User.objects.create_superuser("luiz", "luiz@example.com", "senha-de-teste-123")
    client.force_login(user)
    return client


def test_telas_exigem_login(client, db):
    resp = client.get("/estoque/")
    assert resp.status_code == 302 and "/entrar/" in resp["Location"]


def test_lancar_entrada_pela_tela(admin_client, glicerina, aroma):
    resp = admin_client.post("/estoque/entrada/", {
        "tipo": "ENTRADA", "data": "2026-10-01", "produto": glicerina.pk, "local": aroma.pk,
        "quantidade_kg": "48960", "custo_unitario": "2.4000", "documento": "NF 13399",
    })
    assert resp.status_code == 302
    assert services.saldo(glicerina, aroma) == Decimal("48960")
    pagina = admin_client.get("/estoque/").content.decode()
    assert "Tanque Aroma" in pagina and "48.960" in pagina


def test_saida_sem_saldo_mostra_erro(admin_client, glicerina, aroma):
    resp = admin_client.post("/estoque/saida/", {
        "data": "2026-10-01", "produto": glicerina.pk, "local": aroma.pk, "quantidade_kg": "10",
    })
    assert resp.status_code == 200
    assert "Saldo insuficiente" in resp.content.decode()


def test_admin_abre(admin_client):
    for url in ["/admin/", "/admin/contratos/contrato/", "/admin/fiscal/notafiscal/", "/admin/cadastros/local/add/"]:
        assert admin_client.get(url).status_code == 200, url
