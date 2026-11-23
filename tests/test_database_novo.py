"""Testes das funções novas do database.py (economia, casamento, loja, etc.).

Usa um banco temporário próprio para não sujar o banco real.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB = "/tmp/test_db_novo.sqlite3"
os.environ["DATA_DB"] = DB
if os.path.exists(DB):
    os.remove(DB)

import config  # noqa: E402
config.DATA_DB = DB
import database as db  # noqa: E402

db.init(DB)

U1 = "5511900000001@s.whatsapp.net"
U2 = "5511900000002@s.whatsapp.net"
CHAT = "123-456@g.us"


def _zerar(jid):
    """Deixa a carteira do usuário com o valor pedido e o banco em 0."""
    db.get_balance(jid)
    db._exec("UPDATE economy SET balance=0 WHERE jid=?", (jid,))
    db._exec("INSERT OR REPLACE INTO bank(jid, amount) VALUES(?,0)", (jid,))


def _set_saldo(jid, valor):
    _zerar(jid)
    db.add_balance(jid, valor)


def _limpar_cooldown(jid, kind):
    db._exec("DELETE FROM cooldowns WHERE jid=? AND kind=?", (jid, kind))


# ---------------- economia: carteira e banco ----------------
def test_carteira_e_banco():
    _set_saldo(U1, 500)
    carteira, banco = db.get_wallet_and_bank(U1)
    assert carteira == 500 and banco == 0

    ok, res = db.deposit(U1, 200)
    assert ok and res["balance"] == 300 and res["bank"] == 200

    ok, res = db.withdraw(U1, 50)
    assert ok and res["balance"] == 350 and res["bank"] == 150

    assert db.get_wallet_and_bank(U1) == (350, 150)


def test_deposito_e_saque_invalidos():
    _set_saldo(U1, 100)
    ok, motivo = db.deposit(U1, 0)
    assert not ok and isinstance(motivo, str)
    ok, motivo = db.deposit(U1, -50)
    assert not ok
    ok, motivo = db.deposit(U1, 999999)  # mais do que tem na carteira
    assert not ok
    ok, motivo = db.withdraw(U1, 10)  # banco vazio
    assert not ok
    # nada mudou e nada ficou negativo
    assert db.get_wallet_and_bank(U1) == (100, 0)


def test_valores_negativos_ou_zero_sao_recusados():
    """Defesa em profundidade: valor <= 0 nunca pode mover dinheiro."""
    _set_saldo(U1, 300)
    ok, _ = db.deposit(U1, 100)
    assert ok
    antes = db.get_wallet_and_bank(U1)  # (200, 100)

    for valor in (0, -1, -1000):
        ok, motivo = db.deposit(U1, valor)
        assert not ok and isinstance(motivo, str), f"deposit aceitou {valor}"
        ok, motivo = db.withdraw(U1, valor)
        assert not ok and isinstance(motivo, str), f"withdraw aceitou {valor}"
        ok, motivo = db.place_bet(U1, valor, True)
        assert not ok and isinstance(motivo, str), f"place_bet aceitou {valor}"
        ok, motivo = db.buy_item(U1, "cafe", valor)
        assert not ok and isinstance(motivo, str), f"buy_item aceitou {valor}"
        assert db.get_wallet_and_bank(U1) == antes, f"saldo mexeu com {valor}"

    # roubo com valores não positivos também é recusado, sem consumir cooldown
    _set_saldo(U2, 500)
    _limpar_cooldown(U1, "rob")
    antes = db.get_wallet_and_bank(U1)
    for valor in (0, -1, -1000):
        ok, motivo = db.rob_user(U1, U2, valor, success=True, penalty=10)
        assert not ok and isinstance(motivo, str), f"rob_user aceitou amount {valor}"
        ok, motivo = db.rob_user(U1, U2, 10, success=False, penalty=valor)
        assert not ok and isinstance(motivo, str), f"rob_user aceitou penalty {valor}"
        assert db.get_wallet_and_bank(U1) == antes
        assert db.get_balance(U2) == 500
    # e a quantidade de itens do inventário continua intacta
    assert all(row["quantity"] > 0 for row in db.get_inventory(U1))


def test_saldo_nunca_negativo_na_aposta():
    _set_saldo(U1, 100)
    ok, motivo = db.place_bet(U1, 500, False)
    assert not ok and isinstance(motivo, str)
    assert db.get_balance(U1) == 100

    ok, motivo = db.place_bet(U1, 0, True)
    assert not ok
    ok, motivo = db.place_bet(U1, -10, True)
    assert not ok
    assert db.get_balance(U1) == 100


def test_aposta_ganha_e_perde():
    _set_saldo(U1, 100)
    ok, res = db.place_bet(U1, 100, True, multiplier=2)
    assert ok and res["won"] is True
    assert res["stake"] == 100 and res["payout"] == 200
    assert res["balance"] == 200 == db.get_balance(U1)

    ok, res = db.place_bet(U1, 200, False)
    assert ok and res["won"] is False and res["payout"] == 0
    assert res["balance"] == 0 == db.get_balance(U1)


def test_apostas_concorrentes_nao_estouram_o_saldo():
    _set_saldo(U1, 100)
    vitorias = []
    inicio = threading.Barrier(10)

    def apostar():
        inicio.wait()
        ok, _ = db.place_bet(U1, 100, False)
        if ok:
            vitorias.append(1)

    threads = [threading.Thread(target=apostar) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(vitorias) == 1, "só uma aposta podia caber no saldo"
    assert db.get_balance(U1) == 0


# ---------------- cooldowns ----------------
def test_trabalhar_com_cooldown():
    _set_saldo(U1, 0)
    _limpar_cooldown(U1, "work")
    ok, valor = db.claim_work(U1, reward=150, cooldown=3600)
    assert ok and valor == 150
    assert db.get_balance(U1) == 150

    ok, restante = db.claim_work(U1, reward=150, cooldown=3600)
    assert not ok
    assert isinstance(restante, int) and 0 < restante <= 3600
    assert db.get_balance(U1) == 150  # não ganhou de novo

    # cooldown expirado (simula trabalho de 2h atrás)
    db._exec("UPDATE cooldowns SET ts=? WHERE jid=? AND kind='work'",
             (int(time.time()) - 7200, U1))
    ok, valor = db.claim_work(U1, reward=10, cooldown=3600)
    assert ok and valor == 10


def test_roubo():
    _set_saldo(U1, 100)
    _set_saldo(U2, 300)
    _limpar_cooldown(U1, "rob")

    ok, motivo = db.rob_user(U1, U1, 50)
    assert not ok and isinstance(motivo, str)

    ok, res = db.rob_user(U1, U2, 120, success=True, penalty=30)
    assert ok and res["success"] is True and res["amount"] == 120
    assert db.get_balance(U1) == 220 and db.get_balance(U2) == 180

    # em cooldown devolve os segundos restantes (int)
    ok, restante = db.rob_user(U1, U2, 50, success=True)
    assert not ok and isinstance(restante, int) and restante > 0


def test_roubo_nao_deixa_saldo_negativo():
    _set_saldo(U1, 10)
    _set_saldo(U2, 40)
    _limpar_cooldown(U1, "rob")
    # rouba mais do que a vítima tem: leva só o que existe
    ok, res = db.rob_user(U1, U2, 1000, success=True)
    assert ok and res["amount"] == 40
    assert db.get_balance(U2) == 0 and db.get_balance(U1) == 50

    _limpar_cooldown(U1, "rob")
    _set_saldo(U1, 20)
    ok, res = db.rob_user(U1, U2, 50, success=False, penalty=999)
    assert ok and res["success"] is False and res["amount"] == 20
    assert db.get_balance(U1) == 0


def test_desafio_diario():
    dia = "2026-01-01"
    row = db.get_daily_challenge(U1, day=dia, challenge="Mande 10 mensagens", reward=90)
    assert row["challenge"] == "Mande 10 mensagens"
    assert row["reward"] == 90 and row["claimed"] == 0

    _set_saldo(U1, 0)
    ok, premio = db.claim_daily_challenge(U1, reward=90, challenge="Mande 10 mensagens", day=dia)
    assert ok and premio == 90 and db.get_balance(U1) == 90

    ok, motivo = db.claim_daily_challenge(U1, reward=90, challenge="Mande 10 mensagens", day=dia)
    assert not ok and isinstance(motivo, str)
    assert db.get_balance(U1) == 90

    assert db.get_daily_challenge(U1, day=dia, challenge="x", reward=1)["claimed"] == 1
    # o desafio de outro dia é independente
    assert db.get_daily_challenge(U1, day="2026-01-02", challenge="Outro", reward=50)["claimed"] == 0


# ---------------- casamento ----------------
def _limpar_casamentos():
    db._exec("DELETE FROM marriages")


def test_casamento_fluxo_completo():
    _limpar_casamentos()
    ok, res = db.propose_marriage(CHAT, U1, U2)
    assert ok

    pendente = db.get_marriage(CHAT, U2, include_pending=True)
    assert pendente["status"] == "pendente"
    assert pendente["jid1"] == U1 and pendente["jid2"] == U2
    assert db.get_marriage(CHAT, U2) is None  # sem pendentes não devolve nada

    ok, res = db.accept_marriage(CHAT, U2)
    assert ok and res["jid1"] == U1 and res["jid2"] == U2

    casado = db.get_marriage(CHAT, U1, include_pending=True)
    assert casado["status"] == "casado"

    assert db.divorce(CHAT, U1) is True
    assert db.get_marriage(CHAT, U1, include_pending=True) is None
    assert db.divorce(CHAT, U1) is False


def test_casamento_recusa():
    _limpar_casamentos()
    ok, _ = db.propose_marriage(CHAT, U1, U2)
    assert ok
    assert db.decline_marriage(CHAT, U2) is True
    assert db.decline_marriage(CHAT, U2) is False
    assert db.get_marriage(CHAT, U2, include_pending=True) is None
    ok, motivo = db.accept_marriage(CHAT, U2)
    assert not ok and isinstance(motivo, str)


def test_casamento_impede_bigamia():
    _limpar_casamentos()
    terceiro = "5511900000003@s.whatsapp.net"
    assert db.propose_marriage(CHAT, U1, U2)[0]
    assert db.accept_marriage(CHAT, U2)[0]

    ok, motivo = db.propose_marriage(CHAT, U1, terceiro)
    assert not ok and isinstance(motivo, str)
    ok, motivo = db.propose_marriage(CHAT, terceiro, U2)
    assert not ok
    ok, motivo = db.propose_marriage("outro@g.us", U1, terceiro)
    assert not ok, "casamento é único por pessoa, mesmo em outro grupo"

    ok, motivo = db.propose_marriage(CHAT, terceiro, terceiro)
    assert not ok  # não pode casar consigo mesmo
    _limpar_casamentos()


# ---------------- blacklist ----------------
def test_blacklist():
    db._exec("DELETE FROM blacklist WHERE chat=?", (CHAT,))
    assert db.blacklist_list(CHAT) == []
    assert db.blacklist_add(CHAT, "palavra ruim", U1) is True
    assert db.blacklist_add(CHAT, "PALAVRA RUIM", U1) is False  # duplicada
    assert db.blacklist_add(CHAT, "spam", U1) is True
    assert sorted(db.blacklist_list(CHAT)) == ["palavra ruim", "spam"]

    assert db.find_blacklisted(CHAT, "isso é uma PALAVRA RUIM aqui") == "palavra ruim"
    assert db.find_blacklisted(CHAT, "texto limpo") is None
    assert db.find_blacklisted("outro@g.us", "palavra ruim") is None
    assert db.find_blacklisted(CHAT, "") is None

    assert db.blacklist_remove(CHAT, "Spam") is True
    assert db.blacklist_remove(CHAT, "spam") is False
    assert db.blacklist_list(CHAT) == ["palavra ruim"]


# ---------------- loja e inventário ----------------
def test_loja_e_inventario():
    itens = db.shop_list()
    assert itens and all(row["item_id"] and row["price"] > 0 for row in itens)
    primeiro = itens[0]

    db._exec("DELETE FROM inventory WHERE jid=?", (U1,))
    _set_saldo(U1, primeiro["price"] * 3)

    ok, res = db.buy_item(U1, primeiro["item_id"], 2)
    assert ok
    assert res["quantity"] == 2 and res["total"] == primeiro["price"] * 2
    assert res["name"] == primeiro["name"]
    assert res["balance"] == primeiro["price"] and db.get_balance(U1) == primeiro["price"]

    inv = db.get_inventory(U1)
    assert len(inv) == 1 and inv[0]["item_id"] == primeiro["item_id"]
    assert inv[0]["quantity"] == 2 and inv[0]["name"] == primeiro["name"]

    ok, motivo = db.buy_item(U1, primeiro["item_id"], 50)
    assert not ok and isinstance(motivo, str)
    ok, motivo = db.buy_item(U1, "item_que_nao_existe", 1)
    assert not ok
    ok, motivo = db.buy_item(U1, primeiro["item_id"], 0)
    assert not ok
    assert db.get_balance(U1) == primeiro["price"]

    # compra somando na mesma linha do inventário
    ok, _ = db.buy_item(U1, primeiro["item_id"], 1)
    assert ok
    assert db.get_inventory(U1)[0]["quantity"] == 3
    assert db.get_inventory(U2) == []


# ---------------- lembretes ----------------
def test_lembretes():
    db._exec("DELETE FROM reminders")
    futuro = int(time.time()) + 600
    rid = db.add_reminder(CHAT, U1, "beber água", futuro)
    db.add_reminder(CHAT, U2, "outro dono", futuro)
    db.add_reminder("outro@g.us", U1, "outro chat", futuro)

    linhas = db.list_reminders(U1, CHAT)
    assert len(linhas) == 1
    assert linhas[0]["id"] == rid and linhas[0]["text"] == "beber água"
    assert linhas[0]["due"] == futuro

    assert db.cancel_reminder(rid, U2, CHAT) is False  # não é dono
    assert db.cancel_reminder(rid, U1, "outro@g.us") is False  # outro chat
    assert db.cancel_reminder(rid, U1, CHAT) is True
    assert db.cancel_reminder(rid, U1, CHAT) is False  # já cancelado
    assert db.list_reminders(U1, CHAT) == []


# ---------------- listagens de moderação ----------------
def test_listas_de_mutados_e_banidos():
    db._exec("DELETE FROM roles WHERE chat=?", (CHAT,))
    db._exec("DELETE FROM banlist WHERE chat=?", (CHAT,))
    assert db.list_muted(CHAT) == []
    assert db.list_banned(CHAT) == []

    db.add_role(CHAT, "5511900000001", "muted")
    db.add_role(CHAT, "5511900000002", "muted")
    db.add_role(CHAT, "5511900000009", "vip")
    assert db.list_muted(CHAT) == ["5511900000001", "5511900000002"]

    db.add_ban(CHAT, "5511900000003")
    db.add_ban("outro@g.us", "5511900000004")
    assert db.list_banned(CHAT) == ["5511900000003"]

    db.remove_role(CHAT, "5511900000001", "muted")
    assert db.list_muted(CHAT) == ["5511900000002"]


def test_delete_warn():
    db._exec("DELETE FROM warns")
    db.add_warn(CHAT, "5511900000001", "motivo 1", U2)
    db.add_warn(CHAT, "5511900000001", "motivo 2", U2)
    assert db.get_warns_count(CHAT, "5511900000001") == 2

    assert db.delete_warn(CHAT, "5511900000001", None) is True  # apaga a última
    restantes = db.get_warns(CHAT, "5511900000001")
    assert len(restantes) == 1 and restantes[0]["reason"] == "motivo 1"

    assert db.delete_warn(CHAT, "5511900000001", 1) is True  # pela posição/id
    assert db.get_warns_count(CHAT, "5511900000001") == 0
    assert db.delete_warn(CHAT, "5511900000001", None) is False
    assert db.delete_warn(CHAT, "5511900000001", 99) is False


# ---------------- atividade / inativos ----------------
def test_atividade():
    db._exec("DELETE FROM activity")
    assert db.get_last_activity(CHAT, "5511900000001") is None

    db.record_activity(CHAT, "5511900000001")
    visto = db.get_last_activity(CHAT, "5511900000001")
    assert isinstance(visto, int) and abs(visto - int(time.time())) < 5
    assert db.get_last_activity("outro@g.us", "5511900000001") is None

    # regravar atualiza o horário (não duplica linha)
    db._exec("UPDATE activity SET ts=? WHERE chat=? AND jid=?",
             (100, CHAT, "5511900000001"))
    db.record_activity(CHAT, "5511900000001")
    assert db.get_last_activity(CHAT, "5511900000001") > 100
    assert db._exec("SELECT COUNT(*) c FROM activity", (), "one")["c"] == 1

    # cenário do /inativos: quem tem ts antigo entra na lista
    corte = int(time.time()) - 30 * 86400
    db._exec("UPDATE activity SET ts=? WHERE chat=?", (corte - 10, CHAT))
    assert db.get_last_activity(CHAT, "5511900000001") < corte


# ---------------- pacote de figurinhas ----------------
def test_pacote_de_figurinhas():
    db._exec("DELETE FROM sticker_packs")
    assert db.resolve_sticker_pack(U1, CHAT, "Padrao", "Autor") == ("Padrao", "Autor")

    db.set_sticker_pack(CHAT, "Pack do Grupo", "Grupo", scope="chat")
    assert db.resolve_sticker_pack(U1, CHAT, "Padrao", "Autor") == ("Pack do Grupo", "Grupo")

    db.set_sticker_pack(U1, "Meu Pack", "Eu", scope="user")
    assert db.resolve_sticker_pack(U1, CHAT, "Padrao", "Autor") == ("Meu Pack", "Eu")
    assert db.resolve_sticker_pack(U2, CHAT, "Padrao", "Autor") == ("Pack do Grupo", "Grupo")

    db.set_sticker_pack(U1, "Outro", "Novo", scope="user")
    assert db.resolve_sticker_pack(U1, CHAT, "Padrao", "Autor") == ("Outro", "Novo")
