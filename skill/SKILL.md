---
name: dag
description: >
  Planeja uma tarefa grande como um DAG (grafo de tarefas com dependências) antes de executar,
  distribui o trabalho entre subagentes em paralelo sem conflito de arquivos, verifica cada entrega
  contra um critério de pronto e mostra o progresso num painel HTML ao vivo da Comunidade VarejoTech.
  Acione SOMENTE por pedido explícito: o comando /dag ou uma frase que peça o DAG pelo nome, como
  "faça o dag desta tarefa", "monta um DAG pra isso", "planeja como DAG", "roda isso com o dag".
  NÃO acione por conta própria só porque a tarefa é grande, paralelizável ou longa, nem quando o
  usuário pedir apenas "um plano", "orquestração", "subagentes" ou "paralelizar" sem citar DAG.
argument-hint: "[descrição da tarefa] [--autonomo]"
---

# DAG: planejar antes de executar

Você vai agir como **orquestrador**. Primeiro decompõe a tarefa num grafo de dependências. Depois delega cada parte a um subagente, rodando em paralelo tudo o que não depende de nada pendente. Cada entrega é conferida antes de liberar as seguintes.

Os ganhos são três:
- **velocidade**, porque o trabalho independente roda ao mesmo tempo;
- **menos conflito**, porque duas tarefas nunca escrevem no mesmo arquivo juntas;
- **visibilidade**, porque o painel mostra o que está pronto, rodando e esperando.

Se não houver descrição da tarefa nos argumentos nem na conversa, pergunte qual é a tarefa antes de qualquer coisa.

## A ferramenta: `dag.py`

Todo o estado da execução fica em `.dag/<id>/`, na raiz do projeto. Ele só muda por meio da CLI:

```
~/.claude/skills/dag/scripts/dag.py <comando> ...
```

Escreva o comando sempre por extenso, em cada chamada. A shell não guarda variáveis entre chamadas, e no zsh um atalho como `$DAG` não funciona.

Não edite `dag.json`, `eventos.jsonl` nem `dag.data.js` à mão. O `dag.py` valida cada mudança, recusa transições impossíveis, grava de forma atômica e registra tudo no log que alimenta o painel. Uma edição manual pularia essas garantias e poderia deixar o painel lendo um arquivo pela metade. O formato completo está em `references/schema.md`.

| Comando | Para quê |
|---|---|
| `init <id> --titulo T --objetivo O [--modo autonomo] [--max-paralelo 4] [--max-tentativas 2]` | cria a execução e copia o painel |
| `validate --arquivo rascunho.json` | confere o rascunho sem carregar |
| `load rascunho.json` | valida e carrega o plano (v1) |
| `approve [--auto]` | registra a aprovação; libera a execução |
| `ready [--json]` | põe de volta na fila as reprovadas com tentativas; lista prontas e vagas |
| `start <tarefa> [--agente NOME]` | `pronta → executando`; imprime o nome do agente (`sub-NN`) |
| `set-status <tarefa> <status> [opções]` | fecha a tentativa: `concluida`, `reprovada`, `falhou` ou `pendente` |
| `add-task` / `edit` / `remove-task ... --motivo T` | replaneja; incrementa a versão e revalida |
| `status` | uma linha de progresso para o terminal |
| `summary` | grava `resumo.md` e encerra |
| `open` / `serve` | abre o painel no navegador / serve por http |

Opções do `set-status`: `--resumo`, `--motivo`, `--arquivos a,b`, `--tokens`, `--tool-uses`, `--duracao-ms`, `--juiz-agente`, `--juiz-veredito atende|nao_atende` e `--juiz-tokens`.

## Fase 0: avaliar se compensa

Um DAG custa planejamento e tokens. Ele só se paga quando há trabalho para paralelizar. Se a tarefa tiver menos de 4 partes, ou se todas forem em sequência, diga ao usuário em uma frase que o DAG não compensa e por quê. Em seguida execute direto. Se ele insistir no DAG (por exemplo, para aprender ou demonstrar), siga em frente.

## Fase 1: decompor

Crie a execução com `init`. Use como id a data seguida de um apelido, como `2026-10-08-encarte-loja-x`. Depois escreva o rascunho em `.dag/<id>/rascunho.json`, no formato da seção 1 de `references/schema.md`. Cada tarefa precisa de:

