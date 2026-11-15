"""
Configurações do ERP JT Ecobio.

Tudo que muda entre ambientes (senha, banco, debug) vem de variáveis de
ambiente. Veja o arquivo `.env.example` para a lista completa.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_env(caminho: Path) -> None:
    """Lê um arquivo .env simples (CHAVE=valor) sem depender de bibliotecas."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip())


_carregar_env(BASE_DIR / ".env")


def env(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome, padrao)


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-inseguro-troque-em-producao")
DEBUG = env("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.cadastros",
    "apps.fiscal",
    "apps.contratos",
    "apps.estoque",
    "apps.integracao",
    "apps.vendas",
    "apps.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.contexto.exemplos",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Banco de dados -------------------------------------------------------------
# Sem POSTGRES_DB definido, usa SQLite (bom para testar rápido no computador).
# Em produção, defina as variáveis POSTGRES_* para usar PostgreSQL.
if env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "postgres"),
            "PASSWORD": env("POSTGRES_PASSWORD"),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Banco do ERP antigo (restaurado a partir do .backup). Só leitura: usado
# pelo comando `python manage.py importar_legado`.
if env("LEGADO_DB"):
    DATABASES["legado"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("LEGADO_DB"),
        "USER": env("LEGADO_USER", "postgres"),
        "PASSWORD": env("LEGADO_PASSWORD"),
        "HOST": env("LEGADO_HOST", "localhost"),
        "PORT": env("LEGADO_PORT", "5432"),
    }

# Código da JT Ecobio no ERP antigo (o banco atende mais de uma empresa).
LEGADO_EMPRESA = int(env("LEGADO_EMPRESA", "1"))


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
# Sem separador de milhar automático: ele estragava ids em links (1.051).
# Os números das telas são formatados pelos filtros kg, brl e num (apps/web/templatetags/erp.py).
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

LOGIN_URL = "entrar"
LOGIN_REDIRECT_URL = "web:painel"
LOGOUT_REDIRECT_URL = "entrar"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
