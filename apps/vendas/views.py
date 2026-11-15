import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.cadastros.models import Local, Produto, Tanque
from apps.estoque import services as estoque
from apps.contratos.models import Contrato
from apps.estoque.models import Lote

from . import impostos, services
from .forms import FaturarForm, PedidoForm
from .models import Pedido

PODE_VENDER = "vendas.change_pedido"


@login_required
def lista(request):
    f = request.GET
    pedidos = Pedido.objects.select_related("cliente", "produto", "contrato")
    if f.get("status"):
        pedidos = pedidos.filter(status=f["status"])
    if f.get("busca"):
        b = f["busca"].strip()
        filtro = Q(cliente__razao_social__icontains=b) | Q(cliente__nome_fantasia__icontains=b) | Q(contrato__numero__icontains=b)
        if b.isdigit():
            filtro |= Q(numero=int(b))
        pedidos = pedidos.filter(filtro)
    if f.get("produto"):
        pedidos = pedidos.filter(produto_id=f["produto"])
    for campo, filtro in (("de", "data__gte"), ("ate", "data__lte")):
        try:
            if f.get(campo):
                pedidos = pedidos.filter(**{filtro: date.fromisoformat(f[campo])})
        except ValueError:
            pass
    return render(request, "vendas/lista.html", {
        "pagina": Paginator(pedidos, 50).get_page(f.get("pagina")),
        "opcoes_status": Pedido.Status.choices,
        "produtos": Produto.objects.filter(pedido__isnull=False).distinct(),
    })


@login_required
@permission_required("vendas.add_pedido", raise_exception=True)
def novo(request):
    inicial = {}
    contrato = Contrato.objects.filter(pk=request.GET.get("contrato") or None).first()
    if contrato:
        inicial = {"contrato": contrato.pk, "cliente": contrato.parceiro_id, "produto": contrato.produto_id,
                   "preco_unitario": contrato.preco_unitario}
    elif request.GET.get("cliente", "").isdigit():
        inicial = {"cliente": request.GET["cliente"]}
    form = PedidoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        pedido = form.save(commit=False)
        pedido.usuario = request.user
        pedido.save()
        services.aplicar_sugestao(pedido)
        texto = f"Pedido {pedido.numero} criado com as origens sugeridas."
        d = form.cleaned_data
        if d.get("outro_produto"):
            # O outro produto vira um pedido irmão: mesma venda, tanques e margem próprios.
            outro = Pedido.objects.create(
                data=pedido.data, cliente=pedido.cliente, produto=d["produto2"], quantidade_kg=d["quantidade2"],
                preco_unitario=d["preco2"], destino_cidade=pedido.destino_cidade, destino_uf=pedido.destino_uf,
                exportacao=pedido.exportacao, icms_venda=pedido.icms_venda,
                observacao=pedido.observacao, pedido_principal=pedido, usuario=request.user,
            )
            services.aplicar_sugestao(outro)
            texto += f" O outro produto ({outro.produto}) ficou no pedido {outro.numero}, na mesma venda."
        messages.success(request, texto + " Revise e confirme.")
        return redirect("vendas:detalhe", pk=pedido.pk)
    # Todos os contratos de venda abertos, para a tela mostrar só os do cliente escolhido.
    contratos = [
        {"id": c.pk, "cliente": c.parceiro_id, "produto": c.produto_id, "produto_nome": str(c.produto),
         "preco": str(c.preco_unitario or ""),
         "texto": form.fields["contrato"].label_from_instance(c)}
        for c in Contrato.objects.filter(tipo=Contrato.Tipo.VENDA, status=Contrato.Status.ABERTO)
        .select_related("produto").order_by("-mes_referencia", "numero")
    ]
    return render(request, "vendas/novo.html", {"form": form, "contratos": contratos})


def _decimal(valor, padrao=None):
    """Aceita "1234.5" e o formato brasileiro "1.234,5" ou "18.000"."""
    texto = str(valor or "").strip()
    if not texto:
        return padrao
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "")  # "18.000" é dezoito mil
    try:
        return Decimal(texto)
    except InvalidOperation:
        raise ValidationError(f"Número inválido: {valor}")


def _linhas_de_origem(pedido, origens):
    """Um tanque por linha, com o que a tela precisa para desenhar a escolha:
    saldo, capacidade, custo, frete, custo efetivo, a quantidade já escolhida
    e a quantidade que o sistema sugere (rota de menor custo efetivo)."""
    atuais = {(o.local_id, o.lote_id): o for o in origens}
    opcoes = services.opcoes_de_origem(pedido.produto, pedido.destino_cidade, pedido.destino_uf, excluir=pedido)
    sugestao = services.sugerir_origens(
        pedido.produto, pedido.quantidade_kg, pedido.destino_cidade, pedido.destino_uf, excluir=pedido,
    )
    sugeridas = {(o.local.pk, o.lote.pk if o.lote else None): q for o, q in sugestao.origens}
    capacidades = {t.local_id: t.capacidade_kg for t in Tanque.objects.filter(produto=pedido.produto)}
    linhas = []
    for o in opcoes:
        chave = (o.local.pk, o.lote.pk if o.lote else None)
        atual = atuais.get(chave)
        saldo = estoque.saldo(pedido.produto, o.local, o.lote)
        capacidade = capacidades.get(o.local.pk) or saldo
        linhas.append({
            "opcao": o,
            "quantidade": atual.quantidade_kg if atual else "",
            "frete": atual.frete_kg if atual else o.frete_kg,
            "sugerido": sugeridas.get(chave, Decimal("0")),
            "saldo": saldo,
            "capacidade": max(capacidade, saldo),
        })
    return linhas


