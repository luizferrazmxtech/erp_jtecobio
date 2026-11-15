"""Confere a planilha DRE da JT e aponta fórmulas erradas.

A planilha é feita à mão e as fórmulas são copiadas de bloco em bloco; um
erro de cópia passa despercebido. Aqui o sistema refaz as contas de cada
linha e compara com o que está na planilha.

O que é conferido (nas abas de compra e venda, que seguem o mesmo modelo):
  - células com erro do Excel (#REF!, #DIV/0!, #N/A, #VALOR!);
  - saldo do contrato = quantidade − entrada (ou saída);
  - frete net = frete × alíquota do frete (e não o preço do produto);
  - custo/lucro bruto = preço ± frete com o sinal certo;
  - NF de origem anotada igual à própria NF.

Uso:  python manage.py conferir_planilha "DRE 10.2026.xlsx"
ou pela tela Sistema → Conferir planilha.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import re

import openpyxl

ERROS_EXCEL = ("#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#VALOR!", "#NAME?", "#NOME?", "#NUM!")
TOLERANCIA = Decimal("0.0005")


@dataclass
class Achado:
    aba: str
    celula: str
    problema: str
    efeito: str
    formula: str = ""
    gravidade: str = "alta"  # alta: muda número; media: dado incoerente; baixa: risco futuro


def _num(v) -> Decimal | None:
    if v is None or isinstance(v, str) and not v.strip():
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _fmt(d: Decimal | None, casas=4) -> str:
    if d is None:
        return "—"
    texto = f"{d:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def _texto(v) -> str:
    return " ".join(str(v or "").split()).lower()


def _cabecalho(linha) -> dict[str, str]:
    """Texto do cabeçalho → letra da coluna."""
    return {_texto(c.value): c.column_letter for c in linha if isinstance(c.value, str) and c.value.strip()}


def conferir(arquivo) -> list[Achado]:
    """Lê a planilha (caminho ou arquivo aberto) e devolve os problemas achados."""
    valores = openpyxl.load_workbook(arquivo, data_only=True)
    if hasattr(arquivo, "seek"):
        arquivo.seek(0)
    formulas = openpyxl.load_workbook(arquivo)
    achados: list[Achado] = []
    for ws in valores.worksheets:
        wf = formulas[ws.title]
        achados += _agrupar(_erros_excel(ws, wf))
        achados += _blocos(ws, wf)
    ordem = {"alta": 0, "media": 1, "baixa": 2}
    return sorted(achados, key=lambda a: ordem[a.gravidade])


def _agrupar(achados: list[Achado]) -> list[Achado]:
    """Junta as células quebradas com a mesma fórmula (ex.: D76, E76… F78) numa linha só."""
    grupos: dict[tuple, list[Achado]] = {}
    for a in achados:
        grupos.setdefault((a.problema, a.formula), []).append(a)
    juntos = []
    for lista in grupos.values():
        a = lista[0]
        if len(lista) > 1:
            a.celula = f"{lista[0].celula} a {lista[-1].celula} ({len(lista)} células)"
        juntos.append(a)
    return juntos


def _erros_excel(ws, wf) -> list[Achado]:
    achados = []
    for linha in ws.iter_rows():
        linha_total = any(_texto(c.value) == "total:" for c in linha)
        for c in linha:
            if isinstance(c.value, str) and c.value.strip() in ERROS_EXCEL:
                if c.value.strip() == "#DIV/0!" and linha_total:
                    continue  # média de um bloco ainda sem notas: normal
                f = wf[c.coordinate].value
                achados.append(Achado(
                    ws.title, c.coordinate, f"Fórmula quebrada ({c.value.strip()})",
                    "A conta não é feita; quem usa este número vê erro ou zero.",
                    f if isinstance(f, str) else "",
                ))
    return achados


def _blocos(ws, wf) -> list[Achado]:
    """Percorre os blocos 'contrato + notas' das abas de compra e venda."""
    achados = []
    linhas = list(ws.iter_rows())
    contrato = None  # (cabeçalho, linha de dados) do contrato atual
    notas = None     # cabeçalho das notas do bloco atual
    for i, linha in enumerate(linhas):
        cab = _cabecalho(linha)
        if "nº contrato" in cab and "saldo" in cab and i + 1 < len(linhas):
            contrato, notas = (cab, linhas[i + 1]), None
            achados += _conferir_contrato(ws, wf, cab, linhas[i + 1][0].row)
            continue
        if "nº nota fiscal" in cab and "quantidade" in cab:
            notas = cab
            continue
        if notas is None:
            continue
        if any(_texto(c.value) == "total:" for c in linha):
            notas = None
            continue
        achados += _conferir_nota(ws, wf, notas, contrato, linha[0].row)
    return achados


def _celula(ws, col, linha):
    return ws[f"{col}{linha}"].value if col else None


def _conferir_contrato(ws, wf, cab, r) -> list[Achado]:
    col_qtd, col_saldo = cab.get("quantidade"), cab.get("saldo")
    col_mov = cab.get("entrada (kg)") or cab.get("saída (kg)") or cab.get("saida (kg)")
    if not (col_qtd and col_saldo and col_mov):
        return []
    qtd, mov, saldo = (_num(_celula(ws, c, r)) for c in (col_qtd, col_mov, col_saldo))
    if qtd is None or saldo is None or mov is None:
        return []
    esperado = qtd - mov
    if abs(esperado - saldo) > Decimal("0.5"):
        numero = _celula(ws, cab.get("nº contrato"), r)
        return [Achado(
            ws.title, f"{col_saldo}{r}",
            f"Saldo do contrato {numero} não é quantidade − {'entrada' if 'entrada (kg)' in cab else 'saída'}",
            f"Mostra {_fmt(saldo, 3)} kg; o certo é {_fmt(qtd, 0)} − {_fmt(mov, 0)} = {_fmt(esperado, 3)} kg.",
            str(wf[f"{col_saldo}{r}"].value or ""),
        )]
    return []


def _conferir_nota(ws, wf, cab, contrato, r) -> list[Achado]:
    achados = []
    nf = _celula(ws, cab.get("nº nota fiscal"), r)
    qtd = _num(_celula(ws, cab.get("quantidade"), r))
    if nf in (None, "") or not qtd:
        return []
    nf_txt = str(nf).split(".")[0]
    preco = _num(_celula(ws, cab.get("vr. unit. bruto"), r))
    frete = _num(_celula(ws, cab.get("vr. unit."), r)) or Decimal("0")

    modalidade = _texto(_celula(ws, cab.get("modalidade"), r))
    exportacao = "export" in modalidade

    # Frete net = frete × alíquota do frete do contrato (ex.: 0,88 = crédito de ICMS).
    col_fn = cab.get("pç net frete")
    if col_fn and contrato:
        aliq = _num(_celula(ws, contrato[0].get("aliq direta frete"), contrato[1][0].row))
        fn = _num(_celula(ws, col_fn, r))
        formula = str(wf[f"{col_fn}{r}"].value or "")
        col_net = cab.get("custo net") or cab.get("lucro net")
        net = _num(_celula(ws, col_net, r)) if col_net else None
        if aliq is not None and fn is not None and fn > frete + TOLERANCIA:
            esperado = frete * aliq
            efeito = f"Frete net de R$ {_fmt(fn)}/kg, maior que o próprio frete; o certo é R$ {_fmt(frete)} × {_fmt(aliq, 2)} = R$ {_fmt(esperado)}."
            if net is not None:
                efeito += f" O custo net da carga fica R$ {_fmt(net)}/kg em vez de R$ {_fmt(net - fn + esperado)}."
            achados.append(Achado(ws.title, f"{col_fn}{r}", f"NF {nf_txt}: frete net calculado sobre o preço do produto",
                                  efeito, formula))
        elif frete > 0 and (fn is None or fn == 0):
            achados.append(Achado(ws.title, f"{col_fn}{r}", f"NF {nf_txt}: frete net em branco",
                                  f"A carga tem frete de R$ {_fmt(frete)}/kg, mas o net ficou zero; o custo net sai menor do que é.",
                                  formula, "media"))
        elif (aliq is not None and not exportacao and frete > 0 and fn is not None
              and abs(fn - frete) <= TOLERANCIA and aliq < 1):
            achados.append(Achado(ws.title, f"{col_fn}{r}", f"NF {nf_txt}: frete net sem o crédito de ICMS",
                                  f"Usa o frete cheio (R$ {_fmt(frete)}); outras cargas do mercado interno usam × {_fmt(aliq, 2)} = R$ {_fmt(frete * aliq)}. Confirmar qual é o certo.",
                                  formula, "media"))

    # Custo bruto (compra) / lucro bruto (venda) não podem misturar frete net com preço bruto.
    col_cb, col_lb = cab.get("custo bruto"), cab.get("lucro bruto")
    col = col_cb or col_lb
    if col and preco is not None:
        formula = str(wf[f"{col}{r}"].value or "").upper()
        valor = _num(_celula(ws, col, r))
        if col_fn and re.search(rf"\b{col_fn}{r}\b", formula):
            certo = preco + frete if col_cb else preco - frete
            nome = "custo" if col_cb else "lucro"
            if frete == 0:
                achados.append(Achado(
                    ws.title, f"{col}{r}", f"NF {nf_txt}: {nome} bruto usa o frete net",
                    "Hoje não muda nada porque o frete é zero, mas mistura preço com imposto e frete sem imposto quando houver frete.",
                    formula, "baixa"))
            else:
                achados.append(Achado(
                    ws.title, f"{col}{r}", f"NF {nf_txt}: {nome} bruto usa o frete net",
                    f"Mistura preço com imposto e frete sem imposto: mostra R$ {_fmt(valor)}; com o frete bruto seria R$ {_fmt(certo)}.",
                    formula))
        elif col_lb and re.search(r"SUM\([^)]*,", formula):
            if frete == 0:
                achados.append(Achado(
                    ws.title, f"{col}{r}", f"NF {nf_txt}: lucro bruto soma o frete em vez de subtrair",
                    "Hoje não muda nada porque o frete é zero, mas erra quando a carga tiver frete.", formula, "baixa"))
            else:
                achados.append(Achado(
                    ws.title, f"{col}{r}", f"NF {nf_txt}: lucro bruto soma o frete",
                    f"Mostra R$ {_fmt(valor)}; descontando o frete seria R$ {_fmt(preco - frete)}. Confirmar se o cliente reembolsa o frete.",
                    formula, "media"))

    # A NF de origem (entrada na venda, saída na compra) não pode ser a própria NF.
    col_ref = cab.get("nº nf entrada") or cab.get("nº nf saida") or cab.get("nº nf saída")
    ref = _celula(ws, col_ref, r) if col_ref else None
    if ref not in (None, "") and str(ref).split(".")[0] == nf_txt:
        achados.append(Achado(
            ws.title, f"{col_ref}{r}", f"NF {nf_txt}: a NF de origem anotada é a própria nota",
            "Não dá para saber de qual compra veio essa carga (venda casada).", "", "media",
        ))
    return achados
