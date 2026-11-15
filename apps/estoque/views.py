from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from apps.cadastros.models import Local, Produto, Tanque

from . import services
from .forms import AjusteForm, EntradaForm, SaidaForm, TransferenciaForm
from .models import Configuracao, Movimentacao


ULTIMAS = 4  # quantas entradas e saídas aparecem no resumo de cada tanque


def _cartoes(linhas):
    """Cada saldo (produto × local) como um tanque desenhado, agrupado por produto.

    Junta o que o resumo ao passar o mouse mostra: capacidade e ocupação,
    reservado em pedidos, custo bruto e net, últimas entradas e saídas."""
    from apps.vendas import impostos
    from apps.vendas import services as vendas
    from apps.web.views import _classe_nivel

    tanques = {(t.local_id, t.produto_id): t for t in Tanque.objects.all()}
    grupos = {}
    for l in linhas:
        movs = list(Movimentacao.objects.filter(produto=l.produto, local=l.local)
                    .select_related("nota__parceiro").order_by("-data", "-id")[:40])
        entradas = [m for m in movs if m.quantidade_kg > 0][:ULTIMAS]
        saidas = [m for m in movs if m.quantidade_kg < 0][:ULTIMAS]
        reservado = vendas.reservado(l.produto, l.local) if not l.local.eh_de_terceiros else 0
        tanque = tanques.get((l.local.pk, l.produto.pk))
        capacidade = tanque.capacidade_kg if tanque else None
        base = capacidade or l.quantidade_kg or 1
        pct = max(0, min(100, float(l.quantidade_kg * 100 / base)))
        pct_res = max(0.0, min(pct, float(reservado * 100 / base)))
        fator = impostos.fator(l.produto.icms_compra)
        g = grupos.setdefault(l.produto.pk, {"produto": l.produto, "tanques": [], "kg": 0, "valor": 0})
        g["kg"] += l.quantidade_kg
        if not l.local.eh_de_terceiros:
            g["valor"] += l.valor
        g["tanques"].append({
            "linha": l, "capacidade": capacidade, "estimada": bool(tanque and "estimada" in tanque.identificacao),
            "identificacao": tanque.identificacao if tanque and "estimada" not in tanque.identificacao else "",
            "reservado": reservado, "livre": l.quantidade_kg - reservado,
            "pct": round(pct), "altura_livre": pct - pct_res, "altura_reservado": pct_res,
            "custo_net": l.custo_medio * fator, "valor_net": l.valor * fator, "fator": fator,
            "entradas": entradas, "saidas": saidas,
            "ultima": movs[0] if movs else None,
            "classe": _classe_nivel(l.produto),
            "quem": [(m, m.nota.parceiro.razao_social if m.nota_id and m.nota.parceiro_id else (m.documento or m.get_tipo_display()))
                     for m in saidas],
        })
    for g in grupos.values():
        g["tanques"].sort(key=lambda t: -t["linha"].quantidade_kg)
    return sorted(grupos.values(), key=lambda g: g["produto"].nome)


@login_required
def saldos(request):
    produto = Produto.objects.filter(pk=request.GET.get("produto") or None).first()
    local = Local.objects.filter(pk=request.GET.get("local") or None).first()
    linhas = services.tabela_de_saldos(produto=produto, local=local)
    contexto = {
        "grupos": _cartoes(linhas),
        "linhas": linhas,
        "total_kg": sum(l.quantidade_kg for l in linhas),
        "total_valor": sum(l.valor for l in linhas if not l.local.eh_de_terceiros),
        "produtos": Produto.objects.filter(controla_estoque=True),
        "locais": Local.objects.all(),
        "produto_sel": produto,
        "local_sel": local,
        "metodo": Configuracao.atual().get_metodo_custo_display(),
        "ultimas": Movimentacao.objects.select_related("produto", "local", "usuario")[:15],
    }
    return render(request, "estoque/saldos.html", contexto)


def _tela(request, form_class, titulo, executar):
    # Campos podem vir preenchidos pelo endereço (ex.: ?local=3 a partir da lista de locais).
    inicial = {k: v for k, v in request.GET.items() if k in form_class.base_fields}
    form = form_class(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        try:
            executar(form.cleaned_data, request.user)
        except ValidationError as erro:
            for msg in erro.messages:
                form.add_error(None, msg)
        else:
            messages.success(request, f"{titulo} registrada.")
            return redirect("estoque:saldos")
    return render(request, "estoque/formulario.html", {"form": form, "titulo": titulo})


@login_required
@permission_required("estoque.add_movimentacao", raise_exception=True)
def entrada(request):
    def executar(d, usuario):
        services.registrar_entrada(
            produto=d["produto"], local=d["local"], quantidade_kg=d["quantidade_kg"],
            custo_unitario=d["custo_unitario"], data=d["data"], lote_codigo=d["lote_codigo"], tipo=d["tipo"],
            documento=d["documento"], observacao=d["observacao"], usuario=usuario,
        )
    return _tela(request, EntradaForm, "Entrada", executar)


@login_required
@permission_required("estoque.add_movimentacao", raise_exception=True)
def saida(request):
    def executar(d, usuario):
        services.registrar_saida(
            produto=d["produto"], local=d["local"], quantidade_kg=d["quantidade_kg"], data=d["data"],
            lote=d["lote"], documento=d["documento"], observacao=d["observacao"], usuario=usuario,
        )
    return _tela(request, SaidaForm, "Saída", executar)


@login_required
@permission_required("estoque.add_movimentacao", raise_exception=True)
def transferencia(request):
    def executar(d, usuario):
        services.transferir(
            produto=d["produto"], origem=d["origem"], destino=d["destino"], quantidade_kg=d["quantidade_kg"],
            data=d["data"], lote=d["lote"], documento=d["documento"], observacao=d["observacao"], usuario=usuario,
        )
    return _tela(request, TransferenciaForm, "Transferência", executar)


@login_required
@permission_required("estoque.add_movimentacao", raise_exception=True)
def ajuste(request):
    def executar(d, usuario):
        services.ajustar_inventario(
            produto=d["produto"], local=d["local"], quantidade_contada_kg=d["quantidade_contada_kg"],
            data=d["data"], motivo=d["motivo"], lote=d["lote"], usuario=usuario,
        )
    return _tela(request, AjusteForm, "Ajuste de inventário", executar)
