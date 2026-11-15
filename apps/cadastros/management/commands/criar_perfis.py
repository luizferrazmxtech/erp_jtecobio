"""Cria os grupos de usuários (perfis) com as permissões de cada área.

Rode depois de `migrate`:  python manage.py criar_perfis
Depois, em Sistema → Usuários, marque o perfil de cada usuário.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

VER = "view"
TUDO = ("view", "add", "change", "delete")

PERFIS = {
    "Diretoria": {
        "cadastros": TUDO, "contratos": TUDO, "fiscal": (VER,), "estoque": TUDO, "vendas": TUDO,
        "servicos": TUDO,
    },
    "Comercial": {
        "cadastros": ("view", "add", "change"), "contratos": TUDO, "fiscal": (VER,), "estoque": (VER,),
        "vendas": TUDO, "servicos": TUDO,
    },
    "Logística": {
        "cadastros": (VER,), "contratos": (VER,), "fiscal": (VER,), "estoque": ("view", "add"),
        "vendas": ("view", "change"), "servicos": ("view", "add", "change"),
    },
    "Financeiro": {
        "cadastros": (VER,), "contratos": (VER,), "fiscal": (VER,), "estoque": (VER,), "vendas": (VER,),
        "servicos": (VER,),
    },
}

# Funcionários do envase: só a tela de apontamento (/servicos/operador/).
# Podem apontar início, pausas e fim e concluir a OS com o que foi cheio,
# mas não editam, cancelam nem faturam OS.
PERMISSOES_SOLTAS = {
    "Operação": ["servicos.view_ordemservico", "servicos.add_apontamento", "servicos.view_apontamento",
                 "servicos.view_tiporecipiente"],
}


class Command(BaseCommand):
    help = "Cria ou atualiza os grupos Diretoria, Comercial, Logística, Financeiro e Operação."

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
        for nome, codigos in PERMISSOES_SOLTAS.items():
            grupo, _ = Group.objects.get_or_create(name=nome)
            perms = [Permission.objects.get(content_type__app_label=c.split(".")[0], codename=c.split(".")[1])
                     for c in codigos]
            grupo.permissions.set(perms)
            self.stdout.write(f"{nome}: {len(perms)} permissões")
        self.stdout.write(self.style.SUCCESS("Perfis criados."))
