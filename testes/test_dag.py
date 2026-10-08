"""
Testes da CLI da skill `dag` (critérios de aceite da spec, seção 9).

Rodar:  python3 -m unittest testes/test_dag.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skill"
DAG = SKILL / "scripts" / "dag.py"
SIMULAR = SKILL / "scripts" / "simular.py"


def tarefa(tid, deps=(), escreve=(), le=(), criterio="pronto quando o arquivo existir", **extra):
    t = {
        "id": tid,
        "nome": f"Tarefa {tid}",
        "depende_de": list(deps),
        "escreve": list(escreve),
        "le": list(le),
        "criterio_pronto": criterio,
        "modelo": "haiku",
    }
    t.update(extra)
    return t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dag-teste-"))
        self.env = {k: v for k, v in os.environ.items() if k != "DAG_DIR"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dag(self, *args, rc=0):
        r = subprocess.run([sys.executable, str(DAG), *args], cwd=self.tmp, env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        if rc is not None:
            self.assertEqual(r.returncode, rc, f"dag.py {' '.join(args)}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
        return r

    def rascunho(self, tarefas, nome="plano.json"):
        caminho = self.tmp / nome
        caminho.write_text(json.dumps({"tarefas": tarefas}, ensure_ascii=False), encoding="utf-8")
        return str(caminho)

    def criar(self, tarefas, aprovar=True, max_paralelo=4, max_tentativas=2):
        self.dag("init", "exec", "--titulo", "Teste", "--max-paralelo", str(max_paralelo),
                 "--max-tentativas", str(max_tentativas))
        self.dag("load", self.rascunho(tarefas))
        if aprovar:
            self.dag("approve")

    def estado(self):
        return json.loads((self.tmp / ".dag" / "exec" / "dag.json").read_text(encoding="utf-8"))

    def status_de(self, tid):
        return next(t for t in self.estado()["tarefas"] if t["id"] == tid)["status"]

    def eventos(self):
        linhas = (self.tmp / ".dag" / "exec" / "eventos.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in linhas]

    def saida(self, r):
        return r.stdout + r.stderr


class TestValidacao(Base):
    def test_1_ciclo_rejeitado_aponta_tarefas(self):
        plano = [tarefa("a"), tarefa("b", ["a", "d"]), tarefa("c", ["b"]), tarefa("d", ["c"])]
        r = self.dag("validate", "--arquivo", self.rascunho(plano), rc=1)
        texto = self.saida(r)
        self.assertIn("Ciclo", texto)
        for tid in ("b", "c", "d"):
            self.assertRegex(texto, rf"\b{tid}\b")
        self.assertIn("→", texto)

    def test_2_conflito_de_escrita_paralelo(self):
        plano = [tarefa("a"), tarefa("b", ["a"], escreve=["saida/x.json"]),
                 tarefa("c", ["a"], escreve=["saida/*.json"])]
        texto = self.saida(self.dag("validate", "--arquivo", self.rascunho(plano), rc=1))
        self.assertIn("Conflito de escrita", texto)
        self.assertIn("'b'", texto)
        self.assertIn("'c'", texto)
        self.assertIn("dependência", texto)
        self.assertIn("funda", texto)

    def test_2b_conflito_some_quando_serializado(self):
        plano = [tarefa("a"), tarefa("b", ["a"], escreve=["saida/x.json"]),
                 tarefa("c", ["b"], escreve=["saida/*.json"])]
        self.dag("validate", "--arquivo", self.rascunho(plano), rc=0)

    def test_3_tarefa_sem_criterio_rejeitada(self):
        plano = [tarefa("a"), tarefa("b", ["a"], criterio="")]
        texto = self.saida(self.dag("validate", "--arquivo", self.rascunho(plano), rc=1))
        self.assertIn("criterio_pronto", texto)

    def test_sem_tarefa_inicial(self):
        plano = [tarefa("a", ["b"]), tarefa("b", ["a"])]
        texto = self.saida(self.dag("validate", "--arquivo", self.rascunho(plano), rc=1))
        self.assertIn("Nenhuma tarefa inicial", texto)

    def test_niveis_e_caminho_critico(self):
        plano = [tarefa("a"), tarefa("b", ["a"]), tarefa("c", ["a"]), tarefa("d", ["b"]), tarefa("e", ["c", "d"])]
        self.criar(plano, aprovar=False)
        e = self.estado()
        niveis = {t["id"]: t["nivel"] for t in e["tarefas"]}
        self.assertEqual(niveis, {"a": 0, "b": 1, "c": 1, "d": 2, "e": 3})
        self.assertEqual(e["meta"]["caminho_critico"], ["a", "b", "d", "e"])
        self.assertEqual(e["meta"]["total_niveis"], 4)

    def test_exemplo_encarte_valido(self):
        r = self.dag("validate", "--arquivo", str(SKILL / "references" / "exemplo-encarte.json"))
        self.assertIn("12 tarefas", r.stdout)


class TestSobreposicao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dag_mod", DAG)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_casos(self):
        s = self.m.sobrepoe
        self.assertTrue(s("saida/a.json", "./saida/a.json"))
        self.assertTrue(s("saida/", "saida/fotos/x.png"))
        self.assertTrue(s("saida", "saida/x.png"))
        self.assertTrue(s("saida/*.json", "saida/a.json"))
        self.assertFalse(s("saida/*.json", "saida/sub/a.json"))
        self.assertTrue(s("saida/**/*.json", "saida/sub/a.json"))
        self.assertTrue(s("saida/fotos/*.png", "saida/**"))
        self.assertFalse(s("saida/feed/*.png", "saida/stories/*.png"))
        self.assertFalse(s("saida/*.json", "saida/*.png"))
        self.assertFalse(s("saida/a.json", "saida/b.json"))


class TestExecucao(Base):
    def test_4_transicao_invalida_rejeitada(self):
        self.criar([tarefa("a"), tarefa("b", ["a"])])
        texto = self.saida(self.dag("set-status", "b", "concluida", rc=1))
        self.assertIn("Transição inválida", texto)
        self.assertEqual(self.status_de("b"), "pendente")

    def test_5_falha_bloqueia_descendentes_e_independentes_seguem(self):
        plano = [tarefa("a"), tarefa("b", ["a"]), tarefa("c", ["b"]), tarefa("d", ["c"]), tarefa("x")]
        self.criar(plano, max_tentativas=2)
        self.dag("start", "a")
        self.dag("set-status", "a", "concluida")
        self.dag("start", "b")
        self.dag("set-status", "b", "reprovada", "--motivo", "faltou item")
        self.assertEqual(self.status_de("b"), "reprovada")
        self.dag("ready")
        self.assertEqual(self.status_de("b"), "pronta")
        self.dag("start", "b")
        self.dag("set-status", "b", "reprovada", "--motivo", "faltou de novo")
        self.assertEqual(self.status_de("b"), "falhou")
        self.assertEqual(self.status_de("c"), "bloqueada")
        self.assertEqual(self.status_de("d"), "bloqueada")
        self.assertEqual(self.status_de("x"), "pronta")
        self.dag("start", "x")
        self.dag("set-status", "x", "concluida")
        self.assertEqual(self.status_de("x"), "concluida")

    def test_7_replanejar_incrementa_versao_e_registra(self):
        self.criar([tarefa("a"), tarefa("b", ["a"])])
        self.assertEqual(self.estado()["meta"]["versao"], 1)
        nova = json.dumps(tarefa("c", ["a"], escreve=["saida/c.txt"]))
        self.dag("add-task", "--json", nova, "--motivo", "faltou gerar o texto")
        self.assertEqual(self.estado()["meta"]["versao"], 2)
        rep = [e for e in self.eventos() if e["tipo"] == "replanejado"]
        self.assertEqual(len(rep), 1)
        self.assertEqual(rep[0]["motivo"], "faltou gerar o texto")
        self.assertEqual(rep[0]["versao"], 2)
        # Replanejamento inválido (ciclo) não grava nada.
        self.dag("edit", "a", "--json", json.dumps({"depende_de": ["c"]}), "--motivo", "teste", rc=1)
        self.assertEqual(self.estado()["meta"]["versao"], 2)
        self.dag("edit", "b", "--json", json.dumps({"nome": "Outro nome"}), "--motivo", "renomear")
        self.assertEqual(self.estado()["meta"]["versao"], 3)

    def test_start_recusado_antes_do_approve(self):
        self.criar([tarefa("a")], aprovar=False)
        texto = self.saida(self.dag("start", "a", rc=1))
        self.assertIn("approve", texto)
        self.assertEqual(self.status_de("a"), "pronta")

    def test_start_recusado_acima_do_max_paralelo(self):
        self.criar([tarefa("a"), tarefa("b"), tarefa("c")], max_paralelo=2)
        self.dag("start", "a")
        self.dag("start", "b")
        texto = self.saida(self.dag("start", "c", rc=1))
        self.assertIn("Limite de paralelismo", texto)
        self.assertEqual(self.estado()["meta"]["pico_paralelo"], 2)

    def test_arquivos_fora_do_escreve(self):
        self.criar([tarefa("a", escreve=["saida/a/*.png"])])
        self.dag("start", "a")
        texto = self.saida(self.dag("set-status", "a", "concluida", "--arquivos", "saida/a/1.png,outro.txt", rc=1))
        self.assertIn("outro.txt", texto)
        self.assertEqual(self.status_de("a"), "executando")
        self.dag("set-status", "a", "concluida", "--arquivos", "saida/a/1.png,outro.txt", "--forcar")
        self.assertEqual(self.status_de("a"), "concluida")
        self.assertTrue(any(e["tipo"] == "aviso" for e in self.eventos()))

    def test_arquivos_absolutos_viram_relativos(self):
        self.criar([tarefa("a", escreve=["saida/a.txt"])])
        self.dag("start", "a")
        raiz = os.path.realpath(self.tmp)
        self.dag("set-status", "a", "concluida", "--arquivos", os.path.join(raiz, "saida", "a.txt"))
        t = {x["id"]: x for x in self.estado()["tarefas"]}["a"]
        self.assertEqual(t["status"], "concluida")
        self.assertEqual(t["arquivos_alterados"], ["saida/a.txt"])

    def test_edit_reabre_falhou_e_desbloqueia(self):
        self.criar([tarefa("a"), tarefa("b", ["a"]), tarefa("c", ["b"])], max_tentativas=1)
        self.dag("start", "a")
        self.dag("set-status", "a", "reprovada", "--motivo", "errado")
        self.assertEqual(self.status_de("a"), "falhou")
        self.assertEqual(self.status_de("c"), "bloqueada")
        self.dag("edit", "a", "--json", json.dumps({"criterio_pronto": "critério mais claro"}), "--motivo", "ajuste")
        e = {t["id"]: t for t in self.estado()["tarefas"]}
        self.assertEqual(e["a"]["status"], "pronta")
        self.assertEqual(e["a"]["tentativas"], 0)
        self.assertEqual(e["b"]["status"], "pendente")
        self.assertEqual(e["c"]["status"], "pendente")

    def test_devolvida_nao_gasta_tentativa(self):
        self.criar([tarefa("a")])
        self.dag("start", "a")
        self.dag("set-status", "a", "pendente", rc=2)  # exige --motivo
        self.dag("set-status", "a", "pendente", "--motivo", "precisa alterar arquivo fora da lista")
        t = self.estado()["tarefas"][0]
        self.assertEqual(t["tentativas"], 0)
        self.assertEqual(t["status"], "pronta")
        self.assertEqual(t["historico"][-1]["resultado"], "devolvida")

    def test_metricas_somadas_com_juiz(self):
        self.criar([tarefa("a")])
        self.dag("start", "a", "--agente", "exec-a")
        self.dag("set-status", "a", "reprovada", "--motivo", "x", "--tokens", "1000", "--tool-uses", "3",
                 "--duracao-ms", "5000", "--juiz-agente", "juiz-01", "--juiz-veredito", "nao_atende", "--juiz-tokens", "200")
        self.dag("ready")
        self.dag("start", "a")
        self.dag("set-status", "a", "concluida", "--tokens", "500", "--tool-uses", "2", "--duracao-ms", "1000")
        t = self.estado()["tarefas"][0]
        self.assertEqual(t["tokens"], 1700)
        self.assertEqual(t["tool_uses"], 5)
        self.assertEqual(t["duracao_ms"], 6000)
        self.assertEqual(t["historico"][0]["juiz"]["veredito"], "nao_atende")
        self.assertEqual(t["historico"][0]["agente"], "exec-a")
        self.assertEqual(t["historico"][1]["agente"], "sub-01")

    def test_arquivos_gerados(self):
        self.criar([tarefa("a"), tarefa("b", ["a"])])
        pasta = self.tmp / ".dag" / "exec"
        js = (pasta / "dag.data.js").read_text(encoding="utf-8")
        self.assertTrue(js.startswith("window.DAG_RECEBER"))
        self.assertIn('"tarefas"', js)
        md = (pasta / "dag.md").read_text(encoding="utf-8")
        self.assertIn("flowchart LR", md)
        self.assertEqual((self.tmp / ".dag" / "atual").read_text(encoding="utf-8").strip(), "exec")
        self.assertFalse((pasta / ".lock").exists())

    def test_load_duas_vezes_recusado(self):
        self.criar([tarefa("a")], aprovar=False)
        self.dag("load", self.rascunho([tarefa("z")], "outro.json"), rc=1)

    def test_summary_encerra(self):
        self.criar([tarefa("a")])
        self.dag("start", "a")
        self.dag("summary", rc=1)  # ainda executando
        self.dag("set-status", "a", "concluida", "--tokens", "100")
        self.dag("summary")
        self.assertEqual(self.estado()["meta"]["estado"], "encerrado")
        self.assertTrue((self.tmp / ".dag" / "exec" / "resumo.md").is_file())


class TestSimulacao(Base):
    def test_simular_rapido_do_inicio_ao_fim(self):
        r = subprocess.run([sys.executable, str(SIMULAR), "--rapido"], cwd=self.tmp, env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        e = json.loads((self.tmp / ".dag" / "demo-encarte" / "dag.json").read_text(encoding="utf-8"))
        self.assertEqual(e["meta"]["estado"], "encerrado")
        self.assertGreaterEqual(e["meta"]["pico_paralelo"], 3)
        self.assertTrue(all(t["status"] == "concluida" for t in e["tarefas"]))
        montar = next(t for t in e["tarefas"] if t["id"] == "montar")
        self.assertEqual(montar["tentativas"], 2)

    def test_simular_com_falha_bloqueia_entrega(self):
        r = subprocess.run([sys.executable, str(SIMULAR), "--rapido", "--falha"], cwd=self.tmp, env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        e = {t["id"]: t for t in json.loads(
            (self.tmp / ".dag" / "demo-encarte" / "dag.json").read_text(encoding="utf-8"))["tarefas"]}
        self.assertEqual(e["render-a4"]["status"], "falhou")
        self.assertEqual(e["entregar"]["status"], "bloqueada")
        self.assertEqual(e["render-feed"]["status"], "concluida")


if __name__ == "__main__":
    unittest.main()
