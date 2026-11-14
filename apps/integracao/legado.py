"""
Funções de conversão dos dados do ERP antigo para o formato do sistema
novo. São funções puras, sem acesso a banco, para poderem ser testadas.
"""


def texto(valor) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def documento(numero, digito) -> str:
    """O ERP antigo guarda o CNPJ sem os dois dígitos verificadores
    (numero) e com o dígito separado (digito). Ex.: 230019640001 + 50.

    Sempre monta 14 dígitos (CNPJ), completando zeros à esquerda; quase
    todos os clientes da JT são empresas. Retorna '' se não houver.
    """
    num = texto(numero)
    if not num or not num.isdigit() or int(num) == 0:
        return ""
    dv = texto(digito)
    dv = dv.zfill(2) if dv.isdigit() else "00"
    return (num.zfill(12) + dv)[-14:]


def uf(valor) -> str:
    return texto(valor).upper()[:2]
