from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User

from apps.cadastros.models import Parceiro
from apps.estoque import services as estoque
from apps.vendas.models import Pedido, TabelaFrete

DIA = date(2026, 10, 20)


@pytest.fixture
def logado(client, db):
    client.force_login(User.objects.create_superuser("luiz", "l@example.com", "senha-de-teste-123"))
    return client


@pytest.fixture
def estoque_pronto(glicerina, aroma, biobrotas):
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=18000, custo_unitario="5.58", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=biobrotas, quantidade_kg=40000, custo_unitario="5.70", data=DIA)
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", preco_kg="0.05")
    TabelaFrete.objects.create(origem=biobrotas, destino_uf="SP", preco_kg="0.15")
    return Parceiro.objects.create(razao_social="Pinus Brasil", eh_cliente=True, cidade="Piracicaba", uf="SP")


def test_fluxo_de_venda_pelas_telas(logado, estoque_pronto, glicerina, aroma, biobrotas):
    cliente = estoque_pronto
    resp = logado.post("/vendas/novo/", {
        "data": "2026-10-20", "cliente": cliente.pk, "produto": glicerina.pk,
        "quantidade_kg": "30000", "preco_unitario": "7.35",
    })
    pedido = Pedido.objects.get()
    assert resp.status_code == 302 and resp["Location"] == f"/vendas/{pedido.pk}/"
    assert pedido.destino_uf == "SP" and pedido.destino_cidade == "Piracicaba"
    assert pedido.quantidade_alocada == Decimal("30000")

    pagina = logado.get(f"/vendas/{pedido.pk}/").content.decode()
    assert "Tanque Aroma" in pagina and "Tanque Biobrotas" in pagina

    # Ajuste manual: tudo da Biobrotas, com frete digitado no formato brasileiro
    linhas = {}
    for i, local in enumerate([aroma, biobrotas]):
        linhas[f"local_{i}"] = local.pk
        linhas[f"lote_{i}"] = ""
    linhas.update({"qtd_0": "", "frete_0": "0,05", "qtd_1": "30.000,000", "frete_1": "0,12"})
    logado.post(f"/vendas/{pedido.pk}/origens/", linhas)
    origem = pedido.origens.get()
    assert origem.local == biobrotas and origem.frete_kg == Decimal("0.12")

    logado.post(f"/vendas/{pedido.pk}/confirmar/")
    pedido.refresh_from_db()
    assert pedido.status == Pedido.Status.CONFIRMADO

    logado.post(f"/vendas/{pedido.pk}/faturar/", {"data": "2026-10-21"})
    pedido.refresh_from_db()
    assert pedido.status == Pedido.Status.FATURADO
    assert estoque.saldo(glicerina, biobrotas) == Decimal("10000")
    assert "Faturado" in logado.get("/vendas/").content.decode()


def test_erro_de_regra_aparece_como_mensagem(logado, estoque_pronto, glicerina):
    pedido = Pedido.objects.create(data=DIA, cliente=estoque_pronto, produto=glicerina, quantidade_kg=30000,
                                   preco_unitario="7", destino_cidade="Piracicaba", destino_uf="SP")
    resp = logado.post(f"/vendas/{pedido.pk}/confirmar/", follow=True)
    assert "pelo menos uma origem" in resp.content.decode()


def test_pedido_maior_que_o_estoque_avisa_o_que_falta(logado, estoque_pronto, glicerina):
    pedido = Pedido.objects.create(data=DIA, cliente=estoque_pronto, produto=glicerina, quantidade_kg=100000,
                                   preco_unitario="7", destino_cidade="Piracicaba", destino_uf="SP")
    resp = logado.post(f"/vendas/{pedido.pk}/sugerir/", {"minimo": "0"}, follow=True)
    assert "faltam 42000 kg" in resp.content.decode()


def test_admin_de_frete_e_pedidos_abre(logado):
    for url in ["/admin/vendas/tabelafrete/", "/admin/vendas/tabelafrete/add/", "/admin/vendas/pedido/"]:
        assert logado.get(url).status_code == 200, url


def test_numeros_digitados_em_formato_brasileiro():
    from apps.vendas.views import _decimal
    assert _decimal("18.000") == Decimal("18000")
    assert _decimal("30.000,500") == Decimal("30000.500")
    assert _decimal("0,05") == Decimal("0.05")
    assert _decimal("18000.000") == Decimal("18000.000")
    assert _decimal("0.0500") == Decimal("0.0500")
    assert _decimal("", Decimal("0")) == Decimal("0")
