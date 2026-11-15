"""Cria (ou apaga) fretes e pedidos de EXEMPLO para ver o sistema funcionando.

    python manage.py carregar_exemplos           # cria
    python manage.py carregar_exemplos --apagar  # remove só o que é exemplo
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.integracao import exemplos


class Command(BaseCommand):
    help = "Cria fretes e pedidos fictícios (marcados como exemplo). Use --apagar para remover."

    def add_arguments(self, parser):
        parser.add_argument("--apagar", action="store_true", help="Remove tudo que foi criado como exemplo.")

    def handle(self, *args, apagar, **opts):
        if apagar:
            r = exemplos.apagar()
            self.stdout.write(self.style.SUCCESS(f"Exemplos apagados: {r}"))
            return
        usuario = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        try:
            r = exemplos.carregar(usuario)
        except ValidationError as erro:
            raise CommandError(" ".join(erro.messages))
        self.stdout.write(self.style.SUCCESS(f"Exemplos criados: {r['fretes']} fretes e {r['pedidos']} pedidos."))
        self.stdout.write("Para tirar: python manage.py carregar_exemplos --apagar")
