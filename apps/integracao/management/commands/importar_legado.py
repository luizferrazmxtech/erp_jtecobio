from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.integracao.importador import ImportadorLegado


class Command(BaseCommand):
    help = (
        "Importa clientes, fornecedores, transportadoras, produtos e notas fiscais da JT Ecobio "
        "a partir do banco do ERP antigo (configure LEGADO_DB no .env). Pode ser rodado várias vezes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--desde", default="2026-01-01",
            help="Importa notas a partir desta data (AAAA-MM-DD). Padrão: 2026-01-01.",
        )
        parser.add_argument(
            "--simular", action="store_true",
            help="Roda tudo e mostra o resultado, mas desfaz as gravações no final.",
        )

    def handle(self, *args, desde, simular, **opts):
        if "legado" not in settings.DATABASES:
            raise CommandError("Banco do ERP antigo não configurado. Defina LEGADO_DB (e LEGADO_*) no .env.")
        try:
            data_inicio = date.fromisoformat(desde)
        except ValueError:
            raise CommandError("Data inválida em --desde. Use AAAA-MM-DD.")

        from django.db import transaction

        empresa = settings.LEGADO_EMPRESA
        self.stdout.write(f"Importando empresa {empresa} desde {data_inicio:%d/%m/%Y}...")
        with connections["legado"].cursor() as cursor:
            try:
                with transaction.atomic():
                    resultado = ImportadorLegado(cursor, empresa, data_inicio).executar()
                    if simular:
                        raise _Simulacao(resultado)
            except _Simulacao as s:
                resultado = s.resultado
                self.stdout.write(self.style.WARNING("Simulação: nada foi gravado."))
        self.stdout.write(self.style.SUCCESS("Concluído:"))
        self.stdout.write(str(resultado))


class _Simulacao(Exception):
    def __init__(self, resultado):
        self.resultado = resultado
