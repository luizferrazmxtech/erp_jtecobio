"""
Carga inicial a partir da planilha mensal "DRE" da JT Ecobio (Excel).

O ERP antigo não sabe em que tanque está cada produto e não controla
contratos. Essas duas informações vêm da planilha que a JT já fecha todo
mês:

  aba "Estoque"               → bloco FINAL: produto, quantidade, custo e local
  aba "Saldo Ctt Glicerina"   → contratos de compra e de venda com saldo

A leitura (funções `ler_*`) é separada da gravação (`CargaPlanilha`), para
poder testar a leitura sem banco.
"""

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count

from apps.cadastros.models import Local, Parceiro, Produto, Tanque
from apps.contratos.models import Contrato
from apps.estoque import services as estoque
from apps.estoque.models import Movimentacao

# Códigos dos produtos no ERP antigo (es.esci01, empresa 1)
COD_GLICERINA_80 = "600"
COD_GBD_GRANEL = "958"
COD_GBD_TB = "1292"
COD_GBD_IBC = "1293"


def normalizar(texto) -> str:
    t = unicodedata.normalize("NFD", str(texto or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").upper()
    return re.sub(r"\s+", " ", t).strip()


def _num(valor) -> Decimal | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float, Decimal)):
        return None
    return Decimal(str(valor))


def _texto_contrato(valor) -> str:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip()


# ---------------------------------------------------------------------------
# Produtos e locais da planilha
# ---------------------------------------------------------------------------


def produto_do_texto(texto: str, obs: str = "") -> str | None:
    """Traduz a descrição usada na planilha para o código do ERP antigo."""
    t, o = normalizar(texto), normalizar(obs)
    # A planilha às vezes escreve "GLICEIRNA"
    if "GLIC" not in t and "BI-DEST" not in t and "BI -DEST" not in t:
        return None
    if "EMBALAGEM" in t:
        return None
    # A observação do contrato é mais específica que o título do bloco
    if "IBC" in o:
        return COD_GBD_IBC
    if re.search(r"\bTB\b", o):
        return COD_GBD_TB
    if "INDUSTRIALIZADA" in o:  # glicerina 80% que voltou da industrialização como bi-destilada
        return COD_GBD_GRANEL
    if re.search(r"\bTB\b", t):
        return COD_GBD_TB
    if "IBC" in t:
        return COD_GBD_IBC
    if "BI" in t and "DEST" in t:
        return COD_GBD_GRANEL
    if "80" in t:
        return COD_GLICERINA_80
    return None


@dataclass(frozen=True)
class DefinicaoLocal:
    nome: str
    tipo: str
    busca_parceiro: str  # trecho da razão social do dono do local
    cidade: str = ""
    uf: str = ""


# Como cada "LOCAL" da planilha vira um local no sistema novo.
LOCAIS = {
    "VALE BIO": DefinicaoLocal("Vale Logística (Ibaté)", Local.Tipo.TANQUE_TERCEIRO, "VALE LOGISTICA", "IBATE", "SP"),
    "BULOKE": DefinicaoLocal("Bulokebios (Ibaté)", Local.Tipo.TANQUE_TERCEIRO, "BULOKEBIOS", "IBATE", "SP"),
    "AROMA": DefinicaoLocal("Aroma Bioquímica (Piracicaba)", Local.Tipo.INDUSTRIALIZADOR, "AROMA BIOQUIMICA", "PIRACICABA", "SP"),
    "BIOBROTAS": DefinicaoLocal("Biobrotas (Brotas)", Local.Tipo.INDUSTRIALIZADOR, "BIOBROTAS", "BROTAS", "SP"),
    "JT ECOBIO": DefinicaoLocal("JT Ecobio (Ibaté)", Local.Tipo.PROPRIO, "", "IBATE", "SP"),
    "C JUNIOR": DefinicaoLocal("C Junior Andrade (Guarulhos)", Local.Tipo.ARMAZEM, "C JUNIOR S DE ANDRADE", "GUARULHOS", "SP"),
}


def definicao_local(texto: str) -> DefinicaoLocal:
    t = normalizar(texto)
    for chave, definicao in LOCAIS.items():
        if t.startswith(chave):
            return definicao
    if "TRANSITO" in t:
        detalhe = t.split("(", 1)[1].rstrip(")").strip() if "(" in t else ""
        nome = f"Em trânsito ({detalhe})" if detalhe else "Em trânsito"
        return DefinicaoLocal(nome, Local.Tipo.TRANSITO, "")
    return DefinicaoLocal(str(texto).strip(), Local.Tipo.TANQUE_TERCEIRO, "")


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


