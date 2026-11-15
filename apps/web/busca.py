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

from apps.cadastros.models import Parceiro

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


class BuscaSelect(forms.Select):
    """Lista que vira caixa de busca digitável.

    No HTML só vai a opção escolhida (não as centenas de cadastros); o
    JavaScript busca as outras no servidor enquanto você digita. A validação
    continua a do campo: só aceita um parceiro da lista permitida."""

    template_name = "web/_busca_select.html"

    def __init__(self, papel="", placeholder="Digite o nome, CNPJ ou cidade…", attrs=None):
        super().__init__(attrs)
        self.papel = papel
        self.placeholder = placeholder

    def get_context(self, name, value, attrs):
        contexto = super().get_context(name, value, attrs)
        contexto["widget"]["url"] = reverse("web:busca_parceiros") + (f"?papel={self.papel}" if self.papel else "")
        contexto["widget"]["placeholder"] = self.placeholder
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
