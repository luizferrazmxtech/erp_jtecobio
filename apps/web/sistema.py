"""Telas de sistema: usuários (com o que cada um pode fazer) e configurações.

Substituem a "Administração" do Django, que fica fora do menu (sobra só para
suporte técnico em /admin/). Só o administrador do sistema (superusuário)
abre estas telas.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, password_validation, update_session_auth_hash
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import Group, Permission
from django.shortcuts import get_object_or_404, redirect, render

from apps.estoque.models import Configuracao

Usuario = get_user_model()

# Áreas do sistema → apps do Django. Quem tem a área pode incluir, alterar e
# excluir ali; ver as telas, qualquer usuário ativo pode.
AREAS = {
    "vendas": ("Vendas", "Pedidos, tanques de cada pedido e tabela de frete", ["vendas"]),
    "estoque": ("Estoque", "Entradas, saídas, transferências e ajustes de inventário", ["estoque"]),
    "contratos": ("Contratos", "Contratos de compra e venda e ligação das notas", ["contratos"]),
    "cadastros": ("Cadastros", "Clientes, fornecedores, produtos, locais e tanques", ["cadastros"]),
    "fiscal": ("Notas fiscais", "Ajustes nas notas importadas do sistema emissor", ["fiscal"]),
}
PREFIXO = "Área: "


def _grupo(area: str) -> Group:
    """Grupo de permissões da área (criado na primeira vez que alguém recebe a área)."""
    nome, _, apps = AREAS[area]
    grupo, criado = Group.objects.get_or_create(name=PREFIXO + nome)
    if criado or not grupo.permissions.exists():
        grupo.permissions.set(Permission.objects.filter(content_type__app_label__in=apps))
    return grupo


def areas_do_usuario(u) -> list[str]:
    nomes = set(u.groups.values_list("name", flat=True))
    return [a for a, (nome, _, _) in AREAS.items() if PREFIXO + nome in nomes]


so_admin = user_passes_test(lambda u: u.is_active and u.is_superuser, login_url="entrar")


class UsuarioForm(forms.ModelForm):
    administrador = forms.BooleanField(
        label="Administrador do sistema", required=False,
        help_text="Pode tudo, inclusive criar usuários e mudar as configurações.")
    areas = forms.MultipleChoiceField(
        label="Pode lançar e alterar em", required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(a, nome) for a, (nome, _, _) in AREAS.items()])
    perfis = forms.ModelMultipleChoiceField(
        label="Perfis prontos", required=False, widget=forms.CheckboxSelectMultiple, queryset=Group.objects.none(),
        help_text="grupos com permissões definidas por cargo.")
    senha = forms.CharField(label="Senha", required=False, widget=forms.PasswordInput(render_value=False),
                            strip=False)
    senha2 = forms.CharField(label="Repita a senha", required=False, widget=forms.PasswordInput, strip=False)

    class Meta:
        model = Usuario
        fields = ["username", "first_name", "last_name", "email", "is_active"]
        labels = {"username": "Usuário (para entrar)", "first_name": "Nome", "last_name": "Sobrenome",
                  "email": "E-mail", "is_active": "Ativo (pode entrar no sistema)"}
        help_texts = {"username": "Sem espaços. Ex.: maria.silva", "is_active": ""}

    def __init__(self, *a, editando_a_si=False, **k):
        super().__init__(*a, **k)
        self.editando_a_si = editando_a_si
        u = self.instance
        self.fields["perfis"].queryset = Group.objects.exclude(name__startswith=PREFIXO).order_by("name")
        if u.pk:
            self.fields["perfis"].initial = list(u.groups.exclude(name__startswith=PREFIXO))
            self.fields["administrador"].initial = u.is_superuser
            self.fields["areas"].initial = areas_do_usuario(u)
            self.fields["senha"].label = "Nova senha"
            self.fields["senha"].help_text = "Deixe em branco para manter a senha atual."
        else:
            self.fields["senha"].required = True
            self.fields["senha2"].required = True
            self.fields["is_active"].initial = True

    def clean(self):
        dados = super().clean()
        s1, s2 = dados.get("senha"), dados.get("senha2")
        if s1 or s2:
            if s1 != s2:
                self.add_error("senha2", "As duas senhas não são iguais.")
            else:
                try:
                    password_validation.validate_password(s1, self.instance)
                except forms.ValidationError as e:
                    self.add_error("senha", e)
        if self.editando_a_si and (not dados.get("administrador") or not dados.get("is_active")):
            self.add_error("administrador", "Você não pode tirar o seu próprio acesso de administrador.")
        return dados

    def save(self, commit=True):
        u = super().save(commit=False)
        u.is_superuser = self.cleaned_data["administrador"]
        u.is_staff = u.is_superuser  # acesso de suporte ao /admin/
        if self.cleaned_data.get("senha"):
            u.set_password(self.cleaned_data["senha"])
        u.save()
        u.groups.set([*self.cleaned_data["perfis"], *(_grupo(a) for a in self.cleaned_data["areas"])])
        return u


@so_admin
def usuarios(request):
    lista = Usuario.objects.order_by("-is_active", "username").prefetch_related("groups")
    linhas = [{"u": u, "areas": [AREAS[a][0] for a in areas_do_usuario(u)]} for u in lista]
    return render(request, "web/sistema/usuarios.html", {"linhas": linhas})


@so_admin
def usuario(request, pk=None):
    obj = get_object_or_404(Usuario, pk=pk) if pk else None
    a_si = bool(obj and obj.pk == request.user.pk)
    form = UsuarioForm(request.POST or None, instance=obj, editando_a_si=a_si)
    if request.method == "POST" and form.is_valid():
        salvo = form.save()
        if a_si and form.cleaned_data.get("senha"):
            update_session_auth_hash(request, salvo)  # continua logado depois de trocar a própria senha
        messages.success(request, f"Usuário {salvo.username} salvo.")
        return redirect("web:usuarios")
    return render(request, "web/sistema/usuario.html", {
        "form": form, "obj": obj, "areas": AREAS,
        "titulo": f"Editar usuário {obj.username}" if obj else "Adicionar usuário",
    })


class ConfiguracaoForm(forms.ModelForm):
    class Meta:
        model = Configuracao
        fields = ["metodo_custo", "permitir_saldo_negativo"]
        labels = {"permitir_saldo_negativo": "Permitir saldo negativo"}


@so_admin
def configuracoes(request):
    form = ConfiguracaoForm(request.POST or None, instance=Configuracao.atual())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configurações salvas.")
        return redirect("web:configuracoes")
    return render(request, "web/formulario.html", {
        "form": form, "titulo": "Configurações", "icone": "config", "voltar": "web:painel", "sem_lista": True,
        "obj": None, "verbo": "Configurações", "botao": "Salvar configurações",
        "secoes": [{"leve": "Como o sistema", "forte": "calcula o estoque",
                    "sub": "Valem para todas as telas e todos os usuários.",
                    "campos": [form["metodo_custo"], form["permitir_saldo_negativo"]]}],
    })