@dataclass
class LinhaEstoque:
    linha: int
    descricao: str
    codigo_produto: str | None
    quantidade: Decimal
    custo: Decimal
    local: str


@dataclass
class LinhaContrato:
    linha: int
    tipo: str  # "C" ou "V"
    numero: str
    parceiro: str
    codigo_produto: str | None
    modalidade: str
    quantidade: Decimal
    saldo: Decimal
    preco: Decimal
    frete: Decimal
    mes: date
    obs: str


def _celulas(ws):
    for i, linha in enumerate(ws.iter_rows(values_only=True), start=1):
        yield i, list(linha) + [None] * (14 - len(linha))


def mes_da_planilha(wb) -> date:
    """O mês de referência fica ao lado (ou abaixo) do rótulo "Mês:" no
    cabeçalho da aba de contratos. A outra data do cabeçalho é a de emissão."""
    achou_rotulo = False
    for _, c in _celulas(wb["Saldo Ctt Glicerina"]):
        for v in c:
            if isinstance(v, str) and normalizar(v).startswith("MES"):
                achou_rotulo = True
            elif achou_rotulo and isinstance(v, datetime):
                return v.date().replace(day=1)
    raise ValueError("Não achei o mês de referência (rótulo 'Mês:') na aba 'Saldo Ctt Glicerina'.")


def ler_estoque_final(wb) -> list[LinhaEstoque]:
    """Bloco 'FINAL' da aba Estoque (colunas C=produto, E=qtd, F=custo, H=local)."""
    linhas, no_bloco = [], False
    for i, c in _celulas(wb["Estoque"]):
        if any(normalizar(v) == "FINAL" for v in c if isinstance(v, str)):
            no_bloco = True
            continue
        if not no_bloco:
            continue
        if any(isinstance(v, str) and normalizar(v).startswith("TOTAL") for v in c):
            break
        qtd, custo = _num(c[4]), _num(c[5])
        if isinstance(c[2], str) and qtd is not None and custo is not None and c[7]:
            linhas.append(LinhaEstoque(i, c[2].strip(), produto_do_texto(c[2]), qtd, custo, str(c[7]).strip()))
    return linhas


def ler_contratos(wb) -> list[LinhaContrato]:
    """Aba 'Saldo Ctt Glicerina': colunas B=contrato, C=parceiro, D=qtd,
    E=saldo, F=preço bruto, H=frete bruto, J=mês, K=observação."""
    contratos, tipo, produto_bloco = [], None, ""
    for i, c in _celulas(wb["Saldo Ctt Glicerina"]):
        b = normalizar(c[1]) if isinstance(c[1], str) else ""
        if "CONTRATOS COMPRA" in b:
            tipo = Contrato.Tipo.COMPRA
            continue
        if "CONTRATOS VENDA" in b:
            tipo = Contrato.Tipo.VENDA
            continue
        if b == "PRODUTO:":
            produto_bloco = str(c[2] or "")
            continue
        qtd, saldo = _num(c[3]), _num(c[4])
        if tipo is None or c[1] is None or qtd is None or saldo is None or not isinstance(c[2], str):
            continue
        if b.startswith(("TOTAL", "N")) and not any(ch.isdigit() for ch in b):
            continue
        obs = str(c[10] or "") if isinstance(c[10], str) else ""
        mes = c[9].date().replace(day=1) if isinstance(c[9], datetime) else None
        bloco = normalizar(produto_bloco)
        if "INDUSTRI" in bloco or "INDUSTRI" in normalizar(obs):
            modalidade = Contrato.Modalidade.INDUSTRIALIZACAO
        elif "EXPORT" in bloco:
            modalidade = Contrato.Modalidade.EXPORTACAO
        else:
            modalidade = Contrato.Modalidade.MERCADO_INTERNO
        contratos.append(LinhaContrato(
            linha=i, tipo=tipo, numero=_texto_contrato(c[1]), parceiro=c[2].strip(),
            codigo_produto=produto_do_texto(produto_bloco, obs), modalidade=modalidade,
            quantidade=qtd, saldo=saldo, preco=_num(c[5]) or Decimal("0"), frete=_num(c[7]) or Decimal("0"),
            mes=mes, obs=obs.strip(),
        ))
    return contratos


