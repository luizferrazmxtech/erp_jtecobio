"""Filtros e tags usados nas telas.

    {% load erp %}
    {{ valor|kg }}        → 1.234.567
    {{ valor|brl }}       → 1.234.567,89
    {{ valor|num:4 }}     → 5,5751
    {% icone "estoque" %} → desenho SVG do ícone
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


def _fmt(valor, casas: int) -> str:
    if valor in (None, ""):
        return "—"
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return str(valor)
    texto = f"{d:,.{casas}f}"  # 1,234,567.89
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def num(valor, casas=2):
    return _fmt(valor, int(casas))


@register.filter
def kg(valor):
    return _fmt(valor, 0)


@register.filter
def brl(valor):
    return _fmt(valor, 2)


@register.filter
def toneladas(valor):
    if valor in (None, ""):
        return "—"
    return _fmt(Decimal(str(valor)) / 1000, 1)


@register.filter
def abreviado(valor):
    """R$ 2.721.234 → '2,72 mi'; 534.000 → '534 mil'."""
    if valor in (None, ""):
        return "—"
    v = Decimal(str(valor))
    if abs(v) >= 1_000_000:
        return f"{_fmt(v / 1_000_000, 2)} mi"
    if abs(v) >= 1_000:
        return f"{_fmt(v / 1_000, 0)} mil"
    return _fmt(v, 0)


@register.filter
def porcento(parte, total):
    try:
        p, t = Decimal(str(parte or 0)), Decimal(str(total or 0))
    except InvalidOperation:
        return 0
    if t <= 0:
        return 0
    return max(0, min(100, int(round(p * 100 / t))))


ICONES = {
    "painel": "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    "estoque": "M21 8 12 3 3 8v8l9 5 9-5z M3 8l9 5 9-5 M12 13v8",
    "vendas": "M3 3h2l2.4 12.2a2 2 0 0 0 2 1.8h8.2a2 2 0 0 0 2-1.6L21 7H6 M9 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2z M18 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
    "contratos": "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z M14 3v6h6 M8 13h8 M8 17h6",
    "fiscal": "M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z M9 8h6 M9 12h6",
    "cadastros": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M22 21v-2a4 4 0 0 0-3-3.9 M16 3.1a4 4 0 0 1 0 7.8",
    "frete": "M3 6h11v10H3z M14 9h4l3 3v4h-7 M7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    "tanque": "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3z M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
    "baixo": "m6 9 6 6 6-6",
    "direita": "m9 6 6 6-6 6",
    "mais": "M12 5v14 M5 12h14",
    "buscar": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.3-4.3",
    "ver": "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "editar": "M12 20h9 M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
    "sair": "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4 M16 17l5-5-5-5 M21 12H9",
    "menu": "M3 6h18 M3 12h18 M3 18h18",
    "fechar": "M18 6 6 18 M6 6l12 12",
    "transferir": "M7 7h13l-4-4 M17 17H4l4 4",
    "entrada": "M12 3v12 M7 10l5 5 5-5 M5 21h14",
    "saida": "M12 21V9 M7 14l5-5 5 5 M5 3h14",
    "ajuste": "M4 21v-7 M4 10V3 M12 21v-9 M12 8V3 M20 21v-5 M20 12V3 M1 14h6 M9 8h6 M17 16h6",
    "grafico": "M3 3v18h18 M7 16v-5 M12 16V8 M17 16v-3",
    "ok": "M20 6 9 17l-5-5",
    "limpar": "M18 6 6 18 M6 6l12 12",
    "ajuda": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3 M12 17h.01",
    "alerta": "M12 9v4 M12 17h.01 M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
    "gota": "M12 2.7s-6 6.3-6 11.3a6 6 0 0 0 12 0c0-5-6-11.3-6-11.3z",
    "config": "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M4 12h2 M18 12h2 M12 4v2 M12 18v2 M6.3 6.3l1.4 1.4 M16.3 16.3l1.4 1.4 M6.3 17.7l1.4-1.4 M16.3 7.7l1.4-1.4",
    "produto": "M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z M7.5 7.5h.01",
    "local": "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z M12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    "inicio": "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    "voltar": "M19 12H5 M12 19l-7-7 7-7",
    "pontos": "M12 6h.01 M12 12h.01 M12 18h.01",
}


@register.simple_tag
def icone(nome, classe="ic"):
    caminho = ICONES.get(nome, ICONES["direita"])
    pedacos = caminho.split(" M")
    pedacos = [pedacos[0]] + ["M" + p for p in pedacos[1:]]
    partes = "".join(f'<path d="{d}"/>' for d in pedacos)
    return mark_safe(
        f'<svg class="{classe}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{partes}</svg>'
    )


@register.simple_tag(takes_context=True)
def ativo(context, *prefixos):
    """Devolve 'ativo' se a página atual começa com um dos endereços."""
    caminho = context["request"].path
    return "ativo" if any(caminho.startswith(p) for p in prefixos) else ""


@register.simple_tag(takes_context=True)
def url_com(context, **params):
    """Mantém os filtros da página e troca só os parâmetros informados."""
    q = context["request"].GET.copy()
    for k, v in params.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    return "?" + q.urlencode() if q else "?"


SITUACOES = {
    # pedidos
    "RASC": ("Rascunho", "cinza"), "CONF": ("Reservado", "azul"), "FAT": ("Faturado", "verde"),
    "CANC": ("Cancelado", "vermelho"),
    # contratos
    "ABERTO": ("Aberto", "azul"), "FINAL": ("Finalizado", "verde"), "CANCEL": ("Cancelado", "vermelho"),
}


@register.simple_tag
def situacao(codigo, texto=""):
    rotulo, cor = SITUACOES.get(codigo, (texto or codigo, "cinza"))
    return format_html('<span class="etiqueta {}">{}</span>', cor, rotulo)


@register.filter
def mult(valor, fator):
    """{{ preco|mult:pedido.fator_venda }} → preço net."""
    try:
        return Decimal(str(valor or 0)) * Decimal(str(fator or 0))
    except InvalidOperation:
        return ""


@register.filter
def chave(dicionario, k):
    """{{ areas|chave:"vendas" }} → valor do dicionário naquela chave."""
    try:
        return dicionario.get(k)
    except AttributeError:
        return None


@register.filter
def curto(valor):
    """Rótulo curto para gráfico: 12.345.678 → '12,3 mi'; 534.000 → '534 mil'; 0 → '0'."""
    if valor in (None, ""):
        return ""
    v = Decimal(str(valor))
    if abs(v) >= 1_000_000:
        return f"{_fmt(v / 1_000_000, 0 if abs(v) >= 100_000_000 or v % 1_000_000 == 0 else 1)} mi"
    if abs(v) >= 1_000:
        return f"{_fmt(v / 1_000, 0)} mil"
    return _fmt(v, 0)
