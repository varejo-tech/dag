#!/usr/bin/env python3
"""
dag.py — CLI determinística da skill `dag`.

Mantém o estado de uma execução em `.dag/<id>/` (dag.json, eventos.jsonl,
dag.data.js, dag.md). É o único escritor desse estado: o agente nunca edita
esses arquivos à mão. Só biblioteca padrão, Python 3.8+.

Formato completo em ../references/schema.md.
"""
from __future__ import annotations

import argparse
import copy
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path

PASTA_SKILL = Path(__file__).resolve().parent.parent
PAINEL_ORIGEM = PASTA_SKILL / "assets" / "painel.html"

STATUS = ("pendente", "pronta", "executando", "concluida", "reprovada", "falhou", "bloqueada")
ROTULOS = {
    "pendente": "pendente",
    "pronta": "pronta",
    "executando": "executando",
    "concluida": "concluída",
    "reprovada": "reprovada",
    "falhou": "falhou",
    "bloqueada": "bloqueada",
}
ICONES = {
    "pendente": "○",
    "pronta": "▶",
    "executando": "⟳",
    "concluida": "✓",
    "reprovada": "↺",
    "falhou": "✗",
    "bloqueada": "⊘",
}
CORES = {  # mesmas cores do painel
    "pendente": ("#6B7F90", "#3E4D5A", "#0B1E2D"),
    "pronta": ("#3FA9F5", "#1F6FA8", "#0B1E2D"),
    "executando": ("#F5B83D", "#A87A1F", "#0B1E2D"),
    "concluida": ("#3DD68C", "#1F7A4F", "#0B1E2D"),
    "reprovada": ("#FF8A3D", "#A8551F", "#0B1E2D"),
    "falhou": ("#F2545B", "#9E2A30", "#FFFFFF"),
    "bloqueada": ("#4A5A68", "#2E3A44", "#D9E6EC"),
}
COR_CRITICO = "#AFCB07"

MODELOS = ("haiku", "sonnet", "opus", "fable")
MODELO_PADRAO = "sonnet"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EXEC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_EVENTOS_PAINEL = 300

CAMPOS_PLANO = (
    "id", "nome", "descricao", "entradas", "saidas", "depende_de", "escreve", "le",
    "criterio_pronto", "verificacao", "modelo", "estimativa_min",
)
CAMPOS_EXEC = (
    "nivel", "status", "agente", "tentativas", "inicio", "fim", "resumo_resultado",
    "motivo_reprovacao", "arquivos_alterados", "tokens", "tool_uses", "duracao_ms", "historico",
)
CAMPOS_LISTA = ("entradas", "saidas", "depende_de", "escreve", "le")
CAMPOS_META_RASCUNHO = ("titulo", "objetivo")

# Transições que o orquestrador pode pedir; as demais são automáticas.
TRANSICOES_MANUAIS = {
    "pronta": ("executando",),
    "executando": ("concluida", "reprovada", "falhou", "pendente"),
}


class ErroRegra(Exception):
    """Regra do processo ou validação violada (código de saída 1)."""