def _explicacao(pedido, linhas) -> dict:
    """Números do próprio pedido para a ajuda "Como cada custo é calculado".

    Usa o primeiro tanque da lista (o de menor custo efetivo net) como exemplo."""
    q4 = Decimal("0.0001")
    exemplo = None
    if linhas:
        o = linhas[0]["opcao"]
        frete = linhas[0]["frete"]
        exemplo = {
            "nome": o.local.nome,
            "custo": o.custo_kg,
            "custo_net": o.custo_net_kg.quantize(q4),
            "frete": frete,
            "frete_net": impostos.frete_net(frete).quantize(q4) if frete is not None else None,
        }
        if frete is not None:
            exemplo["efetivo"] = o.custo_kg + frete
            exemplo["efetivo_net"] = exemplo["custo_net"] + exemplo["frete_net"]
            preco_net = (pedido.preco_unitario * pedido.fator_venda).quantize(q4)
            exemplo["margem"] = pedido.preco_unitario - exemplo["efetivo"]
            exemplo["margem_net"] = preco_net - exemplo["efetivo_net"]
            # bruta − net = imposto da venda − créditos (produto e frete)
            exemplo["imposto_venda"] = pedido.preco_unitario - preco_net
            exemplo["creditos"] = exemplo["efetivo"] - exemplo["efetivo_net"]
            exemplo["imposto_pagar"] = exemplo["imposto_venda"] - exemplo["creditos"]
    return {
        "preco_net": (pedido.preco_unitario * pedido.fator_venda).quantize(q4),
        "icms_venda": pedido.icms_venda or 0,
        "icms_compra": pedido.produto.icms_compra,
        "exemplo": exemplo,
    }


@login_required
def detalhe(request, pk):
    pedido = get_object_or_404(Pedido.objects.select_related("cliente", "produto", "contrato", "nota"), pk=pk)
    contexto = {"pedido": pedido, "origens": pedido.origens.select_related("local", "lote", "transportadora")}
    if pedido.editavel:
        from apps.web.views import _classe_nivel  # cor do nível, a mesma do painel

        contexto["linhas"] = _linhas_de_origem(pedido, contexto["origens"])
        contexto["classe_nivel"] = _classe_nivel(pedido.produto)
        contexto["explica"] = _explicacao(pedido, contexto["linhas"])
    if pedido.status == Pedido.Status.CONFIRMADO:
        contexto["faturar_form"] = FaturarForm(pedido=pedido)
    return render(request, "vendas/detalhe.html", contexto)


def _acao(request, pk, funcao, sucesso):
    pedido = get_object_or_404(Pedido, pk=pk)
    try:
        funcao(pedido)
    except ValidationError as erro:
        for msg in erro.messages:
            messages.error(request, msg)
    else:
        messages.success(request, sucesso.format(pedido=pedido))
    return redirect("vendas:detalhe", pk=pk)


@login_required
@permission_required(PODE_VENDER, raise_exception=True)
@require_POST
def salvar_origens(request, pk):
    def executar(pedido):
        origens = []
        i = 0
        while f"local_{i}" in request.POST:
            quantidade = _decimal(request.POST.get(f"qtd_{i}"), Decimal("0"))
            if quantidade > 0:
                lote_id = request.POST.get(f"lote_{i}")
                origens.append({
                    "local": Local.objects.get(pk=request.POST[f"local_{i}"]),
                    "lote": Lote.objects.get(pk=lote_id) if lote_id else None,
                    "quantidade_kg": quantidade,
                    "frete_kg": _decimal(request.POST.get(f"frete_{i}")),
                })
            i += 1
        services.definir_origens(pedido, origens)
    return _acao(request, pk, executar, "Origens salvas.")


@login_required
@permission_required(PODE_VENDER, raise_exception=True)
@require_POST
def sugerir(request, pk):
    def executar(pedido):
        minimo = _decimal(request.POST.get("minimo"), Decimal("0"))
        s = services.aplicar_sugestao(pedido, minimo)
        if s.faltante:
            raise ValidationError(f"Não há saldo livre suficiente: faltam {s.faltante:.0f} kg.")
    return _acao(request, pk, executar, "Origens sugeridas pelo menor custo de produto + frete.")


@login_required
@permission_required(PODE_VENDER, raise_exception=True)
@require_POST
def confirmar(request, pk):
    return _acao(request, pk, services.confirmar, "Pedido {pedido.numero} confirmado: saldo reservado nos tanques.")


@login_required
@permission_required(PODE_VENDER, raise_exception=True)
@require_POST
def reabrir(request, pk):
    return _acao(request, pk, services.voltar_para_rascunho, "Reserva liberada; o pedido voltou para rascunho.")


@login_required
@permission_required(PODE_VENDER, raise_exception=True)
@require_POST
def cancelar(request, pk):
    return _acao(request, pk, services.cancelar, "Pedido {pedido.numero} cancelado.")


@login_required
@permission_required(PODE_VENDER, raise_exception=True)
@require_POST
def faturar(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk)
    form = FaturarForm(request.POST, pedido=pedido)
    if not form.is_valid():
        messages.error(request, "Confira a data e a nota do faturamento.")
        return redirect("vendas:detalhe", pk=pk)

    def executar(p):
        services.faturar(p, form.cleaned_data["data"], form.cleaned_data["nota"], usuario=request.user)
    return _acao(request, pk, executar, "Pedido {pedido.numero} faturado: estoque baixado.")
