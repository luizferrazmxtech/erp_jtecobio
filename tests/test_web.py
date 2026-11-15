"""As telas novas abrem, mostram os dados e os cadastros salvam."""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import User

from apps.cadastros.models import Parceiro, Tanque
from apps.contratos.models import Contrato
from apps.estoque import services as estoque
from apps.fiscal.models import NotaFiscal, NotaItem
from apps.integracao import exemplos
from apps.integracao.models import RegistroExemplo
from apps.vendas.models import Pedido, TabelaFrete

DIA = date(2026, 10, 31)


@pytest.fixture
def logado(client, db):
    client.force_login(User.objects.create_superuser("luiz", "l@example.com", "senha-de-teste-123", first_name="Luiz"))
    return client


@pytest.fixture
def dados(glicerina, aroma, biobrotas):
    Tanque.objects.create(local=aroma, produto=glicerina, capacidade_kg=100000)
    estoque.registrar_entrada(produto=glicerina, local=aroma, quantidade_kg=50000, custo_unitario="5.58", data=DIA)
    estoque.registrar_entrada(produto=glicerina, local=biobrotas, quantidade_kg=40000, custo_unitario="5.70", data=DIA)
    cliente = Parceiro.objects.create(razao_social="MAIAN INDUSTRIA", eh_cliente=True, cidade="JANDIRA", uf="SP")
    contrato = Contrato.objects.create(numero="9291", tipo="V", parceiro=cliente, produto=glicerina,
                                       quantidade_kg=175000, entregue_antes=25000, preco_unitario="7.63",
                                       mes_referencia=date(2026, 11, 1))
    nota = NotaFiscal.objects.create(direcao="S", numero=4016, parceiro=cliente, data_emissao=date.today(),
                                     cfop="5102", valor_total="211950.00", pedido_cliente="9291")
    NotaItem.objects.create(nota=nota, item=1, produto=glicerina, descricao="GLICERINA", quantidade=30000,
                            valor_total="211950.00", contrato=contrato)
    return {"cliente": cliente, "contrato": contrato, "nota": nota}


def test_login_proprio(client, db):
    User.objects.create_user("ana", password="senha-de-teste-123")
    assert "Entrar" in client.get("/entrar/").content.decode()
    resp = client.post("/entrar/", {"username": "ana", "password": "senha-de-teste-123"})
    assert resp.status_code == 302 and resp["Location"] == "/"


def test_painel_mostra_tanques_e_contratos(logado, dados):
    pagina = logado.get("/").content.decode()
    assert "Bom" in pagina or "Boa" in pagina
    assert "Tanque Aroma" in pagina and "50.000 kg" in pagina and "50%" in pagina
    assert "9291" in pagina
    assert "MAIAN INDUSTRIA" in pagina  # maiores clientes do ano


def test_saldo_do_contrato_considera_o_entregue_antes(dados):
    c = Contrato.objects.com_saldo().get(pk=dados["contrato"].pk)
    assert c.saldo == Decimal("120000")  # 175.000 − 25.000 antes − 30.000 na NF
    assert dados["contrato"].calcular_saldo() == Decimal("120000")


@pytest.mark.parametrize("url", [
    "/", "/estoque/", "/estoque/movimentacoes/", "/vendas/", "/vendas/novo/", "/vendas/fretes/",
    "/contratos/", "/contratos/?tipo=V&situacao=ABERTO&com_saldo=1&busca=maian", "/fiscal/?direcao=S",
    "/fiscal/?direcao=E", "/fiscal/?direcao=CTE&de=2026-01-01&ate=2026-12-31", "/cadastros/parceiros/?papel=cliente&busca=jandira",
    "/cadastros/produtos/?estoque=sim", "/cadastros/locais/", "/novo/parceiro/?papel=cliente", "/novo/contrato/",
    "/novo/frete/", "/novo/tanque/", "/estoque/entrada/",
])
def test_telas_abrem(logado, dados, url):
    assert logado.get(url).status_code == 200, url


def test_telas_de_detalhe(logado, dados):
    assert "120.000" in logado.get(f"/contratos/{dados['contrato'].pk}/").content.decode()
    assert "4016" in logado.get(f"/fiscal/{dados['nota'].pk}/").content.decode()
    assert "9291" in logado.get(f"/cadastros/parceiros/{dados['cliente'].pk}/").content.decode()
    assert logado.get(f"/editar/contrato/{dados['contrato'].pk}/").status_code == 200


def test_cadastrar_cliente_pela_tela(logado, db):
    resp = logado.post("/novo/parceiro/?papel=cliente", {
        "razao_social": "UPPER DOG COMERCIAL", "cidade": "Sao Sebastiao do Paraiso", "uf": "MG",
        "ativo": "on", "eh_cliente": "on",
    })
    p = Parceiro.objects.get(razao_social="UPPER DOG COMERCIAL")
    assert resp.status_code == 302 and resp["Location"] == f"/cadastros/parceiros/{p.pk}/"