class ErroUso(Exception):
    """Uso incorreto da CLI (código de saída 2)."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def agora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ler_data(texto):
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def segundos_entre(inicio, fim=None) -> float:
    a = ler_data(inicio)
    if a is None:
        return 0.0
    b = ler_data(fim) if fim else datetime.now().astimezone()
    return max(0.0, (b - a).total_seconds())


def fmt_duracao(segundos: float) -> str:
    s = int(round(segundos))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_tokens(n) -> str:
    n = int(n or 0)
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k".replace(".", ",")
    if n < 1_000_000:
        return f"{n / 1000:.0f}k"
    return f"{n / 1_000_000:.1f}M".replace(".", ",")


def escrever_atomico(caminho: Path, texto: str) -> None:
    """Grava num temporário da mesma pasta e renomeia por cima do original."""
    fd, tmp = tempfile.mkstemp(dir=str(caminho.parent), prefix="." + caminho.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
        os.chmod(tmp, 0o644)
        os.replace(tmp, caminho)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ler_json(caminho: Path, descricao: str):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ErroUso(f"{descricao} não encontrado: {caminho}")
    except json.JSONDecodeError as e:
        raise ErroUso(f"{descricao} não é um JSON válido ({caminho}): {e}")


def ler_json_argumento(args, descricao: str):
    """Lê o JSON de --json ou --arquivo."""
    if getattr(args, "json", None) and getattr(args, "arquivo", None):
        raise ErroUso("Use --json ou --arquivo, não os dois.")
    if getattr(args, "json", None):
        try:
            return json.loads(args.json)
        except json.JSONDecodeError as e:
            raise ErroUso(f"--json inválido: {e}")
    if getattr(args, "arquivo", None):
        return ler_json(Path(args.arquivo), descricao)
    raise ErroUso("Informe o conteúdo com --json '{...}' ou --arquivo caminho.json.")


class Trava:
    """Lock portátil por arquivo, em volta de todo ler-modificar-gravar."""

    ESPERA_MAX = 10.0
    INTERVALO = 0.05
    ABANDONO = 30.0

    def __init__(self, pasta: Path):
        self.caminho = pasta / ".lock"

    def __enter__(self):
        limite = time.time() + self.ESPERA_MAX
        while True:
            try:
                fd = os.open(str(self.caminho), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.caminho.stat().st_mtime > self.ABANDONO:
                        self.caminho.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > limite:
                    raise ErroRegra(
                        f"Outro comando está gravando o estado há mais de {self.ESPERA_MAX:.0f} s "
                        f"({self.caminho}). Tente de novo."
                    )
                time.sleep(self.INTERVALO)

    def __exit__(self, *exc):
        try:
            self.caminho.unlink()
        except FileNotFoundError:
            pass
        return False


# ---------------------------------------------------------------------------
# Sobreposição de caminhos e padrões (conservadora: falso positivo sim,
# falso negativo nunca)
# ---------------------------------------------------------------------------

def normalizar_caminho(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/+", "/", p)
    if p.endswith("/"):
        p += "**"
    return p


def tem_curinga(p: str) -> bool:
    return "*" in p or "?" in p


@functools.lru_cache(maxsize=1024)
def regex_glob(padrao: str):
    i, saida = 0, []
    while i < len(padrao):
        c = padrao[i]
        if c == "*":
            if padrao.startswith("**/", i):
                saida.append("(?:.*/)?")
                i += 3
                continue
            if padrao.startswith("**", i):
                saida.append(".*")
                i += 2
                continue
            saida.append("[^/]*")
        elif c == "?":
            saida.append("[^/]")
        else:
            saida.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(saida) + "$")


def _prefixo_literal(p: str) -> str:
    m = re.search(r"[*?]", p)
    return p if m is None else p[: m.start()]


def _sufixo_literal(p: str) -> str:
    ultimo = max(p.rfind("*"), p.rfind("?"))
    return p if ultimo < 0 else p[ultimo + 1:]


def sobrepoe(a: str, b: str) -> bool:
    """True se os caminhos/padrões a e b podem apontar para o mesmo arquivo."""
    a, b = normalizar_caminho(a), normalizar_caminho(b)
    ga, gb = tem_curinga(a), tem_curinga(b)
    if not ga and not gb:
        return a == b or b.startswith(a + "/") or a.startswith(b + "/")
    if not ga:
        return bool(regex_glob(b).match(a))
    if not gb:
        return bool(regex_glob(a).match(b))
    pa, pb = _prefixo_literal(a), _prefixo_literal(b)
    sa, sb = _sufixo_literal(a), _sufixo_literal(b)
    prefixo_ok = pa.startswith(pb) or pb.startswith(pa)
    sufixo_ok = sa.endswith(sb) or sb.endswith(sa)
    return prefixo_ok and sufixo_ok


# ---------------------------------------------------------------------------
# Normalização e validação do plano
# ---------------------------------------------------------------------------

class Relatorio:
    def __init__(self):
        self.erros: list = []
        self.avisos: list = []
        self.niveis: dict = {}
        self.ordem: list = []
        self.caminho: list = []
        self.ancestrais: dict = {}
        self.largura: dict = {}

    @property
    def ok(self) -> bool:
        return not self.erros

    def imprimir(self, max_paralelo=None) -> None:
        for e in self.erros:
            print(f"✗ {e}")
        for a in self.avisos:
            print(f"! {a}")
        if self.ok:
            print(resumo_plano(self, max_paralelo))
        else:
            print(f"\nPlano recusado: {len(self.erros)} erro(s). Corrija e valide de novo.")


def resumo_plano(rel: Relatorio, max_paralelo=None) -> str:
    total = len(rel.ordem)
    largura_max = max(rel.largura.values()) if rel.largura else 0
    por_nivel = " · ".join(f"nível {n}: {rel.largura[n]}" for n in sorted(rel.largura))
    simultaneos = min(largura_max, max_paralelo) if max_paralelo else largura_max
    linhas = [
        f"✓ Plano válido: {total} tarefas em {len(rel.largura)} níveis.",
        f"  Paralelismo por nível: {por_nivel}",
        f"  Caminho crítico ({len(rel.caminho)} tarefas): {' → '.join(rel.caminho)}",
        f"  Subagentes simultâneos estimados: até {simultaneos}"
        + (f" (limite max_paralelo = {max_paralelo})" if max_paralelo else ""),
    ]
    return "\n".join(linhas)


def normalizar_tarefa(bruta, rel: Relatorio):
    """Converte uma tarefa do rascunho para o formato de plano, registrando erros."""
    if not isinstance(bruta, dict):
        rel.erros.append(f"Tarefa inválida (esperado um objeto JSON): {bruta!r}")
        return None
    t = {}
    tid = bruta.get("id")
    t["id"] = tid.strip() if isinstance(tid, str) else tid
    rotulo = f"Tarefa '{t['id']}'" if t["id"] else "Tarefa sem id"
    for campo in ("nome", "descricao", "criterio_pronto"):
        valor = bruta.get(campo)
        if valor is not None and not isinstance(valor, str):
            rel.erros.append(f"{rotulo}: '{campo}' deve ser texto.")
            valor = str(valor)
        t[campo] = (valor or "").strip()
    for campo in CAMPOS_LISTA:
        valor = bruta.get(campo)
        if valor is None:
            valor = []
        if isinstance(valor, str):
            valor = [valor]
        if not isinstance(valor, list) or not all(isinstance(x, str) for x in valor):
            rel.erros.append(f"{rotulo}: '{campo}' deve ser uma lista de textos.")
            valor = [x for x in valor if isinstance(x, str)] if isinstance(valor, list) else []
        limpos = []
        for x in valor:
            x = x.strip()
            if x and x not in limpos:
                limpos.append(x)
        t[campo] = limpos
    verificacao = bruta.get("verificacao")
    if verificacao is not None:
        if not isinstance(verificacao, dict) or verificacao.get("tipo") not in ("comando", "juiz"):
            rel.erros.append(
                f"{rotulo}: 'verificacao' deve ser {{\"tipo\": \"comando\", \"comando\": \"...\"}} "
                f"ou {{\"tipo\": \"juiz\"}}."
            )
            verificacao = None
        elif verificacao["tipo"] == "comando" and not str(verificacao.get("comando") or "").strip():
            rel.erros.append(f"{rotulo}: verificação do tipo 'comando' sem o campo 'comando'.")
    t["verificacao"] = verificacao
    modelo = bruta.get("modelo") or MODELO_PADRAO
    t["modelo"] = str(modelo).strip().lower()
    estimativa = bruta.get("estimativa_min")
    if estimativa is not None and (
        isinstance(estimativa, bool) or not isinstance(estimativa, (int, float)) or estimativa < 0
    ):
        rel.erros.append(f"{rotulo}: 'estimativa_min' deve ser um número de minutos ≥ 0.")
        estimativa = None
    t["estimativa_min"] = estimativa
    desconhecidos = sorted(set(bruta) - set(CAMPOS_PLANO) - set(CAMPOS_EXEC))
    if desconhecidos:
        rel.avisos.append(f"{rotulo}: campo(s) desconhecido(s) ignorado(s): {', '.join(desconhecidos)}.")
    return t


def plano_da_tarefa(t: dict) -> dict:
    return {campo: copy.deepcopy(t.get(campo)) for campo in CAMPOS_PLANO}


def achar_ciclo(ids, deps):
    """Devolve um ciclo no grafo 'tarefa → dependência' ou None."""
    cor = {i: 0 for i in ids}
    pilha: list = []

    def visitar(u):
        cor[u] = 1
        pilha.append(u)
        for v in deps[u]:
            if cor[v] == 1:
                return pilha[pilha.index(v):] + [v]
            if cor[v] == 0:
                achado = visitar(v)
                if achado:
                    return achado
        pilha.pop()
        cor[u] = 2
        return None

    for i in ids:
        if cor[i] == 0:
            achado = visitar(i)
            if achado:
                return achado
    return None


def ordenar_topologico(ids, deps):
    """Kahn estável: respeita a ordem original sempre que possível."""
    colocados, ordem = set(), []
    restantes = list(ids)
    while restantes:
        for tid in restantes:
            if all(d in colocados for d in deps[tid]):
                ordem.append(tid)
                colocados.add(tid)
                restantes.remove(tid)
                break
        else:  # só acontece com ciclo, que já foi barrado antes
            break
    return ordem


def _alcance_com_ciclo(ids, deps) -> dict:
    """Ancestrais de cada tarefa por busca em profundidade; funciona mesmo com ciclo."""
    alcance = {}
    for tid in ids:
        vistos, pilha = set(), list(deps.get(tid, []))
        while pilha:
            d = pilha.pop()
            if d not in vistos:
                vistos.add(d)
                pilha.extend(deps.get(d, []))
        alcance[tid] = vistos
    return alcance


def _checar_conflitos(rel, ids, vistos, ancestrais) -> None:
    """Conflitos de escrita entre tarefas que podem rodar em paralelo."""
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if a in ancestrais[b] or b in ancestrais[a]:
                continue
            conflito = _primeira_sobreposicao(vistos[a].get("escreve"), vistos[b].get("escreve"))
            if conflito:
                p, q = conflito
                alvo = f"'{p}'" if normalizar_caminho(p) == normalizar_caminho(q) else f"'{p}' × '{q}'"
                rel.erros.append(
                    f"Conflito de escrita: '{a}' e '{b}' podem rodar em paralelo e as duas escrevem em {alvo}. "
                    f"Crie a dependência {a} → {b} (ou {b} → {a}) ou funda as duas tarefas."
                )
            for x, y in ((a, b), (b, a)):
                leitura = _primeira_sobreposicao(vistos[x].get("le"), vistos[y].get("escreve"))
                if leitura:
                    rel.avisos.append(
                        f"'{x}' lê '{leitura[0]}', que '{y}' escreve em paralelo. "
                        f"Se '{x}' precisa da versão final, crie a dependência {y} → {x}."
                    )



def validar(tarefas: list, rel: Relatorio = None) -> Relatorio:
    """Checagens da fase 3. `tarefas` já normalizadas (campos de plano)."""
    rel = rel or Relatorio()
    vistos: dict = {}
    for t in tarefas:
        tid = t.get("id")
        rotulo = f"Tarefa '{tid}'" if tid else "Tarefa sem id"
        if not isinstance(tid, str) or not ID_RE.match(tid):
            rel.erros.append(
                f"{rotulo}: id inválido. Use só letras minúsculas, números, '-' e '_' (ex.: 'validar-precos')."
            )
        elif tid in vistos:
            rel.erros.append(f"Id duplicado: '{tid}' aparece mais de uma vez.")
        else:
            vistos[tid] = t
        if not t.get("nome"):
            rel.erros.append(f"{rotulo}: sem 'nome'.")
        if not (t.get("criterio_pronto") or "").strip():
            rel.erros.append(
                f"{rotulo}: sem 'criterio_pronto'. Defina uma condição verificável de conclusão "
                f"(ex.: \"os testes de simulador/ passam\")."
            )
        if t.get("modelo") and t["modelo"] not in MODELOS:
            rel.avisos.append(
                f"{rotulo}: modelo '{t['modelo']}' desconhecido (use {', '.join(MODELOS)})."
            )

    if not tarefas:
        rel.erros.append("O plano não tem tarefas.")
        return rel

    ids = list(vistos)
    deps: dict = {}
    for tid, t in vistos.items():
        lista = []
        for d in t.get("depende_de") or []:
            if d == tid:
                rel.erros.append(f"Tarefa '{tid}' depende de si mesma.")
            elif d not in vistos:
                rel.erros.append(f"Tarefa '{tid}' depende de '{d}', que não existe no plano.")
            elif d not in lista:
                lista.append(d)
        deps[tid] = lista

    raizes = [i for i in ids if not deps[i]]
    ciclo = achar_ciclo(ids, deps)
    if ciclo:
        ordem_execucao = list(reversed(ciclo))
        rel.erros.append(
            "Ciclo de dependências: " + " → ".join(ordem_execucao)
            + ". Cada tarefa espera a anterior e nenhuma consegue começar; remova uma dessas dependências."
        )
    if ids and not raizes:
        rel.erros.append("Nenhuma tarefa inicial: toda tarefa depende de outra, então nada pode começar.")
    if ciclo:
        # Mesmo com ciclo, relata os conflitos de escrita para o agente corrigir tudo de uma vez.
        _checar_conflitos(rel, ids, vistos, _alcance_com_ciclo(ids, deps))
    if ciclo or not ids:
        return rel

    ordem = ordenar_topologico(ids, deps)
    niveis, ancestrais = {}, {}
    for tid in ordem:
        niveis[tid] = 1 + max((niveis[d] for d in deps[tid]), default=-1)
        anc = set()
        for d in deps[tid]:
            anc.add(d)
            anc |= ancestrais[d]
        ancestrais[tid] = anc
    largura = Counter(niveis.values())

    # Caminho crítico: cadeia de maior peso.
    usar_estimativa = all(
        isinstance(vistos[i].get("estimativa_min"), (int, float))
        and not isinstance(vistos[i].get("estimativa_min"), bool)
        for i in ids
    )
    distancia, anterior = {}, {}
    for tid in ordem:
        peso = float(vistos[tid]["estimativa_min"]) if usar_estimativa else 1.0
        melhor = None
        for d in deps[tid]:
            if melhor is None or distancia[d] > distancia[melhor]:
                melhor = d
        distancia[tid] = peso + (distancia[melhor] if melhor else 0.0)
        anterior[tid] = melhor
    fim = max(ordem, key=lambda x: distancia[x])
    caminho = []
    while fim:
        caminho.append(fim)
        fim = anterior[fim]
    caminho.reverse()

    _checar_conflitos(rel, ids, vistos, ancestrais)

    if len(ids) < 4 or max(largura.values()) < 2:
        rel.avisos.append(
            "Plano pequeno ou sem paralelismo: talvez o DAG não compense e executar direto seja mais rápido."
        )

    rel.ordem, rel.niveis, rel.ancestrais = ordem, niveis, ancestrais
    rel.largura, rel.caminho = dict(largura), caminho
    return rel


def _primeira_sobreposicao(lista_a, lista_b):
    for p in lista_a or []:
        for q in lista_b or []:
            if sobrepoe(p, q):
                return p, q
    return None


def validar_rascunho(dados) -> tuple:
    """Normaliza e valida um rascunho (objeto com 'tarefas' ou lista)."""
    rel = Relatorio()
    if isinstance(dados, list):
        brutas, meta = dados, {}
    elif isinstance(dados, dict):
        brutas, meta = dados.get("tarefas"), dados.get("meta") or {}
        if not isinstance(brutas, list):
            rel.erros.append("O rascunho precisa de uma lista em 'tarefas'.")
            return [], meta, rel
    else:
        rel.erros.append("O rascunho precisa ser um objeto com 'tarefas' ou uma lista de tarefas.")
        return [], {}, rel
    tarefas = [t for t in (normalizar_tarefa(b, rel) for b in brutas) if t is not None]
    validar(tarefas, rel)
    return tarefas, meta, rel


# ---------------------------------------------------------------------------
# Execução (estado em disco)
# ---------------------------------------------------------------------------

def nova_tarefa_execucao(plano: dict) -> dict:
    t = {campo: copy.deepcopy(plano.get(campo)) for campo in CAMPOS_PLANO}
    t.update({
        "nivel": 0,
        "status": "pendente",
        "agente": None,
        "tentativas": 0,
        "inicio": None,
        "fim": None,
        "resumo_resultado": None,
        "motivo_reprovacao": None,
        "arquivos_alterados": [],
        "tokens": 0,
        "tool_uses": 0,
        "duracao_ms": 0,
        "historico": [],
    })
    return t


class Execucao:
    def __init__(self, pasta: Path):
        self.pasta = pasta
        self.arq_dag = pasta / "dag.json"
        self.dag: dict = {}
        self.novos: list = []

    @classmethod
    def abrir(cls, pasta: Path) -> "Execucao":
        ex = cls(pasta)
        if not ex.arq_dag.is_file():
            raise ErroUso(f"Execução não encontrada em {pasta} (falta dag.json). Rode `dag.py init` primeiro.")
        ex.dag = ler_json(ex.arq_dag, "dag.json")
        return ex

    @property
    def meta(self) -> dict:
        return self.dag["meta"]

    @property
    def tarefas(self) -> list:
        return self.dag["tarefas"]

    def indice(self) -> dict:
        return {t["id"]: t for t in self.tarefas}

    def tarefa(self, tid: str) -> dict:
        idx = self.indice()
        if tid not in idx:
            existentes = ", ".join(idx) or "nenhuma"
            raise ErroRegra(f"Tarefa '{tid}' não existe. Tarefas do plano: {existentes}.")
        return idx[tid]

    def evento(self, tipo: str, msg: str, **campos) -> None:
        ev = {"ts": agora(), "tipo": tipo}
        for chave in ("tarefa", "de", "para", "agente", "motivo", "tokens"):
            valor = campos.get(chave)
            if valor is not None:
                ev[chave] = valor
        ev["versao"] = self.meta.get("versao", 0)
        ev["msg"] = msg
        self.novos.append(ev)

    def mudar_status(self, t: dict, para: str, detalhe: str = "", agente=None, motivo=None, tokens=None):
        de = t["status"]
        t["status"] = para
        msg = f"{t['id']} → {ROTULOS[para]}"
        if agente:
            msg += f" ({agente})"
        if detalhe:
            msg += f": {detalhe}"
        self.evento("status", msg, tarefa=t["id"], de=de, para=para, agente=agente, motivo=motivo, tokens=tokens)

    def aplicar_relatorio(self, rel: Relatorio) -> None:
        for t in self.tarefas:
            t["nivel"] = rel.niveis.get(t["id"], 0)
        self.meta["caminho_critico"] = list(rel.caminho)
        self.meta["total_niveis"] = (max(rel.niveis.values()) + 1) if rel.niveis else 0

    def executando(self) -> list:
        return [t for t in self.tarefas if t["status"] == "executando"]

    def salvar(self) -> None:
        self.meta["atualizado_em"] = agora()
        escrever_atomico(self.arq_dag, json.dumps(self.dag, ensure_ascii=False, indent=2) + "\n")
        if self.novos:
            with open(self.pasta / "eventos.jsonl", "a", encoding="utf-8") as f:
                for ev in self.novos:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.novos = []
        gerar_dados_painel(self)
        gerar_mermaid(self)


def promover(ex: Execucao) -> None:
    """Transições automáticas, repetidas até estabilizar."""
    idx = ex.indice()
    mudou = True
    while mudou:
        mudou = False
        for t in ex.tarefas:
            deps = [idx[d] for d in t["depende_de"] if d in idx]
            ruins = [d["id"] for d in deps if d["status"] in ("falhou", "bloqueada")]
            todas_ok = all(d["status"] == "concluida" for d in deps)
            if t["status"] == "pendente":
                if ruins:
                    ex.mudar_status(t, "bloqueada", f"depende de {', '.join(ruins)}")
                    mudou = True
                elif todas_ok:
                    ex.mudar_status(t, "pronta")
                    mudou = True
            elif t["status"] == "bloqueada" and not ruins:
                ex.mudar_status(t, "pendente", "desbloqueada")
                mudou = True
            elif t["status"] == "pronta" and not todas_ok:
                ex.mudar_status(t, "pendente", "dependências mudaram no replanejamento")
                mudou = True


def resolver_pasta(args) -> Path:
    if getattr(args, "dir", None):
        return Path(args.dir)
    env = os.environ.get("DAG_DIR")
    if env:
        return Path(env)
    atual = Path.cwd()
    for pasta in [atual, *atual.parents]:
        ponteiro = pasta / ".dag" / "atual"
        if ponteiro.is_file():
            ident = ponteiro.read_text(encoding="utf-8").strip()
            if ident:
                return ponteiro.parent / ident
    raise ErroUso(
        "Nenhuma execução encontrada. Rode `dag.py init <id> --titulo ...`, "
        "ou informe --dir .dag/<id> (ou a variável DAG_DIR)."
    )


def mutar(args, funcao):
    """Ler-modificar-gravar sob trava, com promoção automática no fim."""
    pasta = resolver_pasta(args)
    with Trava(pasta):
        ex = Execucao.abrir(pasta)
        resultado = funcao(ex)
        promover(ex)
        ex.salvar()
    return resultado


def ler_eventos(pasta: Path, limite: int) -> list:
    arq = pasta / "eventos.jsonl"
    if not arq.is_file():
        return []
    with open(arq, encoding="utf-8") as f:
        linhas = f.readlines()[-limite:]
    eventos = []
    for linha in linhas:
        try:
            eventos.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return eventos


def gerar_dados_painel(ex: Execucao) -> None:
    payload = {"gerado_em": agora(), "dag": ex.dag, "eventos": ler_eventos(ex.pasta, MAX_EVENTOS_PAINEL)}
    texto = "window.DAG_RECEBER && window.DAG_RECEBER(" + json.dumps(payload, ensure_ascii=False) + ");\n"
    escrever_atomico(ex.pasta / "dag.data.js", texto)


def _id_mermaid(tid: str) -> str:
    return "t_" + re.sub(r"[^A-Za-z0-9_]", "_", tid)


def _texto_mermaid(texto: str) -> str:
    return (texto or "").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def gerar_mermaid(ex: Execucao) -> None:
    m = ex.meta
    tarefas = ex.tarefas
    linhas = [f"# DAG — {m.get('titulo') or m.get('id')}", ""]
    if m.get("objetivo"):
        linhas += [f"> {m['objetivo']}", ""]
    linhas += [
        f"**Execução** `{m.get('id')}` · **versão** v{m.get('versao', 0)} · **estado** {m.get('estado')} "
        f"· atualizado em {m.get('atualizado_em')}",
        "",
    ]
    if not tarefas:
        linhas.append("_Plano ainda não carregado._")
        escrever_atomico(ex.pasta / "dag.md", "\n".join(linhas) + "\n")
        return

    contagem = Counter(t["status"] for t in tarefas)
    concluidas = contagem["concluida"]
    tokens = sum(t.get("tokens") or 0 for t in tarefas)
    linhas += [
        f"**Progresso:** {concluidas}/{len(tarefas)} concluídas ({round(100 * concluidas / len(tarefas))}%) "
        f"· {fmt_tokens(tokens)} tokens",
        "",
        "| Status | Tarefas |",
        "|---|---|",
    ]
    for s in STATUS:
        if contagem[s]:
            linhas.append(f"| {ICONES[s]} {ROTULOS[s]} | {contagem[s]} |")

    linhas += ["", "```mermaid", "flowchart LR"]
    for t in sorted(tarefas, key=lambda x: (x.get("nivel", 0), tarefas.index(x))):
        s = t["status"]
        rotulo = f"{ICONES[s]} {_texto_mermaid(t['nome'])}<br/>{t['id']} · {ROTULOS[s]}"
        linhas.append(f'  {_id_mermaid(t["id"])}["{rotulo}"]:::{s}')
    caminho = m.get("caminho_critico") or []
    pares_criticos = set(zip(caminho, caminho[1:]))
    criticas, n = [], 0
    for t in tarefas:
        for d in t["depende_de"]:
            linhas.append(f"  {_id_mermaid(d)} --> {_id_mermaid(t['id'])}")
            if (d, t["id"]) in pares_criticos:
                criticas.append(str(n))
            n += 1
    for s, (fundo, borda, texto) in CORES.items():
        extra = ",stroke-dasharray:5 5" if s == "bloqueada" else ""
        linhas.append(f"  classDef {s} fill:{fundo},stroke:{borda},color:{texto}{extra}")
    if criticas:
        linhas.append(f"  linkStyle {','.join(criticas)} stroke:{COR_CRITICO},stroke-width:4px")
    linhas += ["```", "", f"Caminho crítico: {' → '.join(caminho)}" if caminho else ""]
    escrever_atomico(ex.pasta / "dag.md", "\n".join(linhas).rstrip() + "\n")


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    if not EXEC_ID_RE.match(args.id):
        raise ErroUso("Id da execução inválido. Use letras, números, '.', '-' e '_' (ex.: 2026-10-08-encarte).")
    if args.max_paralelo < 1 or args.max_tentativas < 1:
        raise ErroUso("--max-paralelo e --max-tentativas precisam ser ≥ 1.")
    raiz = Path(args.raiz)
    pasta = raiz / args.id
    if (pasta / "dag.json").exists():
        raise ErroRegra(f"Já existe uma execução em {pasta}. Escolha outro id.")
    pasta.mkdir(parents=True, exist_ok=True)
    momento = agora()
    ex = Execucao(pasta)
    ex.dag = {
        "meta": {
            "id": args.id,
            "titulo": args.titulo,
            "objetivo": args.objetivo or "",
            "versao": 0,
            "estado": "planejando",
            "modo": args.modo,
            "max_paralelo": args.max_paralelo,
            "max_tentativas": args.max_tentativas,
            "criado_em": momento,
            "atualizado_em": momento,
            "aprovado_em": None,
            "aprovacao": None,
            "iniciado_em": None,
            "encerrado_em": None,
            "caminho_critico": [],
            "total_niveis": 0,
            "pico_paralelo": 0,
            "contador_agentes": 0,
        },
        "tarefas": [],
    }
    with Trava(pasta):
        ex.evento("init", f"Execução '{args.id}' criada ({args.modo}, até {args.max_paralelo} em paralelo).")
        ex.salvar()
    if PAINEL_ORIGEM.is_file():
        shutil.copyfile(PAINEL_ORIGEM, pasta / "painel.html")
        painel = f"Painel: {pasta / 'painel.html'} (abra com `dag.py open`)"
    else:
        painel = f"! Painel não copiado: {PAINEL_ORIGEM} ainda não existe."
    escrever_atomico(raiz / "atual", args.id + "\n")
    print(f"Execução criada: {pasta}")
    print(painel)
    print("Próximo passo: escreva o rascunho do plano e rode `dag.py load <rascunho.json>`.")
    return 0


def cmd_load(args) -> int:
    dados = ler_json(Path(args.rascunho), "Rascunho")
    tarefas, meta_rascunho, rel = validar_rascunho(dados)

    def aplicar(ex: Execucao):
        if ex.meta["estado"] != "planejando" or ex.tarefas:
            raise ErroRegra(
                "O plano desta execução já foi carregado. Para mudar, use add-task, edit ou remove-task."
            )
        if not rel.ok:
            return False
        # O rascunho só completa título e objetivo deixados em branco no `init`.
        for chave in CAMPOS_META_RASCUNHO:
            valor = meta_rascunho.get(chave) if isinstance(meta_rascunho, dict) else None
            if isinstance(valor, str) and valor.strip() and not ex.meta.get(chave):
                ex.meta[chave] = valor.strip()
        ex.dag["tarefas"] = [nova_tarefa_execucao(t) for t in tarefas]
        ex.meta["versao"] = 1
        ex.meta["estado"] = "aguardando_aprovacao"
        ex.aplicar_relatorio(rel)
        ex.evento(
            "plano_carregado",
            f"Plano v1 carregado: {len(tarefas)} tarefas em {ex.meta['total_niveis']} níveis. "
            f"Caminho crítico: {' → '.join(rel.caminho)}.",
        )
        for aviso in rel.avisos:
            ex.evento("aviso", aviso)
        return True

    pasta = resolver_pasta(args)
    with Trava(pasta):
        ex = Execucao.abrir(pasta)
        aceito = aplicar(ex)
        if aceito:
            promover(ex)
            ex.salvar()
    rel.imprimir(ex.meta.get("max_paralelo"))
    if not aceito:
        return 1
    print("\nPlano carregado (v1). Mostre o resumo ao usuário e rode `dag.py approve` "
          "(ou `dag.py approve --auto` no modo autônomo).")
    return 0


def cmd_validate(args) -> int:
    if args.arquivo:
        _, _, rel = validar_rascunho(ler_json(Path(args.arquivo), "Rascunho"))
        rel.imprimir()
        return 0 if rel.ok else 1
    pasta = resolver_pasta(args)
    with Trava(pasta):
        ex = Execucao.abrir(pasta)
        planos = [plano_da_tarefa(t) for t in ex.tarefas]
        rel = validar(planos)
        if rel.ok:
            ex.aplicar_relatorio(rel)
            ex.salvar()
    rel.imprimir(ex.meta.get("max_paralelo"))
    return 0 if rel.ok else 1


def cmd_approve(args) -> int:
    def aplicar(ex: Execucao):
        if ex.meta["estado"] != "aguardando_aprovacao":
            raise ErroRegra(
                f"Nada para aprovar: o estado da execução é '{ex.meta['estado']}'. "
                "A aprovação vem depois do `load`."
            )
        ex.meta["estado"] = "executando"
        ex.meta["aprovado_em"] = agora()
        ex.meta["aprovacao"] = "automatica" if args.auto else "humana"
        tipo = "automaticamente (modo autônomo)" if args.auto else "pelo usuário"
        ex.evento("aprovado", f"Plano v{ex.meta['versao']} aprovado {tipo}.")

    mutar(args, aplicar)
    print("Plano aprovado. Rode `dag.py ready` para ver o que pode começar.")
    return 0


def _ordem_prioridade(ex: Execucao, tarefas: list) -> list:
    criticas = set(ex.meta.get("caminho_critico") or [])
    posicao = {t["id"]: i for i, t in enumerate(ex.tarefas)}
    return sorted(tarefas, key=lambda t: (t["id"] not in criticas, t.get("nivel", 0), posicao[t["id"]]))


def cmd_ready(args) -> int:
    def aplicar(ex: Execucao):
        maximo = ex.meta["max_tentativas"]
        for t in ex.tarefas:
            if t["status"] == "reprovada" and t["tentativas"] < maximo:
                ex.mudar_status(
                    t, "pronta", f"volta à fila (tentativa {t['tentativas'] + 1} de {maximo})",
                    motivo=t.get("motivo_reprovacao"),
                )

    mutar(args, aplicar)
    ex = Execucao.abrir(resolver_pasta(args))
    criticas = set(ex.meta.get("caminho_critico") or [])
    prontas = _ordem_prioridade(ex, [t for t in ex.tarefas if t["status"] == "pronta"])
    executando = ex.executando()
    vagas = max(0, ex.meta["max_paralelo"] - len(executando))
    fim = not prontas and not executando
    if args.json:
        saida = {
            "estado": ex.meta["estado"],
            "vagas": vagas,
            "max_paralelo": ex.meta["max_paralelo"],
            "executando": [t["id"] for t in executando],
            "prontas": [
                dict(plano_da_tarefa(t), critico=t["id"] in criticas, nivel=t.get("nivel", 0),
                     tentativas=t["tentativas"], motivo_reprovacao=t.get("motivo_reprovacao"))
                for t in prontas
            ],
            "fim": fim,
        }
        print(json.dumps(saida, ensure_ascii=False, indent=2))
        return 0
    if ex.meta["estado"] != "executando":
        print(f"! Estado '{ex.meta['estado']}': nenhuma tarefa pode começar antes do `approve`.")
    print(f"vagas: {vagas} de {ex.meta['max_paralelo']} ({len(executando)} executando)")
    if prontas:
        largura = max(len(t["id"]) for t in prontas)
        print("prontas:")
        for t in prontas:
            marca = "  ★ crítico" if t["id"] in criticas else ""
            retry = f"  ↺ tentativa {t['tentativas'] + 1}" if t["tentativas"] else ""
            print(f"  {t['id']:<{largura}}  {t['modelo']:<6}  {t['nome']}{marca}{retry}")
    elif fim:
        print("Fim: não há tarefas prontas nem executando. Rode `dag.py summary`.")
    else:
        print("Nenhuma tarefa pronta agora; aguarde as que estão executando.")
    return 0


def _iniciar(ex: Execucao, t: dict, agente=None, modelo=None) -> str:
    if ex.meta["estado"] != "executando":
        raise ErroRegra(
            f"O plano ainda não foi aprovado (estado '{ex.meta['estado']}'). "
            "Nenhuma tarefa começa antes da fase 4: rode `dag.py approve`."
        )
    if t["status"] != "pronta":
        dica = " Rode `dag.py ready` para devolvê-la à fila." if t["status"] == "reprovada" else ""
        raise ErroRegra(f"Transição inválida: {t['id']} está '{t['status']}', só uma tarefa 'pronta' pode começar.{dica}")
    ativos = ex.executando()
    if len(ativos) >= ex.meta["max_paralelo"]:
        raise ErroRegra(
            f"Limite de paralelismo atingido: {len(ativos)} de {ex.meta['max_paralelo']} executando "
            f"({', '.join(a['id'] for a in ativos)}). Espere uma terminar."
        )
    if not agente:
        ex.meta["contador_agentes"] = ex.meta.get("contador_agentes", 0) + 1
        agente = f"sub-{ex.meta['contador_agentes']:02d}"
    momento = agora()
    t["tentativas"] += 1
    t["agente"] = agente
    t["inicio"] = t["inicio"] or momento
    t["fim"] = None
    t["historico"].append({
        "tentativa": t["tentativas"],
        "agente": agente,
        "modelo": modelo or t.get("modelo"),
        "inicio": momento,
        "fim": None,
        "resultado": None,
        "motivo": None,
        "tokens": 0,
        "tool_uses": 0,
        "duracao_ms": 0,
        "juiz": None,
    })
    ex.mudar_status(t, "executando", f"tentativa {t['tentativas']}" if t["tentativas"] > 1 else "", agente=agente)
    ex.meta["iniciado_em"] = ex.meta.get("iniciado_em") or momento
    ex.meta["pico_paralelo"] = max(ex.meta.get("pico_paralelo", 0), len(ativos) + 1)
    return agente


def cmd_start(args) -> int:
    agente = mutar(args, lambda ex: _iniciar(ex, ex.tarefa(args.tarefa), args.agente, args.modelo))
    print(agente)
    return 0


def _relativo_ao_projeto(caminho: str, pasta: Path) -> str:
    """Converte um caminho absoluto dentro da raiz do projeto (pai de .dag/) em relativo."""
    if not os.path.isabs(caminho):
        return caminho
    raiz = os.path.realpath(pasta.resolve().parent.parent)
    try:
        rel = os.path.relpath(os.path.realpath(caminho), raiz)
    except ValueError:  # outro disco no Windows
        return caminho
    return caminho if rel == ".." or rel.startswith(".." + os.sep) else rel.replace(os.sep, "/")


def cmd_set_status(args) -> int:
    para = args.status
    if para in ("pronta", "bloqueada"):
        raise ErroRegra(f"'{para}' é automática: o dag.py aplica sozinho quando as dependências mudam.")
    if para == "executando":
        agente = mutar(args, lambda ex: _iniciar(ex, ex.tarefa(args.tarefa), args.agente, None))
        print(agente)
        return 0
    if para in ("reprovada", "falhou", "pendente") and not (args.motivo or "").strip():
        raise ErroUso(f"Informe --motivo ao marcar '{para}'.")
    arquivos = [a.strip() for a in (args.arquivos or "").split(",") if a.strip()]

    def aplicar(ex: Execucao):
        t = ex.tarefa(args.tarefa)
        de = t["status"]
        if para not in TRANSICOES_MANUAIS.get(de, ()):
            permitidas = TRANSICOES_MANUAIS.get(de)
            dica = (f"A partir de '{de}' só vale: {', '.join(permitidas)}." if permitidas
                    else f"Não há transição manual a partir de '{de}'.")
            raise ErroRegra(f"Transição inválida: {t['id']} {de} → {para}. {dica}")
        # Subagentes costumam devolver caminhos absolutos; compare sempre relativo à raiz do projeto.
        arquivos[:] = [_relativo_ao_projeto(a, ex.pasta) for a in arquivos]
        fora = [a for a in arquivos if not any(sobrepoe(a, p) for p in t["escreve"])]
        if fora and not args.forcar:
            raise ErroRegra(
                f"Arquivo(s) fora da lista 'escreve' de {t['id']}: {', '.join(fora)}. "
                f"Permitidos: {', '.join(t['escreve']) or 'nenhum'}. "
                "Replaneje (edit) ou, se for intencional, repita com --forcar."
            )
        if fora:
            ex.evento("aviso", f"{t['id']} alterou arquivo(s) fora do 'escreve' (aceito com --forcar): {', '.join(fora)}.",
                      tarefa=t["id"])

        tentativa = t["historico"][-1] if t["historico"] else None
        momento = agora()
        duracao = args.duracao_ms
        if duracao is None and tentativa:
            duracao = int(segundos_entre(tentativa["inicio"], momento) * 1000)
        tokens = args.tokens or 0
        tokens_juiz = args.juiz_tokens or 0
        juiz = None
        if args.juiz_agente or args.juiz_veredito or args.juiz_tokens:
            juiz = {"agente": args.juiz_agente, "veredito": args.juiz_veredito, "tokens": tokens_juiz}
        resultado = "devolvida" if para == "pendente" else para
        if tentativa:
            tentativa.update({
                "fim": momento,
                "resultado": resultado,
                "motivo": args.motivo,
                "resumo": args.resumo,
                "tokens": tokens,
                "tool_uses": args.tool_uses or 0,
                "duracao_ms": duracao or 0,
                "juiz": juiz,
            })
            if args.agente:
                tentativa["agente"] = args.agente
        if args.agente:
            t["agente"] = args.agente
        t["tokens"] = (t.get("tokens") or 0) + tokens + tokens_juiz
        t["tool_uses"] = (t.get("tool_uses") or 0) + (args.tool_uses or 0)
        t["duracao_ms"] = (t.get("duracao_ms") or 0) + (duracao or 0)
        if arquivos:
            t["arquivos_alterados"] = arquivos
        total_tokens = tokens + tokens_juiz or None
        agente = t.get("agente")

        if para == "concluida":
            t["fim"] = momento
            t["resumo_resultado"] = args.resumo
            ex.mudar_status(t, "concluida", agente=agente, tokens=total_tokens)
        elif para == "reprovada":
            t["motivo_reprovacao"] = args.motivo
            ex.mudar_status(t, "reprovada", args.motivo, agente=agente, motivo=args.motivo, tokens=total_tokens)
            if t["tentativas"] >= ex.meta["max_tentativas"]:
                t["fim"] = momento
                ex.mudar_status(
                    t, "falhou", f"tentativas esgotadas ({t['tentativas']} de {ex.meta['max_tentativas']})",
                    motivo=args.motivo,
                )
        elif para == "falhou":
            t["fim"] = momento
            t["motivo_reprovacao"] = args.motivo
            ex.mudar_status(t, "falhou", args.motivo, agente=agente, motivo=args.motivo, tokens=total_tokens)
        elif para == "pendente":
            t["tentativas"] = max(0, t["tentativas"] - 1)
            ex.mudar_status(t, "pendente", f"devolvida: {args.motivo}", agente=agente, motivo=args.motivo,
                            tokens=total_tokens)
        return t

    t = mutar(args, aplicar)
    print(f"{t['id']}: {ROTULOS[t['status']]}")
    if t["status"] == "falhou":
        print("As tarefas que dependem dela foram bloqueadas. Replaneje com `dag.py edit` se houver saída.")
    return 0


def _replanejar(args, alterar, descricao: str) -> dict:
    """Aplica uma mudança de plano numa cópia, valida e só grava se passar."""
    if not (args.motivo or "").strip():
        raise ErroUso("Informe --motivo: todo replanejamento fica registrado no log.")
    pasta = resolver_pasta(args)
    with Trava(pasta):
        ex = Execucao.abrir(pasta)
        if ex.meta["estado"] in ("planejando",):
            raise ErroRegra("O plano ainda não foi carregado: use `dag.py load` primeiro.")
        if ex.meta["estado"] == "encerrado":
            raise ErroRegra("Esta execução já foi encerrada; crie uma nova com `dag.py init`.")
        tarefas = copy.deepcopy(ex.tarefas)
        extra = alterar(ex, tarefas)
        rel = validar([plano_da_tarefa(t) for t in tarefas])
        if not rel.ok:
            rel.imprimir()
            raise ErroRegra("Replanejamento recusado: o plano resultante não é válido. Nada foi gravado.")
        ex.dag["tarefas"] = tarefas
        ex.meta["versao"] = ex.meta.get("versao", 0) + 1
        ex.aplicar_relatorio(rel)
        ex.evento("replanejado", f"Plano v{ex.meta['versao']}: {descricao}. Motivo: {args.motivo}",
                  motivo=args.motivo, tarefa=extra.get("tarefa"))
        if extra.get("reaberta"):
            t = next(x for x in ex.tarefas if x["id"] == extra["tarefa"])
            ex.mudar_status(t, "pendente", "reaberta pelo replanejamento", motivo=args.motivo)
        for aviso in rel.avisos:
            ex.evento("aviso", aviso)
        promover(ex)
        ex.salvar()
    for aviso in rel.avisos:
        print(f"! {aviso}")
    print(f"Plano atualizado para v{ex.meta['versao']} ({descricao}).")
    return extra


def cmd_add_task(args) -> int:
    bruta = ler_json_argumento(args, "Tarefa")
    rel = Relatorio()
    plano = normalizar_tarefa(bruta, rel)
    if not rel.ok or plano is None:
        for e in rel.erros:
            print(f"✗ {e}")
        raise ErroRegra("Tarefa inválida. Nada foi gravado.")

    def alterar(ex, tarefas):
        if any(t["id"] == plano["id"] for t in tarefas):
            raise ErroRegra(f"Já existe uma tarefa '{plano['id']}'. Use `dag.py edit` para alterá-la.")
        tarefas.append(nova_tarefa_execucao(plano))
        return {"tarefa": plano["id"]}

    _replanejar(args, alterar, f"tarefa '{plano['id']}' adicionada")
    return 0


def cmd_edit(args) -> int:
    campos = ler_json_argumento(args, "Campos")
    if not isinstance(campos, dict) or not campos:
        raise ErroUso("Informe um objeto JSON com os campos a alterar.")
    proibidos = sorted(set(campos) & set(CAMPOS_EXEC))
    if proibidos:
        raise ErroRegra(f"Campos de execução não podem ser editados: {', '.join(proibidos)}.")
    if "id" in campos:
        raise ErroRegra("O id de uma tarefa não pode mudar. Remova a tarefa e adicione outra.")
    desconhecidos = sorted(set(campos) - set(CAMPOS_PLANO))
    if desconhecidos:
        raise ErroUso(f"Campo(s) desconhecido(s): {', '.join(desconhecidos)}.")

    def alterar(ex, tarefas):
        t = next((x for x in tarefas if x["id"] == args.tarefa), None)
        if t is None:
            ex.tarefa(args.tarefa)  # gera o erro com a lista de tarefas
        if t["status"] in ("executando", "concluida"):
            raise ErroRegra(f"Não é possível editar {t['id']}: ela está '{t['status']}'.")
        rel = Relatorio()
        novo = normalizar_tarefa(dict(plano_da_tarefa(t), **campos), rel)
        if not rel.ok:
            for e in rel.erros:
                print(f"✗ {e}")
            raise ErroRegra("Campos inválidos. Nada foi gravado.")
        t.update({k: novo[k] for k in CAMPOS_PLANO})
        reaberta = False
        if t["status"] == "falhou":
            t["tentativas"] = 0
            t["fim"] = None
            reaberta = True
        return {"tarefa": t["id"], "reaberta": reaberta}

    extra = _replanejar(args, alterar, f"tarefa '{args.tarefa}' editada ({', '.join(sorted(campos))})")
    if extra.get("reaberta"):
        print(f"{args.tarefa} foi reaberta; as tarefas que ela bloqueava voltaram para a fila.")
    return 0


def cmd_remove_task(args) -> int:
    def alterar(ex, tarefas):
        t = next((x for x in tarefas if x["id"] == args.tarefa), None)
        if t is None:
            ex.tarefa(args.tarefa)
        if t["status"] in ("executando", "concluida"):
            raise ErroRegra(f"Não é possível remover {t['id']}: ela está '{t['status']}'.")
        dependentes = [x["id"] for x in tarefas if args.tarefa in x["depende_de"]]
        if dependentes:
            raise ErroRegra(
                f"{', '.join(dependentes)} depende(m) de {args.tarefa}. "
                "Edite essas tarefas (depende_de) antes de remover."
            )
        tarefas.remove(t)
        return {"tarefa": args.tarefa}

    _replanejar(args, alterar, f"tarefa '{args.tarefa}' removida")
    return 0


def _tempo_decorrido(meta: dict) -> float:
    if not meta.get("iniciado_em"):
        return 0.0
    return segundos_entre(meta["iniciado_em"], meta.get("encerrado_em"))


def cmd_status(args) -> int:
    ex = Execucao.abrir(resolver_pasta(args))
    tarefas = ex.tarefas
    contagem = Counter(t["status"] for t in tarefas)
    tokens = sum(t.get("tokens") or 0 for t in tarefas)
    decorrido = _tempo_decorrido(ex.meta)
    if args.json:
        print(json.dumps({
            "id": ex.meta["id"],
            "estado": ex.meta["estado"],
            "versao": ex.meta["versao"],
            "total": len(tarefas),
            "por_status": {s: contagem[s] for s in STATUS},
            "tokens": tokens,
            "tool_uses": sum(t.get("tool_uses") or 0 for t in tarefas),
            "decorrido_s": round(decorrido),
            "pico_paralelo": ex.meta.get("pico_paralelo", 0),
            "executando": [{"id": t["id"], "agente": t["agente"], "desde": t["historico"][-1]["inicio"]}
                           for t in ex.executando()],
            "prontas": [t["id"] for t in tarefas if t["status"] == "pronta"],
        }, ensure_ascii=False, indent=2))
        return 0
    falhas = contagem["falhou"]
    print(
        f"{contagem['concluida']}/{len(tarefas)} concluídas · {contagem['executando']} executando · "
        f"{falhas} falha{'s' if falhas != 1 else ''} · {fmt_tokens(tokens)} tokens · {fmt_duracao(decorrido)}"
    )
    print(f"estado: {ex.meta['estado']} · plano v{ex.meta['versao']} · pico de paralelismo: "
          f"{ex.meta.get('pico_paralelo', 0)}/{ex.meta['max_paralelo']}")
    for t in ex.executando():
        desde = segundos_entre(t["historico"][-1]["inicio"]) if t["historico"] else 0
        print(f"  ⟳ {t['id']} ({t['agente']}, {t['modelo']}) há {fmt_duracao(desde)}")
    prontas = [t["id"] for t in tarefas if t["status"] == "pronta"]
    if prontas:
        print(f"  ▶ prontas: {', '.join(prontas)}")
    for s in ("reprovada", "falhou", "bloqueada"):
        ids = [t["id"] for t in tarefas if t["status"] == s]
        if ids:
            print(f"  {ICONES[s]} {ROTULOS[s]}: {', '.join(ids)}")
    return 0


def _gerar_resumo(ex: Execucao) -> str:
    m = ex.meta
    tarefas = ex.tarefas
    contagem = Counter(t["status"] for t in tarefas)
    nao_executadas = contagem["pendente"] + contagem["pronta"] + contagem["reprovada"]
    decorrido = _tempo_decorrido(m)
    criticas = m.get("caminho_critico") or []
    idx = ex.indice()
    tempo_critico = sum(
        segundos_entre(idx[c]["inicio"], idx[c]["fim"])
        for c in criticas if c in idx and idx[c].get("inicio") and idx[c].get("fim")
    )
    tokens_total = sum(t.get("tokens") or 0 for t in tarefas)
    tool_uses = sum(t.get("tool_uses") or 0 for t in tarefas)
    por_modelo: dict = {}
    for t in tarefas:
        for h in t.get("historico") or []:
            modelo = h.get("modelo") or t.get("modelo") or "?"
            item = por_modelo.setdefault(modelo, {"tentativas": 0, "tokens": 0})
            item["tentativas"] += 1
            item["tokens"] += h.get("tokens") or 0
            juiz = h.get("juiz") or {}
            if juiz.get("tokens"):
                j = por_modelo.setdefault("juiz", {"tentativas": 0, "tokens": 0})
                j["tentativas"] += 1
                j["tokens"] += juiz["tokens"]

    L = [f"# Resumo — {m.get('titulo') or m['id']}", ""]
    if m.get("objetivo"):
        L += [f"> {m['objetivo']}", ""]
    aprovacao = {"humana": "aprovado pelo usuário", "automatica": "aprovado automaticamente"}.get(
        m.get("aprovacao"), "sem aprovação registrada")
    L += [
        f"Execução `{m['id']}` · plano v{m.get('versao')} · modo {m.get('modo')} ({aprovacao}) · "
        f"encerrada em {m.get('encerrado_em')}",
        "",
        "## Resultado",
        "",
        "| Concluídas | Falharam | Bloqueadas | Não executadas | Total |",
        "|---|---|---|---|---|",
        f"| {contagem['concluida']} | {contagem['falhou']} | {contagem['bloqueada']} | {nao_executadas} | {len(tarefas)} |",
        "",
        "## Tempo e paralelismo",
        "",
        f"- Tempo total: {fmt_duracao(decorrido)}",
        f"- Tempo somado no caminho crítico ({' → '.join(criticas)}): {fmt_duracao(tempo_critico)}",
        f"- Pico de paralelismo: {m.get('pico_paralelo', 0)} subagentes ao mesmo tempo (limite {m.get('max_paralelo')})",
        "",
        "## Consumo",
        "",
        f"- Tokens dos subagentes e juízes: {tokens_total:,}".replace(",", "."),
        f"- Tool uses: {tool_uses}",
        "",
        "| Modelo | Tentativas | Tokens |",
        "|---|---|---|",
    ]
    for modelo, item in sorted(por_modelo.items(), key=lambda kv: -kv[1]["tokens"]):
        L.append(f"| {modelo} | {item['tentativas']} | {item['tokens']:,} |".replace(",", "."))
    L += [
        "",
        "## Tarefas",
        "",
        "| Tarefa | Status | Tentativas | Agente | Duração | Tokens |",
        "|---|---|---|---|---|---|",
    ]
    for t in sorted(tarefas, key=lambda x: (x.get("nivel", 0), tarefas.index(x))):
        duracao = segundos_entre(t["inicio"], t["fim"]) if t.get("inicio") and t.get("fim") else 0
        duracao = duracao or (t.get("duracao_ms") or 0) / 1000
        L.append(
            f"| {t['id']} | {ICONES[t['status']]} {ROTULOS[t['status']]} | {t['tentativas']} | "
            f"{t.get('agente') or '—'} | {fmt_duracao(duracao) if duracao else '—'} | {fmt_tokens(t.get('tokens'))} |"
        )
    retentativas = [
        (t["id"], h) for t in tarefas for h in t.get("historico") or []
        if h.get("resultado") in ("reprovada", "falhou", "devolvida")
    ]
    L += ["", "## Retentativas e falhas", ""]
    if retentativas:
        for tid, h in retentativas:
            L.append(f"- **{tid}**, tentativa {h['tentativa']} ({h.get('agente')}): {h['resultado']}: {h.get('motivo') or 'sem motivo'}")
    else:
        L.append("- Nenhuma: todas as entregas passaram na primeira tentativa.")
    if nao_executadas:
        pendentes = [t["id"] for t in tarefas if t["status"] in ("pendente", "pronta", "reprovada")]
        L.append(f"- Não executadas: {', '.join(pendentes)}")
    L += [
        "",
        "## Aprendizados",
        "",
        "<!-- O orquestrador preenche: o que funcionou, o que travou, como planejar melhor da próxima vez. -->",
        "- _a preencher pelo orquestrador_",
        "",
    ]
    return "\n".join(L)


def cmd_summary(args) -> int:
    def aplicar(ex: Execucao):
        ativos = ex.executando()
        if ativos:
            raise ErroRegra(
                f"Ainda há tarefas executando ({', '.join(t['id'] for t in ativos)}). "
                "Espere terminarem antes de encerrar."
            )
        if ex.meta["estado"] != "encerrado":
            ex.meta["estado"] = "encerrado"
            ex.meta["encerrado_em"] = agora()
            contagem = Counter(t["status"] for t in ex.tarefas)
            ex.evento(
                "encerrado",
                f"Execução encerrada: {contagem['concluida']}/{len(ex.tarefas)} concluídas, "
                f"{contagem['falhou']} falha(s), {contagem['bloqueada']} bloqueada(s).",
            )
        texto = _gerar_resumo(ex)
        escrever_atomico(ex.pasta / "resumo.md", texto)
        return texto

    texto = mutar(args, aplicar)
    print(texto)
    print(f"(gravado em {resolver_pasta(args) / 'resumo.md'})")
    return 0


def cmd_mermaid(args) -> int:
    pasta = resolver_pasta(args)
    with Trava(pasta):
        ex = Execucao.abrir(pasta)
        gerar_mermaid(ex)
    print(f"Diagrama gravado em {pasta / 'dag.md'}")
    return 0


def cmd_serve(args) -> int:
    import http.server

    pasta = resolver_pasta(args)
    if not (pasta / "painel.html").is_file():
        raise ErroRegra(f"Painel não encontrado em {pasta}.")

    class Manipulador(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(pasta), **kw)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", args.porta), Manipulador)
    print(f"Painel em http://127.0.0.1:{args.porta}/painel.html (Ctrl+C para parar)", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    return 0


def cmd_open(args) -> int:
    painel = (resolver_pasta(args) / "painel.html").resolve()
    if not painel.is_file():
        raise ErroRegra(f"Painel não encontrado: {painel}")
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(painel)], check=True)
        elif os.name == "nt":
            os.startfile(str(painel))  # type: ignore[attr-defined]
        elif shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(painel)], check=True)
        else:
            webbrowser.open(painel.as_uri())
    except (OSError, subprocess.CalledProcessError):
        webbrowser.open(painel.as_uri())
    print(f"Painel aberto: {painel}")
    return 0


# ---------------------------------------------------------------------------
# Linha de comando
# ---------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"erro de uso: {message}", file=sys.stderr)
        sys.exit(2)


def montar_parser() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--dir", default=argparse.SUPPRESS, help="pasta da execução (.dag/<id>)")

    p = Parser(prog="dag.py", description="CLI determinística da skill dag (planejamento e execução em DAG).")
    p.add_argument("--dir", default=None, help="pasta da execução (.dag/<id>)")
    sub = p.add_subparsers(dest="comando", metavar="<comando>", parser_class=Parser)
    sub.required = True

    def novo(nome, ajuda):
        return sub.add_parser(nome, help=ajuda, parents=[comum])

    s = novo("init", "cria .dag/<id>/ com estado vazio e o painel")
    s.add_argument("id")
    s.add_argument("--titulo", required=True)
    s.add_argument("--objetivo", default="")
    s.add_argument("--modo", choices=("interativo", "autonomo"), default="interativo")
    s.add_argument("--max-paralelo", type=int, default=4)
    s.add_argument("--max-tentativas", type=int, default=2)
    s.add_argument("--raiz", default=".dag")
    s.set_defaults(funcao=cmd_init)

    s = novo("load", "carrega e valida o rascunho do plano (versão 1)")
    s.add_argument("rascunho")
    s.set_defaults(funcao=cmd_load)

    s = novo("validate", "checagens da fase 3; grava níveis e caminho crítico")
    s.add_argument("--arquivo", help="valida um rascunho sem tocar na execução")
    s.set_defaults(funcao=cmd_validate)

    s = novo("approve", "registra a aprovação do plano")
    s.add_argument("--auto", action="store_true", help="aprovação automática (modo autônomo)")
    s.set_defaults(funcao=cmd_approve)

    s = novo("ready", "lista as tarefas prontas e as vagas livres")
    s.add_argument("--json", action="store_true")
    s.set_defaults(funcao=cmd_ready)

    s = novo("start", "pronta → executando; imprime o nome do agente")
    s.add_argument("tarefa")
    s.add_argument("--agente")
    s.add_argument("--modelo")
    s.set_defaults(funcao=cmd_start)

    s = novo("set-status", "aplica uma transição de status válida")
    s.add_argument("tarefa")
    s.add_argument("status", choices=STATUS)
    s.add_argument("--motivo")
    s.add_argument("--resumo")
    s.add_argument("--agente")
    s.add_argument("--tokens", type=int)
    s.add_argument("--tool-uses", type=int)
    s.add_argument("--duracao-ms", type=int)
    s.add_argument("--arquivos", help="arquivos alterados, separados por vírgula")
    s.add_argument("--juiz-agente")
    s.add_argument("--juiz-veredito", choices=("atende", "nao_atende"))
    s.add_argument("--juiz-tokens", type=int)
    s.add_argument("--forcar", action="store_true", help="aceita arquivos fora do 'escreve'")
    s.set_defaults(funcao=cmd_set_status)

    for nome, ajuda, funcao in (
        ("add-task", "adiciona uma tarefa (replanejamento)", cmd_add_task),
        ("edit", "altera campos de plano de uma tarefa (replanejamento)", cmd_edit),
    ):
        s = novo(nome, ajuda)
        if nome == "edit":
            s.add_argument("tarefa")
        s.add_argument("--json")
        s.add_argument("--arquivo")
        s.add_argument("--motivo")
        s.set_defaults(funcao=funcao)

    s = novo("remove-task", "remove uma tarefa (replanejamento)")
    s.add_argument("tarefa")
    s.add_argument("--motivo")
    s.set_defaults(funcao=cmd_remove_task)

    s = novo("status", "progresso compacto")
    s.add_argument("--json", action="store_true")
    s.set_defaults(funcao=cmd_status)

    novo("summary", "grava resumo.md e encerra a execução").set_defaults(funcao=cmd_summary)
    novo("mermaid", "reescreve dag.md").set_defaults(funcao=cmd_mermaid)

    s = novo("serve", "servidor local para o painel")
    s.add_argument("--porta", type=int, default=8765)
    s.set_defaults(funcao=cmd_serve)

    novo("open", "abre o painel no navegador").set_defaults(funcao=cmd_open)
    return p


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = montar_parser().parse_args(argv)
    try:
        return args.funcao(args)
    except ErroRegra as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    except ErroUso as e:
        print(f"erro de uso: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
