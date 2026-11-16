# ERP JT Ecobio

Sistema de gestão da JT Ecobio: estoque de glicerina por tanque e distribuidora, contratos de compra e venda com saldo, pedidos que puxam de vários tanques, e as notas fiscais do ERP antigo.

Feito em **Python + Django** com banco **PostgreSQL**.

## O que já existe (fases 1 e 2)

| Parte | O que faz | Onde fica no código |
| --- | --- | --- |
| Cadastros | Produtos (com densidade para converter litros em kg), parceiros com vários papéis (cliente, fornecedor, transportadora, industrializador, armazém), locais e tanques | `apps/cadastros` |
| Estoque | Livro de movimentações: o saldo de cada local é sempre a soma das movimentações. Entrada, saída, transferência entre tanques e ajuste de inventário com motivo | `apps/estoque` |
| Custo | Custo médio por local **ou** custo por lote, escolhido em *Admin → Configuração* | `apps/estoque/services.py` |
| Contratos | Contratos de compra e venda; o saldo é calculado pelas notas ligadas ao contrato (notas canceladas não contam) | `apps/contratos` |
| Notas fiscais | NF-e de entrada e saída e CT-e, com itens e impostos | `apps/fiscal` |
| Integração | Importa do backup do ERP antigo clientes, fornecedores, transportadoras, produtos e todas as notas (entrada, saída e CT-e). Carrega saldos por tanque e contratos da planilha DRE do mês | `apps/integracao` |
| Telas | Painel, estoque, vendas, contratos, notas e cadastros, no estilo do Gestão Click | `apps/web`, `templates/`, `static/css/app.css` |
| Perfis | Grupos Diretoria, Comercial, Logística e Financeiro | `apps/cadastros/management/commands/criar_perfis.py` |
| Ordens de serviço | Envase para clientes: tipos de recipiente (capacidade, preço por kg, tempo padrão), OS com cálculo do fracionamento, apontamento de início, pausas e fim por funcionário, TMO real × tempo padrão, conclusão e faturamento | `apps/servicos` |
| Vendas (fase 2) | Pedido de venda atendido por vários tanques, tabela de frete, sugestão da combinação mais barata (produto + frete), reserva, faturamento com baixa no estoque e margem | `apps/vendas` |

Telas (menu lateral):

- **Painel** (`/`): saldo de cada tanque com o que está reservado, contratos a entregar, vendas dos últimos 12 meses, maiores clientes e últimos pedidos.
- **Cadastros**: clientes, fornecedores, transportadoras, produtos, locais e tanques (listar, ver, adicionar e editar).
- **Estoque**: saldos por tanque, movimentações com filtros, entrada, saída, transferência e ajuste.
- **Vendas**: pedidos (novo, escolha dos tanques, confirmar, faturar) e tabela de frete.
- **Serviços**: ordens de serviço de envase (lista, nova OS, detalhe com mão de obra e TMO), tela de apontamento para o celular (`/servicos/operador/`) e tipos de recipiente.
- **Contratos**: compra e venda com saldo e percentual entregue.
- **Fiscal**: notas de saída, de entrada e CT-e importados do ERP antigo.
- **Administração** (`/admin/`): continua disponível para o que ainda não tem tela própria.

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

Para o sistema ficar com os dados da JT, siga os três passos da próxima seção.

## Carregar os dados da JT

São três comandos, nesta ordem. Todos podem ser rodados de novo sem duplicar nada.

**1. Tudo o que existe no ERP antigo** (decisão de 15/11/2026: importar tudo o que der, não só clientes).

Restaure o `.backup` num banco `legado` (o script abaixo faz isso pelo Docker; no Windows sem Docker, use o `pg_restore` do PostgreSQL instalado) e rode:

```bash
python manage.py importar_legado              # todo o histórico (desde 2021)
python manage.py importar_legado --desde 2026-01-01   # só a partir de uma data
python manage.py importar_legado --simular    # mostra o resultado sem gravar
```

Traz clientes, fornecedores, transportadoras, produtos (só os que aparecem em notas) e todas as notas de saída, entrada e CT-e, com itens e impostos. Com o backup de 12/11/2026: 3.902 notas de saída, 5.301 de entrada, 2.341 CT-e e 1.403 produtos, em cerca de 1 minuto.

**2. Saldos por tanque e contratos, da planilha DRE do mês.** O ERP antigo não sabe em que tanque está cada produto nem controla contratos; isso vem da planilha que a JT já fecha:

```bash
python manage.py carregar_planilha "caminho/DRE 10.2026.xlsx"
```

