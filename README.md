# ERP JT Ecobio

Sistema de gestão da JT Ecobio: estoque de glicerina por tanque e distribuidora, contratos de compra e venda com saldo, e leitura das notas fiscais emitidas no ERP antigo.

Feito em **Python + Django** com banco **PostgreSQL**.

## O que já existe (fase 1)

| Parte | O que faz | Onde fica no código |
| --- | --- | --- |
| Cadastros | Produtos (com densidade para converter litros em kg), parceiros com vários papéis (cliente, fornecedor, transportadora, industrializador, armazém), locais e tanques | `apps/cadastros` |
| Estoque | Livro de movimentações: o saldo de cada local é sempre a soma das movimentações. Entrada, saída, transferência entre tanques e ajuste de inventário com motivo | `apps/estoque` |
| Custo | Custo médio por local **ou** custo por lote, escolhido em *Admin → Configuração* | `apps/estoque/services.py` |
| Contratos | Contratos de compra e venda; o saldo é calculado pelas notas ligadas ao contrato (notas canceladas não contam) | `apps/contratos` |
| Notas fiscais | NF-e de entrada e saída e CT-e, com itens e impostos | `apps/fiscal` |
| Integração | Importa clientes, fornecedores, transportadoras, produtos e notas do backup do ERP antigo | `apps/integracao` |
| Perfis | Grupos Diretoria, Comercial, Logística e Financeiro | `apps/cadastros/management/commands/criar_perfis.py` |

Telas:

- `/estoque/` saldos por produto e local, últimas movimentações e botões de lançamento.
- `/admin/` cadastros, contratos, notas fiscais e consultas.

## Como rodar no seu computador

Pré-requisitos: Python 3.12 ou mais novo. Docker é opcional (só para o PostgreSQL).

```bash
# 1. Criar o ambiente e instalar as dependências
python -m venv .venv
source .venv/bin/activate          # no Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configuração
cp .env.example .env               # edite se precisar
# Para testar rápido sem PostgreSQL, apague a linha POSTGRES_DB do .env:
# o sistema passa a usar um arquivo SQLite.

# 3. Banco de dados (se for usar PostgreSQL pelo Docker)
docker compose up -d

# 4. Criar as tabelas, os perfis e o seu usuário
python manage.py migrate
python manage.py criar_perfis
python manage.py createsuperuser

# 5. Subir o sistema
python manage.py runserver
```

Abra http://localhost:8000 e entre com o usuário criado no passo 4.

## Importar dados do ERP antigo

O ERP antigo continua emitindo as notas. O sistema novo lê os dados de uma cópia do banco dele, restaurada a partir do arquivo `.backup`:

```bash
scripts/restaurar_backup.sh caminho/para/VivaClean_12h.backup 2026-01-01
```

O script recria o banco `legado`, restaura o backup e roda `python manage.py importar_legado --desde 2026-01-01`. Pode rodar quantas vezes quiser: o que já foi importado é atualizado, não duplicado. Para ver o resultado sem gravar nada, use `python manage.py importar_legado --simular`.

O que se sabe do banco antigo:

- É um PostgreSQL 9.6 compartilhado por várias empresas do grupo; a JT Ecobio é a **empresa 1** (`LEGADO_EMPRESA=1`).
- Notas de saída: `fi.arqnot` (uma linha por CFOP/alíquota, às vezes duplicada) e `fi.arqnoi` (itens). Cancelamento e chave vêm de `fi.arqnfe`.
- Notas de entrada e CT-e: `fi.aent01` e `fi.aent02`. A chave de acesso vem dos XMLs guardados em `fi.danfe`.
- Cadastros: `fa.crci01` (clientes), `es.esci03` (fornecedores), `fa.crci19` (transportadoras), `es.esci01` (produtos).
- Há datas impossíveis (anos 1880, 2028, 9202); essas notas são ignoradas e aparecem como aviso no final da importação.
- Notas de remessa e devolução para fornecedor guardam o código do fornecedor no campo de cliente; o importador procura entre os fornecedores quando não acha o cliente.

Na importação de teste com o backup de 12/11/2026, desde 01/01/2026: 1.165 notas de saída, 1.691 de entrada, 695 CT-e e 727 produtos. Das 140 notas de saída de outubro, 11 estão canceladas.

## Como o estoque funciona

Regra principal: **ninguém digita saldo**. Toda mudança é uma movimentação com data, tipo, quantidade (positiva entra, negativa sai), custo e usuário. Para corrigir uma diferença, lance um *ajuste de inventário* com o motivo.

Toda movimentação passa pelas funções de `apps/estoque/services.py`:

- `registrar_entrada` cria a entrada e um lote (mesmo usando custo médio, para permitir trocar de método depois).
- `registrar_saida` recusa saída maior que o saldo e calcula o custo (médio do local ou do lote).
- `transferir` tira de um local e põe em outro com o mesmo custo.
- `ajustar_inventario` lança a diferença entre o contado e o sistema.

Por isso a tela de movimentações no admin é só de consulta.

Produto de cliente (serviço de envase) fica num local do tipo *Produto de cliente*: aparece no saldo, mas não entra no valor do estoque da JT.

## Testes

```bash
pytest
```

Os testes usam SQLite e não precisam do banco antigo.

## Próximas fases

1. Vendas com várias origens: pedido com reserva por tanque e sugestão da combinação mais barata de produto + frete.
2. Industrialização, fracionamento e serviços (envase, transbordo, armazenagem).
3. Financeiro e DRE automática.
