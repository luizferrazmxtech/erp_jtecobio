"""
Funções de conversão dos dados do ERP antigo (banco PostgreSQL restaurado
do arquivo .backup) para o formato do novo sistema.

São funções puras, sem acesso a banco, para poderem ser testadas.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

ANO_MINIMO = 2000


def texto(valor) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def decimal(valor, casas: int = 2) -> Decimal:
    if valor is None or valor == "":
        return Decimal("0")
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return Decimal("0")
    return d.quantize(Decimal(1).scaleb(-casas))


def data_valida(valor, hoje: date | None = None) -> date | None:
    """O ERP antigo tem datas como 1880-09-03 e 9202-01-01. Descarta essas."""
    if not isinstance(valor, date):
        return None
    hoje = hoje or date.today()
    if valor.year < ANO_MINIMO or valor.year > hoje.year + 1:
        return None
    return valor


def documento(numero, digito) -> str:
    """O ERP antigo guarda o CNPJ/CPF sem os dois dígitos verificadores
    (numero) e com o dígito separado (digito). Ex.: 230019640001 + 50.

    Sempre monta 14 dígitos (CNPJ), completando zeros à esquerda; quase
    todos os parceiros da JT são empresas. Retorna '' se não houver.
    """
    num = texto(numero)
    if not num or not num.isdigit() or int(num) == 0:
        return ""
    dv = texto(digito)
    dv = dv.zfill(2) if dv.isdigit() else "00"
    return (num.zfill(12) + dv)[-14:]


def documento_texto(valor) -> str:
    """Para colunas que já trazem o documento inteiro em texto."""
    return "".join(c for c in texto(valor) if c.isdigit())


MODELOS = {"55": "55", "57": "57", "01": "01", "1": "01"}


def modelo(valor) -> str:
    return MODELOS.get(texto(valor), "99")


def uf(valor) -> str:
    return texto(valor).upper()[:2]


def cancelada(tipo_cancelamento) -> bool:
    """Na tabela de NF-e do ERP antigo, nfetpcan = 1 indica nota cancelada."""
    try:
        return int(tipo_cancelamento or 0) == 1
    except (TypeError, ValueError):
        return False
