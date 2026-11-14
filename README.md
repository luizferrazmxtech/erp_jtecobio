# ERP JT Ecobio

Sistema de gestão da JT Ecobio: estoque de glicerina por tanque e distribuidora, contratos de compra e venda com saldo e notas fiscais.

Feito em **Python + Django** com banco **PostgreSQL**.

## O que já existe (fases 1 e 2)

| Parte | O que faz | Onde fica no código |
| --- | --- | --- |
| Cadastros | Produtos (com densidade para converter litros em kg), parceiros com vários papéis (cliente, fornecedor, transportadora, industrializador, armazém), locais e tanques | `apps/cadastros` |
| Estoque | Livro de movimentações: o saldo de cada local é sempre a soma das movimentações. Entrada, saída, transferência entre tanques e ajuste de inventário com motivo | `apps/estoque` |
| Custo | Custo médio por local **ou** custo por lote, escolhido em *Admin → Configuração* | `apps/estoque/services.py` |
| Contratos | Contratos de compra e venda; o saldo é calculado pelas notas ligadas ao contrato (notas canceladas não contam) | `apps/contratos` |
| Notas fiscais | NF-e de entrada e saída e CT-e, com itens e impostos | `apps/fiscal` |
| Integração | Importa os clientes do backup do ERP antigo; o resto começa do zero | `apps/integracao` |
| Perfis | Grupos Diretoria, Comercial, Logística e Financeiro | `apps/cadastros/management/commands/criar_perfis.py` |
| Vendas (fase 2) | Pedido de venda atendido por vários tanques, tabela de frete, sugestão da combinação mais barata (produto + frete), reserva, faturamento com baixa no estoque e margem | `apps/vendas` |

Telas:

- `/estoque/` saldos por produto e local, últimas movimentações e botões de lançamento.
- `/vendas/` pedidos de venda: novo pedido, escolha das origens, confirmar, faturar.
- `/admin/` cadastros, contratos, notas fiscais, tabela de frete e consultas.

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

## ERP antigo: clientes e consulta

Decisão de 14/11/2026: **só os clientes vêm do ERP antigo**. Produtos, fornecedores, contratos, notas e estoque começam do zero no sistema novo. O banco antigo fica disponível apenas para consulta.

O ERP antigo continua emitindo as notas fiscais. Para atualizar a cópia de consulta e os clientes a partir do arquivo `.backup`:

```bash
scripts/restaurar_backup.sh caminho/para/VivaClean_12h.backup
```

O script recria o banco `legado` (cópia só para consulta), restaura o backup e roda `python manage.py importar_legado`. Pode rodar quantas vezes quiser: clientes já importados são atualizados, não duplicados, e um cliente que já existe com o mesmo CNPJ é reaproveitado. Para ver o resultado sem gravar nada, use `python manage.py importar_legado --simular`.

Para consultar o banco antigo, conecte no banco `legado` com qualquer cliente PostgreSQL (DBeaver, pgAdmin ou `psql`). O que se sabe dele:

- É um PostgreSQL 9.6 compartilhado por várias empresas do grupo; a JT Ecobio é a **empresa 1** (`LEGADO_EMPRESA=1`).
- Clientes: `fa.crci01`. O CNPJ fica em duas colunas: `cr01cgc` (sem os dígitos) e `cr01dacc` (os dois dígitos).
- Notas de saída: `fi.arqnot` e `fi.arqnoi` (itens); cancelamento e chave em `fi.arqnfe`.
- Notas de entrada e CT-e: `fi.aent01` e `fi.aent02`; XMLs recebidos em `fi.danfe`.
- Fornecedores: `es.esci03`; transportadoras: `fa.crci19`; produtos: `es.esci01`.

Na importação de teste com o backup de 12/11/2026, os 551 clientes do ERP antigo viraram 539 no sistema novo: 12 códigos antigos repetiam o CNPJ de outro cliente e foram unificados.

## Como o estoque funciona

Regra principal: **ninguém digita saldo**. Toda mudança é uma movimentação com data, tipo, quantidade (positiva entra, negativa sai), custo e usuário. Para corrigir uma diferença, lance um *ajuste de inventário* com o motivo.

Toda movimentação passa pelas funções de `apps/estoque/services.py`:

- `registrar_entrada` cria a entrada e um lote (mesmo usando custo médio, para permitir trocar de método depois).
- `registrar_saida` recusa saída maior que o saldo e calcula o custo (médio do local ou do lote).
- `transferir` tira de um local e põe em outro com o mesmo custo.
- `ajustar_inventario` lança a diferença entre o contado e o sistema.

Por isso a tela de movimentações no admin é só de consulta.

Produto de cliente (serviço de envase) fica num local do tipo *Produto de cliente*: aparece no saldo, mas não entra no valor do estoque da JT.

## Como a venda funciona

Cada pedido é de um único produto e pode sair de vários tanques (as *origens*).

1. **Tabela de frete** (*Admin → Tabela de frete*): preço em R$/kg de cada local até uma UF ou cidade. A cidade tem prioridade sobre a UF.
2. **Rascunho**: ao criar o pedido, o sistema já sugere as origens. Ele ordena os tanques pelo custo total por kg (custo do produto no tanque + frete até o destino) e vai pegando do mais barato até completar a quantidade. Dá para informar um *mínimo por origem* para não mandar caminhão buscar pouco. As origens podem ser ajustadas à mão.
3. **Confirmado**: as quantidades ficam reservadas. O *saldo livre* de um tanque é o saldo menos o que está reservado em pedidos confirmados. Para mudar as origens, volte o pedido para rascunho.
4. **Faturado**: uma saída de estoque por origem, ligada à NF de saída (emitida no ERP antigo). O custo real substitui o previsto e a margem fica registrada.
5. **Cancelado**: libera a reserva. Pedido faturado não pode ser cancelado.

A tela do pedido mostra receita, custo do produto, frete e margem (total e por kg). Locais de produto de cliente nunca entram numa venda. As regras ficam em `apps/vendas/services.py`.

## Testes

```bash
pytest
```

Os testes usam SQLite e não precisam do banco antigo.

## Próximas fases

1. Industrialização, fracionamento e serviços (envase, transbordo, armazenagem).
2. Financeiro e DRE automática.
