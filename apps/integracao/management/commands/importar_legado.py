from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from apps.integracao.importador import ImportadorClientes


class Command(BaseCommand):
    help = (
        "Importa os clientes da JT Ecobio a partir do banco do ERP antigo "
        "(configure LEGADO_DB no .env). Pode ser rodado várias vezes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--simular", action="store_true",
            help="Roda tudo e mostra o resultado, mas desfaz as gravações no final.",
        )

    def handle(self, *args, simular, **opts):
        if "legado" not in settings.DATABASES:
            raise CommandError("Banco do ERP antigo não configurado. Defina LEGADO_DB (e LEGADO_*) no .env.")

        empresa = settings.LEGADO_EMPRESA
        self.stdout.write(f"Importando clientes da empresa {empresa}...")
        with connections["legado"].cursor() as cursor:
            try:
                with transaction.atomic():
                    resultado = ImportadorClientes(cursor, empresa).executar()
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
