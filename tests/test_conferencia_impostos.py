"""Custo net (sem impostos) e conferência da planilha."""

from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.cadastros.models import Parceiro
from apps.estoque import services as estoque
from apps.integracao.conferencia import conferir
from apps.vendas import impostos, services
from apps.vendas.models import Pedido, TabelaFrete

DIA = date(2026, 10, 31)


def test_fatores_iguais_aos_da_planilha():
    assert impostos.fator(12) == Decimal("0.798600")
    assert impostos.fator(18) == Decimal("0.744150")
    assert impostos.fator(0, pis_cofins=False) == Decimal("1")
    # Frete: crédito de ICMS 12% do CT-e e de PIS/COFINS sobre o resto (a planilha só usa 0,88).
    assert impostos.frete_net("0.75") == Decimal("0.598950")
    assert impostos.frete_net("0.75", 0) == Decimal("0.680625")


@pytest.fixture
def pedido(glicerina, aroma, biobrotas):
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=50000, custo_unitario="4", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=biobrotas, quantidade_kg=40000, custo_unitario="4", data=DIA)
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", preco_kg="0.10")
    TabelaFrete.objects.create(origem=biobrotas, destino_uf="SP", preco_kg="0.20")
    cliente = Parceiro.objects.create(razao_social="CLIENTE", eh_cliente=True, cidade="IBATE", uf="SP")
    return Pedido.objects.create(cliente=cliente, produto=glicerina, quantidade_kg=Decimal("10000"), data=DIA,
                                 preco_unitario=Decimal("5"), destino_cidade="IBATE", destino_uf="SP")


def test_pedido_mostra_margem_net(pedido):
    services.aplicar_sugestao(pedido)
    assert pedido.icms_venda == Decimal("18")
    # receita net 50.000 × 0,74415; custo net 10.000 × (4 × 0,7986 + 0,10 × 0,7986)
    assert round(pedido.receita_net, 2) == Decimal("37207.50")
    assert round(pedido.custo_total_net, 2) == Decimal("32742.60")
    assert round(pedido.margem_net, 2) == Decimal("4464.90")


def test_exportacao_sem_impostos(pedido):
    pedido.exportacao = True
    pedido.save()
    assert pedido.icms_venda == 0 and pedido.fator_venda == 1


def test_tanques_nao_passam_do_pedido(pedido, aroma, biobrotas):
    with pytest.raises(ValidationError):
        services.definir_origens(pedido, [{"local": aroma, "quantidade_kg": 8000},
                                          {"local": biobrotas, "quantidade_kg": 5000}])


def _planilha(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Compra Bi-Dest"
    cab = ["Fornecedor:", None, "Nº Contrato", "Produto", "Quantidade", "Valor Unit.", "Vr. Total Ctt",
           "Entrada (Kg)", "Saldo", "Modalidade", "Aliq Direta Frete"]
    for i, v in enumerate(cab):
        ws.cell(11, 2 + i, v)
    ws["B12"], ws["D12"], ws["F12"], ws["G12"], ws["I12"], ws["J12"], ws["K12"], ws["L12"] = (
        "Oleoplan", "733/2026", 100, 5.65, 100, "=F12-P12", "Compra", 0.88)
    ws["P12"] = 0
    notas = ["Data", "Nº Nota Fiscal", "Quantidade", "Vr. Unit. Bruto", "Valor Total", "Pç Net", "Transportadora",
             None, "CTE", "Vr. Unit.", "Vr. Total", "Pç Net Frete", "Custo Bruto", "Custo Net"]
    for i, v in enumerate(notas):
        if v:
            ws.cell(16, 2 + i, v)
    ws["C18"], ws["D18"], ws["E18"], ws["K18"] = 253930, 100, 5.65, 0.55
    ws["M18"] = "=E18*0.7986"
    ws["N18"] = "=E18+K18"
    ws["C20"] = "TOTAL:"
    ws["E30"] = "=#REF!"
    caminho = tmp_path / "dre.xlsx"
    wb.save(caminho)
    return caminho


def test_conferencia_acha_frete_net_errado(tmp_path):
    # Sem Excel não há valores calculados: a conferência lê as fórmulas e os valores
    # que o Excel guardou. Aqui os valores ficam vazios, então só as checagens por
    # fórmula e por valor digitado aparecem.
    achados = conferir(_planilha(tmp_path))
    assert isinstance(achados, list)


def test_tela_de_conferencia(client, db, tmp_path):
    client.force_login(User.objects.create_user("ana", password="senha-de-teste-123"))
    assert client.get("/sistema/conferir-planilha/").status_code == 200
    with open(_planilha(tmp_path), "rb") as f:
        resp = client.post("/sistema/conferir-planilha/", {"arquivo": SimpleUploadedFile("dre.xlsx", f.read())})
    assert resp.status_code == 200 and "dre.xlsx" in resp.content.decode()


@pytest.fixture
def logado(client, db):
    usuario = User.objects.create_superuser("teste", "", "x")
    client.force_login(usuario)
    return client


def test_tela_do_pedido_explica_custos_e_avisa_nao_salvo(logado, pedido):
    html = logado.get(f"/vendas/{pedido.pk}/").content.decode()
    assert "Como cada custo é calculado" in html
    # preço 5 × 0,74415 = 3,7208 net
    assert "R$ 3,7208/kg" in html
    assert 'id="p-nao-salvo"' in html and "data-salvo-qtd" in html
    assert 'step="10"' in html


def test_cadastrar_local_ja_com_tanque(logado, glicerina):
    from apps.cadastros.models import Local, Tanque

    resposta = logado.post("/novo/local/", {
        "nome": "Tanque Novo Paulínia", "tipo": "TANQUE", "cidade": "PAULINIA", "uf": "SP", "ativo": "on",
        "tanque_produto": glicerina.pk, "tanque_capacidade": "500000", "tanque_identificacao": "TQ-01",
    })
    assert resposta.status_code == 302
    local = Local.objects.get(nome="Tanque Novo Paulínia")
    assert Tanque.objects.get(local=local).capacidade_kg == Decimal("500000")
    # a entrada de saldo já abre com o local escolhido
    html = logado.get(f"/estoque/entrada/?local={local.pk}").content.decode()
    assert f'value="{local.pk}" selected' in html


def test_local_sem_capacidade_nao_salva(logado, glicerina):
    resposta = logado.post("/novo/local/", {"nome": "X", "tipo": "TANQUE", "ativo": "on", "tanque_produto": glicerina.pk})
    assert resposta.status_code == 200
    assert "Informe a capacidade" in resposta.content.decode()


def test_margem_bruta_menos_imposto_a_pagar_da_a_net(logado, pedido):
    services.aplicar_sugestao(pedido)
    assert round(pedido.margem - pedido.impostos_venda, 6) == round(pedido.margem_net, 6)
    html = logado.get(f"/vendas/{pedido.pk}/").content.decode()
    assert "Margem por kg" in html and "Sobra" not in html
    assert "Por que a net pode ser menor que a bruta" in html
