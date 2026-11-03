from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from rotas import acoes
from rotas.regras import etapa_carga
from rotas.models import Documento, Historico, Loja, Parada, Rota, RotaPadrao, Veiculo


class AcoesTest(TestCase):
    def setUp(self):
        self.joao = User.objects.create_user("joao", first_name="João")
        self.beatriz = User.objects.create_user("beatriz")
        self.rota = Rota.objects.create(data=date(2026, 10, 23), nome="BAIXADA II", saida="05:00")
        self.p = [Parada.objects.create(rota=self.rota, ordem=i + 1, loja=n, pes=pes)
                  for i, (n, pes) in enumerate([("FÁBRICA", ""), ("MAGÉ II", "535"), ("PIABETÁ", "437"), ("FÁBRICA", "")])]

    def ordem(self):
        return list(self.rota.paradas.values_list("loja", flat=True))

    def test_atender_grava_usuario_e_hora(self):
        doc = acoes.editar_documento(self.beatriz, self.p[2], "t434", "182")
        acoes.atender(self.beatriz, doc, True)
        doc.refresh_from_db()
        self.assertEqual(doc.atendido_por, "BEATRIZ")
        self.assertIsNotNone(doc.atendido_em)
        n = Historico.objects.count()
        acoes.atender(self.beatriz, doc, False)
        doc.refresh_from_db()
        self.assertEqual((doc.atendido_por, doc.atendido_em), ("", None))
        self.assertEqual(Historico.objects.count(), n + 1)
        self.assertIn("desfez", Historico.objects.last().acao)

    def test_apagar_numero_limpa_atendimento(self):
        doc = acoes.editar_documento(self.beatriz, self.p[2], "t434", "182")
        acoes.atender(self.beatriz, doc, True)
        self.assertIsNone(acoes.editar_documento(self.beatriz, self.p[2], "t434", ""))
        self.assertFalse(Documento.objects.filter(parada=self.p[2]).exists())

    def test_trocar_numero_limpa_atendimento(self):
        doc = acoes.editar_documento(self.beatriz, self.p[2], "t434", "182")
        acoes.atender(self.beatriz, doc, True)
        doc = acoes.editar_documento(self.beatriz, self.p[2], "t434", "183")
        self.assertEqual((doc.numero, doc.atendido_por, doc.atendido_em), ("183", "", None))

    def test_varias_transacoes_na_mesma_loja_com_atendimento_separado(self):
        primeiro = acoes.editar_documento(self.beatriz, self.p[2], "t434", "182, 190")
        docs = list(self.p[2].documentos.order_by("id"))
        self.assertEqual([d.numero for d in docs], ["182", "190"])
        self.assertEqual(primeiro, docs[0])
        acoes.atender(self.beatriz, docs[0], True)
        docs[1].refresh_from_db()
        self.assertEqual(docs[1].atendido_por, "")  # a outra continua faltando
        self.assertEqual(Historico.objects.filter(acao__startswith="BEATRIZ lançou 434").count(), 2)

    def test_tirar_um_numero_mantem_o_atendimento_do_outro(self):
        acoes.editar_documento(self.beatriz, self.p[2], "t434", "182 190")
        d182 = self.p[2].documentos.get(numero="182")
        acoes.atender(self.beatriz, d182, True)
        acoes.editar_documento(self.beatriz, self.p[2], "t434", "182 195")
        self.assertEqual(sorted(self.p[2].documentos.values_list("numero", flat=True)), ["182", "195"])
        d182.refresh_from_db()
        self.assertEqual(d182.atendido_por, "BEATRIZ")
        self.assertTrue(Historico.objects.filter(acao="BEATRIZ apagou 434 190 de PIABETÁ").exists())

    def test_numero_repetido_conta_uma_vez(self):
        acoes.editar_documento(self.beatriz, self.p[2], "t434", "182 182")
        self.assertEqual(self.p[2].documentos.count(), 1)

    def test_varias_cargas_cada_uma_com_suas_etapas(self):
        from rotas.regras import status_parada
        acoes.editar_documento(self.beatriz, self.p[2], "wms", "3636 3637")
        c1, c2 = self.p[2].documentos.order_by("id")
        for _ in range(3):  # só a 3636 chega a atendida
            c1 = acoes.avancar_carga(self.beatriz, c1)
        self.assertEqual((etapa_carga(c1), etapa_carga(c2)), ("atendida", "criada"))
        self.assertEqual(status_parada(self.p[2]), "criada")  # a loja mostra a carga mais atrasada
        for _ in range(3):
            c2 = acoes.avancar_carga(self.beatriz, c2)
        self.assertEqual(status_parada(self.p[2]), "atendida")

    def test_confirmar_retirada_grava_quem_e_volume_novo_recomeca(self):
        p = self.p[2]
        with self.assertRaises(ValueError):
            acoes.confirmar_retirada(self.beatriz, p, "rt", True)  # sem volume não confirma
        acoes.editar_parada(self.beatriz, p, "rt", "5")
        acoes.confirmar_retirada(self.beatriz, p, "rt", True)
        p.refresh_from_db()
        self.assertEqual(p.rt_por, "BEATRIZ")
        self.assertIsNotNone(p.rt_em)
        self.assertTrue(Historico.objects.filter(acao="BEATRIZ confirmou retirada RT 5 de PIABETÁ").exists())
        acoes.editar_parada(self.beatriz, p, "rt", "7")  # outro volume: confirmação recomeça
        p.refresh_from_db()
        self.assertEqual((p.rt_por, p.rt_em), ("", None))
        acoes.editar_parada(self.beatriz, p, "cx", "2")
        acoes.confirmar_retirada(self.joao, p, "cx", True)
        acoes.confirmar_retirada(self.joao, p, "cx", False)
        p.refresh_from_db()
        self.assertEqual(p.cx_por, "")
        with self.assertRaises(ValueError):
            acoes.confirmar_retirada(self.joao, p, "frete_envio", True)

    def carga_atendida(self):
        doc = acoes.editar_documento(self.beatriz, self.p[2], "wms", "3636")
        for _ in range(3):  # expedida → em atendimento → atendida
            doc = acoes.avancar_carga(self.beatriz, doc)
        return doc

    def test_fluxo_da_carga(self):
        doc = acoes.editar_documento(self.beatriz, self.p[2], "wms", "3636")
        self.assertEqual(etapa_carga(doc), "criada")
        doc = acoes.avancar_carga(self.joao, doc)
        self.assertEqual((etapa_carga(doc), doc.expedida_por), ("expedida", "JOÃO"))
        self.assertIsNotNone(doc.expedida_em)
        doc = acoes.avancar_carga(self.beatriz, doc)
        self.assertEqual((etapa_carga(doc), doc.atendido_por), ("em_atendimento", "BEATRIZ"))  # aí vem o usuário
        self.assertIsNotNone(doc.atendido_em)
        doc = acoes.avancar_carga(self.beatriz, doc)
        self.assertEqual((etapa_carga(doc), doc.concluido_por), ("atendida", "BEATRIZ"))
        self.assertIsNotNone(doc.concluido_em)
        with self.assertRaises(ValueError):
            acoes.avancar_carga(self.beatriz, doc)  # já está no fim
        textos = list(Historico.objects.filter(campo="wms").values_list("acao", flat=True))
        self.assertTrue(any("expedida" in t for t in textos) and any("iniciou o atendimento" in t for t in textos)
                        and any("concluiu o atendimento" in t for t in textos))

    def test_voltar_etapa(self):
        doc = self.carga_atendida()
        doc = acoes.voltar_carga(self.joao, doc)
        self.assertEqual((etapa_carga(doc), doc.concluido_em, doc.concluido_por), ("em_atendimento", None, ""))
        doc = acoes.voltar_carga(self.joao, doc)
        self.assertEqual((etapa_carga(doc), doc.atendido_por), ("expedida", ""))
        doc = acoes.voltar_carga(self.joao, doc)
        self.assertEqual((etapa_carga(doc), doc.expedida_por), ("criada", ""))
        with self.assertRaises(ValueError):
            acoes.voltar_carga(self.joao, doc)
        self.assertIn("voltou a carga", Historico.objects.last().acao)

    def test_trocar_numero_da_carga_reinicia_fluxo(self):
        self.carga_atendida()
        doc = acoes.editar_documento(self.beatriz, self.p[2], "wms", "3640")
        self.assertEqual((etapa_carga(doc), doc.expedida_em, doc.atendido_por, doc.concluido_em), ("criada", None, "", None))

    def test_carga_usa_etapas_e_nao_confirmar_atendimento(self):
        doc = acoes.editar_documento(self.beatriz, self.p[2], "wms", "3636")
        with self.assertRaises(ValueError):
            acoes.atender(self.beatriz, doc, True)
        doc434 = acoes.editar_documento(self.beatriz, self.p[2], "t434", "182")
        with self.assertRaises(ValueError):
            acoes.avancar_carga(self.beatriz, doc434)

    def test_mover_rota_conta_e_registra(self):
        acoes.mover_rota(self.joao, self.rota, date(2026, 10, 24))
        acoes.mover_rota(self.joao, self.rota, date(2026, 10, 26))
        self.rota.refresh_from_db()
        self.assertEqual(self.rota.remarcacoes, 2)
        self.assertEqual(self.rota.data, date(2026, 10, 26))
        self.assertIn("moveu de 24/10 para 26/10", Historico.objects.last().acao)

    def test_mover_mesmo_dia_recusa(self):
        with self.assertRaises(ValueError):
            acoes.mover_rota(self.joao, self.rota, date(2026, 10, 23))
        self.rota.refresh_from_db()
        self.assertEqual(self.rota.remarcacoes, 0)

    def test_cancelar_reativar(self):
        acoes.cancelar_rota(self.joao, self.rota)
        self.assertEqual(Rota.objects.get().status, Rota.CANCELADA)
        acoes.reativar_rota(self.joao, self.rota)
        self.assertEqual(Rota.objects.get().status, Rota.ATIVA)
        self.assertEqual(Historico.objects.count(), 2)

    def test_remover_parada_registra(self):
        acoes.remover_parada(self.joao, self.p[2])
        self.assertEqual(self.ordem(), ["FÁBRICA", "MAGÉ II", "FÁBRICA"])
        self.assertEqual(list(self.rota.paradas.values_list("ordem", flat=True)), [1, 2, 3])
        self.assertIn("removeu a parada PIABETÁ", Historico.objects.last().acao)

    def test_mover_parada(self):
        acoes.mover_parada(self.joao, self.p[2], -1)
        self.assertEqual(self.ordem(), ["FÁBRICA", "PIABETÁ", "MAGÉ II", "FÁBRICA"])
        self.assertTrue(Historico.objects.filter(acao__contains="PIABETÁ").exists())

    def test_fabrica_fixa(self):
        for f in [lambda: acoes.mover_parada(self.joao, self.p[0], 1),
                  lambda: acoes.mover_parada(self.joao, self.p[3], -1),
                  lambda: acoes.mover_parada(self.joao, self.p[1], -1),
                  lambda: acoes.mover_parada(self.joao, self.p[2], 1),
                  lambda: acoes.remover_parada(self.joao, self.p[0])]:
            with self.assertRaises(ValueError):
                f()
        self.assertEqual(self.ordem(), ["FÁBRICA", "MAGÉ II", "PIABETÁ", "FÁBRICA"])

    def test_adicionar_parada_antes_da_fabrica(self):
        acoes.adicionar_parada(self.joao, self.rota)
        self.assertEqual(self.ordem(), ["FÁBRICA", "MAGÉ II", "PIABETÁ", "", "FÁBRICA"])
        self.assertTrue(Historico.objects.filter(acao__contains="adicionou").exists())

    def test_conflito_guarda_os_dois(self):
        acoes.editar_rota(self.joao, self.rota, "saida", "6")
        acoes.editar_rota(self.beatriz, self.rota, "saida", "700")
        self.rota.refresh_from_db()
        self.assertEqual(self.rota.saida, "07:00")
        self.assertEqual(list(Historico.objects.values_list("anterior", "novo")), [("05:00", "06:00"), ("06:00", "07:00")])

    def test_editar_sem_mudanca_nao_registra(self):
        acoes.editar_rota(self.joao, self.rota, "saida", "5")
        self.assertEqual(Historico.objects.count(), 0)

    def test_campo_invalido(self):
        with self.assertRaises(ValueError):
            acoes.editar_rota(self.joao, self.rota, "status", "x")
        with self.assertRaises(ValueError):
            acoes.editar_parada(self.joao, self.p[1], "nf_feita", "1")

    def test_editar_parada_frete_e_rastreador(self):
        acoes.editar_parada(self.joao, self.p[1], "frete_envio", "R$ 1.369,11")
        acoes.editar_rota(self.joao, self.rota, "rastreador", "R:71")
        self.p[1].refresh_from_db()
        self.rota.refresh_from_db()
        self.assertEqual(self.p[1].frete_envio, Decimal("1369.11"))
        self.assertEqual(self.rota.rastreador, "71")

    def test_loja_do_cadastro_preenche(self):
        Loja.objects.create(nome="WEST III", pes="351", emp="230", horario="08:30", frete_accelo=Decimal("627.27"), frete_atego=Decimal("576.95"))
        acoes.editar_parada(self.joao, self.p[1], "horario", "10:00")
        p = acoes.editar_parada(self.joao, self.p[1], "loja", "west iii")
        self.assertEqual((p.loja, p.pes, p.emp, p.horario, p.frete_envio), ("WEST III", "351", "230", "10:00", None))  # sem placa: sem frete
        # ABC1234 já vem da frota cadastrada (migration 0005)
        acoes.editar_rota(self.joao, self.rota, "veiculo", "abc-1234")
        p = acoes.editar_parada(self.joao, self.p[2], "loja", "WEST III")
        self.assertEqual((p.frete_envio, p.frete_retorno), (Decimal("576.95"), Decimal("576.95")))

    def test_placa_define_frete(self):
        Loja.objects.create(nome="PIABETÁ", pes="437", frete_accelo=Decimal("1105.36"), frete_atego=Decimal("876.91"))
        # ABC1234 já vem da frota cadastrada (migration 0005)
        # BRA2E19 já vem da frota cadastrada (migration 0005)
        acoes.editar_rota(self.joao, self.rota, "veiculo", "abc-1234")
        self.rota.refresh_from_db()
        self.assertEqual(self.rota.veiculo, "ABC1234")
        self.p[2].refresh_from_db()
        self.assertEqual((self.p[2].frete_envio, self.p[2].frete_retorno), (Decimal("876.91"), Decimal("876.91")))
        self.p[1].refresh_from_db()
        self.assertIsNone(self.p[1].frete_envio)  # MAGÉ II não está no cadastro deste teste
        self.assertIn("ATEGO", Historico.objects.last().acao)
        acoes.editar_rota(self.joao, self.rota, "veiculo", "BRA2E19")
        self.p[2].refresh_from_db()
        self.assertEqual(self.p[2].frete_envio, Decimal("1105.36"))
        acoes.editar_rota(self.joao, self.rota, "veiculo", "AAA0000")  # placa sem cadastro: frete fica como estava
        self.p[2].refresh_from_db()
        self.assertEqual(self.p[2].frete_envio, Decimal("1105.36"))

    def test_varios_ajudantes(self):
        acoes.editar_rota(self.joao, self.rota, "ajudantes", "VITOR, PEDRO;  carlos /  ")
        self.rota.refresh_from_db()
        self.assertEqual(self.rota.ajudantes, "VITOR / PEDRO / carlos")

    def test_duplicar_rota(self):
        acoes.editar_rota(self.joao, self.rota, "motorista", "LEANDRO")
        acoes.editar_documento(self.joao, self.p[2], "t434", "182")
        nova = acoes.duplicar_rota(self.joao, self.rota)
        self.assertEqual((nova.data, nova.nome, nova.saida, nova.motorista, nova.status), (self.rota.data, "BAIXADA II (2)", "05:00", "", Rota.ATIVA))
        self.assertEqual(list(nova.paradas.values_list("loja", "pes")), list(self.rota.paradas.values_list("loja", "pes")))
        self.assertFalse(Documento.objects.filter(parada__rota=nova).exists())  # documentos e NF não vão para a cópia
        self.assertEqual(acoes.duplicar_rota(self.joao, self.rota).nome, "BAIXADA II (3)")
        self.assertTrue(Historico.objects.filter(rota=self.rota, acao__contains="duplicou").exists())
        self.assertTrue(Historico.objects.filter(rota=nova, acao__contains="cópia de BAIXADA II").exists())

    def test_inverter_rota(self):
        adicional = acoes.adicionar_parada(self.joao, self.rota)
        acoes.editar_parada(self.joao, adicional, "loja", "LOTE XV")
        acoes.inverter_rota(self.joao, self.rota)
        self.assertEqual(self.ordem(), ["FÁBRICA", "LOTE XV", "PIABETÁ", "MAGÉ II", "FÁBRICA"])
        self.assertEqual(list(self.rota.paradas.values_list("ordem", flat=True)), [1, 2, 3, 4, 5])
        self.assertIn("inverteu", Historico.objects.last().acao)

    def test_criar_rota_pelo_cadastro(self):
        rp = RotaPadrao.objects.create(nome="Z. OESTE IV", saida="08:00")
        Loja.objects.create(nome="PARK", pes="455", emp="265", rota_padrao=rp, ordem_na_rota=1)
        Loja.objects.create(nome="WEST III", pes="351", emp="230", rota_padrao=rp, ordem_na_rota=2)
        r = acoes.criar_rota(self.joao, date(2026, 10, 24), "z. oeste iv", ["PARK", "WEST III"], False)
        self.assertEqual((r.nome, r.saida, r.fora_do_planejamento), ("Z. OESTE IV", "08:00", False))
        self.assertEqual(list(r.paradas.values_list("loja", "pes")), [("FÁBRICA", ""), ("PARK", "455"), ("WEST III", "351"), ("FÁBRICA", "")])
        r2 = acoes.criar_rota(self.joao, date(2026, 10, 24), "MISSÃO", [], True)
        self.assertEqual(list(r2.paradas.values_list("loja", flat=True)), ["FÁBRICA", "FÁBRICA"])
        self.assertTrue(r2.fora_do_planejamento)
        self.assertGreater(r2.ordem, r.ordem)
        with self.assertRaises(ValueError):
            acoes.criar_rota(self.joao, date(2026, 10, 24), "  ", [], False)


