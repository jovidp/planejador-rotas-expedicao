from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from rotas.models import Documento, Parada, Rota, TipoDocumento
from rotas.regras import fmt_hora, nome_usuario, parse_valor, pes_da_pessoa, pessoa_da_loja, status_parada


class PessoaDaLojaTest(SimpleTestCase):
    def test_100000_na_frente_do_pes(self):
        self.assertEqual(pessoa_da_loja("556"), 100000556)
        self.assertEqual(pessoa_da_loja(11), 100000011)
        self.assertEqual(pessoa_da_loja("4147"), 4147)  # ILHA: PES de 4 dígitos já é a pessoa (TOTVS e Alcis)

    def test_pes_a_partir_da_pessoa(self):
        self.assertEqual(pes_da_pessoa("100000457"), "457")
        self.assertEqual(pes_da_pessoa("100000011"), "11")
        self.assertEqual(pes_da_pessoa("4147"), "4147")
        self.assertIsNone(pes_da_pessoa("200000457"))
        self.assertIsNone(pes_da_pessoa(""))


class FmtHoraTest(SimpleTestCase):
    def test_fmt_hora(self):
        casos = {"730": "07:30", "7h30": "07:30", "7": "07:00", "7h": "07:00", "1330": "13:30", "0500": "05:00",
                 "7:3": "07:30", "13.45": "13:45", "2560": "2560", "RETIRADA MATERIAL": "RETIRADA MATERIAL", "": ""}
        for entrada, saida in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(fmt_hora(entrada), saida)


class ParseValorTest(SimpleTestCase):
    def test_parse_valor(self):
        for v in ["1.369,11", "R$ 1.369,11", "1369.11", " 1369,11 "]:
            with self.subTest(v=v):
                self.assertEqual(parse_valor(v), Decimal("1369.11"))
        self.assertIsNone(parse_valor(""))
        self.assertIsNone(parse_valor("abc"))


class NomeUsuarioTest(TestCase):
    def test_nome_usuario(self):
        self.assertEqual(nome_usuario(User(username="beatriz")), "BEATRIZ")
        self.assertEqual(nome_usuario(User(username="jd", first_name="João")), "JOÃO")


class StatusParadaTest(TestCase):
    def setUp(self):
        self.rota = Rota.objects.create(data="2026-10-23", nome="BAIXADA II")

    def parada(self, pes, docs, **carga):
        p = Parada.objects.create(rota=self.rota, pes=pes, loja="X")
        for codigo, numero in docs.items():
            Documento.objects.create(parada=p, tipo=TipoDocumento.objects.get(codigo=codigo), numero=numero,
                                     **(carga if codigo == "wms" else {}))
        return p

    def test_status_parada(self):
        # Sem carga → Carga criada → Expedida → Em atendimento → Atendida
        from django.utils import timezone
        agora, d = timezone.now(), {"t434": "182", "wms": "3636"}
        self.assertEqual(status_parada(self.parada("437", {"t434": "182"})), "sem_carga")
        self.assertEqual(status_parada(self.parada("437", d)), "criada")
        self.assertEqual(status_parada(self.parada("437", d, expedida_em=agora)), "expedida")
        self.assertEqual(status_parada(self.parada("437", d, expedida_em=agora, atendido_por="BEATRIZ", atendido_em=agora)), "em_atendimento")
        self.assertEqual(status_parada(self.parada("437", d, expedida_em=agora, atendido_por="BEATRIZ", atendido_em=agora, concluido_em=agora)), "atendida")
        self.assertEqual(status_parada(self.parada("437", {})), "sem_carga")  # loja sem nada lançado também é "sem carga"

    def test_status_sem_pes(self):
        self.assertIsNone(status_parada(self.parada("", {"t434": "658"})))
