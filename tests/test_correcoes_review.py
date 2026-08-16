"""Regressões das correções apontadas na revisão de código.

Cada teste aqui existe porque o comportamento antigo estava errado de um jeito
silencioso — apagava a advertência errada, censurava palavra inocente, mostrava
ranking zerado. São exatamente os casos que passariam despercebidos sem teste.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import database as db
from test_commands import FakeClient, GROUP, SENDER, make_msg

CHAT = bot.Jid2String(GROUP)


def _run(texto, admin=True):
    fake = FakeClient(admin=admin)
    bot.client = fake
    bot.handle_command(make_msg(texto), texto)
    return fake


# ---------------- blacklist: palavra inteira ----------------
def test_blacklist_nao_pega_pedaco_de_palavra():
    db.blacklist_add(CHAT, "pix")
    try:
        assert db.find_blacklisted(CHAT, "me manda o pix") == "pix"
        assert db.find_blacklisted(CHAT, "PIX agora") == "pix"
        # o bug: "pixel" continha "pix" e a mensagem era apagada
        assert db.find_blacklisted(CHAT, "essa foto tem pixel demais") is None
        assert db.find_blacklisted(CHAT, "muitos pixels") is None
    finally:
        db.blacklist_remove(CHAT, "pix")
    print("✓ blacklist casa palavra inteira, não pedaço")


def test_blacklist_aceita_expressao_com_espaco():
    db.blacklist_add(CHAT, "compre agora")
    try:
        assert db.find_blacklisted(CHAT, "clique e compre agora!") == "compre agora"
        assert db.find_blacklisted(CHAT, "compre depois") is None
    finally:
        db.blacklist_remove(CHAT, "compre agora")
    print("✓ blacklist ainda funciona com expressão de várias palavras")


# ---------------- delete_warn: posição, não id ----------------
def test_delwarn_apaga_pela_posicao_mostrada():
    jid = "5511777777777"
    for w in db.get_warns(CHAT, jid) or []:
        db.delete_warn(CHAT, jid, 1)
    for i in (1, 2, 3):
        db.add_warn(CHAT, jid, f"motivo {i}", "admin")

    assert db.delete_warn(CHAT, jid, 2) is True
    restantes = [w["reason"] for w in db.get_warns(CHAT, jid)]
    # o bug: o número era tratado como warns.id (global), apagando outra
    assert restantes == ["motivo 1", "motivo 3"], restantes
    assert db.delete_warn(CHAT, jid, 99) is False
    print("✓ /delwarn apaga a advertência da posição mostrada")


# ---------------- ranking: JID com device ----------------
def test_ranking_casa_numero_mesmo_com_device():
    jid_com_device = "5511666666666:12@s.whatsapp.net"
    db.add_xp(jid_com_device, 777)
    # o bug: consultava "5511666666666@s.whatsapp.net" (sem device) e dava 0
    resultado = dict(db.scores_for_phones(["5511666666666"], "xp"))
    assert resultado["5511666666666"] >= 777, resultado
    print("✓ ranking encontra quem tem device no JID")


def test_ranking_nao_cria_linha_para_quem_nao_joga():
    fantasma = "5511000000001"
    antes = db._exec("SELECT COUNT(*) AS n FROM economy", (), "one")["n"]
    db.scores_for_phones([fantasma], "moedas")
    depois = db._exec("SELECT COUNT(*) AS n FROM economy", (), "one")["n"]
    # o bug: get_balance(jid_sintetizado) chamava _ensure_economy e inseria
    assert antes == depois, "consultar o ranking criou linha no banco"
    print("✓ consultar ranking não polui o banco")


# ---------------- /sorteio respeita as opções digitadas ----------------
def test_sorteio_usa_opcoes_digitadas_no_grupo():
    fake = _run("/sorteio pizza sushi")
    resposta = fake.sent[-1]
    # o bug: em grupo, sem "|", ignorava tudo e sorteava um membro
    assert "pizza" in resposta or "sushi" in resposta, resposta
    assert "@" not in resposta, f"sorteou membro em vez da opção: {resposta}"
    print("✓ /sorteio respeita as opções digitadas")


def test_sorteio_com_barra_continua_funcionando():
    fake = _run("/sorteio a | b | c")
    assert any(x in fake.sent[-1] for x in ("a", "b", "c"))
    print("✓ /sorteio com | continua funcionando")


# ---------------- /campominado: coordenada fora do tabuleiro ----------------
def test_campominado_avisa_coordenada_invalida():
    bot._active_games.pop(CHAT, None)
    _run("/campominado")                    # inicia
    fake = _run("/campominado 99 99")       # fora do tabuleiro
    # o bug: minesweeper_reveal devolve valid=False sem levantar, então o
    # except ValueError nunca rodava e o bot só re-desenhava o tabuleiro
    assert "Fora do tabuleiro" in fake.sent[-1], fake.sent[-1]
    bot._active_games.pop(CHAT, None)
    print("✓ /campominado avisa coordenada fora do tabuleiro")


# ---------------- /inativos sem lista de membros ----------------
def test_inativos_nao_mente_quando_nao_ve_o_grupo(monkeypatch):
    monkeypatch.setattr(bot, "_group_phones", lambda ctx: [])
    fake = _run("/inativos 7")
    # o bug: dizia "todos falaram nos últimos 7 dias" quando na verdade
    # não conseguiu nem listar os membros
    assert "Todos falaram" not in fake.sent[-1], fake.sent[-1]
    assert "não consegui" in fake.sent[-1].lower()
    print("✓ /inativos não afirma nada quando não vê os membros")


if __name__ == "__main__":
    test_blacklist_nao_pega_pedaco_de_palavra()
    test_blacklist_aceita_expressao_com_espaco()
    test_delwarn_apaga_pela_posicao_mostrada()
    test_ranking_casa_numero_mesmo_com_device()
    test_ranking_nao_cria_linha_para_quem_nao_joga()
    test_sorteio_usa_opcoes_digitadas_no_grupo()
    test_sorteio_com_barra_continua_funcionando()
    test_campominado_avisa_coordenada_invalida()
    print("\n✅ CORREÇÕES DA REVISÃO OK")
