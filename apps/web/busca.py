"""Busca digitável de cadastros (parceiros e contratos).

Quando há muitos cadastros, uma lista com tudo fica difícil de usar. Estes
campos mostram uma caixa de texto: você digita parte do nome, do CNPJ ou da
cidade e o sistema traz os que combinam (static/js/busca.js).
"""

from django import forms
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse

from apps.cadastros.models import Parceiro, Produto

PAPEIS = {
    "cliente": "eh_cliente", "fornecedor": "eh_fornecedor", "transportadora": "eh_transportadora",
    "industrializador": "eh_industrializador", "armazem": "eh_armazem",
}
LIMITE = 15


def _documento(doc: str) -> str:
    d = "".join(c for c in doc or "" if c.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return doc or ""


def dados_parceiro(p: Parceiro) -> dict:
    local = f"{p.cidade.title()}/{p.uf}" if p.cidade else (p.uf or "")
    detalhe = " · ".join(x for x in (_documento(p.documento), local) if x)
    return {"id": p.pk, "texto": p.razao_social, "fantasia": p.nome_fantasia or "", "detalhe": detalhe}


@login_required
def parceiros(request):
    """GET /busca/parceiros/?q=texto&papel=cliente → até 15 parceiros ativos."""
    q = " ".join(request.GET.get("q", "").split())
    qs = Parceiro.objects.filter(ativo=True)
    papeis = [PAPEIS[p] for p in request.GET.get("papel", "").split(",") if p in PAPEIS]
    if papeis:
        filtro = Q()
        for campo in papeis:
            filtro |= Q(**{campo: True})
        qs = qs.filter(filtro)
    digitos = "".join(c for c in q if c.isdigit())
    for palavra in q.split():
        cond = Q(razao_social__icontains=palavra) | Q(nome_fantasia__icontains=palavra) | Q(cidade__icontains=palavra)
        qs = qs.filter(cond)
    if not q.split() and digitos:
        qs = qs.filter(documento__contains=digitos)
    elif digitos and len(digitos) >= 4 and not qs.exists():
        qs = Parceiro.objects.filter(ativo=True, documento__contains=digitos)
    qs = qs.order_by("razao_social")[:LIMITE]
    return JsonResponse({"resultados": [dados_parceiro(p) for p in qs]})


def _densidade(d) -> str:
    return f"densidade {str(d).rstrip('0').rstrip('.').replace('.', ',')}" if d else ""


@login_required
def produtos(request):
    """GET /busca/produtos/?q=texto&cliente=ID → produtos ativos.

    Com cliente: primeiro os produtos que ele já trouxe para envasar (pelas
    OS anteriores, do mais frequente para o menos); sem texto digitado, só
    eles. Os outros produtos aparecem quando você digita."""
    from django.db.models import Count, Max

    from apps.servicos.models import OrdemServico

    q = " ".join(request.GET.get("q", "").split())
    cliente = request.GET.get("cliente", "")
    do_cliente = {}
    if cliente.isdigit():
        linhas = (OrdemServico.objects.filter(cliente_id=cliente, produto__isnull=False)
                  .exclude(status=OrdemServico.Status.CANCELADA)
                  .values("produto").annotate(n=Count("id"), ultima=Max("data")).order_by("-n", "-ultima"))
        do_cliente = {l["produto"]: l for l in linhas}
    filtro = Q()
    for palavra in q.split():
        filtro &= Q(nome__icontains=palavra) | Q(codigo__icontains=palavra) | Q(codigo_legado=palavra)
    resultados = []

    def dados(p, extra=""):
        detalhe = " · ".join(x for x in (extra, p.codigo, _densidade(p.densidade)) if x)
        return {"id": p.pk, "texto": p.nome, "fantasia": "", "detalhe": detalhe,
                "densidade": str(p.densidade) if p.densidade else ""}

    if do_cliente:
        meus = {p.pk: p for p in Produto.objects.filter(filtro, pk__in=do_cliente, ativo=True)}
        for pk, l in do_cliente.items():
            if pk in meus:
                vezes = "1 vez" if l["n"] == 1 else f"{l['n']} vezes"
                resultados.append(dados(meus[pk], f"★ este cliente já envasou {vezes}, a última em {l['ultima']:%d/%m/%Y}"))
    if q:
        outros = Produto.objects.filter(filtro, ativo=True).exclude(pk__in=do_cliente).order_by("nome")
        resultados += [dados(p) for p in outros[:LIMITE]]
    return JsonResponse({"resultados": resultados[:LIMITE + len(do_cliente)]})


class BuscaSelect(forms.Select):
    """Lista que vira caixa de busca digitável.

    No HTML só vai a opção escolhida (não as centenas de cadastros); o
    JavaScript busca as outras no servidor enquanto você digita. A validação
    continua a do campo: só aceita um parceiro da lista permitida."""

    template_name = "web/_busca_select.html"

    def __init__(self, papel="", placeholder="Digite o nome, CNPJ ou cidade…", attrs=None,
                 url_nome="web:busca_parceiros", depende="", vazio=""):
        """url_nome: endereço da busca. depende: nome de outro campo cujo valor
        vai junto na busca (ex.: "cliente"). vazio: texto quando nada combina."""
        super().__init__(attrs)
        self.papel = papel
        self.placeholder = placeholder
        self.url_nome, self.depende, self.vazio = url_nome, depende, vazio

    def get_context(self, name, value, attrs):
        contexto = super().get_context(name, value, attrs)
        contexto["widget"]["url"] = reverse(self.url_nome) + (f"?papel={self.papel}" if self.papel else "")
        contexto["widget"]["placeholder"] = self.placeholder
        contexto["widget"]["depende"] = self.depende
        contexto["widget"]["vazio"] = self.vazio
        return contexto

    def optgroups(self, name, value, attrs=None):
        # Só a opção vazia e a escolhida; o resto vem da busca.
        escolhidos = {str(v) for v in value if v not in (None, "")}
        grupos = []
        for indice, (val, rotulo) in enumerate(self.choices):
            chave = str(getattr(val, "value", val))
            if chave == "" or chave in escolhidos:
                grupos.append((None, [self.create_option(name, val, rotulo, chave in escolhidos, indice, attrs=attrs)], indice))
        return grupos
