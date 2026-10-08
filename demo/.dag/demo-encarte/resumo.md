# Resumo — Encarte semanal — Loja Exemplo (simulação)

> Gerar, revisar e entregar o encarte da semana em três formatos: feed, stories e A4 para impressão

Execução `demo-encarte` · plano v1 · modo autonomo (aprovado automaticamente) · encerrada em 2026-10-08T15:43:35-03:00

## Resultado

| Concluídas | Falharam | Bloqueadas | Não executadas | Total |
|---|---|---|---|---|
| 10 | 1 | 1 | 0 | 12 |

## Tempo e paralelismo

- Tempo total: 00:00:27
- Tempo somado no caminho crítico (lista → imagens → tratar-fotos → montar → revisar → render-a4 → entregar): 00:00:26
- Pico de paralelismo: 4 subagentes ao mesmo tempo (limite 4)

## Consumo

- Tokens dos subagentes e juízes: 318.706
- Tool uses: 91

| Modelo | Tentativas | Tokens |
|---|---|---|
| sonnet | 4 | 151.318 |
| haiku | 8 | 97.888 |
| opus | 1 | 57.455 |
| juiz | 4 | 12.045 |

## Tarefas

| Tarefa | Status | Tentativas | Agente | Duração | Tokens |
|---|---|---|---|---|---|
| lista | ✓ concluída | 1 | sub-01 | 00:00:03 | 13k |
| validar | ✓ concluída | 1 | sub-03 | 00:00:02 | 9,9k |
| imagens | ✓ concluída | 1 | sub-02 | 00:00:04 | 16k |
| layout | ✓ concluída | 1 | sub-04 | 00:00:03 | 47k |
| textos | ✓ concluída | 1 | sub-05 | 00:00:03 | 26k |
| tratar-fotos | ✓ concluída | 1 | sub-06 | 00:00:03 | 9,9k |
| montar | ✓ concluída | 2 | sub-08 | 00:00:08 | 87k |
| revisar | ✓ concluída | 1 | sub-09 | 00:00:03 | 61k |
| render-feed | ✓ concluída | 1 | sub-11 | 00:00:02 | 14k |
| render-stories | ✓ concluída | 1 | sub-12 | 00:00:03 | 11k |
| render-a4 | ✗ falhou | 2 | sub-13 | 00:00:05 | 24k |
| entregar | ⊘ bloqueada | 0 | — | — | 0 |

## Retentativas e falhas

- **montar**, tentativa 1 (sub-07): reprovada: preço do item 3 diverge da lista
- **render-a4**, tentativa 1 (sub-10): reprovada: PDF saiu em formato Carta, não A4
- **render-a4**, tentativa 2 (sub-13): reprovada: sangria ausente na segunda tentativa

## Aprendizados

<!-- O orquestrador preenche: o que funcionou, o que travou, como planejar melhor da próxima vez. -->
- _a preencher pelo orquestrador_
