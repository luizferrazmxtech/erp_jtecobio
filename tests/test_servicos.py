import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from apps.cadastros.models import Parceiro
from apps.servicos import services
from apps.servicos.models import Apontamento, OrdemServico, TipoRecipiente
from apps.servicos.services import Fracao, fracionar

SP = ZoneInfo("America/Sao_Paulo")
D = Decimal


def hora(h, m=0):
    return datetime(2026, 11, 10, h, m, tzinfo=SP)


# ---------------------------------------------------------------------------
# Fracionamento
# ---------------------------------------------------------------------------


def test_um_tipo_so():
    # 5.000 L de glicerina (6.300 kg) em tambores de 200 L (252 kg) = 25 tambores
    f = [Fracao(D("252"), None)]
    _, sobra = fracionar(D("6300"), f)
    assert f[0].unidades == 25 and f[0].kg == D("6300") and f[0].parcial_kg == 0 and sobra == 0


def test_maiores_primeiro_respeitando_o_que_o_cliente_trouxe():
    ibc, tambor = Fracao(D("1260"), 3), Fracao(D("252"), None)
    _, sobra = fracionar(D("5000"), [tambor, ibc])
    assert ibc.unidades == 3 and ibc.kg == D("3780")
    # 1.220 kg restantes: 4 tambores cheios (1.008) + 1 com 212 kg
    assert tambor.unidades == 4 and tambor.parcial_kg == D("212") and tambor.kg == D("1220")
    assert sobra == 0


def test_sobra_quando_faltam_recipientes():
    ibc = Fracao(D("1000"), 2)
    _, sobra = fracionar(D("2500"), [ibc])
    assert ibc.unidades == 2 and sobra == D("500")


# ---------------------------------------------------------------------------
# OS completa
# ---------------------------------------------------------------------------


@pytest.fixture
def os_kemin(db):
    cliente = Parceiro.objects.create(razao_social="Kemin do Brasil Ltda", eh_cliente=True)
    ibc = TipoRecipiente.objects.get(nome="IBC 1.000 L")  # da migração: 0,25/kg, 12 min
    tambor = TipoRecipiente.objects.get(nome="Tambor 200 L")  # 0,30/kg, 4 min
    os = OrdemServico.objects.create(data=date(2026, 11, 10), cliente=cliente, descricao_produto="EN-HANCEA 132B",
                                     densidade=D("1.26"), quantidade_kg=D("5000"))
    services.novo_item(os, ibc, 3).save()
    services.novo_item(os, tambor).save()
    services.recalcular(os)
    return os


@pytest.fixture
def joao(db):
    return User.objects.create_user("joao", password="x", first_name="João")


@pytest.fixture
def maria(db):
    return User.objects.create_user("maria", password="x", first_name="Maria")


def test_os_calcula_fracionamento_e_valor(os_kemin):
    ibc, tambor = os_kemin.itens.order_by("-capacidade_kg")
    assert ibc.unidades_previstas == 3 and ibc.kg_previsto == D("3780")
    assert tambor.unidades_previstas == 4 and tambor.unidades_total_previstas == 5
    # 3.780 × 0,25 + 1.220 × 0,30
    assert os_kemin.valor_previsto == D("945.00") + D("366.00")
    assert os_kemin.minutos_previstos == 3 * 12 + 5 * 4
    assert os_kemin.numero == 1


def test_apontamento_com_pausa_e_dois_funcionarios(os_kemin, joao, maria):
    services.apontar(os_kemin, joao, "INICIO", item=os_kemin.itens.order_by("-capacidade_kg").first(), momento=hora(8))
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.ANDAMENTO
    services.apontar(os_kemin, maria, "INICIO", item=os_kemin.itens.order_by("-capacidade_kg").first(), momento=hora(8, 30))
    services.apontar(os_kemin, joao, "PAUSA", "REFEICAO", momento=hora(9))
    with pytest.raises(ValidationError):  # pausado não pode pausar de novo
        services.apontar(os_kemin, joao, "PAUSA", "OUTRO", momento=hora(9, 5))
    services.apontar(os_kemin, joao, "RETOMADA", momento=hora(9, 15))
    services.apontar(os_kemin, joao, "FIM", momento=hora(9, 45))
    services.apontar(os_kemin, maria, "FIM", momento=hora(9, 30))

    t = services.tempos(os_kemin)
    assert t[joao.pk].trabalhado == 90 * 60 and t[joao.pk].parado == 15 * 60  # 60 + 30 min; 15 parado
    assert t[maria.pk].trabalhado == 60 * 60
    assert t[joao.pk].pausas[0][2] == "Refeição / café"

    ind = services.indicadores(os_kemin)
    assert ind.homem_min == D("150.0") and ind.duracao_min == D("105.0") and ind.funcionarios == 2


