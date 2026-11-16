"""
Regras das ordens de serviço de envase.

- fracionar(): quantos recipientes de cada tipo a carga enche.
- apontar(): grava início, pausa, retomada e fim de um funcionário.
- tempos(): calcula, a partir dos apontamentos, o tempo trabalhado e o
  tempo parado de cada funcionário (homem-hora).
- indicadores(): TMO real × tempo padrão e eficiência da OS.
- tmo_do_tipo(): TMO médio real de um tipo de recipiente nas últimas OS,
  para sugerir um novo tempo padrão.
- valor_por_mes(): valor das OS concluídas por mês (usado no painel).
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Apontamento, ItemOS, OrdemServico, TipoRecipiente

ZERO = Decimal("0")
Evento = Apontamento.Evento


# ---------------------------------------------------------------------------
# Fracionamento
# ---------------------------------------------------------------------------


@dataclass
class Fracao:
    capacidade_kg: Decimal
    disponivel: int | None  # None = sem limite
    unidades: int = 0  # recipientes cheios
    kg: Decimal = ZERO  # total que vai neste tipo (cheios + incompleto)
    parcial_kg: Decimal = ZERO  # o que vai no recipiente incompleto


def fracionar(quantidade_kg, recipientes: list[Fracao]) -> tuple[list[Fracao], Decimal]:
    """Distribui a carga começando pelos recipientes maiores (menos unidades
    para encher = menos tempo). Respeita quantos o cliente trouxe de cada tipo.

    O que sobra e não completa um recipiente vai num recipiente a mais, só em
    parte, do menor tipo que ainda tiver unidade disponível.

    Devolve as frações (na mesma ordem recebida) e o que não coube em nada.
    """
    restante = Decimal(quantidade_kg or 0)
    for r in recipientes:
        r.unidades, r.kg, r.parcial_kg = 0, ZERO, ZERO
    ordem = sorted((r for r in recipientes if r.capacidade_kg > 0), key=lambda r: -r.capacidade_kg)
    for r in ordem:
        cabe = int((restante / r.capacidade_kg).to_integral_value(rounding=ROUND_DOWN))
        if r.disponivel is not None:
            cabe = min(cabe, r.disponivel)
        r.unidades = cabe
        r.kg = cabe * r.capacidade_kg
        restante -= r.kg
    if restante > 0:
        # Sobra menor que qualquer recipiente livre: um recipiente incompleto.
        for r in reversed(ordem):
            livre = r.disponivel is None or r.unidades < r.disponivel
            if livre and restante < r.capacidade_kg:
                r.parcial_kg = restante
                r.kg += restante
                restante = ZERO
                break
    return recipientes, restante


def recalcular(os: OrdemServico) -> Decimal:
    """Refaz o fracionamento da OS e grava nos itens. Devolve o kg que não coube."""
    itens = list(os.itens.all())
    fracoes = [Fracao(i.capacidade_kg, i.disponivel) for i in itens]
    _, sobra = fracionar(os.quantidade_kg, fracoes)
    for item, f in zip(itens, fracoes):
        item.unidades_previstas, item.kg_previsto, item.parcial_kg = f.unidades, f.kg, f.parcial_kg
        item.save(update_fields=["unidades_previstas", "kg_previsto", "parcial_kg"])
    return sobra


def sobra_kg(os: OrdemServico) -> Decimal:
    return max(ZERO, os.quantidade_kg - sum((i.kg_previsto for i in os.itens.all()), ZERO))


def novo_item(os: OrdemServico, tipo: TipoRecipiente, disponivel=None, preco_kg=None) -> ItemOS:
    """Item com preço, tempo e capacidade copiados do cadastro (a OS guarda
    os valores do dia, mesmo que o cadastro mude depois)."""
    return ItemOS(
        os=os, tipo=tipo, disponivel=disponivel,
        preco_kg=tipo.preco_kg if preco_kg in (None, "") else preco_kg,
        tempo_padrao_min=tipo.tempo_padrao_min, capacidade_kg=tipo.capacidade_kg(os.densidade),
    )


# ---------------------------------------------------------------------------
# Apontamentos
# ---------------------------------------------------------------------------

# Situação do funcionário → eventos que ele pode lançar
LIVRE, TRABALHANDO, PAUSADO, FINALIZADO = "livre", "trabalhando", "pausado", "finalizado"
PERMITIDOS = {
    LIVRE: {Evento.INICIO},
    TRABALHANDO: {Evento.PAUSA, Evento.FIM, Evento.TROCA, Evento.CHEIO},
    PAUSADO: {Evento.RETOMADA, Evento.FIM, Evento.CHEIO},
    FINALIZADO: {Evento.INICIO},  # pode voltar para a OS depois
}
SITUACAO_APOS = {
    Evento.INICIO: TRABALHANDO, Evento.RETOMADA: TRABALHANDO, Evento.TROCA: TRABALHANDO,
    Evento.PAUSA: PAUSADO, Evento.FIM: FINALIZADO,
}
PRECISA_ITEM = {Evento.INICIO, Evento.TROCA, Evento.CHEIO}


@dataclass
class Tempo:
    """Tempo de um funcionário numa OS, calculado pelos apontamentos."""

    funcionario: object
    situacao: str = LIVRE
    trabalhado: float = 0.0  # segundos
    parado: float = 0.0  # segundos
    inicio: datetime | None = None
    fim: datetime | None = None
    desde: datetime | None = None  # início do trecho atual (trabalhando ou pausado)
    item_id: int | None = None  # recipiente em que está trabalhando
    por_item: dict = field(default_factory=dict)  # item_id → segundos trabalhados
    cheios: int = 0  # recipientes que ele apontou como cheios
    pausas: list = field(default_factory=list)  # (início, fim ou None, motivo)
    eventos: list = field(default_factory=list)

    @property
    def horas(self) -> Decimal:
        return Decimal(str(round(self.trabalhado / 3600, 4)))


def tempos(os: OrdemServico, agora=None) -> "OrderedDict[int, Tempo]":
    """Percorre os apontamentos em ordem e soma os trechos de cada funcionário.

    Trecho trabalhando: de INICIO/RETOMADA/TROCA até PAUSA/TROCA/FIM, contado
    no recipiente em que ele estava. Trecho parado: de PAUSA até RETOMADA/FIM.
    "Recipiente cheio" só conta, não muda a situação.
    Quem ainda está trabalhando (ou pausado) conta até agora."""
    agora = agora or timezone.now()
    por_func: "OrderedDict[int, Tempo]" = OrderedDict()

    def fechar_trecho(t, ate):
        segundos = max(0, (ate - t.desde).total_seconds()) if t.desde else 0
        if t.situacao == TRABALHANDO:
            t.trabalhado += segundos
            t.por_item[t.item_id] = t.por_item.get(t.item_id, 0) + segundos
        elif t.situacao == PAUSADO:
            t.parado += segundos

    for a in os.apontamentos.select_related("funcionario").order_by("momento", "pk"):
        t = por_func.setdefault(a.funcionario_id, Tempo(a.funcionario))
        t.eventos.append(a)
        if a.evento == Evento.CHEIO:
            t.cheios += a.quantidade
            continue
        fechar_trecho(t, a.momento)
        if t.situacao == PAUSADO:
            t.pausas[-1][1] = a.momento
        if a.evento == Evento.PAUSA:
            t.pausas.append([a.momento, None, a.get_motivo_display() or "Sem motivo", a.observacao])
        if a.evento == Evento.INICIO and t.inicio is None:
            t.inicio = a.momento
        if a.evento in (Evento.INICIO, Evento.TROCA, Evento.RETOMADA) and a.item_id:
            t.item_id = a.item_id
        t.situacao = SITUACAO_APOS[a.evento]
        t.desde = a.momento
        t.fim = a.momento if a.evento == Evento.FIM else None
    for t in por_func.values():
        fechar_trecho(t, agora)
    return por_func


def tempo_por_item(por_func) -> dict:
    """Segundos de mão de obra em cada recipiente da OS, somando a equipe."""
    total: dict = {}
    for t in por_func.values():
        for item_id, seg in t.por_item.items():
            total[item_id] = total.get(item_id, 0) + seg
    return total


def tempo_do_funcionario(os: OrdemServico, usuario, agora=None) -> Tempo:
    return tempos(os, agora).get(usuario.pk) or Tempo(usuario)


@transaction.atomic
def apontar(os: OrdemServico, usuario, evento: str, motivo: str = "", observacao: str = "",
            momento=None, registrado_por=None, item=None, quantidade: int = 1) -> Apontamento:
    """Grava um toque do funcionário. A hora é sempre a do sistema; só uma
    correção de supervisor (momento informado) grava outra hora.

    item: o recipiente da OS (obrigatório para iniciar, trocar e contar cheio).
    quantidade: só para "Recipiente cheio" (+1, ou −1 para desfazer)."""
    os = OrdemServico.objects.select_for_update().get(pk=os.pk)
    if not os.em_aberto:
        raise ValidationError(f"A {os} está {os.get_status_display().lower()}: não aceita mais apontamentos.")
    corrigido = momento is not None
    momento = momento or timezone.now()
    atual = tempo_do_funcionario(os, usuario, momento)
    if evento not in PERMITIDOS[atual.situacao]:
        acoes = ", ".join(Evento(e).label.lower() for e in PERMITIDOS[atual.situacao])
        raise ValidationError(f"{usuario} está {atual.situacao} nesta OS: agora só dá para {acoes}.")
    if item is not None and item.os_id != os.pk:
        raise ValidationError("Este recipiente não é desta OS.")
    if evento == Evento.RETOMADA and item is None and atual.item_id:
        item = os.itens.get(pk=atual.item_id)
    if evento == Evento.CHEIO and item is None and atual.item_id:
        item = os.itens.get(pk=atual.item_id)
    if evento in PRECISA_ITEM and item is None and os.itens.exists():
        raise ValidationError("Escolha qual recipiente você vai envasar.")
    if evento == Evento.TROCA and item and item.pk == atual.item_id:
        raise ValidationError(f"Você já está no {item.tipo}.")
    if evento == Evento.PAUSA and not motivo:
        raise ValidationError("Escolha o motivo da pausa.")
    if evento == Evento.CHEIO:
        quantidade = 1 if quantidade >= 0 else -1
        if quantidade < 0 and item.contados <= 0:
            raise ValidationError("Não há recipiente cheio para desfazer.")
    else:
        quantidade = 0
    if corrigido:
        ultimo = os.apontamentos.filter(funcionario=usuario).order_by("-momento").first()
        if ultimo and momento < ultimo.momento:
            raise ValidationError("A hora da correção é anterior ao último apontamento deste funcionário.")
        if momento > timezone.now():
            raise ValidationError("A hora da correção não pode estar no futuro.")
    a = Apontamento.objects.create(
        os=os, funcionario=usuario, evento=evento, momento=momento, motivo=motivo if evento == Evento.PAUSA else "",
        observacao=observacao, registrado_por=registrado_por or usuario, corrigido=corrigido,
        item=item, quantidade=quantidade,
    )
    if os.status == OrdemServico.Status.ABERTA and evento == Evento.INICIO:
        os.status = OrdemServico.Status.ANDAMENTO
        os.save(update_fields=["status"])
    return a


def trabalhando_agora(os: OrdemServico) -> list[Tempo]:
    return [t for t in tempos(os).values() if t.situacao == TRABALHANDO]


def distribuir_kg(os: OrdemServico, unidades: dict) -> dict:
    """kg em cada recipiente a partir de quantos foram enchidos.

    Começa pelos maiores; o último recipiente pode ficar incompleto (recebe só
    o que sobrou da carga). unidades = {item_pk: n} → {item_pk: kg}."""
    restante = os.quantidade_kg
    kg = {}
    for item in sorted(os.itens.all(), key=lambda i: -i.capacidade_kg):
        n = int(unidades.get(item.pk) or 0)
        kg[item.pk] = max(ZERO, min(n * item.capacidade_kg, restante))
        restante -= kg[item.pk]
    return kg


def resumo_envase(os: OrdemServico) -> dict:
    """Quanto já foi apontado como cheio, item a item, e quanto falta."""
    itens = list(os.itens.select_related("tipo").prefetch_related("apontamentos"))
    contados = {i.pk: i.contados for i in itens}
    kg = distribuir_kg(os, contados)
    envasado = sum(kg.values(), ZERO)
    linhas = []
    for i in itens:
        meta = i.unidades_total_previstas
        linhas.append({"item": i, "meta": meta, "contados": contados[i.pk], "faltam": max(0, meta - contados[i.pk]),
                       "pct": min(100, int(contados[i.pk] * 100 / meta)) if meta else (100 if contados[i.pk] else 0),
                       "kg": kg[i.pk]})
    falta_kg = max(ZERO, os.quantidade_kg - envasado)
    return {"linhas": linhas, "envasado_kg": envasado, "falta_kg": falta_kg, "completo": falta_kg < Decimal("0.5"),
            "pct": min(100, int(envasado * 100 / os.quantidade_kg)) if os.quantidade_kg else 0}


@transaction.atomic
def concluir(os: OrdemServico, usuario, realizados: dict | None = None, justificativa: str = "",
             pode_abaixo: bool = False) -> OrdemServico:
    """Conclui a OS com o que foi cheio de fato.

    realizados = {item_pk: unidades} ou {item_pk: (unidades, kg)}; sem ele,
    vale o que os funcionários apontaram como cheio. Quem ainda estava com a
    OS aberta recebe um FIM nesta hora.

    Se o envasado não cobre o que chegou, só conclui quem pode (supervisor,
    pode_abaixo=True) e com justificativa."""
    os = OrdemServico.objects.select_for_update().get(pk=os.pk)
    if not os.em_aberto:
        raise ValidationError(f"A {os} já está {os.get_status_display().lower()}.")
    if not os.apontamentos.exists():
        raise ValidationError("Ninguém apontou início nesta OS: aponte o início antes de concluir.")
    itens = list(os.itens.all())
    if realizados is None:
        realizados = {i.pk: i.contados for i in itens}
    unidades = {pk: (v[0] if isinstance(v, tuple) else v) for pk, v in realizados.items()}
    kg_informado = {pk: v[1] for pk, v in realizados.items() if isinstance(v, tuple) and v[1] is not None}
    kg = distribuir_kg(os, unidades)
    kg.update({pk: Decimal(v) for pk, v in kg_informado.items()})
    total_kg = sum(kg.values(), ZERO)
    if total_kg <= 0:
        raise ValidationError("Nenhum recipiente cheio: informe quanto foi envasado.")
    falta = os.quantidade_kg - total_kg
    justificativa = (justificativa or "").strip()
    if falta >= Decimal("0.5"):
        texto = (f"Foram envasados {total_kg:,.0f} kg dos {os.quantidade_kg:,.0f} kg que chegaram "
                 f"(faltam {falta:,.0f} kg).").replace(",", ".")
        if not pode_abaixo:
            raise ValidationError(f"{texto} Termine o envase ou peça ao supervisor para concluir com justificativa.")
        if not justificativa:
            raise ValidationError(f"{texto} Para concluir assim, escreva a justificativa.")
    agora = timezone.now()
    for t in tempos(os, agora).values():
        if t.situacao in (TRABALHANDO, PAUSADO):
            Apontamento.objects.create(os=os, funcionario=t.funcionario, evento=Evento.FIM, momento=agora,
                                       registrado_por=usuario, observacao="Fechado ao concluir a OS")
    for item in itens:
        item.unidades_cheias, item.kg_realizado = int(unidades.get(item.pk) or 0), kg[item.pk]
        item.save(update_fields=["unidades_cheias", "kg_realizado"])
    os.status, os.concluida_em, os.kg_envasado = OrdemServico.Status.CONCLUIDA, agora, total_kg
    os.justificativa, os.concluida_por = justificativa, usuario
    os.save(update_fields=["status", "concluida_em", "kg_envasado", "justificativa", "concluida_por"])
    return os


@transaction.atomic
def reabrir(os: OrdemServico) -> OrdemServico:
    """Volta uma OS concluída para andamento (ex.: quantidade digitada errada)."""
    if os.status != OrdemServico.Status.CONCLUIDA:
        raise ValidationError("Só uma OS concluída (e ainda não faturada) pode ser reaberta.")
    os.status, os.concluida_em, os.kg_envasado = OrdemServico.Status.ANDAMENTO, None, None
    os.save(update_fields=["status", "concluida_em", "kg_envasado"])
    return os


def faturar(os: OrdemServico, nota=None, data=None) -> OrdemServico:
    if os.status != OrdemServico.Status.CONCLUIDA:
        raise ValidationError("Só uma OS concluída pode ser faturada.")
    os.status = OrdemServico.Status.FATURADA
    os.nota = nota
    os.data_faturamento = data or (nota.data_emissao if nota else timezone.localdate())
    os.save(update_fields=["status", "nota", "data_faturamento"])
    return os


def cancelar(os: OrdemServico) -> OrdemServico:
    if os.status in (OrdemServico.Status.FATURADA, OrdemServico.Status.CANCELADA):
        raise ValidationError(f"A {os} já está {os.get_status_display().lower()}.")
    os.status = OrdemServico.Status.CANCELADA
    os.save(update_fields=["status"])
    return os


# ---------------------------------------------------------------------------
# TMO e eficiência
# ---------------------------------------------------------------------------


@dataclass
class Indicadores:
    homem_min: Decimal  # minutos trabalhados, somando todos os funcionários
    parado_min: Decimal
    previsto_min: Decimal  # unidades × tempo padrão
    unidades: int  # recipientes cheios (realizado) ou previstos
    tmo_min: Decimal | None  # minutos de mão de obra por recipiente
    eficiencia: int | None  # previsto ÷ real, em %
    funcionarios: int
    duracao_min: Decimal  # do primeiro início ao último fim (relógio, não homem-hora)
    alerta: str = ""  # tempo fora do normal: não entra na média do TMO por tipo


def _min(segundos) -> Decimal:
    return Decimal(str(round(segundos / 60, 1)))


def indicadores(os: OrdemServico, por_func=None) -> Indicadores:
    por_func = por_func if por_func is not None else tempos(os)
    itens = list(os.itens.all())
    homem = sum(t.trabalhado for t in por_func.values())
    parado = sum(t.parado for t in por_func.values())
    if os.concluida:
        unidades = sum(i.unidades_cheias or 0 for i in itens)  # inclui o recipiente incompleto
        previsto = sum(((i.unidades_cheias or 0) * i.tempo_padrao_min for i in itens), ZERO)
    else:
        # Em andamento: TMO pelo que já foi apontado como cheio (ou pelo previsto, se nada ainda)
        unidades = sum(i.contados for i in itens) or sum(i.unidades_total_previstas for i in itens)
        previsto = sum((i.minutos_previstos for i in itens), ZERO)
    homem_min = _min(homem)
    tmo = (homem_min / unidades).quantize(Decimal("0.01"), ROUND_HALF_UP) if unidades and homem else None
    eficiencia = int(previsto * 100 / homem_min) if (os.concluida and homem_min and previsto) else None
    inicios = [t.inicio for t in por_func.values() if t.inicio]
    fins = [t.fim or timezone.now() for t in por_func.values() if t.inicio]
    duracao = _min((max(fins) - min(inicios)).total_seconds()) if inicios else ZERO
    alerta = ""
    if os.concluida:
        if homem < 60:
            alerta = "Menos de 1 minuto de mão de obra apontado. Confira os apontamentos."
        elif eficiencia is not None and eficiencia > EFICIENCIA_MAX:
            alerta = (f"Eficiência de {eficiencia}%: rápido demais para o tempo padrão. "
                      "Confira os apontamentos ou o tempo padrão do recipiente.")
        elif eficiencia is not None and eficiencia < EFICIENCIA_MIN:
            alerta = f"Eficiência de {eficiencia}%: muito mais lento que o padrão. Confira se alguém esqueceu de pausar."
    return Indicadores(homem_min, _min(parado), previsto, unidades, tmo, eficiencia, len(por_func), duracao, alerta)


# Fora desta faixa a OS aparece com alerta e não entra no TMO médio por tipo.
EFICIENCIA_MAX, EFICIENCIA_MIN = 200, 25


def tmo_por_item(os: OrdemServico, por_func=None) -> dict:
    """{item_pk: minutos por recipiente} com o tempo apontado em cada recipiente."""
    por_func = por_func if por_func is not None else tempos(os)
    seg = tempo_por_item(por_func)
    resultado = {}
    for i in os.itens.all():
        n = i.unidades_cheias if os.concluida else i.contados
        if n and seg.get(i.pk):
            resultado[i.pk] = (Decimal(str(seg[i.pk])) / 60 / n).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return resultado


def tmo_do_tipo(tipo: TipoRecipiente, ultimas: int = 10) -> dict | None:
    """TMO médio real de um tipo de recipiente nas últimas OS concluídas.

    Quando a OS teve mais de um tipo, o tempo é repartido entre eles na
    proporção do tempo padrão (ou das unidades, se não houver padrão)."""
    oss = (OrdemServico.objects.filter(status__in=[OrdemServico.Status.CONCLUIDA, OrdemServico.Status.FATURADA],
                                       itens__tipo=tipo, itens__unidades_cheias__gt=0)
           .distinct().order_by("-concluida_em")[:ultimas])
    minutos, unidades, n = ZERO, 0, 0
    for os in oss:
        itens = list(os.itens.all())
        por_func = tempos(os)
        ind = indicadores(os, por_func)
        homem = ind.homem_min
        if not homem or ind.alerta:
            continue
        seg = tempo_por_item(por_func)
        item = next((i for i in itens if i.tipo_id == tipo.pk and i.unidades_cheias), None)
        if item and seg.get(item.pk):  # tempo apontado no próprio recipiente
            minutos += Decimal(str(seg[item.pk])) / 60
            unidades += item.unidades_cheias
            n += 1
            continue
        pesos = {i.pk: (i.unidades_cheias or 0) * i.tempo_padrao_min for i in itens}
        if not sum(pesos.values()):
            pesos = {i.pk: Decimal(i.unidades_cheias or 0) for i in itens}
        soma = sum(pesos.values())
        for i in itens:
            if i.tipo_id == tipo.pk and i.unidades_cheias and soma:
                minutos += homem * pesos[i.pk] / soma
                unidades += i.unidades_cheias
        n += 1
    if not unidades:
        return None
    return {"tmo": (minutos / unidades).quantize(Decimal("0.01"), ROUND_HALF_UP), "ordens": n, "unidades": unidades}


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


def valor_por_mes(inicio: date, fim: date | None = None, excluir_notas=None) -> dict[str, Decimal]:
    """Valor (bruto) das OS concluídas ou faturadas por mês de conclusão.

    {"2026-10": Decimal("11062.50"), ...}. Usado no gráfico de vendas do painel.
    `excluir_notas` (filtro Q de NotaFiscal) deixa de fora as OS cuja nota já
    é contada de outro jeito, para não somar o mesmo serviço duas vezes."""
    qs = OrdemServico.objects.filter(
        status__in=[OrdemServico.Status.CONCLUIDA, OrdemServico.Status.FATURADA],
        concluida_em__date__gte=inicio,
    ).prefetch_related("itens")
    if fim:
        qs = qs.filter(concluida_em__date__lte=fim)
    if excluir_notas is not None:
        from apps.fiscal.models import NotaFiscal

        qs = qs.exclude(nota__in=NotaFiscal.objects.filter(excluir_notas))
    resultado: dict[str, Decimal] = {}
    for os in qs:
        chave = timezone.localtime(os.concluida_em).strftime("%Y-%m")
        resultado[chave] = resultado.get(chave, ZERO) + os.valor_realizado
    return resultado
