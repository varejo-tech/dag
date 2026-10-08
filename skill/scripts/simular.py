#!/usr/bin/env python3
"""
simular.py — simula a execução do exemplo do encarte sem gastar tokens.

Serve para demonstrar o painel ao vivo e testar o orquestrador. Faz o papel
do orquestrador chamando o próprio dag.py (nunca edita o estado à mão):
ready → start até as vagas → espera → set-status. Os "subagentes" são falsos:
nenhum arquivo de trabalho é gravado, só o estado em .dag/<id>/.

Uso:
  python3 simular.py                 # ~4–12 s por tarefa
  python3 simular.py --velocidade 3  # três vezes mais rápido
  python3 simular.py --rapido        # sem pausas (testes)
  python3 simular.py --falha         # render-a4 falha de vez e bloqueia a entrega
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

PASTA = Path(__file__).resolve().parent
DAG = [sys.executable, str(PASTA / "dag.py")]
EXEMPLO = PASTA.parent / "references" / "exemplo-encarte.json"

# Faixas plausíveis de consumo por modelo (tokens, tool uses).
CONSUMO = {
    "haiku": ((8_000, 25_000), (3, 10)),
    "sonnet": ((20_000, 60_000), (6, 18)),
    "opus": ((40_000, 120_000), (8, 24)),
    "fable": ((40_000, 120_000), (8, 24)),
}
TOKENS_JUIZ = (1_500, 4_000)


def dag(pasta: Path, *args: str) -> str:
    r = subprocess.run(DAG + ["--dir", str(pasta), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"dag.py {' '.join(args)} falhou ({r.returncode}):\n{r.stderr.strip()}")
    return r.stdout


def arquivo_exemplo(padrao: str) -> str:
    """Um caminho literal que casa com o padrão do `escreve`."""
    p = padrao[:-1] + "/exemplo" if padrao.endswith("/") else padrao
    return p.replace("**/", "").replace("**", "exemplo").replace("*", "exemplo").replace("?", "x")


def main() -> int:
    ap = argparse.ArgumentParser(description="Simula a execução do exemplo do encarte (sem gastar tokens).")
    ap.add_argument("--id", default="demo-encarte")
    ap.add_argument("--raiz", default=".dag")
    ap.add_argument("--velocidade", type=float, default=1.0, help="multiplicador de velocidade (2 = duas vezes mais rápido)")
    ap.add_argument("--rapido", action="store_true", help="sem pausas, para testes")
    ap.add_argument("--falha", action="store_true", help="render-a4 falha de vez e bloqueia a entrega")
    ap.add_argument("--max-paralelo", type=int, default=4)
    ap.add_argument("--semente", type=int, default=7)
    ap.add_argument("--substituir", action="store_true", help="apaga uma simulação anterior com o mesmo id")
    args = ap.parse_args()
    if args.velocidade <= 0:
        ap.error("--velocidade precisa ser maior que zero")

    rng = random.Random(args.semente)
    pasta = Path(args.raiz) / args.id
    if (pasta / "dag.json").exists():
        if not args.substituir:
            raise SystemExit(f"Já existe {pasta}. Use --substituir ou outro --id.")
        shutil.rmtree(pasta)

    r = subprocess.run(
        DAG + ["init", args.id, "--titulo", "Encarte semanal — Loja Exemplo (simulação)",
               "--modo", "autonomo", "--max-paralelo", str(args.max_paralelo), "--raiz", args.raiz],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise SystemExit(r.stderr.strip())
    dag(pasta, "load", str(EXEMPLO))
    dag(pasta, "approve", "--auto")
    print(f"Simulação em {pasta} — abra {pasta / 'painel.html'} para acompanhar.", flush=True)

    plano = {t["id"]: t for t in json.loads((pasta / "dag.json").read_text(encoding="utf-8"))["tarefas"]}
    rodando: dict = {}
    tentativas: dict = {}
    juizes = 0

    while True:
        info = json.loads(dag(pasta, "ready", "--json"))
        for p in info["prontas"][: info["vagas"]]:
            agente = dag(pasta, "start", p["id"]).strip()
            segundos = 0.0 if args.rapido else rng.uniform(4, 12) / args.velocidade
            rodando[p["id"]] = {"agente": agente, "inicio": time.time(), "fim": time.time() + segundos}
            tentativas[p["id"]] = tentativas.get(p["id"], 0) + 1
        if not rodando:
            break

        tid = min(rodando, key=lambda k: rodando[k]["fim"])
        espera = rodando[tid]["fim"] - time.time()
        if espera > 0:
            time.sleep(espera)
        corrida = rodando.pop(tid)
        tarefa = plano[tid]
        (tmin, tmax), (umin, umax) = CONSUMO.get(tarefa["modelo"], CONSUMO["sonnet"])
        tokens = rng.randint(tmin, tmax)
        usos = rng.randint(umin, umax)
        duracao = rng.randint(40_000, 240_000) if args.rapido else int((time.time() - corrida["inicio"]) * 1000)
        metricas = ["--tokens", str(tokens), "--tool-uses", str(usos), "--duracao-ms", str(duracao)]

        juiz = []
        tem_juiz = (tarefa.get("verificacao") or {}).get("tipo") == "juiz" or tid == "montar"
        if tem_juiz:
            juizes += 1
            juiz = ["--juiz-agente", f"juiz-{juizes:02d}", "--juiz-tokens", str(rng.randint(*TOKENS_JUIZ))]

        if tid == "montar" and tentativas[tid] == 1:
            dag(pasta, "set-status", tid, "reprovada", "--motivo", "preço do item 3 diverge da lista",
                *metricas, *juiz, "--juiz-veredito", "nao_atende")
        elif tid == "render-a4" and args.falha:
            dag(pasta, "set-status", tid, "reprovada", "--motivo",
                "PDF saiu em formato Carta, não A4" if tentativas[tid] == 1 else "sangria ausente na segunda tentativa",
                *metricas)
        else:
            arquivos = ",".join(arquivo_exemplo(p) for p in tarefa["escreve"])
            extra = ["--arquivos", arquivos] if arquivos else []
            if juiz:
                extra += juiz + ["--juiz-veredito", "atende"]
            dag(pasta, "set-status", tid, "concluida", "--resumo", f"(simulação) {tarefa['nome']} concluída",
                *metricas, *extra)
        print(dag(pasta, "status").splitlines()[0], flush=True)

    print()
    print(dag(pasta, "summary"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