def test_usuario_sem_permissao_nao_cadastra(client, db):
    client.force_login(User.objects.create_user("visitante", password="senha-de-teste-123"))
    resp = client.post("/novo/produto/", {"codigo": "X", "nome": "X", "tipo": "OUTRO", "unidade": "KG"})
    assert resp.status_code == 302
    from apps.cadastros.models import Produto
    assert not Produto.objects.filter(codigo="X").exists()


def test_novo_pedido_ja_vem_preenchido_pelo_contrato(logado, dados):
    pagina = logado.get(f"/vendas/novo/?contrato={dados['contrato'].pk}").content.decode()
    assert f'<option value="{dados["cliente"].pk}" selected>' in pagina


def test_exemplos_entram_e_saem_sem_mexer_no_resto(dados, glicerina, aroma, biobrotas):
    saldo_antes = estoque.saldo(glicerina, aroma) + estoque.saldo(glicerina, biobrotas)
    r = exemplos.carregar()
    assert r["fretes"] > 0 and r["pedidos"] == 1  # só existe o contrato 9291 nesta base
    pedido = Pedido.objects.get()
    assert pedido.origens.count() == 2  # 60.000 kg: precisa dos dois tanques
    exemplos.apagar()
    assert not Pedido.objects.exists() and not TabelaFrete.objects.exists()
    assert not RegistroExemplo.objects.exists()
    assert estoque.saldo(glicerina, aroma) + estoque.saldo(glicerina, biobrotas) == saldo_antes
    assert Contrato.objects.filter(pk=dados["contrato"].pk).exists()


def test_link_do_tanque_nao_usa_separador_de_milhar(logado, db, aroma):
    """No painel, o link do tanque levava a ?produto=1.051 e dava erro."""
    from apps.cadastros.models import Produto

    produto = Produto.objects.create(pk=1051, nome="GBD USP")
    estoque.registrar_entrada(produto=produto, local=aroma, quantidade_kg=1000, custo_unitario="5", data=DIA)
    pagina = logado.get("/").content.decode()
    assert "produto=1051" in pagina and "produto=1.051" not in pagina
    assert logado.get("/estoque/movimentacoes/?local=1&produto=1051").status_code == 200


def test_pedido_mostra_tanques_com_custo_efetivo_e_rota_sugerida(logado, dados, glicerina, aroma, biobrotas):
    TabelaFrete.objects.create(origem=aroma, destino_uf="SP", preco_kg="0.30")
    TabelaFrete.objects.create(origem=biobrotas, destino_uf="SP", preco_kg="0.10")
    pedido = Pedido.objects.create(cliente=dados["cliente"], produto=glicerina, quantidade_kg=60000, data=DIA,
                                   preco_unitario="7.63", destino_cidade="JANDIRA", destino_uf="SP")
    pagina = logado.get(f"/vendas/{pedido.pk}/").content.decode()
    # Um cartão por tanque, com custo e sugestão: Biobrotas (5,70+0,10=5,80) completa
    # 40.000 kg antes do Aroma (5,58+0,30=5,88), que entra com os 20.000 que faltam.
    assert pagina.count('class="origem') == 2
    assert 'data-sugerido="40000' in pagina and 'data-sugerido="20000' in pagina
    assert "js/rota.js" in pagina


def test_pedido_novo_so_lista_contratos_do_cliente(logado, dados, glicerina):
    outro = Parceiro.objects.create(razao_social="OUTRO CLIENTE", eh_cliente=True, uf="SP")
    Contrato.objects.create(numero="OUTRO-1", tipo="V", parceiro=outro, produto=glicerina, quantidade_kg=1000,
                            preco_unitario="5", mes_referencia=date(2026, 11, 1))
    from apps.vendas.forms import PedidoForm

    form = PedidoForm(initial={"cliente": dados["cliente"].pk})
    assert list(form.fields["contrato"].queryset) == [dados["contrato"]]
    assert not PedidoForm().fields["contrato"].queryset.exists()  # sem cliente, nenhum contrato
    html = logado.get(f"/vendas/novo/?cliente={dados['cliente'].pk}").content.decode()
    assert "OUTRO-1" in html  # está na lista que a tela filtra ao trocar o cliente…
    assert 'value="' + str(dados["contrato"].pk) + '">9291' in html  # …mas no seletor só o do cliente


def test_ligar_e_desligar_nota_no_contrato(logado, dados, glicerina):
    c = dados["contrato"]
    nota = NotaFiscal.objects.create(direcao="S", numero=4020, parceiro=dados["cliente"], data_emissao=DIA)
    item = NotaItem.objects.create(nota=nota, item=1, produto=glicerina, descricao="GLICERINA", quantidade=10000)
    assert "4020" in logado.get(f"/contratos/{c.pk}/").content.decode()
    logado.post(f"/contratos/{c.pk}/notas/", {"item": item.pk})
    assert Contrato.objects.com_saldo().get(pk=c.pk).saldo == Decimal("110000")
    logado.post(f"/contratos/{c.pk}/notas/", {"item": item.pk, "acao": "desligar"})
    assert Contrato.objects.com_saldo().get(pk=c.pk).saldo == Decimal("120000")


def test_menu_sem_administracao(logado, dados):
    html = logado.get("/").content.decode()
    assert "Administração" not in html and "Usuários e acessos" in html
