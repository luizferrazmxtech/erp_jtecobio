"""
Telas do sistema: painel, consultas (contratos, notas, cadastros,
movimentações) e formulários de cadastro.

As regras de negócio continuam nos `services` de cada app; aqui só se
busca e organiza o que cada tela mostra.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.cadastros.models import Local, Parceiro, Produto, Tanque
from apps.contratos.models import Contrato
from apps.estoque import services as estoque
from apps.estoque.models import Movimentacao
from apps.fiscal.models import NotaFiscal, NotaItem
from apps.vendas import services as vendas
from apps.vendas.models import Pedido, TabelaFrete

ZERO = Decimal("0")
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
POR_PAGINA = 50


def _paginar(request, qs, por_pagina=POR_PAGINA):
    pagina = Paginator(qs, por_pagina).get_page(request.GET.get("pagina"))
    return pagina


def _data(texto):
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Entrar / sair
# ---------------------------------------------------------------------------


class Entrar(auth_views.LoginView):
    template_name = "web/entrar.html"
    redirect_authenticated_user = True


class Sair(auth_views.LogoutView):
    next_page = "entrar"


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


def _saudacao(agora) -> str:
    if agora.hour < 12:
        return "Bom dia"
    if agora.hour < 18:
        return "Boa tarde"
    return "Boa noite"


def _classe_nivel(produto: Produto) -> str:
    if produto.tipo == Produto.Tipo.GBD_FRACIONADA:
        return "frac"
    if produto.tipo == Produto.Tipo.GBD_GRANEL:
        return "gbd"
    return ""


def tanques_com_saldo():
    """Cada linha de saldo (produto × local) com capacidade e reserva."""
    tanques = {(t.local_id, t.produto_id): t for t in Tanque.objects.all()}
    lista = []
    for linha in estoque.tabela_de_saldos():
        if linha.local.eh_de_terceiros:
            continue
        reservado = vendas.reservado(linha.produto, linha.local)
        tanque = tanques.get((linha.local.pk, linha.produto.pk))
        capacidade = tanque.capacidade_kg if tanque else None
        base = capacidade or linha.quantidade_kg or 1
        pct = max(0, min(100, int(linha.quantidade_kg * 100 / base)))
        pct_reservado = max(0, min(pct, int(reservado * 100 / base)))
        lista.append({
            "local": linha.local, "produto": linha.produto, "saldo": linha.quantidade_kg,
            "valor": linha.valor, "custo": linha.custo_medio, "reservado": reservado,
            "livre": linha.quantidade_kg - reservado, "capacidade": capacidade,
            "pct": pct, "pct_reservado": pct_reservado, "pct_livre": pct - pct_reservado,
            "estimada": bool(tanque and "estimada" in tanque.identificacao),
            "classe": _classe_nivel(linha.produto),
        })
    lista.sort(key=lambda t: -t["saldo"])
    return lista


def _vendas_por_mes(hoje: date, meses=12):
    """Faturamento das NF-e de venda (CFOP 5.1xx, 6.1xx, 7.1xx), não canceladas."""
    inicio = date(hoje.year - (1 if hoje.month < meses else 0), (hoje.month - meses) % 12 + 1, 1)
    linhas = (
        NotaFiscal.objects.filter(direcao=NotaFiscal.Direcao.SAIDA, cancelada=False, data_emissao__gte=inicio)
        .filter(Q(cfop__startswith="51") | Q(cfop__startswith="61") | Q(cfop__startswith="71"))
        .annotate(mes=TruncMonth("data_emissao")).values("mes").annotate(total=Sum("valor_total")).order_by("mes")
    )
    por_mes = {l["mes"].strftime("%Y-%m") if hasattr(l["mes"], "strftime") else str(l["mes"])[:7]: l["total"] for l in linhas}
    resultado, ano, mes = [], inicio.year, inicio.month
    for _ in range(meses):
        chave = f"{ano}-{mes:02d}"
        resultado.append({"rotulo": f"{MESES[mes - 1]}/{str(ano)[2:]}", "total": por_mes.get(chave) or ZERO})
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1
    maior = max((r["total"] for r in resultado), default=ZERO) or 1
    for r in resultado:
        r["altura"] = int(r["total"] * 100 / maior)
    return resultado


def _somar_saldos(contratos) -> dict:
    """Soma em Python: o saldo já é uma soma (anotação) e o banco não
    deixa somar uma soma direto."""
    total = {"kg": ZERO, "valor": ZERO, "qtd": ZERO}
    for c in contratos:
        total["kg"] += c.saldo
        total["valor"] += c.saldo * c.preco_unitario
        total["qtd"] += c.quantidade_kg
    return total


def _saldo_contratos(tipo):
    qs = list(Contrato.objects.com_saldo().filter(tipo=tipo, status=Contrato.Status.ABERTO, saldo__gt=0)
              .select_related("parceiro", "produto").order_by("mes_referencia", "numero"))
    return qs, _somar_saldos(qs)


@login_required
def painel(request):
    hoje = timezone.localdate()
    tanques = tanques_com_saldo()
    venda_abertos, venda_total = _saldo_contratos(Contrato.Tipo.VENDA)
    compra_abertos, compra_total = _saldo_contratos(Contrato.Tipo.COMPRA)
    vendas_mes = _vendas_por_mes(hoje)
    notas_mes = NotaFiscal.objects.filter(
        direcao=NotaFiscal.Direcao.SAIDA, cancelada=False, data_emissao__year=hoje.year, data_emissao__month=hoje.month,
    ).filter(Q(cfop__startswith="51") | Q(cfop__startswith="61") | Q(cfop__startswith="71"))
    pedidos_abertos = Pedido.objects.filter(status__in=[Pedido.Status.RASCUNHO, Pedido.Status.CONFIRMADO])
    maiores_clientes = (
        NotaFiscal.objects.filter(direcao=NotaFiscal.Direcao.SAIDA, cancelada=False, data_emissao__year=hoje.year)
        .filter(Q(cfop__startswith="51") | Q(cfop__startswith="61") | Q(cfop__startswith="71"))
        .values("parceiro", "parceiro__razao_social", "parceiro__nome_fantasia")
        .annotate(total=Sum("valor_total"), notas=Count("id")).order_by("-total")[:6]
    )
    maior_cliente = maiores_clientes[0]["total"] if maiores_clientes else 1
    contexto = {
        "saudacao": _saudacao(timezone.localtime()),
        "hoje": hoje,
        "tanques": tanques,
        "estoque_kg": sum((t["saldo"] for t in tanques), ZERO),
        "estoque_valor": sum((t["valor"] for t in tanques), ZERO),
        "reservado_kg": sum((t["reservado"] for t in tanques), ZERO),
        "venda_total": venda_total, "venda_qtd": len(venda_abertos),
        "compra_total": compra_total, "compra_qtd": len(compra_abertos),
        "contratos_venda": venda_abertos[:8],
        "faturado_mes": notas_mes.aggregate(t=Sum("valor_total"))["t"] or ZERO,
        "notas_mes": notas_mes.count(),
        "pedidos_abertos": pedidos_abertos.count(),
        "ultimos_pedidos": Pedido.objects.select_related("cliente", "produto")[:6],
        "vendas_mes": vendas_mes,
        "total_12m": sum((v["total"] for v in vendas_mes), ZERO),
        "maiores_clientes": [
            {**c, "pct": int(c["total"] * 100 / maior_cliente)} for c in maiores_clientes
        ],
        "ultimas_notas": NotaFiscal.objects.filter(direcao=NotaFiscal.Direcao.SAIDA).select_related("parceiro")[:6],
    }
    return render(request, "web/painel.html", contexto)


# ---------------------------------------------------------------------------
# Estoque: movimentações
# ---------------------------------------------------------------------------


@login_required
def movimentacoes(request):
    qs = Movimentacao.objects.select_related("produto", "local", "usuario", "lote")
    f = request.GET
    if f.get("produto"):
        qs = qs.filter(produto_id=f["produto"])
    if f.get("local"):
        qs = qs.filter(local_id=f["local"])
    if f.get("tipo"):
        qs = qs.filter(tipo=f["tipo"])
    if _data(f.get("de")):
        qs = qs.filter(data__gte=_data(f["de"]))
    if _data(f.get("ate")):
        qs = qs.filter(data__lte=_data(f["ate"]))
    return render(request, "web/movimentacoes.html", {
        "pagina": _paginar(request, qs),
        "produtos": Produto.objects.filter(movimentacoes__isnull=False).distinct(),
        "locais": Local.objects.filter(movimentacoes__isnull=False).distinct(),
        "tipos": Movimentacao.Tipo.choices,
    })


# ---------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------


@login_required
def contratos(request):
    f = request.GET
    qs = Contrato.objects.com_saldo().select_related("parceiro", "produto")
    if f.get("tipo") in ("C", "V"):
        qs = qs.filter(tipo=f["tipo"])
    if f.get("situacao"):
        qs = qs.filter(status=f["situacao"])
    if f.get("produto"):
        qs = qs.filter(produto_id=f["produto"])
    if f.get("modalidade"):
        qs = qs.filter(modalidade=f["modalidade"])
    if f.get("busca"):
        b = f["busca"].strip()
        qs = qs.filter(Q(numero__icontains=b) | Q(parceiro__razao_social__icontains=b) | Q(parceiro__nome_fantasia__icontains=b))
    if f.get("com_saldo"):
        qs = qs.filter(saldo__gt=0)
    soma = _somar_saldos(qs)
    totais = {"qtd": soma["qtd"], "saldo": soma["kg"]}
    tipo = f.get("tipo")
    titulo = {"V": "Contratos de venda", "C": "Contratos de compra"}.get(tipo, "Contratos")
    return render(request, "web/contratos.html", {
        "pagina": _paginar(request, qs.order_by("-mes_referencia", "numero")),
        "totais": totais, "titulo": titulo, "tipo": tipo,
        "produtos": Produto.objects.filter(contrato__isnull=False).distinct(),
        "situacoes": Contrato.Status.choices, "modalidades": Contrato.Modalidade.choices,
    })


@login_required
def contrato(request, pk):
    c = get_object_or_404(Contrato.objects.com_saldo().select_related("parceiro", "produto"), pk=pk)
    itens = c.itens_nota.select_related("nota").order_by("nota__data_emissao")
    return render(request, "web/contrato.html", {
        "c": c, "itens": itens, "em_notas": c.quantidade_entregue - c.entregue_antes, "pedidos": c.pedidos.select_related("produto"),
        "pct": int((c.quantidade_kg - c.saldo) * 100 / c.quantidade_kg) if c.quantidade_kg else 0,
    })


# ---------------------------------------------------------------------------
# Notas fiscais
# ---------------------------------------------------------------------------


@login_required
def notas(request):
    f = request.GET
    qs = NotaFiscal.objects.select_related("parceiro")
    direcao = f.get("direcao", "S")
    if direcao == "CTE":
        qs = qs.filter(modelo=NotaFiscal.Modelo.CTE)
        titulo = "CT-e (conhecimentos de frete)"
    elif direcao == "E":
        qs = qs.filter(direcao=NotaFiscal.Direcao.ENTRADA).exclude(modelo=NotaFiscal.Modelo.CTE)
        titulo = "Notas de entrada"
    else:
        direcao = "S"
        qs = qs.filter(direcao=NotaFiscal.Direcao.SAIDA)
        titulo = "Notas de saída"
    if f.get("numero"):
        qs = qs.filter(numero=f["numero"]) if f["numero"].isdigit() else qs.none()
    if f.get("busca"):
        b = f["busca"].strip()
        qs = qs.filter(Q(parceiro__razao_social__icontains=b) | Q(parceiro__nome_fantasia__icontains=b) | Q(pedido_cliente__icontains=b))
    if _data(f.get("de")):
        qs = qs.filter(data_emissao__gte=_data(f["de"]))
    if _data(f.get("ate")):
        qs = qs.filter(data_emissao__lte=_data(f["ate"]))
    if f.get("cfop"):
        qs = qs.filter(cfop__startswith=f["cfop"])
    if f.get("situacao") == "validas":
        qs = qs.filter(cancelada=False)
    elif f.get("situacao") == "canceladas":
        qs = qs.filter(cancelada=True)
    if f.get("produto"):
        qs = qs.filter(itens__produto_id=f["produto"]).distinct()
    total = qs.filter(cancelada=False).aggregate(t=Sum("valor_total"))["t"] or ZERO
    return render(request, "web/notas.html", {
        "pagina": _paginar(request, qs.order_by("-data_emissao", "-numero")),
        "titulo": titulo, "direcao": direcao, "total": total,
        "produtos": Produto.objects.filter(controla_estoque=True).order_by("nome"),
    })


@login_required
def nota(request, pk):
    n = get_object_or_404(NotaFiscal.objects.select_related("parceiro"), pk=pk)
    return render(request, "web/nota.html", {
        "n": n, "itens": n.itens.select_related("produto", "contrato"),
        "pedidos": n.pedidos.all(), "movimentacoes": n.movimentacoes.select_related("local", "produto"),
    })


# ---------------------------------------------------------------------------
# Cadastros
# ---------------------------------------------------------------------------

PAPEIS = {
    "cliente": ("eh_cliente", "Clientes"),
    "fornecedor": ("eh_fornecedor", "Fornecedores"),
    "transportadora": ("eh_transportadora", "Transportadoras"),
    "industrializador": ("eh_industrializador", "Industrializadores"),
    "armazem": ("eh_armazem", "Armazéns e distribuidoras"),
}


@login_required
def parceiros(request):
    f = request.GET
    papel = f.get("papel", "cliente")
    campo, titulo = PAPEIS.get(papel, PAPEIS["cliente"])
    qs = Parceiro.objects.filter(**{campo: True}).annotate(n_notas=Count("notas"))
    if f.get("busca"):
        b = f["busca"].strip()
        digitos = "".join(c for c in b if c.isdigit())
        filtro = Q(razao_social__icontains=b) | Q(nome_fantasia__icontains=b) | Q(cidade__icontains=b)
        if len(digitos) >= 4:
            filtro |= Q(documento__contains=digitos)
        qs = qs.filter(filtro)
    if f.get("uf"):
        qs = qs.filter(uf=f["uf"].upper())
    if f.get("situacao") == "inativos":
        qs = qs.filter(ativo=False)
    elif f.get("situacao") != "todos":
        qs = qs.filter(ativo=True)
    ordem = "-n_notas" if f.get("ordem") == "movimento" else "razao_social"
    return render(request, "web/parceiros.html", {
        "pagina": _paginar(request, qs.order_by(ordem)), "papel": papel, "titulo": titulo,
        "ufs": Parceiro.objects.exclude(uf="").values_list("uf", flat=True).distinct().order_by("uf"),
    })


@login_required
def parceiro(request, pk):
    p = get_object_or_404(Parceiro, pk=pk)
    notas_qs = p.notas.order_by("-data_emissao")
    vendas_ano = (notas_qs.filter(direcao=NotaFiscal.Direcao.SAIDA, cancelada=False)
                  .filter(Q(cfop__startswith="51") | Q(cfop__startswith="61") | Q(cfop__startswith="71"))
                  .aggregate(t=Sum("valor_total"), n=Count("id")))
    return render(request, "web/parceiro.html", {
        "p": p, "notas": notas_qs[:15], "total_notas": notas_qs.count(), "vendas": vendas_ano,
        "contratos": Contrato.objects.com_saldo().filter(parceiro=p).select_related("produto")[:15],
        "locais": p.local_set.all(),
    })


@login_required
def produtos(request):
    f = request.GET
    qs = Produto.objects.annotate(n_itens=Count("notaitem"))
    if f.get("busca"):
        b = f["busca"].strip()
        qs = qs.filter(Q(nome__icontains=b) | Q(codigo__icontains=b) | Q(codigo_legado=b))
    if f.get("tipo"):
        qs = qs.filter(tipo=f["tipo"])
    if f.get("estoque") == "sim":
        qs = qs.filter(controla_estoque=True)
    ordem = "nome" if f.get("ordem") == "nome" else "-n_itens"
    return render(request, "web/produtos.html", {
        "pagina": _paginar(request, qs.order_by(ordem, "nome")), "tipos": Produto.Tipo.choices,
    })


@login_required
def locais(request):
    linhas = defaultdict(list)
    for t in tanques_com_saldo():
        linhas[t["local"].pk].append(t)
    lista = []
    for local in Local.objects.select_related("parceiro").prefetch_related("tanques__produto"):
        lista.append({"local": local, "saldos": linhas.get(local.pk, []), "tanques": list(local.tanques.all())})
    return render(request, "web/locais.html", {"lista": lista})


@login_required
def fretes(request):
    f = request.GET
    qs = TabelaFrete.objects.select_related("origem", "transportadora")
    if f.get("origem"):
        qs = qs.filter(origem_id=f["origem"])
    if f.get("uf"):
        qs = qs.filter(destino_uf=f["uf"].upper())
    return render(request, "web/fretes.html", {
        "fretes": qs, "locais": Local.objects.filter(fretes__isnull=False).distinct(),
        "ufs": TabelaFrete.objects.values_list("destino_uf", flat=True).distinct().order_by("destino_uf"),
    })


# ---------------------------------------------------------------------------
# Formulários de cadastro (adicionar e editar)
# ---------------------------------------------------------------------------


class ParceiroForm(forms.ModelForm):
    class Meta:
        model = Parceiro
        fields = ["razao_social", "nome_fantasia", "documento", "inscricao_estadual", "cidade", "uf", "ativo",
                  "eh_cliente", "eh_fornecedor", "eh_transportadora", "eh_industrializador", "eh_armazem"]


class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = ["codigo", "nome", "tipo", "unidade", "densidade", "ncm", "icms_compra", "controla_estoque", "ativo"]


class LocalForm(forms.ModelForm):
    """Local de armazenagem. Ao criar, já dá para cadastrar o tanque dele."""

    tanque_produto = forms.ModelChoiceField(
        label="Produto do tanque", queryset=Produto.objects.none(), required=False,
        help_text="Deixe em branco se ainda não sabe; dá para adicionar tanques depois.")
    tanque_capacidade = forms.DecimalField(label="Capacidade (kg)", required=False, min_value=0,
                                           max_digits=14, decimal_places=3)
    tanque_identificacao = forms.CharField(label="Identificação", required=False, max_length=50,
                                           help_text="Ex.: TQ-03")

    class Meta:
        model = Local
        fields = ["nome", "tipo", "parceiro", "cidade", "uf", "ativo"]
        help_texts = {"parceiro": "Dono do local (distribuidora, industrializador, armazém). Se não estiver na lista, cadastre o parceiro antes."}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # Qualquer parceiro ativo: uma distribuidora nova ainda pode não estar marcada como armazém.
        self.fields["parceiro"].queryset = Parceiro.objects.filter(
            Q(ativo=True) | Q(pk=self.instance.parceiro_id or 0)).order_by("razao_social")
        if self.instance.pk:  # editar: os tanques ficam em "Adicionar tanque"
            for c in ("tanque_produto", "tanque_capacidade", "tanque_identificacao"):
                del self.fields[c]
        else:
            self.fields["tanque_produto"].queryset = Produto.objects.filter(controla_estoque=True, ativo=True)

    def clean(self):
        dados = super().clean()
        if dados.get("tanque_produto") and not dados.get("tanque_capacidade"):
            self.add_error("tanque_capacidade", "Informe a capacidade do tanque.")
        return dados

    def save(self, commit=True):
        local = super().save(commit)
        produto = self.cleaned_data.get("tanque_produto")
        if commit and produto:
            Tanque.objects.create(local=local, produto=produto,
                                  capacidade_kg=self.cleaned_data["tanque_capacidade"],
                                  identificacao=self.cleaned_data.get("tanque_identificacao") or "")
        return local


class TanqueForm(forms.ModelForm):
    class Meta:
        model = Tanque
        fields = ["local", "produto", "capacidade_kg", "identificacao"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["produto"].queryset = Produto.objects.filter(controla_estoque=True, ativo=True)


class FreteForm(forms.ModelForm):
    class Meta:
        model = TabelaFrete
        fields = ["origem", "destino_uf", "destino_cidade", "preco_kg", "transportadora"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["transportadora"].queryset = Parceiro.objects.filter(eh_transportadora=True).order_by("razao_social")


class ContratoForm(forms.ModelForm):
    class Meta:
        model = Contrato
        fields = ["tipo", "numero", "parceiro", "produto", "modalidade", "quantidade_kg", "preco_unitario",
                  "frete_previsto", "mes_referencia", "entregue_antes", "status", "observacao"]
        widgets = {"mes_referencia": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "observacao": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["parceiro"].queryset = Parceiro.objects.filter(
            Q(eh_cliente=True) | Q(eh_fornecedor=True), ativo=True).order_by("razao_social")
        self.fields["produto"].queryset = Produto.objects.filter(controla_estoque=True, ativo=True)


# nome: (modelo, form, permissão, título, ícone, para onde voltar)
CADASTROS = {
    "parceiro": (Parceiro, ParceiroForm, "cadastros", "parceiro", "cadastros", "web:parceiros"),
    "produto": (Produto, ProdutoForm, "cadastros", "produto", "produto", "web:produtos"),
    "local": (Local, LocalForm, "cadastros", "local", "local", "web:locais"),
    "tanque": (Tanque, TanqueForm, "cadastros", "tanque", "tanque", "web:locais"),
    "frete": (TabelaFrete, FreteForm, "vendas", "frete", "frete", "web:fretes"),
    "contrato": (Contrato, ContratoForm, "contratos", "contrato", "contratos", "web:contratos"),
}

SECOES = {
    "local": [("Preencha os", "dados do local", "Onde o produto fica guardado: distribuidora, armazém, industrializador.", ["nome", "tipo", "parceiro", "cidade", "uf", "ativo"]),
              ("Já cadastre o", "tanque", "Opcional. Um tanque por produto em cada local; outros podem ser adicionados depois.", ["tanque_produto", "tanque_capacidade", "tanque_identificacao"])],
    "parceiro": [("Preencha os", "dados gerais", "Nome, documento e cidade.", ["razao_social", "nome_fantasia", "documento", "inscricao_estadual", "cidade", "uf", "ativo"]),
                 ("Marque os", "papéis", "Uma mesma empresa pode ser cliente e fornecedora, por exemplo.", ["eh_cliente", "eh_fornecedor", "eh_transportadora", "eh_industrializador", "eh_armazem"])],
    "contrato": [("Preencha os", "dados do contrato", "Quem, o quê e quando.", ["tipo", "numero", "parceiro", "produto", "modalidade", "mes_referencia", "status"]),
                 ("Informe", "quantidades e preços", "O saldo é calculado: contratado − entregue antes − notas ligadas.", ["quantidade_kg", "preco_unitario", "frete_previsto", "entregue_antes"]),
                 ("Adicione", "observações", "", ["observacao"])],
}


def _form_cadastro(request, nome, pk=None):
    modelo, form_class, app, codigo, ic, voltar = CADASTROS[nome]
    acao = "change" if pk else "add"
    if not request.user.has_perm(f"{app}.{acao}_{modelo._meta.model_name}"):
        messages.error(request, "Você não tem permissão para isso.")
        return redirect(voltar)
    obj = get_object_or_404(modelo, pk=pk) if pk else None
    inicial = {k: v for k, v in request.GET.items() if k in form_class.base_fields}
    if nome == "parceiro" and not pk and request.GET.get("papel") in PAPEIS:
        inicial[PAPEIS[request.GET["papel"]][0]] = True
    form = form_class(request.POST or None, instance=obj, initial=inicial)
    if request.method == "POST" and form.is_valid():
        salvo = form.save()
        messages.success(request, f"{modelo._meta.verbose_name.capitalize()} salvo: {salvo}.")
        if nome == "parceiro":
            return redirect("web:parceiro", pk=salvo.pk)
        if nome == "contrato":
            return redirect("web:contrato", pk=salvo.pk)
        if nome in ("local", "tanque") and not pk:
            messages.info(request, "Para colocar saldo no tanque, use Dar entrada na lista de locais.")
        return redirect(voltar)
    secoes = []
    for leve, forte, sub, campos in SECOES.get(nome, [("Preencha os", "dados", "", list(form.fields))]):
        campos = [form[c] for c in campos if c in form.fields]
        if campos:
            secoes.append({"leve": leve, "forte": forte, "sub": sub, "campos": campos})
    verbo = "Editar" if pk else "Adicionar"
    return render(request, "web/formulario.html", {
        "form": form, "secoes": secoes, "titulo": f"{verbo} {modelo._meta.verbose_name}", "icone": ic,
        "voltar": voltar, "obj": obj, "verbo": verbo,
    })


@login_required
def adicionar(request, nome):
    return _form_cadastro(request, nome)


@login_required
def editar(request, nome, pk):
    return _form_cadastro(request, nome, pk)


# ---------------------------------------------------------------------------
# Conferir a planilha DRE
# ---------------------------------------------------------------------------

GRAVIDADES = {
    "alta": ("Muda o número", "vermelho"),
    "media": ("Confirmar", "amarelo"),
    "baixa": ("Risco futuro", "cinza"),
}


@login_required
def conferir_planilha(request):
    """Recebe a planilha DRE (.xlsx), refaz as contas e lista as fórmulas erradas.

    O arquivo não é guardado: é lido, conferido e descartado."""
    from apps.integracao.conferencia import conferir

    achados, nome, erro = None, "", ""
    if request.method == "POST" and request.FILES.get("arquivo"):
        arquivo = request.FILES["arquivo"]
        nome = arquivo.name
        if not nome.lower().endswith((".xlsx", ".xlsm")):
            erro = "Envie a planilha no formato .xlsx."
        else:
            try:
                achados = conferir(arquivo)
            except Exception as e:  # planilha corrompida ou em outro formato
                erro = f"Não consegui ler a planilha: {e}"
    linhas = []
    for a in achados or []:
        rotulo, cor = GRAVIDADES[a.gravidade]
        linhas.append({"a": a, "rotulo": rotulo, "cor": cor})
    contagem = {g: sum(1 for a in achados or [] if a.gravidade == g) for g in GRAVIDADES}
    return render(request, "web/conferir_planilha.html", {
        "linhas": linhas, "nome": nome, "erro": erro, "conferiu": achados is not None, "contagem": contagem,
    })