# ---------------------------------------------------------------------------
# Achar o parceiro pelo nome escrito na planilha
# ---------------------------------------------------------------------------

PALAVRAS_IGNORADAS = {
    "LTDA", "LTD", "LT", "SA", "ME", "EPP", "IND", "COM", "DE", "DO", "DA", "DOS", "DAS", "E", "IMP", "EXP",
    "INDUSTRIA", "COMERCIO", "PROD", "PRODUTO", "PRODUTOS", "CO", "INTERNATIONAL", "BRASIL", "BRAZIL",
}
APELIDOS = {"JR": "JUNIOR"}


def palavras(nome: str) -> list[str]:
    t = normalizar(nome).replace(".", "")  # "K.S.A" → "KSA", "Ind." → "IND"
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\b([A-Z]) (?=[A-Z]\b)", r"\1", t)  # "M D" → "MD"
    t = re.sub(r"\b([A-Z]) (?=[A-Z]\b)", r"\1", t)
    return [APELIDOS.get(p, p) for p in t.split() if p not in PALAVRAS_IGNORADAS and len(p) >= 2]


def _parecida(p: str, alvo: list[str]) -> bool:
    """'CARGIL' casa com 'CARGILL', 'CONDIM' com 'CONDIMENTOS' (abreviações e
    erros de digitação da planilha)."""
    for a in alvo:
        if a == p or (len(p) >= 4 and len(a) >= 4 and (a.startswith(p) or p.startswith(a))):
            return True
        if len(p) >= 6 and a[:5] == p[:5] and abs(len(a) - len(p)) <= 2:
            return True
    return False


class BuscaParceiro:
    """Acha o parceiro pelo nome escrito na planilha: precisa bater a primeira
    palavra e pelo menos duas das três primeiras. Empate: o que tem mais notas."""

    def __init__(self):
        self._cache = {}

    def _candidatos(self, papel):
        if papel not in self._cache:
            self._cache[papel] = [
                (p, palavras(p.razao_social) + palavras(p.nome_fantasia))
                for p in Parceiro.objects.filter(**{papel: True}).annotate(n=Count("notas"))
            ]
        return self._cache[papel]

    def achar(self, nome: str, papel: str) -> Parceiro | None:
        chave = palavras(nome)[:3]
        if not chave:
            return None
        minimo = min(2, len(chave))
        melhores, melhor = [], 0
        for p, alvo in self._candidatos(papel):
            if not _parecida(chave[0], alvo):
                continue
            acertos = sum(1 for c in chave if _parecida(c, alvo))
            if acertos < minimo:
                continue
            if acertos > melhor:
                melhores, melhor = [p], acertos
            elif acertos == melhor:
                melhores.append(p)
        return max(melhores, key=lambda p: p.n) if melhores else None


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------

DOCUMENTO = "Planilha DRE {mes:%m/%Y}"


def capacidade_estimada(quantidade: Decimal) -> Decimal:
    """A planilha não informa a capacidade dos tanques. Estima com folga
    (saldo + 50%, arredondado para 10 t) até alguém cadastrar a real."""
    base = max(quantidade * Decimal("1.5"), Decimal("10000"))
    return (base / 10000).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 10000