def test_pausa_exige_motivo(os_kemin, joao):
    services.apontar(os_kemin, joao, "INICIO", item=os_kemin.itens.order_by("-capacidade_kg").first(), momento=hora(8))
    with pytest.raises(ValidationError):
        services.apontar(os_kemin, joao, "PAUSA", momento=hora(8, 10))


def test_concluir_fecha_quem_ficou_aberto_e_calcula_tmo(os_kemin, joao, maria):
    services.apontar(os_kemin, joao, "INICIO", item=os_kemin.itens.order_by("-capacidade_kg").first(), momento=timezone.now() - timedelta(minutes=56))
    services.apontar(os_kemin, maria, "INICIO", item=os_kemin.itens.order_by("-capacidade_kg").first(), momento=timezone.now() - timedelta(minutes=10))
    services.apontar(os_kemin, maria, "PAUSA", "CAMINHAO", momento=timezone.now() - timedelta(minutes=5))
    ibc, tambor = os_kemin.itens.order_by("-capacidade_kg")
    services.concluir(os_kemin, joao, {ibc.pk: (3, D("3780")), tambor.pk: (5, D("1220"))})
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.CONCLUIDA and os_kemin.kg_envasado == D("5000")
    assert os_kemin.apontamentos.filter(evento="FIM").count() == 2
    assert os_kemin.valor == D("1311.00")
    assert os_kemin.impostos == D("147.49")  # 11,25% de 1.311
    ind = services.indicadores(os_kemin)
    assert ind.unidades == 8
    assert ind.homem_min == D("61.0")  # 56 do João + 5 da Maria
    assert ind.tmo_min == D("7.63")
    assert ind.eficiencia == int(D("56") * 100 / D("61.0"))  # 3×12 + 5×4 = 56 min previstos

    # Não aceita mais apontamento
    with pytest.raises(ValidationError):
        services.apontar(os_kemin, joao, "INICIO")

    # TMO por tipo: os dois apontaram no IBC, então todo o tempo é do IBC
    real = services.tmo_do_tipo(ibc.tipo)
    assert real["unidades"] == 3 and real["tmo"] == (D("61") / 3).quantize(D("0.01"), "ROUND_HALF_UP")

    # Valor por mês para o painel
    mes = timezone.localtime(os_kemin.concluida_em).strftime("%Y-%m")
    assert services.valor_por_mes(date(2026, 1, 1))[mes] == D("1311.00")


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------


@pytest.fixture
def logado(client, db):
    client.force_login(User.objects.create_superuser("luiz", "l@example.com", "senha-de-teste-123"))
    return client


def test_nova_os_pela_tela(logado, db):
    cliente = Parceiro.objects.create(razao_social="Peter Cremer", eh_cliente=True)
    ibc = TipoRecipiente.objects.get(nome="IBC 1.000 L")
    resp = logado.post("/servicos/nova/", {
        "data": "2026-11-10", "cliente": cliente.pk, "descricao_produto": "Glicerina do cliente",
        "densidade": "1,26", "quantidade": "2000", "unidade_quantidade": "L",
        "aliquota_iss": "2", "aliquota_pis": "1,65", "aliquota_cofins": "7,6",
        "tipo_0": ibc.pk, "disponivel_0": "", "preco_0": "0,28", "tipo_1": "", "n_linhas": "2",
    })
    os = OrdemServico.objects.get()
    assert resp.status_code == 302 and resp["Location"] == f"/servicos/{os.pk}/"
    assert os.quantidade_kg == D("2520.000")
    item = os.itens.get()
    assert item.preco_kg == D("0.28") and item.unidades_previstas == 2 and item.parcial_kg == 0
    pagina = logado.get(f"/servicos/{os.pk}/").content.decode()
    assert "Peter Cremer" in pagina and "IBC 1.000 L" in pagina
    assert logado.get("/servicos/").status_code == 200
    assert logado.get("/servicos/nova/").status_code == 200
    assert logado.get("/servicos/recipientes/").status_code == 200
    assert logado.get(f"/servicos/{os.pk}/editar/").status_code == 200


