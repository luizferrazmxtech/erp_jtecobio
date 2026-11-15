"""
Importa cadastros e notas fiscais da JT Ecobio a partir do banco do ERP
antigo (restaurado do arquivo .backup num PostgreSQL auxiliar).

Pode ser rodado quantas vezes quiser: registros já importados são
atualizados, não duplicados. Só lê do banco antigo, nunca grava nele.

Tabelas usadas no ERP antigo (empresa = 1 é a JT Ecobio):
  fa.crci01   clientes            es.esci03   fornecedores
  fa.crci19   transportadoras     es.esci01   produtos
  fi.arqnot   NF de saída         fi.arqnoi   itens da NF de saída
  fi.arqnfe   situação da NF-e (chave, cancelamento)
  fi.aent01   NF de entrada/CT-e  fi.aent02   itens da NF de entrada
  fi.danfe    XMLs recebidos (usado para achar a chave de acesso das entradas)
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.cadastros.models import Parceiro, Produto
from apps.fiscal.models import NotaFiscal, NotaItem

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


def _linhas(cursor, sql, params):
    cursor.execute(sql, params)
    colunas = [c[0] for c in cursor.description]
    for linha in cursor.fetchall():
        yield dict(zip(colunas, linha))


class ImportadorLegado:
    def __init__(self, cursor, empresa: int, desde: date, hoje: date | None = None):
        self.cur = cursor
        self.empresa = empresa
        self.desde = desde
        self.hoje = hoje or timezone.localdate()
        self.r = Resultado()
        self._clientes: dict[int, Parceiro] = {}
        self._fornecedores: dict[int, Parceiro] = {}
        self._produtos: dict[str, Produto] = {}

    # ------------------------------------------------------------------
    def executar(self) -> Resultado:
        with transaction.atomic():
            self.importar_clientes()
            self.importar_fornecedores()
            self.importar_transportadoras()
            self.importar_produtos()
            self.importar_notas_saida()
            self.importar_notas_entrada()
        return self.r

    # Parceiros ----------------------------------------------------------
    def _parceiro(self, *, doc, nome, fantasia, ie, cidade, uf, papel, campo_codigo, codigo):
        """Acha o parceiro pelo código antigo ou pelo CNPJ; cria se não existir."""
        p = Parceiro.objects.filter(**{campo_codigo: codigo}).first()
        if p is None and doc:
            p = Parceiro.objects.filter(documento=doc).first()
        criado = p is None
        if criado:
            p = Parceiro(documento=doc)
        p.razao_social = nome or p.razao_social or f"Sem nome ({codigo})"
        p.nome_fantasia = fantasia or p.nome_fantasia
        p.inscricao_estadual = ie or p.inscricao_estadual
        p.cidade = cidade or p.cidade
        p.uf = uf or p.uf
        if doc and not p.documento:
            p.documento = doc
        setattr(p, papel, True)
        setattr(p, campo_codigo, codigo)
        p.save()
        self.r.soma(f"parceiros {'criados' if criado else 'atualizados'}")
        return p

    def importar_clientes(self):
        sql = """
            select cr01codc as codigo, cr01nome as nome, cr01nfan as fantasia, cr01cgc as num,
                   cr01dacc as dv, cr01insc as ie, cr01cida as cidade, cr01est as uf
            from fa.crci01 where cr01empr = %s
        """
        for l in _linhas(self.cur, sql, [self.empresa]):
            self._clientes[l["codigo"]] = self._parceiro(
                doc=L.documento(l["num"], l["dv"]), nome=L.texto(l["nome"]), fantasia=L.texto(l["fantasia"]),
                ie=L.texto(l["ie"])[:20], cidade=L.texto(l["cidade"]), uf=L.uf(l["uf"]),
                papel="eh_cliente", campo_codigo="codigo_legado_cliente", codigo=l["codigo"],
            )

    def importar_fornecedores(self):
        sql = """
            select es03codf as codigo, es03nome as nome, es03nfan as fantasia, es03ncgc as num,
                   es03dcgc as dv, es03insc as ie, es03cida as cidade, es03est as uf
            from es.esci03 where es03empr = %s
        """
        for l in _linhas(self.cur, sql, [self.empresa]):
            self._fornecedores[l["codigo"]] = self._parceiro(
                doc=L.documento(l["num"], l["dv"]), nome=L.texto(l["nome"]), fantasia=L.texto(l["fantasia"]),
                ie=L.texto(l["ie"])[:20], cidade=L.texto(l["cidade"]), uf=L.uf(l["uf"]),
                papel="eh_fornecedor", campo_codigo="codigo_legado_fornecedor", codigo=l["codigo"],
            )

    def importar_transportadoras(self):
        sql = """
            select cr19codt as codigo, cr19nome as nome, cr19fan as fantasia, cr19cgc as doc,
                   cr19insc as ie, cr19cida as cidade, cr19est as uf
            from fa.crci19 where cr19empr = %s
        """
        for l in _linhas(self.cur, sql, [self.empresa]):
            self._parceiro(
                doc=L.documento_texto(l["doc"])[:14], nome=L.texto(l["nome"]), fantasia=L.texto(l["fantasia"]),
                ie=L.texto(l["ie"])[:20], cidade=L.texto(l["cidade"]), uf=L.uf(l["uf"]),
                papel="eh_transportadora", campo_codigo="codigo_legado_transportadora", codigo=l["codigo"],
            )

    # Produtos -----------------------------------------------------------
    def importar_produtos(self):
        """O cadastro antigo tem ~1.800 itens de todas as empresas do grupo.
        Importa só os que aparecem em notas da JT no período."""
        sql = """
            select p.e01codp as codigo, p.e01nome as nome, p.e01unid as unidade
            from es.esci01 p
            where p.e01empr = %(e)s and p.e01codp in (
                select licodp from fi.arqnoi i join fi.arqnot n
                  on n.lsempr = i.liempr and n.lsnnot = i.linnot and n.lsserie = i.liserie
                 where i.liempr = %(e)s and n.lsdate >= %(d)s
                union
                select lfcodp from fi.aent02 i join fi.aent01 n
                  on n.enempr = i.lfempr and n.codemi = i.lfcodc and n.nronot = i.lfnnot
                 where i.lfempr = %(e)s and n.datent >= %(d)s
            )
        """
        for l in _linhas(self.cur, sql, {"e": self.empresa, "d": self.desde}):
            codigo = L.texto(l["codigo"])
            nome = L.texto(l["nome"])[:200]
            tipo, densidade = L.classificar_produto(nome)
            # Nome vem sempre do ERP antigo; tipo, densidade e "controla
            # estoque" só são sugeridos na criação (depois quem manda é o
            # cadastro do sistema novo).
            prod, criado = Produto.objects.update_or_create(
                codigo_legado=codigo,
                defaults={"nome": nome},
                create_defaults={
                    "codigo": f"L{codigo}", "codigo_legado": codigo, "nome": nome,
                    "unidade": L.texto(l["unidade"])[:6] or "UN", "tipo": tipo, "densidade": densidade,
                    "controla_estoque": tipo in L.TIPOS_COM_ESTOQUE,
                },
            )
            self._produtos[codigo] = prod
            self.r.soma(f"produtos {'criados' if criado else 'atualizados'}")

    def _produto(self, codigo):
        return self._produtos.get(L.texto(codigo))

    # Notas de saída -------------------------------------------------------
    def importar_notas_saida(self):
        # O livro fiscal antigo grava uma linha por CFOP/alíquota e às vezes
        # duplica linhas idênticas: tira as duplicadas e soma por nota.
        sql = """
            with linhas as (
                select distinct on (lsnnot, lsserie, lscodf, lsaliq, lsvcont) *
                from fi.arqnot where lsempr = %(e)s and lsdate >= %(d)s
            )
            select n.lsnnot as numero, n.lsserie as serie, min(n.lsdate) as data,
                   min(n.lscodcl) as cliente, max(n.lsmodnf) as modelo, max(n.lsflag) as flag,
                   (array_agg(n.lscodf order by n.lsvcont desc))[1] as cfop,
                   sum(n.lsvcont) as total, sum(n.lsimp) as icms, sum(n.lsipi) as ipi,
                   sum(n.lspis) as pis, sum(n.lscof) as cofins, sum(n.lsfret) as frete,
                   max(n.lscped) as pedido_cliente,
                   max(f.nfechave) as chave, max(f.nfetpcan) as tpcan, max(f.nfemtcan) as motivo
            from linhas n
            left join fi.arqnfe f
              on f.nfeempr = n.lsempr and f.nfennota = n.lsnnot and trim(f.nfeserie) = trim(n.lsserie)
            group by n.lsnnot, n.lsserie
        """
        itens = self._itens_saida()
        for l in _linhas(self.cur, sql, {"e": self.empresa, "d": self.desde}):
            data = L.data_valida(l["data"], self.hoje)
            if data is None:
                self.r.avisos.append(f"NF saída {l['numero']}: data inválida {l['data']}, ignorada")
                continue
            # Notas de devolução/retorno (situação "D") gravam o código do
            # FORNECEDOR no mesmo campo do cliente. Conferido no backup de
            # 12/11/2026: a NF 4023 tem código 523, que é o cliente Neuripe,
            # mas a nota é para a Peter Cremer (fornecedor 523).
            parceiro = self._destinatario_saida(l["cliente"], L.texto(l["flag"]))
            if parceiro is None:
                self.r.avisos.append(f"NF saída {l['numero']}: destinatário {l['cliente']} não encontrado, ignorada")
                continue
            serie = L.texto(l["serie"])
            nota = self._gravar_nota(
                origem=f"S:{serie}:{l['numero']}",
                direcao=NotaFiscal.Direcao.SAIDA, modelo=L.modelo(l["modelo"] or "55"),
                numero=l["numero"], serie=serie, parceiro=parceiro, data_emissao=data, data_entrada=data,
                cfop=L.texto(l["cfop"]), chave=L.documento_texto(l["chave"])[:44],
                valor_total=L.decimal(l["total"]), valor_icms=L.decimal(l["icms"]), valor_ipi=L.decimal(l["ipi"]),
                valor_pis=L.decimal(l["pis"]), valor_cofins=L.decimal(l["cofins"]), valor_frete=L.decimal(l["frete"]),
                cancelada=L.cancelada(l["tpcan"]) or L.texto(l["flag"]) == L.SITUACAO_CANCELADA,
                motivo_cancelamento=L.texto(l["motivo"])[:200],
                situacao_legado=L.texto(l["flag"])[:5], pedido_cliente=L.texto(l["pedido_cliente"])[:30],
            )
            self._gravar_itens(nota, itens.get((l["numero"], serie), []))
            self.r.soma("notas de saída")

    def _destinatario_saida(self, codigo, situacao):
        if situacao == L.SITUACAO_DEVOLUCAO:
            return self._fornecedores.get(codigo) or self._clientes.get(codigo)
        return self._clientes.get(codigo) or self._fornecedores.get(codigo)

    def _itens_saida(self):
        sql = """
            select distinct on (i.linnot, i.liserie, i.liitem)
                   i.linnot as numero, i.liserie as serie, i.liitem as item, i.licodp as codigo,
                   i.linome as descricao, i.liunid as unidade, i.liqtdep as quantidade, i.livprod as total,
                   i.livicm as icms, i.liipi as ipi, i.lipis as pis, i.licof as cofins, i.licodf as cfop
            from fi.arqnoi i join fi.arqnot n
              on n.lsempr = i.liempr and n.lsnnot = i.linnot and n.lsserie = i.liserie
            where i.liempr = %(e)s and n.lsdate >= %(d)s
        """
        itens = defaultdict(list)
        for l in _linhas(self.cur, sql, {"e": self.empresa, "d": self.desde}):
            itens[(l["numero"], L.texto(l["serie"]))].append(l)
        return itens

    # Notas de entrada e CT-e -------------------------------------------
    def importar_notas_entrada(self):
        sql = """
            with linhas as (
                select distinct on (codemi, nronot, serie, codfis, aliq, valcont) *
                from fi.aent01 where enempr = %(e)s and datent >= %(d)s
            )
            select n.codemi as fornecedor, n.nronot as numero, n.serie as serie,
                   min(n.datemi) as emissao, min(n.datent) as entrada, max(n.tiponf) as modelo,
                   sum(n.valcont) as total, sum(n.impcre) as icms, sum(n.vipi) as ipi,
                   sum(n.vlpis) as pis, sum(n.vlcof) as cofins, sum(n.tfrete) as frete,
                   max(n.observ) as observacao
            from linhas n
            group by n.codemi, n.nronot, n.serie
        """
        itens = self._itens_entrada()
        chaves, por_numero = self._chaves_xml()
        for l in _linhas(self.cur, sql, {"e": self.empresa, "d": self.desde}):
            entrada = L.data_valida(l["entrada"], self.hoje)
            emissao = L.data_valida(l["emissao"], self.hoje) or entrada
            if entrada is None:
                self.r.avisos.append(f"NF entrada {l['numero']}: data inválida {l['entrada']}, ignorada")
                continue
            serie = L.texto(l["serie"])
            modelo = L.modelo(l["modelo"])
            parceiro = self._fornecedores.get(l["fornecedor"])
            if parceiro is None:
                # Alguns CT-e são lançados sem fornecedor (código 0). Se houver
                # um único XML recebido com esse número, usa o emitente dele.
                parceiro = self._parceiro_pelo_xml(por_numero.get((int(l["numero"]), modelo), []))
            if parceiro is None:
                self.r.avisos.append(f"NF entrada {l['numero']}: fornecedor {l['fornecedor']} não encontrado, ignorada")
                continue
            chave = chaves.get((parceiro.documento, int(l["numero"]), modelo), "")
            nota = self._gravar_nota(
                origem=f"E:{l['fornecedor']}:{serie}:{l['numero']}",
                direcao=NotaFiscal.Direcao.ENTRADA, modelo=modelo, numero=l["numero"], serie=serie,
                parceiro=parceiro, data_emissao=emissao, data_entrada=entrada, chave=chave,
                valor_total=L.decimal(l["total"]), valor_icms=L.decimal(l["icms"]), valor_ipi=L.decimal(l["ipi"]),
                valor_pis=L.decimal(l["pis"]), valor_cofins=L.decimal(l["cofins"]), valor_frete=L.decimal(l["frete"]),
                observacao=L.texto(l["observacao"]),
            )
            self._gravar_itens(nota, itens.get((l["fornecedor"], l["numero"]), []))
            self.r.soma("notas de entrada" if modelo != "57" else "CT-e")

    def _itens_entrada(self):
        sql = """
            select distinct on (i.lfcodc, i.lfnnot, i.lfitem)
                   i.lfcodc as fornecedor, i.lfnnot as numero, i.lfitem as item, i.lfcodp as codigo,
                   i.lfnome as descricao, i.lfunid as unidade, i.lfqtdep as quantidade, i.lfvprod as total,
                   i.lfvicm as icms, i.lfipi as ipi, i.lfvpis as pis, i.lfvcof as cofins, i.lfcodf as cfop
            from fi.aent02 i join fi.aent01 n
              on n.enempr = i.lfempr and n.codemi = i.lfcodc and n.nronot = i.lfnnot
            where i.lfempr = %(e)s and n.datent >= %(d)s
        """
        itens = defaultdict(list)
        for l in _linhas(self.cur, sql, {"e": self.empresa, "d": self.desde}):
            itens[(l["fornecedor"], l["numero"])].append(l)
        return itens

    def _chaves_xml(self):
        """O livro de entradas não guarda a chave de acesso; ela vem dos XMLs
        recebidos, ligando por CNPJ do emitente + número + modelo."""
        sql = """
            select emit_cnpj as cnpj, emit_xnome as nome, emit_xmun as cidade, emit_uf as uf,
                   ide_nnf as numero, ide_mod as modelo, ide_chv as chave
            from fi.danfe where ide_demi >= %(d)s
        """
        chaves, por_numero = {}, defaultdict(list)
        for l in _linhas(self.cur, sql, {"d": self.desde}):
            if l["numero"] is None:
                continue
            cnpj = L.documento_texto(l["cnpj"]).zfill(14)
            modelo = L.modelo(l["modelo"])
            chaves[(cnpj, int(l["numero"]), modelo)] = L.documento_texto(l["chave"])[:44]
            por_numero[(int(l["numero"]), modelo)].append({**l, "cnpj": cnpj, "modelo": modelo})
        return chaves, por_numero

    def _parceiro_pelo_xml(self, candidatos):
        emitentes = {c["cnpj"]: c for c in candidatos}
        if len(emitentes) != 1:
            return None
        c = next(iter(emitentes.values()))
        p = Parceiro.objects.filter(documento=c["cnpj"]).first()
        if p is None:
            p = Parceiro.objects.create(
                documento=c["cnpj"], razao_social=L.texto(c["nome"])[:200],
                cidade=L.texto(c["cidade"]), uf=L.uf(c["uf"]),
            )
            self.r.soma("parceiros criados pelo XML")
        papel = "eh_transportadora" if c["modelo"] == "57" else "eh_fornecedor"
        if not getattr(p, papel):
            setattr(p, papel, True)
            p.save(update_fields=[papel])
        return p

    # Gravação -----------------------------------------------------------
    def _gravar_nota(self, *, origem, **campos) -> NotaFiscal:
        campos["importada_em"] = timezone.now()
        nota, _ = NotaFiscal.objects.update_or_create(origem_legado=origem, defaults=campos)
        return nota

    def _gravar_itens(self, nota: NotaFiscal, linhas):
        vistos = set()
        for l in linhas:
            item = int(l["item"] or 0)
            vistos.add(item)
            NotaItem.objects.update_or_create(
                nota=nota, item=item,
                defaults={
                    "produto": self._produto(l["codigo"]),
                    "codigo_produto_legado": L.texto(l["codigo"])[:30],
                    "descricao": L.texto(l["descricao"])[:200] or "(sem descrição)",
                    "unidade": L.texto(l["unidade"])[:6],
                    "quantidade": L.decimal(l["quantidade"], 4),
                    "valor_total": L.decimal(l["total"]),
                    "valor_icms": L.decimal(l["icms"]),
                    "valor_ipi": L.decimal(l["ipi"]),
                    "valor_pis": L.decimal(l["pis"]),
                    "valor_cofins": L.decimal(l["cofins"]),
                    "cfop": L.texto(l["cfop"])[:4],
                },
            )
            self.r.soma("itens de nota")
        # Itens que sumiram no ERP antigo (nota corrigida) saem daqui também,
        # desde que não estejam ligados a contrato.
        nota.itens.exclude(item__in=vistos).filter(contrato__isnull=True).delete()