class CargaPlanilha:
    def __init__(self, wb, usuario=None):
        self.wb = wb
        self.usuario = usuario
        self.mes = mes_da_planilha(wb)
        self.data_saldo = self.mes.replace(day=calendar.monthrange(self.mes.year, self.mes.month)[1])
        self.documento = DOCUMENTO.format(mes=self.mes)
        self.contagem: dict[str, int] = {}
        self.avisos: list[str] = []
        self.busca = BuscaParceiro()

    def soma(self, chave, n=1):
        self.contagem[chave] = self.contagem.get(chave, 0) + n

    def _produto(self, codigo: str | None) -> Produto | None:
        if codigo is None:
            return None
        produto = Produto.objects.filter(codigo_legado=codigo).first()
        if produto and (not produto.controla_estoque or produto.unidade != "KG" or not produto.densidade):
            # Os fracionados (TB, IBC) estão em UN no ERP antigo, mas a JT
            # controla tudo em kg.
            produto.controla_estoque, produto.unidade = True, "KG"
            produto.densidade = produto.densidade or Decimal("1.2600")
            produto.save(update_fields=["controla_estoque", "unidade", "densidade"])
        return produto

    def _local(self, texto: str) -> Local:
        d = definicao_local(texto)
        local = Local.objects.filter(nome=d.nome).first()
        if local:
            return local
        dono = None
        if d.busca_parceiro:
            dono = (Parceiro.objects.filter(razao_social__icontains=d.busca_parceiro)
                    .annotate(n=Count("notas")).order_by("-n").first())
            if dono is None:
                self.avisos.append(f"Local {d.nome}: dono '{d.busca_parceiro}' não encontrado nos parceiros")
            elif d.tipo == Local.Tipo.INDUSTRIALIZADOR and not dono.eh_industrializador:
                dono.eh_industrializador = True
                dono.save(update_fields=["eh_industrializador"])
            elif d.tipo in (Local.Tipo.TANQUE_TERCEIRO, Local.Tipo.ARMAZEM) and not dono.eh_armazem:
                dono.eh_armazem = True
                dono.save(update_fields=["eh_armazem"])
        self.soma("locais criados")
        return Local.objects.create(nome=d.nome, tipo=d.tipo, parceiro=dono, cidade=d.cidade, uf=d.uf)

    @transaction.atomic
    def carregar_estoque(self):
        for linha in ler_estoque_final(self.wb):
            produto = self._produto(linha.codigo_produto)
            if produto is None:
                self.avisos.append(f"Estoque, linha {linha.linha}: '{linha.descricao}' não é glicerina, ignorado")
                continue
            if linha.quantidade <= 0:
                continue
            local = self._local(linha.local)
            ja = Movimentacao.objects.filter(
                produto=produto, local=local, tipo=Movimentacao.Tipo.SALDO_INICIAL, documento=self.documento)
            if ja.exists():
                self.soma("saldos já carregados antes")
                continue
            Tanque.objects.get_or_create(
                local=local, produto=produto,
                defaults={"capacidade_kg": capacidade_estimada(linha.quantidade),
                          "identificacao": "capacidade estimada"},
            )
            estoque.registrar_entrada(
                produto=produto, local=local, quantidade_kg=linha.quantidade,
                custo_unitario=linha.custo.quantize(Decimal("0.0001")), data=self.data_saldo,
                tipo=Movimentacao.Tipo.SALDO_INICIAL, lote_codigo=f"INICIAL-{self.mes:%Y%m}-L{local.pk}",
                documento=self.documento, origem_lote=self.documento, usuario=self.usuario,
                observacao=f"Saldo final de {self.mes:%m/%Y} da planilha (linha {linha.linha} da aba Estoque).",
            )
            self.soma("saldos iniciais lançados")

    @transaction.atomic
    def carregar_contratos(self):
        for c in ler_contratos(self.wb):
            papel = "eh_fornecedor" if c.tipo == Contrato.Tipo.COMPRA else "eh_cliente"
            if normalizar(c.parceiro).startswith("JT ECOBIO"):
                continue  # linhas de estoque próprio, não são contratos
            parceiro = self.busca.achar(c.parceiro, papel)
            if parceiro is None:
                self.avisos.append(f"Contrato {c.numero} (linha {c.linha}): parceiro '{c.parceiro}' não encontrado")
                continue
            produto = self._produto(c.codigo_produto)
            if produto is None:
                self.avisos.append(f"Contrato {c.numero} (linha {c.linha}): produto não identificado")
                continue
            entregue = max(c.quantidade - c.saldo, Decimal("0"))
            _, criado = Contrato.objects.update_or_create(
                tipo=c.tipo, numero=c.numero[:40], parceiro=parceiro,
                defaults={
                    "produto": produto, "modalidade": c.modalidade, "quantidade_kg": c.quantidade,
                    "entregue_antes": entregue, "preco_unitario": c.preco.quantize(Decimal("0.0001")),
                    "frete_previsto": c.frete.quantize(Decimal("0.0001")),
                    "mes_referencia": c.mes or self.mes,
                    "status": Contrato.Status.ABERTO if c.saldo > 0 else Contrato.Status.FINALIZADO,
                    "observacao": f"Carregado da {self.documento} (linha {c.linha}). {c.obs}".strip(),
                },
            )
            if palavras(c.parceiro)[:1] and palavras(c.parceiro)[0] not in palavras(parceiro.razao_social):
                self.avisos.append(f"Contrato {c.numero}: '{c.parceiro}' ligado a {parceiro.razao_social} (confira)")
            self.soma(f"contratos de {'compra' if c.tipo == 'C' else 'venda'} {'criados' if criado else 'atualizados'}")

    def executar(self):
        self.carregar_estoque()
        self.carregar_contratos()
        return self
