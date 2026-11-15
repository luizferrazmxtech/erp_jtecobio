#!/usr/bin/env bash
# Restaura o .backup do ERP antigo no banco "legado" e importa as novidades.
#
# Uso:  scripts/restaurar_backup.sh backups/VivaClean_12h.backup [AAAA-MM-DD]
#
# Apaga e recria o banco "legado" a cada execução (ele é só uma cópia
# para leitura). O banco "erp" do sistema novo não é tocado aqui, só
# recebe as notas e cadastros pelo comando importar_legado.
set -euo pipefail

ARQUIVO="${1:?informe o caminho do arquivo .backup}"
DESDE="${2:-2000-01-01}"
NOME=$(basename "$ARQUIVO")

if [ ! -f "$ARQUIVO" ]; then
  echo "Arquivo não encontrado: $ARQUIVO" >&2
  exit 1
fi

mkdir -p backups
[ "$(realpath "$ARQUIVO")" = "$(realpath "backups/$NOME" 2>/dev/null || true)" ] || cp "$ARQUIVO" "backups/$NOME"

echo ">> Recriando o banco legado..."
docker compose exec -T db dropdb -U erp --if-exists legado
docker compose exec -T db createdb -U erp legado

echo ">> Restaurando $NOME (pode levar alguns minutos)..."
# --no-owner/--no-acl: o backup vem de outro servidor, com outros usuários.
# O pg_restore avisa que o schema "public" já existe; é esperado.
docker compose exec -T db pg_restore -U erp -d legado --no-owner --no-acl -j 4 "/backups/$NOME" || true

echo ">> Importando notas e cadastros desde $DESDE..."
python manage.py importar_legado --desde "$DESDE"
