# Skill `dag-planner` — Especificação v0.1

> **Para quem vai construir a skill:** este documento descreve o comportamento esperado, os arquivos que a skill entrega e os critérios de aceite. Decisões marcadas com `[EM ABERTO]` ainda precisam ser fechadas.

---

## 1. Objetivo

Garantir que o agente, diante de uma tarefa grande, siga quatro compromissos:

1. **Planeja antes de executar:** decompõe a tarefa num DAG (grafo direcionado acíclico) com dependências explícitas.
2. **Paraleliza sem conflito:** distribui as tarefas entre subagentes de forma que dois deles nunca escrevam nos mesmos arquivos ao mesmo tempo.
3. **Verifica o que entrega:** cada tarefa só é concluída quando atende ao critério de pronto definido no plano.
4. **Dá visibilidade:** um painel HTML desenha o grafo inteiro e se atualiza conforme o progresso.

O painel é a face visível da skill. O núcleo é o processo obrigatório da seção 4 e os scripts que impedem o agente de pular etapas.

## 2. Quando a skill deve ser usada

**Acionar quando:**

- a tarefa tem várias partes que podem ser executadas por subagentes;
- a execução deve durar muito tempo ou rodar sem supervisão;
- o usuário pede um plano, um DAG, orquestração ou execução em paralelo.

**Dispensar quando:**

- a tarefa cabe em poucos passos lineares, sem nada para paralelizar;
- o custo de planejar supera o ganho (regra prática: menos de 4 tarefas ou nenhuma tarefa paralelizável). Nesse caso o agente informa que vai executar direto e explica por quê.

Rascunho da `description` do SKILL.md:

> Planeja tarefas grandes como um DAG antes de executar, distribui o trabalho entre subagentes sem conflito de arquivos, verifica cada entrega contra um critério de pronto e mostra o progresso num painel HTML ao vivo. Use quando a tarefa tiver várias partes, puder rodar em paralelo ou for longa, ou quando o usuário pedir plano, DAG ou orquestração.

## 3. Estrutura de arquivos

**Da skill:**

```
dag-planner/
├── SKILL.md                     # processo obrigatório (seção 4) e regras
├── scripts/
│   └── dag.py                   # CLI determinística (seção 7), só biblioteca padrão
├── assets/
│   └── painel.html              # painel autocontido (seção 8)
└── references/
    ├── schema.md                # formato do dag.json (seção 5)
    ├── contrato-subagente.md    # modelo de delegação (seção 6.3)
    └── exemplo-encarte.json     # DAG de exemplo completo
```

**Gerados no projeto, por execução:**

```
.dag/<id-da-execucao>/
├── dag.json        # estado único do plano (fonte da verdade)
├── eventos.jsonl   # log de eventos, uma linha por mudança
├── dag.md          # diagrama Mermaid para Obsidian/VS Code/GitHub
└── painel.html     # cópia do painel apontando para o dag.json desta execução
```

## 4. Processo obrigatório

O agente segue as fases em ordem. Nenhuma tarefa de execução começa antes da fase 4.

### Fase 0 — Avaliar
Decidir se a tarefa justifica um DAG (seção 2). Se não justificar, registrar o motivo e sair da skill.

### Fase 1 — Decompor
Quebrar a tarefa em unidades com:

- `id` curto e único, `nome` e `descricao`;
- `entradas` (o que precisa para começar) e `saidas` (o que entrega);
- `escreve`: arquivos ou padrões de arquivo que a tarefa vai criar ou alterar;
- `le`: arquivos que a tarefa só consulta;
- `criterio_pronto`: condição verificável de conclusão (ex.: "testes de `simulador/` passam", "o PNG existe e tem 1080×1350");
- `modelo` sugerido (seção 6.4).

Regra de granularidade: cada tarefa deve caber numa única delegação a um subagente. Tarefas grandes demais são quebradas de novo.

### Fase 2 — Ligar as dependências
Preencher `depende_de` de cada tarefa. Uma tarefa depende de outra quando:

- precisa de uma saída dela; ou
- **escreve em algum arquivo que a outra também escreve** (conflito de escrita). Nesse caso, ou se cria uma dependência para serializar as duas, ou elas são fundidas numa só.

### Fase 3 — Validar o plano
Rodar `dag.py validate`. O plano só avança se passar em todas as checagens:

- ids únicos e todas as dependências apontam para tarefas existentes;
- nenhum ciclo;
- nenhum par de tarefas que pode rodar em paralelo com `escreve` sobreposto;
- toda tarefa tem `criterio_pronto` preenchido;
- existe pelo menos uma tarefa inicial (sem dependências).

O script também calcula e grava no `dag.json` os **níveis** (profundidade topológica) e o **caminho crítico**, usados pelo painel.

