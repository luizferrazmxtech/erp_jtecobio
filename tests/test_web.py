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
    assert "Administração" not in html and "/admin/" not in html
    assert "/sistema/usuarios/" in html and "/sistema/configuracoes/" in html


def test_saldos_por_tanque_mostra_tanques_com_resumo(logado, dados, glicerina, aroma):
    estoque.registrar_saida(produto=glicerina, local=aroma, quantidade_kg=5000, data=DIA, documento="NF 4100")
    html = logado.get("/estoque/").content.decode()
    assert "cilindro-grande" in html and "Últimas saídas" in html and "Últimas entradas" in html
    assert "NF 4100" in html and "Livre para vender" in html
    assert "45%" in html  # 45.000 kg num tanque de 100.000 kg
    assert "Ver em tabela" in html


def test_busca_de_clientes_digitando(logado, dados):
    Parceiro.objects.create(razao_social="OUTRA EMPRESA", eh_fornecedor=True, cidade="IBATE", uf="SP")
    r = logado.get("/busca/parceiros/?papel=cliente&q=maian").json()["resultados"]
    assert [x["texto"] for x in r] == ["MAIAN INDUSTRIA"] and "Jandira/SP" in r[0]["detalhe"]
    assert logado.get("/busca/parceiros/?papel=cliente&q=outra").json()["resultados"] == []  # é fornecedor
    # a tela não manda a lista inteira de clientes, só a caixa de busca
    html = logado.get("/vendas/novo/").content.decode()
    assert "busca-texto" in html and "MAIAN INDUSTRIA</option>" not in html


def test_pedido_com_outro_produto_na_mesma_venda(logado, dados, glicerina):
    from apps.cadastros.models import Produto

    outro = Produto.objects.create(nome="GLICERINA BIDESTILADA", tipo=glicerina.tipo, controla_estoque=True)
    resp = logado.post("/vendas/novo/", {
        "data": "2026-11-15", "cliente": dados["cliente"].pk, "contrato": dados["contrato"].pk,
        "produto": glicerina.pk, "quantidade_kg": "10000", "preco_unitario": "7.63",
        "destino_cidade": "", "destino_uf": "", "observacao": "",
        "outro_produto": "on", "produto2": outro.pk, "quantidade2": "2000", "preco2": "9.40",
    })
    assert resp.status_code == 302
    principal = Pedido.objects.get(produto=glicerina, cliente=dados["cliente"])
    irmao = Pedido.objects.get(produto=outro)
    assert irmao.pedido_principal == principal and irmao.contrato is None
    assert irmao.quantidade_kg == Decimal("2000") and irmao.destino_uf == "SP"
    assert principal.mesma_venda == [irmao] and irmao.mesma_venda == [principal]
    assert f"Pedido {irmao.numero}" in logado.get(f"/vendas/{principal.pk}/").content.decode()


def test_outro_produto_exige_dados(logado, dados, glicerina):
    resp = logado.post("/vendas/novo/", {
        "data": "2026-11-15", "cliente": dados["cliente"].pk, "produto": glicerina.pk, "quantidade_kg": "10000",
        "preco_unitario": "7.63", "destino_uf": "SP", "outro_produto": "on",
    })
    assert resp.status_code == 200 and "Escolha o outro produto." in resp.content.decode()


def test_criar_usuario_com_areas_pela_tela(logado, db):
    resp = logado.post("/sistema/usuarios/novo/", {
        "username": "maria.silva", "first_name": "Maria", "is_active": "on", "areas": ["vendas", "estoque"],
        "senha": "Tanque-Glicerina-77", "senha2": "Tanque-Glicerina-77"})
    assert resp.status_code == 302 and resp["Location"] == "/sistema/usuarios/"
    u = User.objects.get(username="maria.silva")
    assert u.check_password("Tanque-Glicerina-77") and not u.is_superuser
    assert u.has_perm("vendas.add_pedido") and u.has_perm("estoque.add_movimentacao")
    assert not u.has_perm("cadastros.add_produto")
    html = logado.get("/sistema/usuarios/").content.decode()
    assert "maria.silva" in html and "Vendas" in html


def test_editar_usuario_tira_area_e_mantem_senha(logado, db):
    from django.contrib.auth.models import Group
    perfil = Group.objects.create(name="Comercial")
    u = User.objects.create_user("joao", password="Senha-Antiga-Forte-9")
    logado.post("/sistema/usuarios/novo/", {})  # formulário vazio não quebra
    resp = logado.post(f"/sistema/usuarios/{u.pk}/", {
        "username": "joao", "is_active": "on", "areas": ["cadastros"], "perfis": [perfil.pk]})
    assert resp.status_code == 302
    u = User.objects.get(pk=u.pk)
    assert u.check_password("Senha-Antiga-Forte-9")
    assert u.has_perm("cadastros.add_produto") and not u.has_perm("vendas.add_pedido")
    assert set(u.groups.values_list("name", flat=True)) == {"Comercial", "Área: Cadastros"}


def test_senhas_diferentes_e_tirar_o_proprio_admin(logado, db):
    resp = logado.post("/sistema/usuarios/novo/", {"username": "x", "is_active": "on", "senha": "Abc-12345-xyz", "senha2": "outra"})
    assert resp.status_code == 200 and "não são iguais" in resp.content.decode()
    eu = User.objects.get(username="luiz")
    resp = logado.post(f"/sistema/usuarios/{eu.pk}/", {"username": "luiz", "is_active": "on"})
    assert resp.status_code == 200 and User.objects.get(pk=eu.pk).is_superuser


def test_telas_de_sistema_so_para_administrador(client, db):
    client.force_login(User.objects.create_user("visitante", password="senha-de-teste-123"))
    for url in ("/sistema/usuarios/", "/sistema/usuarios/novo/", "/sistema/configuracoes/"):
        assert client.get(url).status_code == 302
    assert "/sistema/usuarios/" not in client.get("/").content.decode()


def test_configuracoes_pela_tela(logado, db):
    from apps.estoque.models import Configuracao
    assert logado.get("/sistema/configuracoes/").status_code == 200
    resp = logado.post("/sistema/configuracoes/", {"metodo_custo": Configuracao.atual().metodo_custo, "permitir_saldo_negativo": "on"})
    assert resp.status_code == 302
    assert Configuracao.atual().permitir_saldo_negativo
