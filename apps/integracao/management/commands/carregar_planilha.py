"""Carga inicial: saldos por tanque e contratos a partir da planilha DRE.

Uso:  python manage.py carregar_planilha "caminho/DRE 10.2026.xlsx"

Rode DEPOIS de `importar_legado` (os clientes, fornecedores e produtos
precisam existir). Pode rodar de novo: saldos já lançados não duplicam e
os contratos são atualizados.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.integracao.planilha import CargaPlanilha


class Command(BaseCommand):
    help = "Carrega saldos por tanque (aba Estoque) e contratos (aba Saldo Ctt Glicerina) da planilha DRE."

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="Caminho do arquivo .xlsx da planilha DRE do mês.")
        parser.add_argument("--simular", action="store_true", help="Mostra o resultado sem gravar.")

    def handle(self, *args, arquivo, simular, **opts):
        import openpyxl

        caminho = Path(arquivo)
        if not caminho.exists():
            raise CommandError(f"Arquivo não encontrado: {caminho}")
        wb = openpyxl.load_workbook(caminho, data_only=True, read_only=True)
        try:
            with transaction.atomic():
                carga = CargaPlanilha(wb).executar()
                self.stdout.write(f"Planilha de {carga.mes:%m/%Y}; saldos lançados em {carga.data_saldo:%d/%m/%Y}.")
                if simular:
                    raise _Simulacao(carga)
        except _Simulacao as s:
            carga = s.carga
            self.stdout.write(self.style.WARNING("Simulação: nada foi gravado."))
        except (KeyError, ValueError) as erro:
            raise CommandError(f"A planilha não está no formato esperado: {erro}")
        self.stdout.write(self.style.SUCCESS("Concluído:"))
        for chave, n in sorted(carga.contagem.items()):
            self.stdout.write(f"  {chave}: {n}")
        if carga.avisos:
            self.stdout.write(self.style.WARNING(f"  avisos: {len(carga.avisos)}"))
            for a in carga.avisos:
                self.stdout.write(f"    - {a}")


class _Simulacao(Exception):
    def __init__(self, carga):
        self.carga = carga
