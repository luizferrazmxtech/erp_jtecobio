"""Telas das ordens de serviço de envase.

Duas famílias de tela:
- escritório (menu Serviços): lista, nova OS com cálculo do fracionamento,
  detalhe com tempos, TMO e valor, cadastro de recipientes;
- operador (/servicos/operador/): tela simples, de botões grandes, para o
  funcionário apontar início, pausas e fim no celular ou tablet.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.fiscal.models import NotaFiscal
from apps.web.templatetags.erp import kg, num

from . import services
from .forms import CorrecaoForm, OrdemServicoForm
from .models import Apontamento, ItemOS, OrdemServico, TipoRecipiente

PODE_EDITAR = "servicos.change_ordemservico"
PODE_APONTAR = "servicos.add_apontamento"
GRUPO_OPERACAO = "Operação"
ZERO = Decimal("0")


def so_operacao(usuario) -> bool:
    """Funcionário do envase que só usa a tela de apontamento."""
    if not usuario.is_authenticated or usuario.is_superuser:
        return False
    nomes = set(usuario.groups.values_list("name", flat=True))
    return nomes == {GRUPO_OPERACAO}


def _decimal(texto):
    """Aceita 1250,5 / 1.250,5 / 1250.5."""
    t = (texto or "").strip().replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ValidationError(f"Número inválido: {texto}")


def _inteiro(texto):
    t = (texto or "").strip().replace(".", "")
    if not t:
        return None
    if not t.isdigit():
        raise ValidationError(f"Quantidade inválida: {texto}")
    return int(t)


def _inteiro_com_sinal(texto):
    """Número inteiro que pode ser negativo (correção de contagem). Vazio = None."""
    t = (texto or "").strip()
    if not t:
        return None
    try:
        return int(t)
    except ValueError:
        raise ValidationError(f"Quantidade inválida: {texto}")


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------


@login_required
def lista(request):
    f = request.GET
    qs = OrdemServico.objects.select_related("cliente", "produto").prefetch_related("itens__tipo")
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("busca"):
        b = f["busca"].strip()
        filtro = (Q(cliente__razao_social__icontains=b) | Q(cliente__nome_fantasia__icontains=b)
                  | Q(descricao_produto__icontains=b) | Q(placa__icontains=b) | Q(pedido_cliente__icontains=b))
        if b.isdigit():
            filtro |= Q(numero=int(b))
        qs = qs.filter(filtro)
    for campo, filtro in (("de", "data__gte"), ("ate", "data__lte")):
        try:
            if f.get(campo):
                qs = qs.filter(**{filtro: f[campo]})
        except ValidationError:
            pass
    hoje = timezone.localdate()
    do_mes = services.valor_por_mes(hoje.replace(day=1)).get(hoje.strftime("%Y-%m"), ZERO)
    contagem = {
        "abertas": OrdemServico.objects.filter(status=OrdemServico.Status.ABERTA).count(),
        "andamento": OrdemServico.objects.filter(status=OrdemServico.Status.ANDAMENTO).count(),
        "a_faturar": OrdemServico.objects.filter(status=OrdemServico.Status.CONCLUIDA).count(),
        "valor_mes": do_mes,
    }
    pagina = Paginator(qs, 50).get_page(f.get("pagina"))
    for os in pagina:
        os.progresso = services.progresso(os) if os.status != OrdemServico.Status.CANCELADA else None
    return render(request, "servicos/lista.html", {
        "pagina": pagina,
        "opcoes_status": OrdemServico.Status.choices, "contagem": contagem,
    })


# ---------------------------------------------------------------------------
# Nova / editar
# ---------------------------------------------------------------------------


def _linhas_recipientes(post) -> list[tuple]:
    """Lê as linhas tipo_N / disponivel_N / preco_N do formulário."""
    tipos = {t.pk: t for t in TipoRecipiente.objects.all()}
    linhas, erros = [], []
    indices = sorted({int(k.split("_")[1]) for k in post if k.startswith("tipo_") and k.split("_")[1].isdigit()})
    for i in indices:
        tipo_id = post.get(f"tipo_{i}")
        if not tipo_id:
            continue
        tipo = tipos.get(int(tipo_id)) if tipo_id.isdigit() else None
        if not tipo:
            erros.append("Tipo de recipiente inválido.")
            continue
        try:
            linhas.append((tipo, _inteiro(post.get(f"disponivel_{i}")), _decimal(post.get(f"preco_{i}"))))
        except ValidationError as e:
            erros.append(f"{tipo}: {e.messages[0]}")
    if not linhas and not erros:
        erros.append("Informe pelo menos um tipo de recipiente que o cliente trouxe.")
    return linhas, erros


def _form_os(request, os=None):
    if os and not os.editavel:
        messages.error(request, "Só dá para editar uma OS que ainda não começou.")
        return redirect(os)
    form = OrdemServicoForm(request.POST or None, instance=os)
    erros_recip = []
    if request.method == "POST":
        linhas, erros_recip = _linhas_recipientes(request.POST)
        if form.is_valid() and not erros_recip:
            with transaction.atomic():
                nova = form.save(commit=False)
                if not nova.pk:
                    nova.usuario = request.user
                nova.save()
                nova.itens.all().delete()
                for tipo, disponivel, preco in linhas:
                    services.novo_item(nova, tipo, disponivel, preco).save()
                sobra = services.recalcular(nova)
            texto = f"{nova} salva."
            if sobra > 0:
                messages.warning(request, f"{texto} Atenção: {kg(sobra)} kg não cabem nos recipientes informados.")
            else:
                messages.success(request, texto)
            return redirect(nova)
    tipos = TipoRecipiente.objects.filter(Q(ativo=True) | Q(itemos__os=os) if os else Q(ativo=True)).distinct()
    if request.method == "POST":
        linhas_tela = [{"tipo": request.POST.get(f"tipo_{i}", ""), "disponivel": request.POST.get(f"disponivel_{i}", ""),
                        "preco": request.POST.get(f"preco_{i}", "")} for i in range(int(request.POST.get("n_linhas") or 0))]
    elif os:
        linhas_tela = [{"tipo": str(i.tipo_id), "disponivel": i.disponivel or "", "preco": str(i.preco_kg).replace(".", ",")}
                       for i in os.itens.all()]
    else:
        linhas_tela = []
    linhas_tela += [{"tipo": "", "disponivel": "", "preco": ""}] * max(1, 3 - len(linhas_tela))
    return render(request, "servicos/formulario.html", {
        "form": form, "os": os, "tipos": tipos, "linhas": linhas_tela, "erros_recip": erros_recip,
        "titulo": f"Editar {os}" if os else "Nova ordem de serviço",
    })


@login_required
@permission_required("servicos.add_ordemservico", raise_exception=True)
def nova(request):
    return _form_os(request)


@login_required
@permission_required(PODE_EDITAR, raise_exception=True)
def editar(request, pk):
    return _form_os(request, get_object_or_404(OrdemServico, pk=pk))


# ---------------------------------------------------------------------------
# Detalhe e ações do escritório
# ---------------------------------------------------------------------------


def _notas_para_faturar(os):
    """Notas de saída do cliente, perto da conclusão, ainda sem OS."""
    base = timezone.localtime(os.concluida_em).date() if os.concluida_em else os.data
    return (NotaFiscal.objects.filter(parceiro=os.cliente, direcao=NotaFiscal.Direcao.SAIDA, cancelada=False,
                                      data_emissao__gte=base - timedelta(days=15),
                                      data_emissao__lte=base + timedelta(days=45))
            .exclude(modelo=NotaFiscal.Modelo.CTE).order_by("-data_emissao")[:20])


@login_required
def detalhe(request, pk):
    os = get_object_or_404(OrdemServico.objects.select_related("cliente", "produto", "nota"), pk=pk)
    por_func = services.tempos(os)
    ind = services.indicadores(os, por_func)
    itens = list(os.itens.select_related("tipo").prefetch_related("apontamentos"))
    tmo_item = services.tmo_por_item(os, por_func)
    for i in itens:
        i.tmo_real = tmo_item.get(i.pk)
    return render(request, "servicos/detalhe.html", {
        "os": os, "itens": itens, "tempos": list(por_func.values()), "ind": ind,
        "progresso": services.progresso(os, por_func) if os.status != OrdemServico.Status.CANCELADA else None,
        "resumo": services.resumo_envase(os) if os.em_aberto else None,
        "vazios": [(i.tipo.nome, i.voltam_vazios) for i in itens if i.voltam_vazios],
        "sobra": services.sobra_kg(os) if not os.concluida else ZERO,
        "pode_editar": request.user.has_perm(PODE_EDITAR),
        "notas": _notas_para_faturar(os) if os.status == OrdemServico.Status.CONCLUIDA else [],
        "correcao": CorrecaoForm(os=os) if os.em_aberto else None,
        "apontamentos": os.apontamentos.select_related("funcionario", "registrado_por", "item__tipo"),
    })


def _realizados(post, os):
    """Unidades (e kg, se informado) de cada recipiente, do formulário do escritório."""
    realizados = {}
    for item in os.itens.all():
        unidades = _inteiro(post.get(f"unidades_{item.pk}")) or 0
        realizados[item.pk] = (unidades, _decimal(post.get(f"kg_{item.pk}")))
    return realizados


@login_required
@require_POST
def concluir(request, pk):
    os = get_object_or_404(OrdemServico, pk=pk)
    if not (request.user.has_perm(PODE_EDITAR) or request.user.has_perm(PODE_APONTAR)):
        messages.error(request, "Você não tem permissão para concluir OS.")
        return redirect(os)
    voltar = request.POST.get("voltar") == "operador"
    supervisor = request.user.has_perm(PODE_EDITAR)
    try:
        # Na tela do operador vale o que foi apontado como cheio; no escritório, o que o supervisor informar.
        realizados = None if voltar or not supervisor else _realizados(request.POST, os)
        services.concluir(os, request.user, realizados, request.POST.get("justificativa", ""), pode_abaixo=supervisor)
        messages.success(request, f"{os} concluída. Obrigado!")
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect("servicos:operador" if voltar else "servicos:detalhe", **({} if voltar else {"pk": pk}))


def _acao(request, pk, funcao, texto):
    os = get_object_or_404(OrdemServico, pk=pk)
    try:
        funcao(os)
        messages.success(request, texto.format(os=os))
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect(os)


@login_required
@permission_required(PODE_EDITAR, raise_exception=True)
@require_POST
def reabrir(request, pk):
    return _acao(request, pk, services.reabrir, "{os} reaberta: os funcionários podem apontar de novo.")


@login_required
@permission_required(PODE_EDITAR, raise_exception=True)
@require_POST
def cancelar(request, pk):
    return _acao(request, pk, services.cancelar, "{os} cancelada.")


@login_required
@permission_required(PODE_EDITAR, raise_exception=True)
@require_POST
def faturar(request, pk):
    os = get_object_or_404(OrdemServico, pk=pk)
    nota = None
    if request.POST.get("nota"):
        nota = get_object_or_404(NotaFiscal, pk=request.POST["nota"], parceiro=os.cliente)
    try:
        services.faturar(os, nota)
        messages.success(request, f"{os} faturada" + (f" na NF {nota.numero}." if nota else "."))
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect(os)


@login_required
@permission_required(PODE_EDITAR, raise_exception=True)
@require_POST
def corrigir(request, pk):
    """Supervisor lança um apontamento com hora informada (esquecimento)."""
    os = get_object_or_404(OrdemServico, pk=pk)
    form = CorrecaoForm(request.POST, os=os)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.apontar(os, d["funcionario"], d["evento"], d["motivo"], d["observacao"],
                             momento=d["momento"], registrado_por=request.user, item=d.get("item"),
                             quantidade=d.get("quantidade") or 1)
            messages.success(request, "Apontamento corrigido.")
        except ValidationError as e:
            messages.error(request, e.messages[0])
    else:
        messages.error(request, "Preencha funcionário, evento, hora e o motivo da correção.")
    return redirect(os)


# ---------------------------------------------------------------------------
# Tipos de recipiente
# ---------------------------------------------------------------------------


@login_required
def recipientes(request):
    linhas = [{"t": t, "real": services.tmo_do_tipo(t)} for t in TipoRecipiente.objects.all()]
    return render(request, "servicos/recipientes.html", {
        "linhas": linhas, "pode": request.user.has_perm("servicos.change_tiporecipiente"),
    })


@login_required
@permission_required("servicos.change_tiporecipiente", raise_exception=True)
@require_POST
def usar_tmo(request, pk):
    """Troca o tempo padrão do recipiente pelo TMO médio real."""
    tipo = get_object_or_404(TipoRecipiente, pk=pk)
    real = services.tmo_do_tipo(tipo)
    if real:
        antes = tipo.tempo_padrao_min
        tipo.tempo_padrao_min = real["tmo"]
        tipo.save(update_fields=["tempo_padrao_min"])
        messages.success(request, f"{tipo}: tempo padrão de {num(antes)} para {num(real['tmo'])} min por recipiente.")
    return redirect("servicos:recipientes")


# ---------------------------------------------------------------------------
# Tela do operador
# ---------------------------------------------------------------------------


@login_required
def operador(request):
    """Lista de OS que aceitam apontamento, com a situação do funcionário em cada uma."""
    abertas = (OrdemServico.objects.filter(status__in=[OrdemServico.Status.ABERTA, OrdemServico.Status.ANDAMENTO])
               .select_related("cliente").prefetch_related("itens__tipo").order_by("data", "numero"))
    cartoes = []
    for os in abertas:
        por_func = services.tempos(os)
        meu = por_func.get(request.user.pk)
        cartoes.append({
            "os": os, "meu": meu, "situacao": meu.situacao if meu else services.LIVRE,
            "progresso": services.progresso(os, por_func),
            "trabalhando": [t for t in por_func.values() if t.situacao == services.TRABALHANDO],
        })
    # As minhas primeiro (trabalhando, pausado), depois as livres
    ordem = {services.TRABALHANDO: 0, services.PAUSADO: 1, services.LIVRE: 2, services.FINALIZADO: 3}
    cartoes.sort(key=lambda c: ordem[c["situacao"]])
    return render(request, "servicos/operador.html", {"cartoes": cartoes, "so_operacao": so_operacao(request.user)})


@login_required
def operador_os(request, pk):
    os = get_object_or_404(OrdemServico.objects.select_related("cliente", "produto"), pk=pk)
    if request.method == "POST":
        if not request.user.has_perm(PODE_APONTAR):
            messages.error(request, "Você não tem permissão para apontar.")
            return redirect("servicos:operador_os", pk=pk)
        item = None
        if request.POST.get("item", "").isdigit():
            item = get_object_or_404(ItemOS, pk=request.POST["item"], os=os)
        try:
            services.apontar(os, request.user, request.POST.get("evento", ""), request.POST.get("motivo", ""),
                             request.POST.get("observacao", "")[:200], item=item,
                             quantidade=_inteiro_com_sinal(request.POST.get("quantidade")) or 0,
                             cheios=_inteiro_com_sinal(request.POST.get("cheios")))
        except ValidationError as e:
            messages.error(request, e.messages[0])
        return redirect("servicos:operador_os", pk=pk)
    agora = timezone.now()
    por_func = services.tempos(os, agora)
    meu = por_func.get(request.user.pk) or services.Tempo(request.user)
    outros_ativos = [t for t in por_func.values() if t.funcionario.pk != request.user.pk
                     and t.situacao in (services.TRABALHANDO, services.PAUSADO)]
    resumo = services.resumo_envase(os)
    # Quem está em cada recipiente agora
    equipe_item = {}
    for t in por_func.values():
        if t.situacao == services.TRABALHANDO and t.item_id:
            equipe_item.setdefault(t.item_id, []).append(t.funcionario.get_full_name() or t.funcionario.username)
    for linha in resumo["linhas"]:
        linha["equipe"] = equipe_item.get(linha["item"].pk, [])
        linha["meu"] = meu.item_id == linha["item"].pk and meu.situacao in (services.TRABALHANDO, services.PAUSADO)
    item_atual = next((l for l in resumo["linhas"] if l["meu"]), None)
    return render(request, "servicos/operador_os.html", {
        "trecho": services.trecho_atual(os, request.user, agora),
        "progresso": services.progresso(os, por_func),
        "os": os, "meu": meu, "agora": agora, "outros": [t for t in por_func.values() if t.funcionario.pk != request.user.pk],
        "resumo": resumo, "item_atual": item_atual, "motivos": Apontamento.Motivo.choices,
        "ind": services.indicadores(os, por_func),
        "pode_concluir": (os.em_aberto and meu.situacao == services.FINALIZADO and not outros_ativos),
        "supervisor": request.user.has_perm(PODE_EDITAR),
        "so_operacao": so_operacao(request.user),
        "eventos": [a for a in meu.eventos][-8:],
    })
