from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from apps.cadastros.models import Local, Produto

from . import services
from .forms import AjusteForm, EntradaForm, SaidaForm, TransferenciaForm
from .models import Configuracao, Movimentacao


@login_required
def saldos(request):
    produto = Produto.objects.filter(pk=request.GET.get("produto") or None).first()
    local = Local.objects.filter(pk=request.GET.get("local") or None).first()
    linhas = services.tabela_de_saldos(produto=produto, local=local)
    contexto = {
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
    form = form_class(request.POST or None)
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
