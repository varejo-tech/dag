# Formato do estado de uma execução

Cada execução vive em `.dag/<id>/`, na raiz do projeto:

| Arquivo | Quem escreve | Para quê |
|---|---|---|
| `dag.json` | só o `dag.py` | estado único do plano, fonte da verdade |
| `eventos.jsonl` | só o `dag.py` | log de eventos, uma linha JSON por mudança |
| `dag.data.js` | só o `dag.py` | cópia do estado + últimos eventos, lida pelo painel |
| `dag.md` | só o `dag.py` | diagrama Mermaid colorido por status |
| `painel.html` | copiado no `init` | painel ao vivo, abre direto do disco |
| `resumo.md` | `dag.py summary` | resumo final da execução |

O arquivo `.dag/atual` guarda o id da execução corrente. Os comandos do `dag.py` usam esse id quando `--dir` não é informado.

## 1. Rascunho do plano (entrada do `dag.py load`)

O agente escreve o rascunho num arquivo temporário e o entrega ao `load`. O rascunho tem só as tarefas e, opcionalmente, campos de `meta`. Os campos de execução (status, níveis, tempos) são preenchidos pelo `dag.py`.

```json
{
  "meta": { "objetivo": "Gerar e validar o encarte da semana" },
  "tarefas": [
    {
      "id": "validar",
      "nome": "Validar se cada oferta paga",
      "descricao": "Rodar o simulador de promoção em cada item da lista",
      "entradas": ["entrada/ofertas.csv"],
      "saidas": ["saida/validacao.json"],
      "depende_de": ["lista"],
      "escreve": ["saida/validacao.json"],
      "le": ["entrada/ofertas.csv"],
      "criterio_pronto": "validacao.json tem um veredito para cada oferta",
      "verificacao": { "tipo": "comando", "comando": "python3 -c \"import json;d=json.load(open('saida/validacao.json'));assert len(d)>0\"" },
      "modelo": "haiku",
      "estimativa_min": 3
    }
  ]
}
```

| Campo | Obrigatório | Regra |
|---|---|---|
| `id` | sim | curto, único, só `a-z`, `0-9`, `-` e `_` |
| `nome` | sim | frase curta, aparece no cartão do painel |
| `descricao` | não | o que a tarefa faz, em uma ou duas frases |
| `entradas` / `saidas` | não | o que a tarefa consome e entrega |
| `depende_de` | não | ids de tarefas existentes; vazio = tarefa inicial |
| `escreve` | não | arquivos, pastas (terminadas em `/`) ou padrões glob (`*`, `**`, `?`) que a tarefa cria ou altera |
| `le` | não | arquivos que a tarefa só consulta |
| `criterio_pronto` | sim | condição verificável de conclusão |
| `verificacao` | não | `{"tipo": "comando", "comando": "..."}` ou `{"tipo": "juiz"}`; ausente = o orquestrador confere |
| `modelo` | não | `haiku`, `sonnet`, `opus` ou `fable`; padrão `sonnet` |
| `estimativa_min` | não | minutos estimados; quando todas as tarefas têm, o caminho crítico usa esse peso |

## 2. Estado completo (`dag.json`)

```json
{
  "meta": {
    "id": "2026-10-08-encarte-loja-x",
    "titulo": "Encarte semanal — Loja X",
    "objetivo": "Gerar e validar o encarte da semana",
    "versao": 1,
    "estado": "executando",
    "modo": "interativo",
    "max_paralelo": 4,
    "max_tentativas": 2,
    "criado_em": "2026-10-08T14:00:00-03:00",
    "atualizado_em": "2026-10-08T14:12:31-03:00",
    "aprovado_em": "2026-10-08T14:03:02-03:00",
    "aprovacao": "humana",
    "iniciado_em": "2026-10-08T14:03:10-03:00",
    "encerrado_em": null,
    "caminho_critico": ["lista", "validar", "montar", "juiz", "render"],
    "total_niveis": 5,
    "pico_paralelo": 3,
    "contador_agentes": 4
  },
  "tarefas": [
    {
      "id": "validar",
      "nome": "Validar se cada oferta paga",
      "descricao": "Rodar o simulador de promoção em cada item",
      "entradas": ["entrada/ofertas.csv"],
      "saidas": ["saida/validacao.json"],
      "depende_de": ["lista"],
      "escreve": ["saida/validacao.json"],
      "le": ["entrada/ofertas.csv"],
      "criterio_pronto": "validacao.json tem um veredito para cada oferta",
      "verificacao": null,
      "modelo": "haiku",
      "estimativa_min": null,
      "nivel": 1,
      "status": "executando",
      "agente": "sub-02",
      "tentativas": 1,
      "inicio": "2026-10-08T14:05:10-03:00",
      "fim": null,
      "resumo_resultado": null,
      "motivo_reprovacao": null,
      "arquivos_alterados": [],
      "tokens": 0,
      "tool_uses": 0,
      "duracao_ms": 0,
      "historico": [
        {
          "tentativa": 1,
          "agente": "sub-02",
          "modelo": "haiku",
          "inicio": "2026-10-08T14:05:10-03:00",
          "fim": null,
          "resultado": null,
          "motivo": null,
          "tokens": 0,
          "tool_uses": 0,
          "duracao_ms": 0,
          "resumo": null,
          "juiz": null
        }
      ]
    }
  ]
}
```