def test_nova_os_sem_recipiente_nao_salva(logado, db):
    cliente = Parceiro.objects.create(razao_social="Peter Cremer", eh_cliente=True)
    resp = logado.post("/servicos/nova/", {
        "data": "2026-11-10", "cliente": cliente.pk, "descricao_produto": "X", "densidade": "1", "quantidade": "100",
        "unidade_quantidade": "KG", "aliquota_iss": "2", "aliquota_pis": "1", "aliquota_cofins": "7", "tipo_0": "",
        "n_linhas": "1",
    })
    assert resp.status_code == 200 and not OrdemServico.objects.exists()
    assert "pelo menos um tipo" in resp.content.decode()


def test_operador_aponta_e_conclui(client, os_kemin, joao):
    call_command("criar_perfis", stdout=io.StringIO())
    joao.groups.add(Group.objects.get(name="Operação"))
    client.force_login(joao)
    assert client.get("/")["Location"] == "/servicos/operador/"  # vai direto para o apontamento
    assert "OS 1" in client.get("/servicos/operador/").content.decode()

    ibc, tambor = os_kemin.itens.order_by("-capacidade_kg")
    url = f"/servicos/operador/{os_kemin.pk}/"
    pagina = client.get(url).content.decode()
    assert "O que envasar" in pagina and "Tambor 200 L" in pagina and "Começar aqui" in pagina

    client.post(url, {"evento": "INICIO", "item": ibc.pk})
    pagina = client.get(url).content.decode()
    assert "Envasando IBC 1.000 L" in pagina and "Quantos <b>IBC 1.000 L</b> você encheu" in pagina
    assert "+1 cheio" not in pagina  # não aponta mais recipiente a recipiente
    # Ao pausar, informa quantos encheu no trecho
    client.post(url, {"evento": "PAUSA", "motivo": "REFEICAO", "cheios": "2"})
    assert ibc.contados == 2
    client.post(url, {"evento": "RETOMADA"})
    # Ao trocar de recipiente, a contagem vai para o recipiente que ele deixou
    client.post(url, {"evento": "TROCA", "item": tambor.pk, "cheios": "1"})
    assert ibc.contados == 3 and tambor.contados == 0
    # Errou a contagem: corrige para mais ou para menos
    client.post(url, {"evento": "CHEIO", "item": tambor.pk, "quantidade": "6"})
    client.post(url, {"evento": "CHEIO", "item": tambor.pk, "quantidade": "-1"})
    assert tambor.contados == 5 and ibc.contados == 3
    # Não deixa tirar mais do que foi contado
    client.post(url, {"evento": "CHEIO", "item": ibc.pk, "quantidade": "-9"})
    assert ibc.contados == 3

    # Antes de finalizar, ainda não aparece o concluir
    assert "Concluir OS" not in client.get(url).content.decode()
    client.post(url, {"evento": "FIM", "cheios": "0"})
    pagina = client.get(url).content.decode()
    assert "Tudo envasado" in pagina and "Concluir OS" in pagina

    resp = client.post(f"/servicos/{os_kemin.pk}/concluir/", {"voltar": "operador"})
    assert resp["Location"] == "/servicos/operador/"
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.CONCLUIDA
    assert os_kemin.kg_envasado == D("5000")  # 3 IBC (3.780) + 5 tambores, o último com 212 kg
    ibc.refresh_from_db(); tambor.refresh_from_db()
    assert ibc.unidades_cheias == 3 and tambor.unidades_cheias == 5 and tambor.kg_realizado == D("1220")
    # Tempo separado por recipiente
    tmo = services.tmo_por_item(os_kemin)
    assert set(tmo) == {ibc.pk, tambor.pk}

    # Operador não edita nem fatura
    assert client.post(f"/servicos/{os_kemin.pk}/faturar/").status_code == 403


