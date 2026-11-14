"""
Importa os CLIENTES da JT Ecobio a partir do banco do ERP antigo
(restaurado do arquivo .backup num PostgreSQL auxiliar).

Decisão de 14/11/2026: só os clientes vêm do sistema antigo. Produtos,
fornecedores, contratos, notas e estoque começam do zero no sistema novo,
e o banco antigo fica apenas para consulta.

Pode ser rodado quantas vezes quiser: clientes já importados são
atualizados, não duplicados. Só lê do banco antigo, nunca grava nele.

Tabela usada no ERP antigo (empresa = 1 é a JT Ecobio):
  fa.crci01   clientes
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.db import transaction

from apps.cadastros.models import Parceiro

from . import legado as L


@dataclass
class Resultado:
    contagem: dict = field(default_factory=lambda: defaultdict(int))
    avisos: list = field(default_factory=list)

    def soma(self, chave, n=1):
        self.contagem[chave] += n

    def __str__(self):
        linhas = [f"  {k}: {v}" for k, v in sorted(self.contagem.items())]
        if self.avisos:
            linhas.append(f"  avisos: {len(self.avisos)} (primeiros abaixo)")
            linhas += [f"    - {a}" for a in self.avisos[:20]]
        return "\n".join(linhas)


SQL_CLIENTES = """
    select cr01codc as codigo, cr01nome as nome, cr01nfan as fantasia, cr01cgc as num,
           cr01dacc as dv, cr01insc as ie, cr01cida as cidade, cr01est as uf
    from fa.crci01
    where cr01empr = %s
    order by cr01codc
"""


def _linhas(cursor, sql, params):
    cursor.execute(sql, params)
    colunas = [c[0] for c in cursor.description]
    for linha in cursor.fetchall():
        yield dict(zip(colunas, linha))


class ImportadorClientes:
    def __init__(self, cursor, empresa: int):
        self.cur = cursor
        self.empresa = empresa
        self.r = Resultado()

    def executar(self) -> Resultado:
        with transaction.atomic():
            for linha in _linhas(self.cur, SQL_CLIENTES, [self.empresa]):
                self.gravar_cliente(linha)
        return self.r

    def gravar_cliente(self, l: dict) -> Parceiro | None:
        """Acha o cliente pelo código antigo ou pelo CNPJ; cria se não existir.

        Campos já preenchidos no sistema novo não são apagados por campos
        vazios do antigo.
        """
        codigo = l["codigo"]
        nome = L.texto(l["nome"])
        if not nome:
            self.r.avisos.append(f"Cliente {codigo}: sem nome, ignorado")
            return None
        doc = L.documento(l["num"], l["dv"])

        p = Parceiro.objects.filter(codigo_legado_cliente=codigo).first()
        if p is None and doc:
            p = Parceiro.objects.filter(documento=doc).first()
        criado = p is None
        if criado:
            p = Parceiro(documento=doc)

        p.razao_social = nome[:200]
        p.nome_fantasia = L.texto(l["fantasia"])[:200] or p.nome_fantasia
        p.inscricao_estadual = L.texto(l["ie"])[:20] or p.inscricao_estadual
        p.cidade = L.texto(l["cidade"])[:100] or p.cidade
        p.uf = L.uf(l["uf"]) or p.uf
        if doc and not p.documento:
            p.documento = doc
        p.eh_cliente = True
        p.codigo_legado_cliente = codigo
        p.save()
        self.r.soma("clientes criados" if criado else "clientes atualizados")
        return p
