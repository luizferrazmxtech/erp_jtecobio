"""
Ordens de serviço de envase (fracionamento).

Como funciona na JT: o cliente manda o caminhão com o produto dele e as
embalagens dele (IBC, tambor, bombona, octabin…). A JT enche os recipientes
e cobra por kg envasado, com um preço que depende do tipo de recipiente
(na planilha DRE: flexitank 0,12, IBC 0,25, tambor/bombona 0,30, octabin 0,40).

Fluxo de uma OS:
  ABERTA      → cadastrada, com os recipientes que o cliente trouxe;
                o sistema calcula o fracionamento (quantos de cada enche)
  ANDAMENTO   → algum funcionário apontou o início
  CONCLUIDA   → informado quantos recipientes foram cheios de fato
  FATURADA    → ligada (ou não) à nota fiscal de prestação de serviço
  CANCELADA

O tempo de mão de obra vem do livro de apontamentos: cada toque do
funcionário (iniciar, pausar, retomar, finalizar) vira uma linha com a hora
do sistema. O tempo trabalhado é calculado a partir dessas linhas, nunca
digitado (ver services.py).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import Max
from django.urls import reverse

from apps.cadastros.models import Parceiro, Produto
from apps.fiscal.models import NotaFiscal

ZERO = Decimal("0")
CENTAVO = Decimal("0.01")


class TipoRecipiente(models.Model):
    """Cadastro dos tipos de embalagem que os clientes trazem."""

    class Unidade(models.TextChoices):
        LITROS = "L", "litros"
        KG = "KG", "kg"

    nome = models.CharField(max_length=60, unique=True, help_text="Ex.: IBC 1.000 L, Tambor 200 L")
    capacidade = models.DecimalField(max_digits=10, decimal_places=2,
                                     help_text="Quanto cabe em um recipiente, na unidade ao lado.")
    unidade = models.CharField(max_length=2, choices=Unidade.choices, default=Unidade.LITROS)
    preco_kg = models.DecimalField(
        "preço do serviço (R$/kg)", max_digits=8, decimal_places=4, default=ZERO,
        help_text="Preço padrão por kg envasado. Pode ser mudado em cada OS.",
    )
    tempo_padrao_min = models.DecimalField(
        "tempo padrão (min por recipiente)", max_digits=7, decimal_places=2, default=ZERO,
        help_text="Tempo médio de mão de obra para encher UM recipiente. Usado para prever a duração da OS.",
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "tipo de recipiente"
        verbose_name_plural = "tipos de recipiente"

    def __str__(self):
        return self.nome

    def capacidade_kg(self, densidade) -> Decimal:
        """Capacidade em kg; litros são convertidos pela densidade do produto."""
        if self.unidade == self.Unidade.KG:
            return self.capacidade
        return self.capacidade * Decimal(densidade or 1)


class OrdemServico(models.Model):
    class Status(models.TextChoices):
        ABERTA = "ABERTA", "Aberta"
        ANDAMENTO = "ANDAMENTO", "Em andamento"
        CONCLUIDA = "CONCLUIDA", "Concluída"
        FATURADA = "FATURADA", "Faturada"
        CANCELADA = "CANCELADA", "Cancelada"

    numero = models.PositiveIntegerField("número", unique=True, editable=False)
    data = models.DateField("data de abertura")
    cliente = models.ForeignKey(Parceiro, on_delete=models.PROTECT, related_name="ordens_servico")
    produto = models.ForeignKey(
        Produto, on_delete=models.PROTECT, null=True, blank=True,
        help_text="Opcional: o produto é do cliente e pode não estar cadastrado.",
    )
    descricao_produto = models.CharField(
        "descrição do produto", max_length=150, blank=True, help_text="Ex.: EN-HANCEA 132B líquido",
    )
    densidade = models.DecimalField(
        "densidade (kg/L)", max_digits=6, decimal_places=4, default=Decimal("1.26"),
        help_text="Converte litros em kg. Glicerina ≈ 1,26.",
    )
    quantidade_kg = models.DecimalField("quantidade recebida (kg)", max_digits=14, decimal_places=3)
    placa = models.CharField("placa do caminhão", max_length=20, blank=True)
    nf_cliente = models.CharField("NF / remessa do cliente", max_length=60, blank=True)
    pedido_cliente = models.CharField("pedido / OC do cliente", max_length=60, blank=True)
    lote = models.CharField(max_length=60, blank=True)

    aliquota_iss = models.DecimalField("ISS (%)", max_digits=5, decimal_places=2, default=Decimal("2"))
    aliquota_pis = models.DecimalField("PIS (%)", max_digits=5, decimal_places=2, default=Decimal("1.65"))
    aliquota_cofins = models.DecimalField("COFINS (%)", max_digits=5, decimal_places=2, default=Decimal("7.6"))

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ABERTA)
    concluida_em = models.DateTimeField("concluída em", null=True, blank=True)
    kg_envasado = models.DecimalField("kg envasados", max_digits=14, decimal_places=3, null=True, blank=True)
    nota = models.ForeignKey(
        NotaFiscal, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordens_servico",
        help_text="Nota de prestação de serviço emitida no sistema emissor.",
    )
    data_faturamento = models.DateField(null=True, blank=True)
    observacao = models.TextField("observação", blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="+")
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-numero"]
        verbose_name = "ordem de serviço"
        verbose_name_plural = "ordens de serviço"

    def __str__(self):
        return f"OS {self.numero}"

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = (OrdemServico.objects.aggregate(m=Max("numero"))["m"] or 0) + 1
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("servicos:detalhe", args=[self.pk])

    @property
    def nome_produto(self) -> str:
        return self.descricao_produto or (str(self.produto) if self.produto else "—")

    @property
    def editavel(self) -> bool:
        return self.status == self.Status.ABERTA

    @property
    def em_aberto(self) -> bool:
        """Ainda aceita apontamentos."""
        return self.status in (self.Status.ABERTA, self.Status.ANDAMENTO)

    @property
    def concluida(self) -> bool:
        return self.status in (self.Status.CONCLUIDA, self.Status.FATURADA)

    # Valores ---------------------------------------------------------------

    @property
    def valor_previsto(self) -> Decimal:
        return sum((i.valor_previsto for i in self.itens.all()), ZERO)

    @property
    def valor_realizado(self) -> Decimal:
        return sum((i.valor_realizado for i in self.itens.all()), ZERO)

    @property
    def valor(self) -> Decimal:
        """Valor do serviço: o realizado depois de concluída, o previsto antes."""
        return self.valor_realizado if self.concluida else self.valor_previsto

    @property
    def aliquota_total(self) -> Decimal:
        return self.aliquota_iss + self.aliquota_pis + self.aliquota_cofins

    @property
    def impostos(self) -> Decimal:
        return (self.valor * self.aliquota_total / 100).quantize(CENTAVO, ROUND_HALF_UP)

    @property
    def valor_liquido(self) -> Decimal:
        return self.valor - self.impostos

    @property
    def minutos_previstos(self) -> Decimal:
        return sum((i.minutos_previstos for i in self.itens.all()), ZERO)


class ItemOS(models.Model):
    """Um tipo de recipiente que o cliente trouxe para esta OS."""

    os = models.ForeignKey(OrdemServico, on_delete=models.CASCADE, related_name="itens")
    tipo = models.ForeignKey(TipoRecipiente, on_delete=models.PROTECT)
    disponivel = models.PositiveIntegerField(
        "quantos o cliente trouxe", null=True, blank=True,
        help_text="Em branco: quantos forem precisos.",
    )
    preco_kg = models.DecimalField("preço (R$/kg)", max_digits=8, decimal_places=4)
    tempo_padrao_min = models.DecimalField("tempo padrão (min/un.)", max_digits=7, decimal_places=2, default=ZERO)
    capacidade_kg = models.DecimalField("capacidade (kg)", max_digits=12, decimal_places=3)

    # Fracionamento calculado
    unidades_previstas = models.PositiveIntegerField(default=0)
    kg_previsto = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO)
    parcial_kg = models.DecimalField(
        "kg do recipiente incompleto", max_digits=12, decimal_places=3, default=ZERO,
        help_text="Sobra que não completa um recipiente e vai num recipiente a mais, só em parte.",
    )

    # Realizado (informado ao concluir)
    unidades_cheias = models.PositiveIntegerField(null=True, blank=True)
    kg_realizado = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["-capacidade_kg", "pk"]
        verbose_name = "recipiente da OS"
        verbose_name_plural = "recipientes da OS"

    def __str__(self):
        return f"{self.tipo} ({self.os})"

    @property
    def unidades_total_previstas(self) -> int:
        return self.unidades_previstas + (1 if self.parcial_kg > 0 else 0)

    @property
    def valor_previsto(self) -> Decimal:
        return (self.kg_previsto * self.preco_kg).quantize(CENTAVO, ROUND_HALF_UP)

    @property
    def valor_realizado(self) -> Decimal:
        return ((self.kg_realizado or ZERO) * self.preco_kg).quantize(CENTAVO, ROUND_HALF_UP)

    @property
    def minutos_previstos(self) -> Decimal:
        return self.unidades_total_previstas * self.tempo_padrao_min


class Apontamento(models.Model):
    """Um toque do funcionário na tela de apontamento. A hora é a do sistema."""

    class Evento(models.TextChoices):
        INICIO = "INICIO", "Início"
        PAUSA = "PAUSA", "Pausa"
        RETOMADA = "RETOMADA", "Retomada"
        FIM = "FIM", "Fim"

    class Motivo(models.TextChoices):
        REFEICAO = "REFEICAO", "Refeição / café"
        CAMINHAO = "CAMINHAO", "Esperando caminhão"
        RECIPIENTE = "RECIPIENTE", "Falta de recipiente"
        MANUTENCAO = "MANUTENCAO", "Manutenção / equipamento"
        OUTRA_TAREFA = "TAREFA", "Outra tarefa"
        FIM_TURNO = "TURNO", "Fim do turno"
        OUTRO = "OUTRO", "Outro"

    os = models.ForeignKey(OrdemServico, on_delete=models.CASCADE, related_name="apontamentos")
    funcionario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="apontamentos")
    evento = models.CharField(max_length=10, choices=Evento.choices)
    momento = models.DateTimeField()
    motivo = models.CharField(max_length=10, choices=Motivo.choices, blank=True)
    observacao = models.CharField("observação", max_length=200, blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Quem fez o lançamento (diferente do funcionário quando é uma correção do supervisor).",
    )
    corrigido = models.BooleanField(default=False, help_text="Lançado à mão por um supervisor.")

    class Meta:
        ordering = ["momento", "pk"]
        verbose_name = "apontamento"

    def __str__(self):
        return f"{self.get_evento_display()} {self.funcionario} {self.momento:%d/%m %H:%M}"