def test_faturar_e_correcao_pelo_escritorio(logado, os_kemin, joao):
    agora = timezone.localtime()
    resp = logado.post(f"/servicos/{os_kemin.pk}/corrigir/", {
        "funcionario": joao.pk, "evento": "INICIO", "momento": (agora - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        "motivo": "", "observacao": "Esqueceu de apontar", "item": os_kemin.itens.order_by("-capacidade_kg").first().pk,
    })
    assert resp.status_code == 302
    a = Apontamento.objects.get()
    assert a.corrigido and a.funcionario == joao
    ibc, tambor = os_kemin.itens.order_by("-capacidade_kg")
    logado.post(f"/servicos/{os_kemin.pk}/concluir/", {f"unidades_{ibc.pk}": "3", f"kg_{ibc.pk}": "3780",
                                                      f"unidades_{tambor.pk}": "5", f"kg_{tambor.pk}": "1220"})
    logado.post(f"/servicos/{os_kemin.pk}/faturar/", {"nota": ""})
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.FATURADA
    assert logado.get(f"/servicos/{os_kemin.pk}/").status_code == 200


def test_cadastro_de_recipiente_pela_tela(logado, db):
    resp = logado.post("/novo/recipiente/", {"nome": "Bombona 20 L", "capacidade": "20", "unidade": "L",
                                             "preco_kg": "0,35", "tempo_padrao_min": "1", "ativo": "on"})
    assert resp.status_code == 302
    assert TipoRecipiente.objects.get(nome="Bombona 20 L").preco_kg == D("0.35")


def test_busca_de_produto_traz_primeiro_os_do_cliente(logado, os_kemin):
    from apps.cadastros.models import Produto

    enhancea = Produto.objects.create(codigo="ENH", nome="EN-HANCEA 132B", densidade=D("1.10"))
    Produto.objects.create(codigo="GLI", nome="Glicerina bi-destilada")
    Produto.objects.create(codigo="VELHO", nome="Glicerina antiga", ativo=False)
    os_kemin.produto = enhancea
    os_kemin.save()

    # Sem digitar: só os produtos que o cliente já envasou
    r = logado.get(f"/busca/produtos/?q=&cliente={os_kemin.cliente_id}").json()["resultados"]
    assert [x["texto"] for x in r] == ["EN-HANCEA 132B"]
    assert "já envasou 1 vez" in r[0]["detalhe"] and r[0]["densidade"] == "1.1000"

    # Digitando: os do cliente primeiro, depois os outros ativos (inativos nunca)
    r = logado.get(f"/busca/produtos/?q=gli&cliente={os_kemin.cliente_id}").json()["resultados"]
    assert [x["texto"] for x in r] == ["Glicerina bi-destilada"]
    r = logado.get(f"/busca/produtos/?q=e&cliente={os_kemin.cliente_id}").json()["resultados"]
    assert r[0]["texto"] == "EN-HANCEA 132B" and "Glicerina antiga" not in [x["texto"] for x in r]

    # A tela não manda mais a lista inteira de produtos
    pagina = logado.get("/servicos/nova/").content.decode()
    assert "Glicerina antiga" not in pagina and "/busca/produtos/" in pagina


def test_operador_nao_conclui_com_falta(client, os_kemin, joao):
    """Como no teste do Luiz: encheu 1 recipiente em segundos e tentou concluir."""
    call_command("criar_perfis", stdout=io.StringIO())
    joao.groups.add(Group.objects.get(name="Operação"))
    client.force_login(joao)
    ibc = os_kemin.itens.order_by("-capacidade_kg").first()
    url = f"/servicos/operador/{os_kemin.pk}/"
    client.post(url, {"evento": "INICIO", "item": ibc.pk})
    client.post(url, {"evento": "FIM", "cheios": "1"})
    assert ibc.contados == 1
    pagina = client.get(url).content.decode()
    assert "Ainda faltam" in pagina and "avise o supervisor" in pagina
    client.post(f"/servicos/{os_kemin.pk}/concluir/", {"voltar": "operador"})
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.ANDAMENTO


def test_supervisor_conclui_com_falta_so_com_justificativa(logado, os_kemin, joao):
    ibc, tambor = os_kemin.itens.order_by("-capacidade_kg")
    services.apontar(os_kemin, joao, "INICIO", item=ibc, momento=timezone.now() - timedelta(seconds=30))
    services.apontar(os_kemin, joao, "CHEIO", item=ibc)
    dados = {f"unidades_{ibc.pk}": "1", f"kg_{ibc.pk}": "", f"unidades_{tambor.pk}": "0", f"kg_{tambor.pk}": ""}
    logado.post(f"/servicos/{os_kemin.pk}/concluir/", dados)
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.ANDAMENTO  # sem justificativa, não conclui

    logado.post(f"/servicos/{os_kemin.pk}/concluir/", {**dados, "justificativa": "Cliente levou o resto a granel"})
    os_kemin.refresh_from_db()
    assert os_kemin.status == OrdemServico.Status.CONCLUIDA and os_kemin.kg_envasado == D("1260")
    assert os_kemin.justificativa == "Cliente levou o resto a granel"

    # 30 s para 1 IBC (padrão 12 min) = tempo fora do normal: alerta e fora da média
    ind = services.indicadores(os_kemin)
    assert ind.alerta and services.tmo_do_tipo(ibc.tipo) is None
    pagina = logado.get(f"/servicos/{os_kemin.pk}/").content.decode()
    assert "Tempo fora do normal" in pagina and "Cliente levou o resto" in pagina


def test_recipientes_que_voltam_vazios(logado, db):
    cliente = Parceiro.objects.create(razao_social="Cliente X", eh_cliente=True)
    octabin = TipoRecipiente.objects.get(nome="Octabin 1.000 kg")
    tambor = TipoRecipiente.objects.get(nome="Tambor 200 L")
    os = OrdemServico.objects.create(data=date(2026, 11, 16), cliente=cliente, descricao_produto="Glicerina",
                                     quantidade_kg=D("12000"))
    services.novo_item(os, octabin, 13).save()
    services.novo_item(os, tambor, 4).save()
    services.recalcular(os)
    o, t = os.itens.order_by("-capacidade_kg")
    assert o.unidades_total_previstas == 12 and o.voltam_vazios == 1 and t.voltam_vazios == 4
    pagina = logado.get(f"/servicos/{os.pk}/").content.decode()
    assert "voltam vazios para o cliente" in pagina and "4 × Tambor 200 L" in pagina


def test_progresso_e_tempo_estourado(os_kemin, joao):
    """Barra de progresso: kg apontado × chegou; tempo fica vermelho ao passar do previsto."""
    ibc, tambor = os_kemin.itens.order_by("-capacidade_kg")
    p = services.progresso(os_kemin)
    assert p["pct_kg"] == 0 and p["cor"] == "cinza"
    previsto = os_kemin.minutos_previstos
    inicio = timezone.now() - timedelta(minutes=float(previsto) * 0.9)
    services.apontar(os_kemin, joao, "INICIO", item=ibc, momento=inicio)
    p = services.progresso(os_kemin)
    assert p["cor"] == "amarelo" and 85 <= p["pct_tempo"] <= 95
    tempos = services.tempos(os_kemin, agora=inicio + timedelta(minutes=float(previsto) + 30))
    p = services.progresso(os_kemin, tempos)
    assert p["cor"] == "vermelho" and "passou" in p["texto"]
    services.apontar(os_kemin, joao, "CHEIO", item=ibc, quantidade=3)
    assert services.progresso(os_kemin)["pct_kg"] == int(D("3780") * 100 / os_kemin.quantidade_kg)


def test_sugestao_de_cheios_pelo_tempo_padrao(os_kemin, joao):
    ibc = os_kemin.itens.order_by("-capacidade_kg").first()  # padrão 12 min
    agora = timezone.now()
    services.apontar(os_kemin, joao, "INICIO", item=ibc, momento=agora - timedelta(minutes=30))
    trecho = services.trecho_atual(os_kemin, joao, agora)
    assert trecho["item"] == ibc and trecho["sugestao"] == 2
    # Não passa do que o cliente trouxe
    ibc.disponivel = 3
    ibc.save()
    with pytest.raises(ValidationError):
        services.apontar(os_kemin, joao, "PAUSA", motivo="REFEICAO", cheios=4)
    services.apontar(os_kemin, joao, "PAUSA", motivo="REFEICAO", cheios=2)
    assert ibc.contados == 2
