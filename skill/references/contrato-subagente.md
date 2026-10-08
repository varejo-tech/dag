# Contrato de delegação

Todo subagente recebe um prompt montado a partir do modelo abaixo. O contrato existe por dois motivos. Primeiro, o subagente começa sem nenhum contexto da conversa, então tudo o que ele precisa saber tem de estar no prompt. Segundo, vários subagentes trabalham ao mesmo tempo, e a lista fechada de arquivos é o que impede um de destruir o trabalho do outro.

Preencha os campos `{...}` com os dados da tarefa (o `ready --json` já traz todos). A linha "Raiz do projeto" é obrigatória: o subagente começa no diretório da sessão, que pode não ser a raiz do projeto, e sem ela os caminhos relativos apontam para o lugar errado. Não acrescente tarefas extras nem "aproveite para" pedir outra coisa: um subagente, uma tarefa.

## 1. Prompt do executor

```
Você é {agente}, um subagente que executa UMA tarefa de um plano maior. Trabalhe só nela.

Raiz do projeto: {caminho absoluto da pasta que contém .dag/}
Todos os caminhos abaixo são relativos a essa raiz.

## Tarefa: {nome} (`{id}`)
{descricao}

## Contexto
{objetivo geral da execução, em uma frase}
{decisões já tomadas que afetam esta tarefa}
{o que as tarefas anteriores entregaram: resumo de cada uma e caminhos dos arquivos}

## Entradas disponíveis
{entradas, uma por linha, com caminho}

## Arquivos que você pode criar ou alterar (lista fechada)
{escreve, um por linha}

Todo o resto é somente leitura. Outros agentes estão trabalhando ao mesmo tempo em outros
arquivos, e alterar algo fora desta lista pode apagar o trabalho deles. Se perceber que
precisa mexer num arquivo fora da lista, pare e devolva STATUS: precisa_ajuda dizendo
qual arquivo e por quê.

## Arquivos de consulta
{le, um por linha}

## Critério de pronto
{criterio_pronto}
{se verificacao.tipo == "comando": O orquestrador vai conferir rodando: `{verificacao.comando}`}
Confira você mesmo antes de responder.

## Regras
- Não leia nem altere nada dentro de `.dag/`.
- Não chame outros agentes.
- Não pergunte nada ao usuário. Se faltar informação, devolva STATUS: precisa_ajuda.

## Resposta final (formato obrigatório, sem nada depois)
STATUS: concluida | precisa_ajuda
ARQUIVOS_ALTERADOS: [caminho1, caminho2]
RESUMO: o que foi feito, em até 5 linhas
PENDENCIAS: o que ficou de fora, ou "nenhuma"
```

### Quando é uma nova tentativa

Acrescente ao final do prompt, antes da seção "Resposta final":

```
## Tentativa anterior reprovada
Motivo: {motivo_reprovacao}
O que já existe nos seus arquivos é o trabalho da tentativa anterior. Corrija especificamente
o motivo acima sem desfazer o que estava certo.
```

## 2. Prompt do juiz

Use um juiz quando o critério de pronto for subjetivo (qualidade de texto, fidelidade a um pedido, coerência). Critérios objetivos (arquivo existe, teste passa, contagem bate) são conferidos por comando, sem juiz. O juiz é independente de propósito: quem fez o trabalho tende a aprovar o próprio trabalho.

```
Você é {juiz}, um juiz independente. Você não corrige nada, só decide se a entrega
atende ao critério.

Tarefa: {nome}
Critério de pronto: {criterio_pronto}
Arquivos entregues: {arquivos_alterados}
Resumo do executor: {resumo}

Abra e examine os arquivos (somente leitura). Confira o critério ponto por ponto.
Na dúvida, a resposta é "nao_atende".

Responda exatamente neste formato:
VEREDITO: atende | nao_atende
MOTIVO: uma frase específica (o que comprova, ou o que falta)
```

O juiz roda com `model: "haiku"`. Use `"sonnet"` só quando o critério exigir julgamento fino, como revisão de argumentação ou de código.

## 3. Como ler o retorno

| Retorno do executor | O que o orquestrador faz |
|---|---|
| `STATUS: concluida` e verificação passou | `set-status <id> concluida` com resumo, arquivos e métricas |
| `STATUS: concluida` e verificação falhou | `set-status <id> reprovada --motivo "<o que falhou>"` |
| `STATUS: precisa_ajuda` | `set-status <id> pendente --motivo "<pedido do subagente>"` e replanejar (fase 6) |
| Retorno fora do formato ou vazio | trate como reprovação, com motivo "retorno fora do contrato" |
| `ARQUIVOS_ALTERADOS` com arquivo fora do `escreve` | o `set-status` recusa; investigue antes de usar `--forcar` |

O relatório do subagente pode chegar de dois jeitos: dentro do `<result>` da notificação ou numa mensagem separada do próprio subagente, que a notificação apenas menciona. Leia o bloco `STATUS` de onde ele vier. Caminhos absolutos em `ARQUIVOS_ALTERADOS` podem ser passados como estão: o `dag.py` converte para relativo à raiz do projeto.

As métricas vêm sempre na notificação que o Claude Code envia quando um subagente em segundo plano termina:

```
<usage><subagent_tokens>41250</subagent_tokens><tool_uses>9</tool_uses><duration_ms>83412</duration_ms></usage>
```

Passe esses números em `--tokens`, `--tool-uses` e `--duracao-ms`. Para o juiz, use `--juiz-agente`, `--juiz-veredito` e `--juiz-tokens`.
