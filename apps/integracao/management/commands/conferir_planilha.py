"""Confere as fórmulas da planilha DRE e lista os erros.

    python manage.py conferir_planilha "DRE 10.2026.xlsx"
"""

from django.core.management.base import BaseCommand

from apps.integracao.conferencia import conferir


class Command(BaseCommand):
    help = "Confere as fórmulas da planilha DRE e lista os erros (aba, célula, efeito)."

    def add_arguments(self, parser):
        parser.add_argument("arquivo")

    def handle(self, *args, arquivo, **opcoes):
        achados = conferir(arquivo)
        if not achados:
            self.stdout.write(self.style.SUCCESS("Nenhum problema encontrado."))
            return
        for a in achados:
            self.stdout.write(f"[{a.gravidade}] {a.aba} {a.celula}: {a.problema}")
            self.stdout.write(f"    {a.efeito}")
            if a.formula:
                self.stdout.write(f"    fórmula: {a.formula}")
        self.stdout.write(self.style.WARNING(f"{len(achados)} problema(s)."))