- **`id`, `nome`, `descricao`:** o nome aparece no cartão do painel, então escreva para uma pessoa ler.
- **`escreve`:** todo arquivo, pasta (terminada em `/`) ou padrão que a tarefa cria ou altera. É daqui que sai a garantia de não haver conflito. Uma lista incompleta é o erro mais caro do plano, porque dois subagentes acabam sobrescrevendo um ao outro sem aviso.
- **`le`:** o que a tarefa só consulta.
- **`criterio_pronto`:** uma condição que alguém consegue conferir, como "os testes de `simulador/` passam" ou "o PNG existe e tem 1080×1350". Evite "ficou bom".
- **`verificacao`:** use `{"tipo": "comando", "comando": "..."}` sempre que o critério puder ser conferido por comando. Use `{"tipo": "juiz"}` quando o critério for subjetivo.
- **`modelo`:** o mais barato que dá conta da tarefa (tabela abaixo).
- **`estimativa_min`:** opcional. Quando todas as tarefas têm, o caminho crítico passa a considerar a duração.

**Granularidade:** cada tarefa deve caber numa única delegação a um subagente, com começo, fim e entrega verificável. Tarefa grande demais rende pouco paralelismo e é difícil de conferir, então quebre de novo. Tarefa pequena demais só gera custo de coordenação, então funda. Cada subagente consome dezenas de milhares de tokens só para carregar o contexto inicial (cerca de 55 mil por subagente `haiku` nos testes). Uma tarefa de poucos segundos quase nunca compensa uma delegação própria.

| Tipo de tarefa | `modelo` |
|---|---|
| Mecânica: copiar, converter, formatar, buscar, rodar um comando e conferir | `haiku` |
| Código, texto, análise com julgamento moderado | `sonnet` |
| Raciocínio difícil: arquitetura, revisão crítica, depuração complexa, decisões ambíguas | `opus` |

O planejamento em si (fases 1 a 3 e o replanejamento) fica com você, porque exige o contexto da conversa. A skill não troca o modelo desta sessão.

## Fase 2: ligar as dependências

Preencha `depende_de`. Uma tarefa depende de outra quando:

1. precisa de uma saída dela; ou
2. escreve em algum arquivo que a outra também escreve. Nesse caso, serialize as duas com uma dependência ou funda-as numa só.

Não desenhe setas "voltando" para revisão. Uma entrega reprovada é a mesma tarefa rodando de novo, e o `dag.py` já cuida disso com tentativas.

## Fase 3: validar

Rode `validate --arquivo .dag/<id>/rascunho.json`. Corrija até passar. Depois rode `load` com o mesmo arquivo. O validador recusa:
- ids repetidos;
- dependências inexistentes;
- ciclos, mostrando o caminho;
- conflito de escrita entre tarefas que podem rodar juntas;
- tarefa sem critério de pronto;
- plano sem tarefa inicial.

Avisos não bloqueiam, mas leia todos. O de "plano pequeno" é a fase 0 avisando de novo.

## Fase 4: apresentar e aprovar

Abra o painel com `open` e mostre ao usuário um resumo curto, montado com a saída do `load`:

```
**Plano: <título>** (v1)
- 12 tarefas em 7 níveis; até 4 rodam ao mesmo tempo (nível 1)
- Caminho crítico: lista → imagens → tratar-fotos → montar → revisar → render-a4 → entregar
- Modelos: 8 haiku, 3 sonnet, 1 opus
- Painel: .dag/<id>/painel.html (já aberto no navegador)
Posso executar? Se quiser, ajusto o plano antes.
```

- **Modo interativo (padrão):** pare e espere a resposta. Ajustes pedidos entram como `edit`, `add-task` ou `remove-task` antes do `approve`.
- **Modo autônomo:** ativado quando os argumentos trazem `--autonomo` ou o usuário pediu para não ser consultado. Crie a execução com `init ... --modo autonomo`, mostre o mesmo resumo e siga com `approve --auto`.

O `start` recusa qualquer tarefa antes da aprovação. Essa trava é proposital.

## Fase 5: executar

O ciclo é orientado a eventos: assim que uma vaga abre, a próxima tarefa pronta entra. Não espere um nível inteiro terminar.

1. **Ver o que pode começar:** `ready --json` devolve `vagas` e `prontas`, com as tarefas do caminho crítico primeiro.
2. **Delegar até preencher as vagas.** Para cada tarefa escolhida:
   - rode `start <id>` **antes** de delegar, porque o painel e o limite de paralelismo dependem disso;
   - chame a ferramenta Agent com `subagent_type: "general-purpose"`, `model` igual ao `modelo` da tarefa, `run_in_background: true`, `description` curta com o agente e a tarefa (ex.: `sub-03 validar ofertas`) e o prompt montado com `references/contrato-subagente.md`, incluindo a raiz do projeto em caminho absoluto.

   Faça todas as chamadas Agent de uma rodada **na mesma mensagem**, para que rodem de fato em paralelo. Se uma delegação falhar ao ser lançada, rode `set-status <id> pendente --motivo "falha ao delegar"`.
