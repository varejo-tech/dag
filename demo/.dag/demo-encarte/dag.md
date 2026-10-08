# DAG — Encarte semanal — Loja Exemplo (simulação)

> Gerar, revisar e entregar o encarte da semana em três formatos: feed, stories e A4 para impressão

**Execução** `demo-encarte` · **versão** v1 · **estado** encerrado · atualizado em 2026-10-08T15:43:35-03:00

**Progresso:** 10/12 concluídas (83%) · 319k tokens

| Status | Tarefas |
|---|---|
| ✓ concluída | 10 |
| ✗ falhou | 1 |
| ⊘ bloqueada | 1 |

```mermaid
flowchart LR
  t_lista["✓ Receber lista de ofertas<br/>lista · concluída"]:::concluida
  t_validar["✓ Validar se cada oferta paga<br/>validar · concluída"]:::concluida
  t_imagens["✓ Buscar imagens dos produtos<br/>imagens · concluída"]:::concluida
  t_layout["✓ Definir tema e layout<br/>layout · concluída"]:::concluida
  t_textos["✓ Escrever chamadas das ofertas<br/>textos · concluída"]:::concluida
  t_tratar_fotos["✓ Remover fundo e padronizar fotos<br/>tratar-fotos · concluída"]:::concluida
  t_montar["✓ Montar o encarte<br/>montar · concluída"]:::concluida
  t_revisar["✓ Revisão final de preços e textos<br/>revisar · concluída"]:::concluida
  t_render_feed["✓ Renderizar post do feed<br/>render-feed · concluída"]:::concluida
  t_render_stories["✓ Renderizar story<br/>render-stories · concluída"]:::concluida
  t_render_a4["✗ Gerar PDF A4 para impressão<br/>render-a4 · falhou"]:::falhou
  t_entregar["⊘ Empacotar e entregar<br/>entregar · bloqueada"]:::bloqueada
  t_lista --> t_validar
  t_lista --> t_imagens
  t_lista --> t_layout
  t_lista --> t_textos
  t_imagens --> t_tratar_fotos
  t_validar --> t_montar
  t_tratar_fotos --> t_montar
  t_layout --> t_montar
  t_textos --> t_montar
  t_montar --> t_revisar
  t_revisar --> t_render_feed
  t_revisar --> t_render_stories
  t_revisar --> t_render_a4
  t_render_feed --> t_entregar
  t_render_stories --> t_entregar
  t_render_a4 --> t_entregar
  classDef pendente fill:#6B7F90,stroke:#3E4D5A,color:#0B1E2D
  classDef pronta fill:#3FA9F5,stroke:#1F6FA8,color:#0B1E2D
  classDef executando fill:#F5B83D,stroke:#A87A1F,color:#0B1E2D
  classDef concluida fill:#3DD68C,stroke:#1F7A4F,color:#0B1E2D
  classDef reprovada fill:#FF8A3D,stroke:#A8551F,color:#0B1E2D
  classDef falhou fill:#F2545B,stroke:#9E2A30,color:#FFFFFF
  classDef bloqueada fill:#4A5A68,stroke:#2E3A44,color:#D9E6EC,stroke-dasharray:5 5
  linkStyle 1,4,6,9,12,15 stroke:#AFCB07,stroke-width:4px
```

Caminho crítico: lista → imagens → tratar-fotos → montar → revisar → render-a4 → entregar
