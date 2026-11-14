#!/usr/bin/env bash
# Restaura o .backup do ERP antigo no banco "legado" (para consulta) e
# importa/atualiza os clientes no sistema novo.
#
# Uso:  scripts/restaurar_backup.sh backups/VivaClean_12h.backup
#
# Apaga e recria o banco "legado" a cada execução (ele é só uma cópia
# para consulta). Do sistema novo, só o cadastro de clientes é tocado.
set -euo pipefail

ARQUIVO="${1:?informe o caminho do arquivo .backup}"
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

echo ">> Importando clientes..."
python manage.py importar_legado
