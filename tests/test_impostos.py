"""Créditos de imposto por kg, quadro "Impostos desta venda" e conta corrente."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User

from apps.cadastros.models import Local, Parceiro
from apps.estoque import services as estoque
from apps.fiscal import apuracao, tributos
from apps.fiscal.models import NotaFiscal, NotaItem
from apps.vendas import services
from apps.vendas.models import Pedido, TabelaFrete

DIA = date(2026, 10, 20)
D = Decimal


def _r(v):
    return round(v, 2)


def test_regras_dos_impostos():
    # Compra de R$ 50.000 com ICMS 12%: o ICMS sai da base do PIS e da COFINS.
    c = tributos.sobre_valor(50000, 12)
    assert (_r(c.icms), _r(c.pis), _r(c.cofins)) == (D("6000.00"), D("726.00"), D("3344.00"))
    assert tributos.debito_venda(70000, 18, exportacao=True).total == 0
    frete = tributos.credito_frete(3000)
    assert (_r(frete.icms), _r(frete.pis), _r(frete.cofins)) == (D("360.00"), D("43.56"), D("200.64"))


@pytest.fixture
def cliente(db):
    return Parceiro.objects.create(razao_social="CLIENTE SP", eh_cliente=True, cidade="IBATE", uf="SP")


def test_quadro_da_venda_com_compra_frete_e_margem(glicerina, aroma, cliente):
    """O exemplo do chat: compra 10 t a R$ 5 (ICMS 12%), vende a R$ 7 em SP, frete R$ 3.000."""
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=10000, custo_unitario="5", data=DIA)
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", preco_kg="0.30")
    pedido = Pedido.objects.create(cliente=cliente, produto=glicerina, quantidade_kg=D("10000"), data=DIA,
                                   preco_unitario=D("7"), destino_cidade="IBATE", destino_uf="SP")
    services.aplicar_sugestao(pedido)

    deb, prod, frete, pagar = pedido.debitos, pedido.creditos_produto, pedido.creditos_frete, pedido.a_pagar
    assert (_r(deb.icms), _r(deb.pis), _r(deb.cofins)) == (D("12600.00"), D("947.10"), D("4362.40"))
    assert (_r(prod.icms), _r(prod.pis), _r(prod.cofins)) == (D("6000.00"), D("726.00"), D("3344.00"))
    assert (_r(frete.icms), _r(frete.pis), _r(frete.cofins)) == (D("360.00"), D("43.56"), D("200.64"))
    assert (_r(pagar.icms), _r(pagar.pis), _r(pagar.cofins)) == (D("6240.00"), D("177.54"), D("817.76"))
    # Margem real = 70.000 − 50.000 − 3.000 − 7.235,30 de imposto líquido
    assert _r(pedido.margem_net) == D("9764.70")
    assert _r(pedido.margem - pedido.impostos_venda) == _r(pedido.margem_net)


def test_tanque_com_compra_de_sp_tem_custo_net_menor(glicerina, aroma, biobrotas, cliente):
    """Mesmo preço bruto, mas a compra dentro de SP (18%) dá mais crédito: o tanque vem primeiro."""
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=20000, custo_unitario="5", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=biobrotas, quantidade_kg=20000, custo_unitario="5",
                              data=DIA, icms_pct=18)
    for local in (aroma, biobrotas):
        TabelaFrete.objects.create(origem=local, destino_uf="SP", preco_kg="0.10")
    opcoes = services.opcoes_de_origem(glicerina, "IBATE", "SP")
    assert [o.local for o in opcoes] == [biobrotas, aroma]
    assert _r(opcoes[0].custo_net_kg * 1000) == D("3720.75")   # 5 × 0,82 × 0,9075
    assert _r(opcoes[1].custo_net_kg * 1000) == D("3993.00")   # 5 × 0,88 × 0,9075


def test_credito_medio_do_tanque_e_transferencia(glicerina, aroma, biobrotas):
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=1000, custo_unitario="5", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=1000, custo_unitario="5", data=DIA,
                              icms_pct=18)
    medio = estoque.creditos_medios(glicerina, aroma)
    assert medio.icms == D("0.750000")   # média de 0,60 e 0,90
    saida, entrada = estoque.transferir(produto=glicerina, origem=aroma, destino=biobrotas,
                                        quantidade_kg=500, data=DIA)
    assert entrada.creditos_kg == medio
    assert estoque.creditos_medios(glicerina, biobrotas) == medio
    assert estoque.creditos_medios(glicerina, aroma) == medio


def test_credito_vem_da_nota_de_compra(glicerina, aroma):
    usina = Parceiro.objects.create(razao_social="USINA MT", eh_fornecedor=True, uf="MT")
    nota = NotaFiscal.objects.create(direcao="E", numero=9121, parceiro=usina, data_emissao=DIA)
    NotaItem.objects.create(nota=nota, item=1, produto=glicerina, descricao="GLICERINA", quantidade=D("38720"),
                            valor_total=D("162624"), valor_icms=D("19514.88"), valor_pis=D("2361.30"),
                            valor_cofins=D("10876.29"))
    mov = estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=38720, custo_unitario="4.20",
                                    data=DIA, nota=nota)
    assert mov.credito_icms_kg == D("0.504000")
    assert mov.lote.credito_pis_kg == D("0.060984")


def _nota(direcao, numero, dia, parceiro, valor, icms, pis, cofins, cfop):
    n = NotaFiscal.objects.create(direcao=direcao, numero=numero, parceiro=parceiro, data_emissao=dia,
                                  data_entrada=dia, cfop=cfop)
    NotaItem.objects.create(nota=n, item=1, descricao="GLICERINA", quantidade=D("1000"), valor_total=D(valor),
                            valor_icms=D(icms), valor_pis=D(pis), valor_cofins=D(cofins), cfop=cfop)
    return n


def test_conta_corrente_leva_o_saldo_credor_para_o_mes_seguinte(db):
    usina = Parceiro.objects.create(razao_social="USINA", eh_fornecedor=True, uf="MT")
    cli = Parceiro.objects.create(razao_social="CLIENTE", eh_cliente=True, uf="SP")
    # Setembro: compra muito, vende pouco → sobra crédito
    _nota("E", 1, date(2026, 9, 5), usina, "100000", "12000", "1452", "6688", "2102")
    _nota("S", 2, date(2026, 9, 20), cli, "50000", "9000", "676.50", "3116", "5102")
    # Outubro: vende mais do que compra → usa o saldo e paga o resto
    _nota("S", 3, date(2026, 10, 10), cli, "100000", "18000", "1353", "6232", "5102")
    # Nota cancelada não conta; entrada emitida pela JT no livro de saídas também não
    cancelada = _nota("S", 4, date(2026, 10, 11), cli, "999999", "99999", "0", "0", "5102")
    cancelada.cancelada = True
    cancelada.save()
    _nota("S", 5, date(2026, 10, 12), cli, "5000", "900", "0", "0", "1202")
    # Compra sem crédito de PIS/COFINS lançado
    _nota("E", 6, date(2026, 10, 15), usina, "10000", "1200", "0", "0", "2102")

    icms = next(c for c in apuracao.contas_correntes() if c.tributo.codigo == "ICMS")
    set_, out = icms.mes(2026, 9), icms.mes(2026, 10)
    assert set_.saldo_atual == D("3000") and set_.a_pagar == 0
    assert out.saldo_anterior == D("3000") and out.creditos == D("1200")
    assert out.a_pagar == D("13800") and out.saldo_atual == 0

    pis = next(c for c in apuracao.contas_correntes() if c.tributo.codigo == "PIS")
    assert _r(pis.mes(2026, 10).credito_nao_lancado) == D("145.20")   # (10.000 − 1.200) × 1,65%

    uf = {l["uf"]: l for l in apuracao.icms_por_uf(2026, 10)}
    assert uf["MT"]["credito"] == D("1200") and uf["SP"]["debito"] == D("18000")


@pytest.fixture
def logado(client, db):
    client.force_login(User.objects.create_superuser("luiz", "l@example.com", "senha-de-teste-123"))
    return client


def test_telas_mostram_os_impostos(logado, glicerina, aroma, cliente):
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=10000, custo_unitario="5", data=DIA)
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", preco_kg="0.30")
    pedido = Pedido.objects.create(cliente=cliente, produto=glicerina, quantidade_kg=D("10000"), data=DIA,
                                   preco_unitario=D("7"), destino_cidade="IBATE", destino_uf="SP")
    services.aplicar_sugestao(pedido)
    pagina = logado.get(f"/vendas/{pedido.pk}/").content.decode()
    assert "Impostos <b>desta venda</b>" in pagina and "Margem real" in pagina and "6.240,00" in pagina

    usina = Parceiro.objects.create(razao_social="USINA", eh_fornecedor=True, uf="MT")
    _nota("E", 1, date(2026, 9, 5), usina, "100000", "12000", "1452", "6688", "2102")
    resp = logado.get("/fiscal/impostos/?ano=2026")
    assert resp.status_code == 200 and "Conta corrente" in resp.content.decode()
    assert "Conta corrente de <b>impostos</b>" in logado.get("/").content.decode()
