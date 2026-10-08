# DAG: planejar antes de executar

Skill do Claude Code da **Comunidade VarejoTech**. Diante de uma tarefa grande, o agente faz cinco coisas:

- decompõe a tarefa num DAG (grafo de tarefas com dependências);
- distribui o trabalho entre subagentes em paralelo;
- garante que dois subagentes nunca escrevam no mesmo arquivo ao mesmo tempo;
- confere cada entrega contra um critério de pronto;
- mostra tudo num painel ao vivo.

O conceito está explicado em [`o-que-e-dag.md`](o-que-e-dag.md). A especificação que originou a skill está em [`skill-dag-spec.md`](skill-dag-spec.md).

## Como usar

A skill só é acionada por pedido explícito:

```
/dag gerar os criativos da campanha de dezembro: 3 variações de copy, imagens, feed e stories
```

Também vale uma frase que cite o DAG, como "faça o dag desta tarefa". O agente decompõe a tarefa e valida o plano. Depois mostra o resumo e abre o painel, e só executa depois da sua aprovação. Para rodar sem aprovação, acrescente `--autonomo`.

Cada execução fica em `.dag/<id>/`, na raiz do projeto onde o Claude Code está aberto, com a própria cópia do painel (`painel.html`). A pasta começa com ponto, então o Finder a esconde; para vê-la, use `Cmd + Shift + .`.

## Instalação

A skill vale para todos os projetos quando instalada em `~/.claude/skills/dag`. Para manter uma cópia só, crie um link para a pasta `skill/` deste repositório:

```bash
ln -s "$(pwd)/skill" ~/.claude/skills/dag
```

Ela só precisa de Python 3.8 ou mais recente, sem nenhuma dependência externa.

## Estrutura

```
skill/
├── SKILL.md                       processo em 8 fases (0 a 7) e regras para o agente
├── scripts/dag.py                 CLI que guarda o estado, valida o plano e impede pular etapas
├── scripts/simular.py             demonstração do painel sem gastar tokens
├── assets/painel.html             painel ao vivo, com a identidade VarejoTech
└── references/
    ├── schema.md                  formato do rascunho, do dag.json e dos eventos
    ├── contrato-subagente.md      prompts do executor e do juiz
    └── exemplo-encarte.json       plano de exemplo com 12 tarefas
testes/test_dag.py                 testes da CLI (critérios de aceite da spec)
demo/.dag/demo-encarte/            execução simulada pronta para abrir no painel
```

## Painel

Abra `demo/.dag/demo-encarte/painel.html` no navegador para ver uma execução de exemplo, com uma falha e o bloqueio que ela causa. O painel abre direto do disco, sem servidor, e se atualiza a cada 2 segundos. Ele mostra:

- o grafo por níveis, com o caminho crítico destacado;
- os agentes e o modelo de cada um;
- os tokens consumidos;
- a linha do tempo, com o paralelismo ao longo da execução;
- os eventos;
- os detalhes de cada tarefa, num painel que abre ao clicar no cartão.

Para assistir a uma execução simulada ao vivo, rode numa pasta qualquer:

```bash
~/.claude/skills/dag/scripts/simular.py --velocidade 1 --falha
~/.claude/skills/dag/scripts/dag.py open
```

## Testes

```bash
python3 -m unittest testes/test_dag.py
```