3. **Esperar.** Avise o usuário em uma linha o que está rodando e encerre o turno. O Claude Code chama você de volta a cada subagente que termina. Não fique consultando em laço.
4. **A cada notificação:**
   - leia o bloco `STATUS / ARQUIVOS_ALTERADOS / RESUMO / PENDENCIAS` do resultado e o `<usage>` (`subagent_tokens`, `tool_uses`, `duration_ms`);
   - confira o critério de pronto: rode o comando da `verificacao` ou chame um juiz (seção 2 do contrato), também em segundo plano, com `model: "haiku"`;
   - feche a tentativa com as métricas:
     ```
     ~/.claude/skills/dag/scripts/dag.py set-status montar concluida --resumo "..." --arquivos saida/encarte.html --tokens 41250 --tool-uses 9 --duracao-ms 83412
     ```
     Se a entrega reprovar, use `set-status <id> reprovada --motivo "<o que falhou, específico>"`. O `dag.py` devolve a tarefa à fila enquanto houver tentativas. Esgotadas as tentativas, a tarefa vira `falhou`, e tudo o que depende dela fica `bloqueada`. As tarefas independentes continuam.
   - Se o subagente pediu ajuda, rode `set-status <id> pendente --motivo "<pedido>"` e vá para a fase 6 antes de delegar a tarefa de novo.
   - Volte ao passo 1.
5. **Na nova tentativa**, inclua no prompt o motivo da reprovação (o `ready --json` traz `motivo_reprovacao`), para o subagente corrigir o ponto exato.

**Regras da execução:**
- **Um único escritor.** Só você altera o estado, e sempre via `dag.py`. Subagentes não leem nem escrevem em `.dag/`.
- **Lista fechada de arquivos.** O subagente só altera o que está no `escreve` da tarefa. O `set-status --arquivos` recusa entregas com arquivos fora da lista. Investigue antes de usar `--forcar`.
- **Verifique antes de concluir.** "O subagente disse que terminou" não basta. Uma tarefa concluída sem conferência libera as dependentes sobre uma base possivelmente errada.
- **Tarefas triviais** (ler um arquivo de entrada, por exemplo) podem ser feitas por você mesmo. Passe pelo `start --agente orquestrador` e pelo `set-status` do mesmo jeito, para o painel continuar fiel.
- Para dar notícias ao usuário durante a execução, use `status`.

## Fase 6: replanejar quando o mundo mudar

Use os comandos de replanejamento quando surgir trabalho não previsto, quando um subagente pedir um arquivo fora da lista ou quando uma tarefa falhar por erro de plano:

- `add-task --json '{...}' --motivo "..."`
- `edit <id> --json '{campos}' --motivo "..."`. Um `edit` numa tarefa `falhou` a reabre e desbloqueia as dependentes.
- `remove-task <id> --motivo "..."`

Cada mudança incrementa a versão, revalida o plano inteiro (e só grava se passar) e aparece no log do painel com o motivo. No modo interativo, avise o usuário quando a mudança alterar o escopo.

## Fase 7: encerrar

Quando `ready` indicar `fim: true` e nada estiver executando:

1. Rode `summary`. Ele grava `.dag/<id>/resumo.md` com:
   - concluídas, falhas e bloqueios;
   - tempo total e tempo no caminho crítico;
   - pico de paralelismo;
   - tokens por modelo;
   - retentativas com motivos.
2. Preencha a seção **Aprendizados** do `resumo.md` com o que vale lembrar na próxima vez: estimativas que erraram, critérios vagos que geraram retrabalho, tarefas que deveriam ter sido quebradas ou fundidas. Se o projeto tiver um `learnings.md` na raiz, acrescente lá uma seção curta e datada com esses mesmos pontos.
3. Responda ao usuário com:
   - o que foi entregue e onde está;
   - o que falhou ou ficou bloqueado, e o que você sugere fazer a respeito;
   - os números principais: tempo, tokens e pico de agentes;
   - o caminho do painel e do resumo.

## Referências

- `references/schema.md`: formato do rascunho, do `dag.json`, dos eventos e do `dag.data.js`, com as transições de status.
- `references/contrato-subagente.md`: prompts do executor e do juiz e como ler o retorno. Leia antes da primeira delegação.
- `references/exemplo-encarte.json`: um rascunho completo, com 12 tarefas e conflitos de escrita resolvidos. Serve de modelo para o formato e o nível de detalhe.
- `scripts/simular.py`: roda o exemplo sem gastar tokens, com agentes fictícios, para demonstrar o painel (`--velocidade 2`, `--falha`).