### Fase 4 — Apresentar e aprovar
- **Modo interativo:** o agente mostra um resumo (número de tarefas, quantas paralelas por nível, caminho crítico, estimativa de subagentes simultâneos) e o caminho do painel, e espera aprovação.
- **Modo autônomo:** segue direto, registrando no log que o plano foi aprovado automaticamente.

`[EM ABERTO]` O modo padrão deve ser interativo ou autônomo? Sugestão: interativo, com uma flag para autônomo.

### Fase 5 — Executar
Loop do orquestrador:

1. `dag.py ready` lista as tarefas com todas as dependências concluídas.
2. O orquestrador delega essas tarefas a subagentes até o limite de paralelismo (`max_paralelo`, padrão sugerido 4), marcando cada uma como `executando` **antes** de delegar.
3. Ao receber o retorno de um subagente, verifica o `criterio_pronto` (diretamente ou com um juiz, seção 6.5).
4. Marca como `concluida` ou `reprovada`. Uma reprovada volta para a fila com o motivo anexado, até o limite de `max_tentativas` (padrão sugerido 2).
5. Esgotadas as tentativas, a tarefa vira `falhou` e todas as descendentes viram `bloqueada`. Tarefas independentes continuam rodando.
6. Repete até não haver tarefas prontas nem executando.

### Fase 6 — Replanejar (quando necessário)
Se surgir trabalho não previsto, o orquestrador usa `dag.py add-task` ou `dag.py edit`, que incrementam `versao`, revalidam o plano inteiro e registram o motivo no log. O agente nunca edita o `dag.json` à mão.

### Fase 7 — Encerrar
Gerar um resumo final: tarefas concluídas, falhas e bloqueios, tempo total, tempo no caminho crítico, maior número de subagentes simultâneos e aprendizados relevantes para próximas execuções.

## 5. Formato do estado (`dag.json`)

```json
{
  "meta": {
    "id": "2026-10-08-encarte-loja-x",
    "titulo": "Encarte semanal — Loja X",
    "objetivo": "Gerar e validar o encarte da semana",
    "versao": 1,
    "modo": "interativo",
    "max_paralelo": 4,
    "max_tentativas": 2,
    "criado_em": "2026-10-08T14:00:00-03:00",
    "atualizado_em": "2026-10-08T14:12:31-03:00",
    "caminho_critico": ["lista", "validar", "montar", "juiz", "render"]
  },
  "tarefas": [
    {
      "id": "validar",
      "nome": "Validar se cada oferta paga",
      "descricao": "Rodar o simulador de promoção em cada item",
      "depende_de": ["lista"],
      "escreve": ["saida/validacao.json"],
      "le": ["entrada/ofertas.csv"],
      "criterio_pronto": "validacao.json tem um veredito para cada oferta",
      "modelo": "haiku",
      "nivel": 1,
      "status": "executando",
      "agente": "sub-02",
      "tentativas": 1,
      "inicio": "2026-10-08T14:05:10-03:00",
      "fim": null,
      "resumo_resultado": null,
      "motivo_reprovacao": null
    }
  ]
}
```

**Status e transições permitidas:**

```
pendente ──► pronta ──► executando ──► concluida
                            │
                            ├──► reprovada ──► pronta   (se ainda há tentativas)
                            └──► falhou                 (tentativas esgotadas)
pendente ──► bloqueada                                  (alguma dependência falhou)
```

O `dag.py` rejeita qualquer transição fora desse diagrama.

## 6. Regras de execução

### 6.1 Um único escritor
Só o orquestrador altera o `dag.json`, sempre via `dag.py`. Subagentes nunca leem nem escrevem o estado: recebem a tarefa e devolvem o resultado ao orquestrador.

### 6.2 Escrita atômica
O `dag.py` grava num arquivo temporário e renomeia por cima do original, para o painel nunca ler um JSON pela metade. Cada mudança também gera uma linha em `eventos.jsonl`.

### 6.3 Contrato do subagente
Toda delegação usa o modelo de `references/contrato-subagente.md`, com:

- objetivo da tarefa e contexto mínimo necessário;
- entradas disponíveis;
- **lista fechada de arquivos que pode alterar** (o `escreve` da tarefa);
- critério de pronto;
- formato obrigatório de retorno:

```
STATUS: concluida | precisa_ajuda
ARQUIVOS_ALTERADOS: [...]
RESUMO: o que foi feito, em até 5 linhas
PENDENCIAS: o que ficou de fora, se houver
```

Se o subagente perceber que precisa alterar um arquivo fora da sua lista, ele para e devolve `precisa_ajuda`. O orquestrador decide se replaneja (fase 6).

### 6.4 Escolha de modelo
- Planejamento (fases 1 a 3) e replanejamento: o modelo mais capaz disponível.
- Execução: o modelo mais barato que dê conta da tarefa, conforme o campo `modelo`.
- `[EM ABERTO]` Tabela padrão de tipo de tarefa → modelo.

