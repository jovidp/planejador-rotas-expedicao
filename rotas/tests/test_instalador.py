import os
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

import instalador


class InstaladorTest(SimpleTestCase):
    def test_script_inicializacao_roda_escondido(self):
        s = instalador.script_inicializacao(Path(r"C:\RotasExpedicao\RotasServidor.exe"), 8000)
        self.assertEqual(s, 'CreateObject("WScript.Shell").Run """C:\RotasExpedicao\RotasServidor.exe"" servir --porta 8000", 0, False\r\n')

    def test_backup_banco(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertIsNone(instalador.backup_banco(d))  # primeira instalação: nada para copiar
            (d / "db.sqlite3").write_bytes(b"dados")
            b = instalador.backup_banco(d)
            self.assertEqual((b.parent.name, b.read_bytes()), ("backups", b"dados"))

    def test_modo_pelo_nome_do_exe(self):
        self.assertEqual(instalador.modo_padrao(Path(r"C:\RotasExpedicao\RotasServidor.exe")), "servir")
        self.assertEqual(instalador.modo_padrao(Path(r"C:\Users\x\Downloads\InstalarRotas.exe")), "instalar")


class AtualizacaoAutomaticaTest(SimpleTestCase):
    def arquivos(self, d, idade_instalado, idade_novo):
        import time
        instalado, novo = Path(d, "RotasServidor.exe"), Path(d, "pasta", "InstalarRotas.exe")
        novo.parent.mkdir()
        instalado.write_bytes(b"v1")
        novo.write_bytes(b"v2")
        agora = time.time()
        os.utime(instalado, (agora - idade_instalado, agora - idade_instalado))
        os.utime(novo, (agora - idade_novo, agora - idade_novo))
        return instalado, novo

    def test_exe_mais_novo_na_pasta_e_atualizacao(self):
        with tempfile.TemporaryDirectory() as d:
            instalado, novo = self.arquivos(d, idade_instalado=3600, idade_novo=60)
            self.assertEqual(instalador.atualizacao_disponivel(novo.parent, instalado), novo)

    def test_mesmo_exe_nao_atualiza(self):
        with tempfile.TemporaryDirectory() as d:
            instalado, novo = self.arquivos(d, idade_instalado=60, idade_novo=60)  # copy2 mantém a data do original
            self.assertIsNone(instalador.atualizacao_disponivel(novo.parent, instalado))

    def test_exe_ainda_sendo_copiado_espera(self):
        with tempfile.TemporaryDirectory() as d:
            instalado, novo = self.arquivos(d, idade_instalado=3600, idade_novo=2)
            self.assertIsNone(instalador.atualizacao_disponivel(novo.parent, instalado))

    def test_pasta_sem_exe_ou_inexistente(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(instalador.atualizacao_disponivel(Path(d), Path(d, "RotasServidor.exe")))
            self.assertIsNone(instalador.atualizacao_disponivel(Path(d, "nao-existe"), Path(d, "RotasServidor.exe")))

    def test_pasta_de_atualizacao_guardada_na_instalacao(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(instalador.pasta_atualizacao(Path(d)))
            instalador.guardar_pasta_atualizacao(Path(d), Path(r"C:\Users\x\Desktop\planejador"))
            self.assertEqual(instalador.pasta_atualizacao(Path(d)), Path(r"C:\Users\x\Desktop\planejador"))


class CopiaParaInstalarTest(SimpleTestCase):
    def test_copia_local_mantem_data(self):
        """O instalador da pasta compartilhada é copiado antes de rodar: o original fica livre para ser trocado."""
        with tempfile.TemporaryDirectory() as d:
            novo = Path(d, "pasta", "InstalarRotas.exe")
            novo.parent.mkdir()
            novo.write_bytes(b"v2")
            os.utime(novo, (1700000000, 1700000000))
            copia = instalador.copiar_para_instalar(novo, Path(d, "dados"))
            self.assertEqual((copia.parent.name, copia.read_bytes(), copia.stat().st_mtime), ("atualizacao", b"v2", 1700000000))

    def test_assinatura_muda_enquanto_copia(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d, "InstalarRotas.exe")
            arq.write_bytes(b"pedaco")
            a = instalador.assinatura(arq)
            arq.write_bytes(b"pedaco e mais um pedaco")
            self.assertNotEqual(a, instalador.assinatura(arq))
            self.assertIsNone(instalador.assinatura(Path(d, "nao-existe")))


class PararServidorTest(SimpleTestCase):
    def test_para_so_o_servidor_desta_instalacao(self):
        """Outra instalação na mesma máquina (ex.: a de teste) não pode derrubar o servidor de verdade.
        O Windows pode mostrar o caminho curto (JOAO~1.DUA) ou o longo: compara pelo arquivo, não pelo texto."""
        import ctypes
        with tempfile.TemporaryDirectory() as d:
            alvo, outro = Path(d, "a", "RotasServidor.exe"), Path(d, "b", "RotasServidor.exe")
            for p in (alvo, outro):
                p.parent.mkdir()
                p.write_bytes(b"x")
            buf = ctypes.create_unicode_buffer(500)
            ctypes.windll.kernel32.GetShortPathNameW(str(alvo), buf, 500)
            lista = f"11|{alvo}\n12|{outro}\n13|{buf.value}\n14|\n"
            self.assertEqual(instalador.pids_da_instalacao(lista, alvo), ["11", "13"])
