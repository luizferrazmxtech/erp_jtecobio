"""Cria os grupos de usuários (perfis) com as permissões de cada área.

Rode depois de `migrate`:  python manage.py criar_perfis
Depois, no admin, coloque cada usuário no grupo certo.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

VER = "view"
TUDO = ("view", "add", "change", "delete")

PERFIS = {
    "Diretoria": {
        "cadastros": TUDO, "contratos": TUDO, "fiscal": (VER,), "estoque": TUDO,
    },
    "Comercial": {
        "cadastros": ("view", "add", "change"), "contratos": TUDO, "fiscal": (VER,), "estoque": (VER,),
    },
    "Logística": {
        "cadastros": (VER,), "contratos": (VER,), "fiscal": (VER,), "estoque": ("view", "add"),
    },
    "Financeiro": {
        "cadastros": (VER,), "contratos": (VER,), "fiscal": (VER,), "estoque": (VER,),
    },
}


class Command(BaseCommand):
    help = "Cria ou atualiza os grupos Diretoria, Comercial, Logística e Financeiro."

    def handle(self, *args, **opts):
        for nome, apps in PERFIS.items():
            grupo, _ = Group.objects.get_or_create(name=nome)
            perms = []
            for app, acoes in apps.items():
                for acao in acoes:
                    perms += list(Permission.objects.filter(
                        content_type__app_label=app, codename__startswith=f"{acao}_"))
            grupo.permissions.set(perms)
            self.stdout.write(f"{nome}: {len(perms)} permissões")
        self.stdout.write(self.style.SUCCESS("Perfis criados."))