### 6.5 Verificação e juiz
- Critérios objetivos (arquivo existe, teste passa, contagem bate) são checados por comando.
- Critérios subjetivos podem usar um **juiz**: um modelo rápido e barato que só responde "atende / não atende + motivo". O juiz nunca corrige o trabalho, apenas aprova ou devolve.
- `[EM ABERTO]` Qual modelo usar como juiz e se ele é opcional por tarefa.

## 7. CLI `scripts/dag.py`

Python, só biblioteca padrão, para rodar em qualquer máquina ou VPS sem instalação.

| Comando | Função |
|---|---|
| `init <id> --titulo ...` | Cria `.dag/<id>/` com `dag.json` vazio e copia o `painel.html` |
| `validate` | Executa as checagens da fase 3 e grava níveis e caminho crítico |
| `ready` | Lista as tarefas prontas para iniciar |
| `set-status <tarefa> <status> [--motivo] [--agente] [--resumo]` | Aplica uma transição válida |
| `add-task` / `edit` | Altera o plano, incrementa `versao` e revalida |
| `summary` | Gera o resumo da fase 7 |
| `mermaid` | Reescreve `dag.md` com o diagrama colorido por status |
| `serve [--porta 8765]` | Sobe um servidor local para o painel e mostra a URL |

Todo comando que altera o estado também regenera o `dag.md`, para quem acompanha pelo Obsidian.

## 8. Painel HTML

### 8.1 Requisitos técnicos
- Arquivo único, sem build, sem dependências externas obrigatórias.
- Lê o `dag.json` por polling a cada 2 segundos.
- Navegadores bloqueiam leitura de arquivo local via `file://`, por isso o painel é aberto pelo `dag.py serve`. `[EM ABERTO]` Alternativa sem servidor: o `dag.py` reescrever o próprio HTML com os dados embutidos a cada mudança e o painel se recarregar sozinho.
- Layout calculado pela própria página: colunas por nível topológico, da esquerda para a direita, com as setas de dependência desenhadas em SVG.
- Funciona em tema claro e escuro e numa tela de notebook sem rolagem horizontal para até ~30 tarefas.

### 8.2 Elementos da tela
1. **Cabeçalho:** título, objetivo, versão do plano, barra de progresso (% de tarefas concluídas), tempo decorrido e número de subagentes trabalhando agora.
2. **Grafo:** cada tarefa é um cartão com nome, status, agente e tempo na etapa. Cores por status, com ícone além da cor para não depender só dela:

   | Status | Cor sugerida |
   |---|---|
   | pendente | cinza |
   | pronta | azul |
   | executando | âmbar, com animação discreta |
   | concluida | verde |
   | reprovada | laranja |
   | falhou | vermelho |
   | bloqueada | cinza escuro, tracejado |

3. **Caminho crítico** destacado com borda ou seta mais grossa.
4. **Painel lateral** ao clicar num cartão: descrição, dependências, arquivos que escreve e lê, critério de pronto, tentativas, motivo de reprovação e resumo do resultado.
5. **Linha do tempo de eventos** (últimas mudanças, vinda do `eventos.jsonl`).
6. **Legenda** dos status.

### 8.3 Integração futura
`[EM ABERTO]` Um adaptador opcional que, além do arquivo, grava o estado numa tabela do Supabase, para o Mission Control mostrar as execuções de vários projetos ou clientes num só lugar. O formato da seção 5 já deve servir como esquema dessa tabela.

## 9. Critérios de aceite

1. `validate` rejeita um plano com ciclo e aponta as tarefas envolvidas.
2. `validate` rejeita duas tarefas paralelizáveis com `escreve` sobreposto e sugere a dependência ou a fusão.
3. `validate` rejeita tarefa sem `criterio_pronto`.
4. `set-status` rejeita transição inválida (ex.: `pendente → concluida`).
5. Quando uma tarefa falha, todas as descendentes ficam `bloqueada` e as independentes seguem.
6. O painel reflete qualquer mudança de status em até 3 segundos.
7. Replanejar incrementa a versão, revalida e aparece no log de eventos.
8. Uma execução de exemplo (`references/exemplo-encarte.json`) roda do início ao fim com ao menos 3 subagentes em paralelo no mesmo nível, sem conflito de escrita.
9. Para uma tarefa de 2 ou 3 passos lineares, o agente dispensa a skill e explica o motivo.

## 10. Fora do escopo da v1

- Execução distribuída em várias máquinas.
- Edição do plano pelo painel (o painel só exibe).
- Estimativa automática de custo em tokens por tarefa.

## 11. Questões em aberto (resumo)

- Modo padrão: interativo ou autônomo.
- Tabela padrão de modelo por tipo de tarefa e modelo do juiz.
- Painel com servidor local ou HTML autoatualizável.
- Adaptador Supabase/Mission Control na v1 ou depois.
- Onde ficam os aprendizados da fase 7: no `learnings.md` do projeto ou num arquivo próprio da skill.