### Campos de `meta`

| Campo | Significado |
|---|---|
| `estado` | `planejando` (após `init`), `aguardando_aprovacao` (após `load`), `executando` (após `approve`), `encerrado` (após `summary`) |
| `aprovacao` | `humana` (modo interativo) ou `automatica` (modo autônomo); `null` antes do `approve` |
| `iniciado_em` | momento do primeiro `start` |
| `caminho_critico` | ids da cadeia mais longa, do começo ao fim |
| `total_niveis` | número de colunas do grafo |
| `pico_paralelo` | maior número de tarefas `executando` ao mesmo tempo |
| `contador_agentes` | último número usado para nomear subagentes (`sub-01`, `sub-02`, ...) |

### Campos de execução da tarefa

| Campo | Significado |
|---|---|
| `nivel` | profundidade topológica: 0 para tarefas iniciais, senão 1 + o maior nível entre as dependências |
| `status` | ver seção 3 |
| `agente` | nome do subagente da tentativa atual ou da última |
| `tentativas` | quantas vezes a tarefa foi para `executando` |
| `inicio` / `fim` | início da primeira tentativa / fim da tentativa final |
| `tokens`, `tool_uses`, `duracao_ms` | soma de todas as tentativas, incluindo o juiz |
| `historico` | uma entrada por tentativa; `resultado` é `concluida`, `reprovada` ou `devolvida`; numa tentativa esgotada fica `reprovada` e a tarefa vira `falhou` |
| `historico[].resumo` | o `RESUMO` devolvido pelo subagente naquela tentativa |
| `historico[].juiz` | `{"agente": "juiz-01", "veredito": "atende" \| "nao_atende", "tokens": 1200}` quando houve juiz |

## 3. Status e transições

```
pendente ──► pronta ──► executando ──► concluida
   ▲                        │
   │                        ├──► reprovada ──► pronta     (ainda há tentativas)
   │                        ├──► falhou                   (tentativas esgotadas)
   └────────────────────────┴──► pendente                 (subagente pediu ajuda; não gasta tentativa)
pendente ──► bloqueada ──► pendente                       (dependência falhou / foi reaberta)
falhou ──► pendente                                       (só via `dag.py edit`, que reabre a tarefa)
```

| Transição | Quem faz |
|---|---|
| `pendente → pronta` | automática, quando todas as dependências estão `concluida` |
| `pronta → pendente` | automática, quando um `edit` acrescenta uma dependência ainda não concluída |
| `pendente → bloqueada` | automática, quando alguma dependência está `falhou` ou `bloqueada` |
| `bloqueada → pendente` | automática, quando a dependência que bloqueava é reaberta |
| `pronta → executando` | `dag.py start` |
| `executando → concluida / reprovada / falhou / pendente` | `dag.py set-status` |
| `reprovada → pronta` | `dag.py ready`, se ainda há tentativas |
| `executando → reprovada` com tentativas esgotadas | o `dag.py` converte em `falhou` e bloqueia as descendentes |
| `falhou → pendente` | `dag.py edit` (replanejamento) |

Qualquer outra transição é recusada com erro.

## 4. Eventos (`eventos.jsonl`)

Uma linha JSON por mudança, em ordem cronológica:

```json
{"ts": "2026-10-08T14:05:10-03:00", "tipo": "status", "tarefa": "validar", "de": "pronta", "para": "executando", "agente": "sub-02", "versao": 1, "msg": "validar → executando (sub-02)"}
```

| `tipo` | Quando |
|---|---|
| `init` | execução criada |
| `plano_carregado` | `load` aceitou o plano |
| `aprovado` | `approve` registrou a aprovação |
| `status` | qualquer mudança de status, manual ou automática |
| `replanejado` | `add-task`, `edit` ou `remove-task`; traz `motivo` e a nova `versao` |
| `aviso` | algo que merece atenção, mas não bloqueia |
| `encerrado` | `summary` encerrou a execução |

Campos opcionais por evento: `tarefa`, `de`, `para`, `agente`, `motivo`, `tokens`, `versao`. O campo `msg` sempre traz uma frase legível em português.

## 5. Dados do painel (`dag.data.js`)

O navegador bloqueia `fetch` em páginas abertas via `file://`, mas não bloqueia um `<script src>` clássico. Por isso o `dag.py` grava, a cada mudança:

```js
window.DAG_RECEBER && window.DAG_RECEBER({"gerado_em": "...", "dag": { /* dag.json inteiro */ }, "eventos": [ /* últimos 300 eventos */ ]});
```

O painel define `window.DAG_RECEBER` e, a cada 2 segundos, injeta de novo `<script src="dag.data.js?t=<timestamp>">`. Funciona aberto direto do disco e também via `dag.py serve`.

## 6. Compatibilidade futura

O formato foi pensado para virar tabela no Supabase sem conversão: `meta` vira uma linha de `execucoes`, cada item de `tarefas` uma linha de `tarefas` (chave `execucao_id + id`) e cada linha de `eventos.jsonl` uma linha de `eventos`.
