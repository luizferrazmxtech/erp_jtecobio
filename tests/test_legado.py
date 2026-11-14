from apps.cadastros.models import Parceiro
from apps.integracao import legado as L
from apps.integracao.importador import ImportadorClientes


def test_documento_monta_cnpj_com_digito():
    assert L.documento(230019640001, 50) == "23001964000150"
    assert L.documento(16158140064, 87) == "01615814006487"
    assert L.documento(0, 0) == ""
    assert L.documento(None, None) == ""


class CursorFalso:
    """Imita o cursor do banco antigo devolvendo linhas fixas."""

    colunas = ["codigo", "nome", "fantasia", "num", "dv", "ie", "cidade", "uf"]

    def __init__(self, linhas):
        self.linhas = linhas
        self.description = [(c,) for c in self.colunas]

    def execute(self, sql, params):
        assert "fa.crci01" in sql and params == [1]

    def fetchall(self):
        return self.linhas


def test_importa_clientes_sem_duplicar(db):
    linhas = [
        (106, "AROMA BIOQUIMICA LTDA", "AROMA", 134267900001, 13, "535012345110", "PIRACICABA", "sp"),
        (421, "UNILEVER BRASIL INDUSTRIAL LTDA", "", 16158140064, 87, "", "VINHEDO", "SP"),
        (999, "   ", "", 0, 0, "", "", ""),
    ]
    r = ImportadorClientes(CursorFalso(linhas), 1).executar()
    assert r.contagem["clientes criados"] == 2
    assert len(r.avisos) == 1

    aroma = Parceiro.objects.get(codigo_legado_cliente=106)
    assert aroma.documento == "13426790000113"
    assert aroma.eh_cliente and aroma.uf == "SP"

    # Rodar de novo atualiza em vez de duplicar
    r2 = ImportadorClientes(CursorFalso(linhas), 1).executar()
    assert r2.contagem["clientes atualizados"] == 2
    assert Parceiro.objects.count() == 2


def test_cliente_que_ja_existe_pelo_cnpj_e_reaproveitado(db):
    existente = Parceiro.objects.create(razao_social="Aroma", documento="13426790000113",
                                        eh_industrializador=True)
    linhas = [(106, "AROMA BIOQUIMICA LTDA", "", 134267900001, 13, "", "", "")]
    ImportadorClientes(CursorFalso(linhas), 1).executar()
    existente.refresh_from_db()
    assert existente.eh_cliente and existente.eh_industrializador
    assert existente.codigo_legado_cliente == 106
    assert Parceiro.objects.count() == 1