class PlanejarSemanaTest(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("joao")
        rp = RotaPadrao.objects.create(nome="Z. OESTE IV", saida="08:00")
        Loja.objects.create(nome="PARK", pes="455", emp="265", rota_padrao=rp, ordem_na_rota=1)
        Loja.objects.create(nome="WEST III", pes="351", emp="230", rota_padrao=rp, ordem_na_rota=2)
        RotaPadrao.objects.create(nome="R. LAGOS", saida="04:00")

    def test_planejar_cria_com_lojas_do_cadastro(self):
        res = acoes.planejar_semana(self.u, {date(2026, 10, 26): ["Z. OESTE IV", "R. LAGOS"], date(2026, 10, 27): ["Z. OESTE IV"]})
        self.assertEqual((len(res["criadas"]), res["reativadas"], res["canceladas"]), (3, [], []))
        r = Rota.objects.get(data=date(2026, 10, 26), nome="Z. OESTE IV")
        self.assertEqual((r.saida, r.fora_do_planejamento), ("08:00", False))
        self.assertEqual(list(r.paradas.values_list("loja", flat=True)), ["FÁBRICA", "PARK", "WEST III", "FÁBRICA"])
        self.assertTrue(Historico.objects.filter(rota=r, acao__contains="planejamento").exists())

    def test_planejar_nao_duplica(self):
        acoes.planejar_semana(self.u, {date(2026, 10, 26): ["Z. OESTE IV"]})
        self.assertEqual(acoes.planejar_semana(self.u, {date(2026, 10, 26): ["z. oeste iv"]})["criadas"], [])
        self.assertEqual(Rota.objects.count(), 1)

    def test_desmarcar_cancela_e_remarcar_reativa(self):
        r, = acoes.planejar_semana(self.u, {date(2026, 10, 26): ["Z. OESTE IV"]})["criadas"]
        res = acoes.planejar_semana(self.u, {}, {date(2026, 10, 26): ["Z. OESTE IV"]})
        r.refresh_from_db()
        self.assertEqual((r.status, res["canceladas"]), (Rota.CANCELADA, [r]))
        self.assertEqual(acoes.planejar_semana(self.u, {date(2026, 10, 26): ["Z. OESTE IV"]})["reativadas"], [r])
        r.refresh_from_db()
        self.assertEqual(r.status, Rota.ATIVA)  # reativa a mesma rota em vez de criar outra
        self.assertEqual(Rota.objects.count(), 1)

    def test_planejar_rota_fora_do_cadastro_recusa(self):
        with self.assertRaises(ValueError):
            acoes.planejar_semana(self.u, {date(2026, 10, 26): ["MISSÃO"]})
        self.assertEqual(Rota.objects.count(), 0)