- Aba **Estoque**, bloco FINAL: vira o saldo inicial de cada local no último dia do mês, com o custo da planilha. Os locais (Vale Logística, Bulokebios, Aroma, Biobrotas, JT, C Junior, Em trânsito) são criados e ligados ao parceiro dono.
- Aba **Saldo Ctt Glicerina**: vira os contratos de compra e de venda. O que já tinha sido entregue fica em "entregue antes da implantação", para o saldo bater com a planilha.
- A capacidade dos tanques não está na planilha: o sistema estima (saldo + 50%) e marca como "estimada". Corrija em *Cadastros → Locais e tanques*.
- No final aparecem avisos: nomes da planilha que não foram achados no cadastro (por exemplo "Multgel") ou que foram ligados por aproximação ("Steinpher" → STEINFER). Confira esses.

**3. (Opcional) Pedidos e fretes de exemplo**, para ver as telas funcionando:

```bash
python manage.py carregar_exemplos           # cria 40 fretes e 5 pedidos inventados
python manage.py carregar_exemplos --apagar  # remove só o que é exemplo
```

Enquanto houver exemplos, todas as telas mostram uma faixa amarela avisando. Os fretes são inventados; os pedidos usam contratos reais (Maian, Upper Dog, Aroma, Polynt, GMP), e um deles puxa de dois tanques.

### O que se sabe do banco antigo

- É um PostgreSQL 9.6 compartilhado por várias empresas do grupo; a JT Ecobio é a **empresa 1** (`LEGADO_EMPRESA=1`).
- Notas de saída: `fi.arqnot` (uma linha por CFOP/alíquota, às vezes duplicada) e `fi.arqnoi` (itens). Cancelamento e chave vêm de `fi.arqnfe`.
- Situação da nota de saída (`lsflag`): `N` venda, `S` remessa, `D` devolução/retorno, `F` entrada emitida pela JT, `*` cancelada ou inutilizada.
- **Cuidado:** nas notas `D`, o campo de cliente guarda o código do **fornecedor**. Exemplo: a NF 4023 tem o código 523, que é o cliente Neuripe, mas a nota é para a Peter Cremer (fornecedor 523). O importador procura primeiro entre os fornecedores nesses casos.
- Notas de entrada e CT-e: `fi.aent01` e `fi.aent02`. A chave de acesso vem dos XMLs guardados em `fi.danfe`.
- Cadastros: `fa.crci01` (clientes), `es.esci03` (fornecedores), `fa.crci19` (transportadoras), `es.esci01` (produtos). O CNPJ fica em duas colunas (número sem os dígitos + dígitos).
- Há datas impossíveis (anos 1880, 2028, 9202); essas notas são ignoradas e aparecem como aviso no final da importação, assim como 14 notas de entrada sem fornecedor.

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

1. Ligar as notas novas aos contratos automaticamente (pelo número do pedido do cliente que vem na nota) e dar baixa no tanque certo.
2. Industrialização, fracionamento e serviços (envase, transbordo, armazenagem).
3. Financeiro e DRE automática.

## Ordens de serviço de envase

O cliente manda o caminhão com o produto e os recipientes dele; a JT enche e cobra por kg envasado.

1. **Tipos de recipiente** (Serviços → Tipos de recipiente): capacidade em litros ou kg, preço do serviço por kg e **tempo padrão** para encher um recipiente. Vêm cadastrados flexitank, IBC, octabin, tambor e bombona com os preços da planilha; os tempos padrão iniciais são estimativas, ajuste-os.
2. **Nova OS**: cliente, produto, quantidade (kg ou litros) e quantos recipientes de cada tipo o cliente trouxe. O sistema calcula na hora quantos de cada tipo enche (maiores primeiro), o valor e o tempo previsto.
3. **Apontamento** (`/servicos/operador/`, no celular ou tablet): a tela mostra **o que envasar**, um cartão por recipiente com a meta, quantos já foram cheios e quantos voltam vazios ao cliente. O funcionário toca em **Começar aqui** no recipiente, em **+1 cheio** a cada recipiente cheio (−1 desfaz), **Passar para este** para trocar de recipiente, **Pausar** (com o motivo), Retomar e Finalizar. A hora é sempre a do sistema e o tempo fica separado por recipiente.
4. **Conclusão**: o operador só conclui quando tudo que chegou foi apontado como cheio. Abaixo disso, só o supervisor conclui, com justificativa (fica registrada na OS).
5. **TMO**: minutos de mão de obra ÷ recipientes, e o TMO real de cada recipiente pelo tempo apontado nele. OS com tempo fora do normal (menos de 1 minuto, eficiência acima de 200% ou abaixo de 25%) aparecem com alerta e não entram na média do TMO por tipo.
6. **Faturar**: liga a OS à nota de prestação de serviço importada do sistema emissor (ou marca como faturada sem nota).

Funcionários do envase: crie o usuário em Sistema → Usuários e marque só o perfil **Operação** (rode `python manage.py criar_perfis` uma vez para ele existir). Ao entrar, eles vão direto para a tela de apontamento. Se alguém esquecer de apontar, o supervisor lança a correção no detalhe da OS (fica marcada como correção).
