"""
Fretes e pedidos de EXEMPLO, para ver o sistema funcionando com os dados
reais (clientes, contratos e tanques vindos do ERP antigo e da planilha).

Tudo o que é criado aqui fica marcado em `RegistroExemplo` e sai com
`python manage.py carregar_exemplos --apagar`. Nada disso existe na JT:
os preços de frete e os pedidos são inventados.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.cadastros.models import Local, Parceiro
from apps.contratos.models import Contrato
from apps.estoque.models import Movimentacao
from apps.vendas import services as vendas
from apps.vendas.models import Pedido, PedidoOrigem, TabelaFrete

from .models import RegistroExemplo

MARCA = "[EXEMPLO]"

# Frete inventado em R$/kg por UF de destino, conforme a região do tanque.
FRETE_POR_UF = {"SP": Decimal("0.09"), "MG": Decimal("0.17"), "PR": Decimal("0.19"), "RJ": Decimal("0.21"),
                "SC": Decimal("0.24"), "GO": Decimal("0.23"), "RS": Decimal("0.29"), "BA": Decimal("0.38")}
# Ajuste por local (quanto mais longe da capital, mais caro).
AJUSTE_LOCAL = {Local.Tipo.INDUSTRIALIZADOR: Decimal("0.00"), Local.Tipo.TANQUE_TERCEIRO: Decimal("0.02"),
                Local.Tipo.PROPRIO: Decimal("0.02"), Local.Tipo.ARMAZEM: Decimal("-0.02")}

# (trecho do número do contrato de venda, kg, situação final, dias atrás)
PEDIDOS = [
    ("0010-26", 25000, Pedido.Status.CONFIRMADO, 1),   # Upper Dog (MG)
    ("JT2310/2026", 28000, Pedido.Status.CONFIRMADO, 2),  # Aroma, glicerina 80%
    ("4265001905-1", 15000, Pedido.Status.FATURADO, 3),  # Polynt
    ("0028-26", 1250, Pedido.Status.FATURADO, 4),      # GMP Pet Food, IBC
    # Por último, para já enxergar o que os outros reservaram: Maian puxa de dois tanques
    ("9291", 60000, Pedido.Status.RASCUNHO, 0),
]


def _transportadoras():
    return list(Parceiro.objects.filter(eh_transportadora=True).annotate(n=Count("notas")).order_by("-n")[:4])


@transaction.atomic
def carregar(usuario=None) -> dict:
    if RegistroExemplo.existe():
        raise ValidationError("Os exemplos já foram carregados. Use --apagar antes de carregar de novo.")
    hoje = timezone.localdate()
    contagem = {"fretes": 0, "pedidos": 0}
    transportadoras = _transportadoras() or [None]
    locais = Local.objects.filter(movimentacoes__isnull=False).exclude(
        tipo__in=[Local.Tipo.TRANSITO, Local.Tipo.TERCEIROS]).distinct()
    for i, local in enumerate(locais):
        for uf, preco in FRETE_POR_UF.items():
            if TabelaFrete.objects.filter(origem=local, destino_uf=uf, destino_cidade="").exists():
                continue
            frete = TabelaFrete.objects.create(
                origem=local, destino_uf=uf, preco_kg=max(preco + AJUSTE_LOCAL.get(local.tipo, 0), Decimal("0.04")),
                transportadora=transportadoras[i % len(transportadoras)],
            )
            RegistroExemplo.marcar(frete)
            contagem["fretes"] += 1

    for numero, kg, status, dias in PEDIDOS:
        contrato = Contrato.objects.filter(tipo=Contrato.Tipo.VENDA, numero=numero).select_related("parceiro").first()
        if contrato is None or not contrato.parceiro.uf:
            continue
        cliente = contrato.parceiro
        pedido = Pedido.objects.create(
            data=hoje - timedelta(days=dias), cliente=cliente, contrato=contrato, produto=contrato.produto,
            quantidade_kg=kg, preco_unitario=contrato.preco_unitario, destino_cidade=cliente.cidade,
            destino_uf=cliente.uf, usuario=usuario, observacao=f"{MARCA} Pedido fictício para demonstração.",
        )
        RegistroExemplo.marcar(pedido)
        sugestao = vendas.aplicar_sugestao(pedido)
        if sugestao.faltante:
            pedido.observacao += f" Faltou saldo: {sugestao.faltante:.0f} kg."
            pedido.save(update_fields=["observacao"])
        elif status != Pedido.Status.RASCUNHO:
            vendas.confirmar(pedido)
            if status == Pedido.Status.FATURADO:
                for mov in vendas.faturar(pedido, hoje - timedelta(days=dias), usuario=usuario):
                    RegistroExemplo.marcar(mov)
        contagem["pedidos"] += 1
    return contagem


@transaction.atomic
def apagar() -> dict:
    """Remove só o que foi marcado como exemplo, na ordem certa."""
    pedidos = RegistroExemplo.ids(Pedido)
    movs = RegistroExemplo.ids(Movimentacao)
    fretes = RegistroExemplo.ids(TabelaFrete)
    PedidoOrigem.objects.filter(pedido_id__in=pedidos).delete()
    n_pedidos, _ = Pedido.objects.filter(pk__in=pedidos).delete()
    n_movs, _ = Movimentacao.objects.filter(pk__in=movs).delete()
    n_fretes, _ = TabelaFrete.objects.filter(pk__in=fretes).delete()
    RegistroExemplo.objects.all().delete()
    return {"pedidos": len(pedidos), "movimentações": len(movs), "fretes": len(fretes)}
