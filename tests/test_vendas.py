from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.cadastros.models import Local, Parceiro
from apps.estoque import services as estoque
from apps.estoque.models import Configuracao
from apps.fiscal.models import NotaFiscal
from apps.vendas import services as v
from apps.vendas.models import Pedido, TabelaFrete

D = Decimal
DIA = date(2026, 10, 20)


@pytest.fixture
def vale(db):
    return Local.objects.create(nome="Tanque Vale", tipo="TANQUE")


@pytest.fixture
def cliente(db):
    return Parceiro.objects.create(razao_social="Pinus Brasil", eh_cliente=True, cidade="Piracicaba", uf="SP")


@pytest.fixture
def cenario(glicerina, aroma, biobrotas, vale):
    """Aroma: barato e perto. Biobrotas: mais caro. Vale: barato mas frete alto."""
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=18000, custo_unitario="5.58", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=biobrotas, quantidade_kg=40000, custo_unitario="5.70", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=vale, quantidade_kg=40000, custo_unitario="5.40", data=DIA)
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", destino_cidade="Piracicaba", preco_kg="0.05")
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", preco_kg="0.22")
    TabelaFrete.objects.create(origem=biobrotas, destino_uf="SP", preco_kg="0.15")
    TabelaFrete.objects.create(origem=vale, destino_uf="SP", preco_kg="0.60")


def _pedido(glicerina, cliente, kg=30000, preco="7.35", cidade="Piracicaba"):
    return Pedido.objects.create(data=DIA, cliente=cliente, produto=glicerina, quantidade_kg=kg,
                                 preco_unitario=preco, destino_cidade=cidade, destino_uf="SP")


def test_frete_prefere_cidade_e_cai_para_uf(cenario, aroma):
    assert TabelaFrete.buscar(aroma, "piracicaba", "sp").preco_kg == D("0.05")
    assert TabelaFrete.buscar(aroma, "Campinas", "SP").preco_kg == D("0.22")
    assert TabelaFrete.buscar(aroma, "Curitiba", "PR") is None


def test_sugestao_pega_a_combinacao_mais_barata(cenario, glicerina, aroma, biobrotas):
    s = v.sugerir_origens(glicerina, 30000, "Piracicaba", "SP")
    assert [(o.local, q) for o, q in s.origens] == [(aroma, D("18000")), (biobrotas, D("12000"))]
    assert s.faltante == 0
    # 18.000 × (5,58 + 0,05) + 12.000 × (5,70 + 0,15)
    assert s.custo_total == D("171540.00")


def test_sugestao_respeita_minimo_por_origem(cenario, glicerina, aroma, biobrotas):
    # Com mínimo de 20 t por caminhão, a Aroma (18 t) é pulada.
    s = v.sugerir_origens(glicerina, 30000, "Piracicaba", "SP", minimo_por_origem_kg=20000)
    assert [(o.local, q) for o, q in s.origens] == [(biobrotas, D("30000"))]


def test_sugestao_avisa_o_que_falta(cenario, glicerina):
    s = v.sugerir_origens(glicerina, 120000, "Piracicaba", "SP")
    assert s.faltante == D("22000")


def test_ciclo_completo_com_reserva_e_faturamento(cenario, glicerina, cliente, aroma, biobrotas):
    p1 = _pedido(glicerina, cliente)
    v.aplicar_sugestao(p1)
    v.confirmar(p1)
    assert v.saldo_livre(glicerina, aroma) == 0
    assert estoque.saldo(glicerina, aroma) == D("18000")

    # Um segundo pedido já não vê o que está reservado para o primeiro
    p2 = _pedido(glicerina, cliente, kg=10000)
    s = v.aplicar_sugestao(p2)
    assert aroma not in [o.local for o, _ in s.origens]

    nota = NotaFiscal.objects.create(direcao="S", numero=3907, serie="1", parceiro=cliente, data_emissao=DIA)
    movs = v.faturar(p1, DIA, nota=nota)
    p1.refresh_from_db()
    assert p1.status == Pedido.Status.FATURADO and len(movs) == 2
    assert estoque.saldo(glicerina, aroma) == 0
    assert estoque.saldo(glicerina, biobrotas) == D("28000")
    assert p1.receita == D("220500")
    assert p1.custo_produto == D("168840")
    assert p1.custo_frete == D("2700")
    assert p1.margem == D("48960")


def test_confirmar_exige_origens_que_fecham_a_quantidade(cenario, glicerina, cliente, aroma):
    p = _pedido(glicerina, cliente)
    with pytest.raises(ValidationError, match="pelo menos uma origem"):
        v.confirmar(p)
    v.definir_origens(p, [{"local": aroma, "quantidade_kg": 10000}])
    with pytest.raises(ValidationError, match="somam"):
        v.confirmar(p)


def test_nao_reserva_mais_que_o_saldo_livre(cenario, glicerina, cliente, aroma):
    p1 = _pedido(glicerina, cliente, kg=15000)
    v.definir_origens(p1, [{"local": aroma, "quantidade_kg": 15000}])
    v.confirmar(p1)
    p2 = _pedido(glicerina, cliente, kg=5000)
    v.definir_origens(p2, [{"local": aroma, "quantidade_kg": 5000}])
    with pytest.raises(ValidationError, match="saldo livre"):
        v.confirmar(p2)
    # Cancelar o primeiro libera a reserva
    v.cancelar(p1)
    v.confirmar(p2)


def test_frete_manual_substitui_a_tabela(cenario, glicerina, cliente, aroma):
    p = _pedido(glicerina, cliente, kg=1000)
    v.definir_origens(p, [{"local": aroma, "quantidade_kg": 1000, "frete_kg": D("0.10")}])
    assert p.origens.get().frete_kg == D("0.10")


def test_produto_de_cliente_nao_pode_ser_vendido(cenario, glicerina, cliente):
    envase = Local.objects.create(nome="Envase clientes", tipo=Local.Tipo.TERCEIROS)
    estoque.registrar_entrada(produto=glicerina, local=envase, quantidade_kg=5000, custo_unitario=0, data=DIA)
    assert envase not in [o.local for o in v.opcoes_de_origem(glicerina, "Piracicaba", "SP")]
    p = _pedido(glicerina, cliente, kg=1000)
    with pytest.raises(ValidationError, match="produto de cliente"):
        v.definir_origens(p, [{"local": envase, "quantidade_kg": 1000}])


def test_pedido_faturado_nao_pode_ser_cancelado(cenario, glicerina, cliente, aroma):
    p = _pedido(glicerina, cliente, kg=1000)
    v.definir_origens(p, [{"local": aroma, "quantidade_kg": 1000}])
    v.confirmar(p)
    v.faturar(p, DIA)
    with pytest.raises(ValidationError):
        v.cancelar(p)


def test_custo_por_lote_na_venda(cenario, glicerina, cliente, aroma):
    Configuracao.objects.create(metodo_custo=Configuracao.MetodoCusto.LOTE)
    opcoes = v.opcoes_de_origem(glicerina, "Piracicaba", "SP")
    assert all(o.lote is not None for o in opcoes)
    p = _pedido(glicerina, cliente, kg=1000)
    v.aplicar_sugestao(p)
    v.confirmar(p)
    v.faturar(p, DIA)
    assert p.origens.get().custo_unitario == D("5.58")


def test_numero_do_pedido_e_sequencial(cenario, glicerina, cliente):
    assert _pedido(glicerina, cliente).numero == 1
    assert _pedido(glicerina, cliente).numero == 2
