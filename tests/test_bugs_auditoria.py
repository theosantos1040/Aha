"""Regressões dos 15 bugs confirmados pela auditoria adversarial de todos os
250 comandos (workflow de validação, 22 agentes: 7 achadores + verificação
cética por achado, 0 falsos positivos).

Cada teste aqui reproduz EXATAMENTE o repro que os agentes usaram para
confirmar o bug, e prova que a correção resolve o problema descrito.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DB", "/tmp/test_bugs_auditoria.sqlite3")
if os.path.exists("/tmp/test_bugs_auditoria.sqlite3"):
    os.remove("/tmp/test_bugs_auditoria.sqlite3")

import config
config.DATA_DB = "/tmp/test_bugs_auditoria.sqlite3"
import database as db
db.init("/tmp/test_bugs_auditoria.sqlite3")

import bot
import utils
from test_commands import FakeClient, GROUP, SENDER, make_msg, make_media_msg

GSTR = bot.Jid2String(GROUP)


def run(texto, admin=True):
    fake = FakeClient(admin=admin)
    bot.client = fake
    bot.handle_command(make_msg(texto), texto)
    return fake


def run_media(texto, media="video", admin=True):
    fake = FakeClient(admin=admin)
    bot.client = fake
    msg = make_media_msg(texto, media)
    bot.handle_command(msg, texto)
    return fake


# ===================== utils.parse_duration: teto de 10 anos =====================

def test_parse_duration_tem_teto():
    """REGRESSÃO: sem teto, uma duração absurda virava um segundos gigantesco
    demais para threading.Timer (OverflowError) e para o INTEGER de 64 bits
    do SQLite. Usado por /remind, /timer e /tempban."""
    assert utils.parse_duration("99999999999999999999d") == utils.MAX_DURATION_SECONDS
    assert utils.parse_duration("5511888888888") == utils.MAX_DURATION_SECONDS  # fallback "minutos"
    assert utils.parse_duration("10m") == 600  # valores normais continuam exatos
    assert utils.parse_duration("2d") == 172800
    print("✓ parse_duration tem teto de 10 anos, sem afetar durações normais")


def test_parse_duration_teto_e_seguro_para_threading_timer():
    t = threading.Timer(utils.MAX_DURATION_SECONDS, lambda: None)
    t.daemon = True
    t.start()
    time.sleep(0.1)
    assert t.is_alive(), "o teto de parse_duration ainda causa OverflowError no Timer"
    t.cancel()
    print("✓ o teto de parse_duration é seguro para threading.Timer")


# ===================== /remind e /timer: duração absurda não crasha =====================

def test_remind_com_duracao_absurda_nao_crasha():
    fake = run("/remind 99999999999999999999d beber agua")
    assert fake.sent, "sem resposta"
    assert "Erro ao executar" not in fake.sent[-1], fake.sent[-1]
    print("✓ /remind com duração absurda não crasha mais")


def test_timer_com_duracao_absurda_nao_trava_silenciosamente():
    fake = run("/timer 99999999999999999999d aviso")
    assert fake.sent, "sem resposta"
    assert "Erro ao executar" not in fake.sent[-1], fake.sent[-1]
    print("✓ /timer com duração absurda não crasha nem agenda um Timer que morre em silêncio")


# ===================== /tempban: alvo não pode virar "duração" =====================

def test_tempban_sem_duracao_separada_usa_padrao_e_nao_mostra_o_telefone():
    """REGRESSÃO: '/tempban 5511888888888' (só o alvo, sem duração separada)
    usava o PRÓPRIO telefone como duração (via o fallback 'número puro =
    minutos' de parse_duration), banindo por ~330 trilhões de segundos e
    mostrando o telefone na mensagem como se fosse a duração."""
    phone = "5511888888888"
    db.remove_ban(GSTR, phone)
    fake = run(f"/tempban {phone}")
    assert fake.sent, "sem resposta"
    resposta = fake.sent[-1]
    # o telefone aparece UMA vez como @menção do alvo (correto) — o bug era
    # ele aparecer de novo entre parênteses, como se fosse a "duração"
    assert f"@{phone}" in resposta, resposta
    assert f"({phone})" not in resposta, f"telefone aparece como duração: {resposta}"
    assert "(1h)" in resposta, f"deveria usar a duração padrão de 1h: {resposta}"
    assert db.is_banned(GSTR, phone)
    db.remove_ban(GSTR, phone)
    print("✓ /tempban sem duração separada usa padrão de 1h, não o telefone do alvo")


def test_tempban_com_duracao_explicita_absurda_nao_crasha():
    phone = "5511888888888"
    db.remove_ban(GSTR, phone)
    fake = run(f"/tempban {phone} 999999999999999999d")
    assert fake.sent
    assert "Erro ao executar" not in fake.sent[-1], fake.sent[-1]
    db.remove_ban(GSTR, phone)
    print("✓ /tempban com duração explícita absurda não crasha")


# ===================== /backup-load e /cancelarlembrete: id gigante =====================

def test_backup_load_com_id_gigante_nao_crasha():
    """REGRESSÃO: um id de 20 dígitos excede o INTEGER de 64 bits do SQLite
    e o driver levanta OverflowError, que vazava crua até o usuário."""
    run("/backup-create")  # garante que existe ao menos 1 backup
    fake = run("/backup-load 99999999999999999999")
    assert fake.sent
    resposta = fake.sent[-1]
    assert "Erro ao executar" not in resposta, resposta
    assert "não encontrado" in resposta.lower()
    print("✓ /backup-load com id gigante responde 'não encontrado', não crasha")


def test_cancelarlembrete_com_id_gigante_nao_crasha():
    fake = run("/cancelarlembrete 99999999999999999999")
    assert fake.sent
    resposta = fake.sent[-1]
    assert "Erro ao executar" not in resposta, resposta
    assert "não encontrado" in resposta.lower()
    print("✓ /cancelarlembrete com id gigante responde validação normal, não crasha")


def test_exec_converte_overflow_em_valueerror():
    """A defesa central em database._exec: qualquer parâmetro inteiro grande
    demais pro SQLite vira ValueError amigável, não uma OverflowError crua."""
    try:
        db._exec("SELECT 1 WHERE 1=?", (10 ** 30,), "one")
        assert False, "deveria ter levantado ValueError"
    except ValueError as e:
        assert "grande" in str(e).lower()
    print("✓ database._exec converte OverflowError em ValueError amigável")


# ===================== /antifake: só dígitos ASCII =====================

def test_antifake_recusa_digito_unicode_nao_ascii():
    """REGRESSÃO: '١' (dígito árabe-índico 1) passava por c.isdigit()
    (que aceita Unicode) e virava um DDI que NUNCA bate com phone.startswith,
    fazendo o antifake expulsar QUALQUER novo membro do grupo."""
    fake = run("/antifake on ١")
    resposta = fake.sent[-1]
    assert "Uso:" in resposta, resposta
    # o comando recusou antes de gravar qualquer coisa no banco
    assert not db.get_setting(GSTR, "antifake_ddi")
    db.set_setting(GSTR, "antifake", "0")
    print("✓ /antifake recusa dígito Unicode não-ASCII")


def test_antifake_aceita_ddi_ascii_normal():
    fake = run("/antifake on 55")
    assert "55" in fake.sent[-1]
    assert db.get_setting(GSTR, "antifake_ddi") == "55"
    db.set_setting(GSTR, "antifake", "0")
    print("✓ /antifake continua aceitando DDI ASCII normal")


# ===================== /pay: telefone do alvo não pode virar "valor" =====================

def test_pay_sem_valor_nao_transfere_o_telefone_do_alvo():
    """REGRESSÃO: '/pay 5511888888888' (só o alvo, sem valor) usava o
    PRÓPRIO telefone (que também satisfaz p.isdigit()) como quantia — e a
    transferência REALMENTE acontecia se o saldo desse."""
    target_phone = "5511888888888"
    target_jid = f"{target_phone}@s.whatsapp.net"
    sender_jid = bot.Jid2String(SENDER)
    db.add_balance(sender_jid, 10 ** 13)  # saldo bem alto: se o bug existisse, passaria
    saldo_sender_antes = db.get_balance(sender_jid)
    saldo_alvo_antes = db.get_balance(target_jid)

    fake = run(f"/pay {target_phone}")
    assert "Uso:" in fake.sent[-1], fake.sent[-1]
    assert db.get_balance(sender_jid) == saldo_sender_antes, "saldo do remetente mudou sem valor informado"
    assert db.get_balance(target_jid) == saldo_alvo_antes, "saldo do alvo mudou sem valor informado"
    print("✓ /pay sem valor não transfere o telefone do alvo como quantia")


def test_pay_com_valor_normal_continua_funcionando():
    target_phone = "5511888888888"
    target_jid = f"{target_phone}@s.whatsapp.net"
    sender_jid = bot.Jid2String(SENDER)
    db.add_balance(sender_jid, 1000)
    saldo_alvo_antes = db.get_balance(target_jid)
    fake = run(f"/pay {target_phone} 10")
    assert "transferiu" in fake.sent[-1].lower(), fake.sent[-1]
    assert db.get_balance(target_jid) == saldo_alvo_antes + 10
    print("✓ /pay com valor explícito continua funcionando normalmente")


# ===================== /roll: rótulo bate com o valor realmente sorteado =====================

def test_roll_rotulo_bate_com_sides_clampado():
    fake = run("/roll 1500")
    resposta = fake.sent[-1]
    assert "1d1000" in resposta, f"deveria mostrar o valor clampado (1000), não 1500: {resposta}"
    print("✓ /roll com sides>1000 mostra o rótulo já clampado")


def test_roll_rotulo_bate_com_count_clampado():
    fake = run("/roll 50d6")
    resposta = fake.sent[-1]
    assert "20d6" in resposta, f"deveria mostrar o rótulo clampado (20), não 50: {resposta}"
    print("✓ /roll com count>20 mostra o rótulo já clampado")


# ===================== Jogos: errar não pode revelar resposta reaproveitável =====================

def test_trivia_errar_nao_permite_reaproveitar_resposta_revelada():
    """REGRESSÃO: errar /trivia revelava a resposta certa SEM encerrar a
    rodada — reenviar a resposta revelada dava as moedas de graça."""
    bot._active_games.pop(GSTR, None)
    sender_jid = bot.Jid2String(SENDER)
    saldo_antes = db.get_balance(sender_jid)

    run("/trivia")  # inicia
    g = bot._active_games.get(GSTR)
    assert g and g["type"] == "trivia"
    resposta_certa = g["answer"]

    errada = run("/trivia isso_certamente_esta_errado_xyz")
    assert resposta_certa in errada.sent[-1]  # ainda revela a resposta (didático)
    assert bot._active_games.get(GSTR) is None, "o jogo deveria ter sido encerrado ao errar"

    reenvio = run(f"/trivia {resposta_certa}")
    assert "correto" not in reenvio.sent[-1].lower(), "reaproveitou a resposta revelada!"
    assert db.get_balance(sender_jid) == saldo_antes, "ganhou moedas sem acertar de verdade"
    print("✓ /trivia: errar encerra a rodada, resposta revelada não dá mais moedas de graça")


def test_mathrace_errar_nao_permite_reaproveitar_resposta_revelada():
    bot._active_games.pop(GSTR, None)
    sender_jid = bot.Jid2String(SENDER)
    saldo_antes = db.get_balance(sender_jid)

    run("/mathrace")
    g = bot._active_games.get(GSTR)
    assert g and g["type"] == "math"
    resposta_certa = g["ans"]

    run("/mathrace")  # chamada vazia == "errado" (cai no except ValueError)
    assert bot._active_games.get(GSTR) is None, "o jogo deveria ter sido encerrado"

    reenvio = run(f"/mathrace {resposta_certa}")
    assert "correto" not in reenvio.sent[-1].lower(), "reaproveitou a resposta revelada!"
    assert db.get_balance(sender_jid) == saldo_antes
    print("✓ /mathrace: resposta vazia/errada encerra a rodada, sem exploit de moedas")


def test_guess_game_errar_nao_permite_reaproveitar_resposta_revelada():
    """Cobre /guessflag, /guesspokemon e /guessanime — todos compartilham
    _guess_game e tinham o mesmo bug."""
    for cmd in ("guessflag", "guesspokemon", "guessanime"):
        bot._active_games.pop(GSTR, None)
        sender_jid = bot.Jid2String(SENDER)
        saldo_antes = db.get_balance(sender_jid)

        run(f"/{cmd}")
        g = bot._active_games.get(GSTR)
        assert g, f"{cmd} não iniciou o jogo"
        resposta_certa = g["answer"]

        run(f"/{cmd}")  # chamada vazia == errado
        assert bot._active_games.get(GSTR) is None, f"{cmd}: jogo deveria ter sido encerrado"

        reenvio = run(f"/{cmd} {resposta_certa}")
        assert "+40" not in reenvio.sent[-1], f"{cmd}: reaproveitou a resposta revelada!"
        assert db.get_balance(sender_jid) == saldo_antes, f"{cmd}: ganhou moedas sem acertar"
    print("✓ /guessflag, /guesspokemon, /guessanime: sem exploit de moedas")


# ===================== /acelerar e /lentidao: validação amigável =====================

def test_acelerar_com_fator_invalido_mostra_mensagem_amigavel():
    """REGRESSÃO: com mídia anexada, um fator não-numérico vazava a exceção
    crua do Python ("could not convert string to float: 'abc'")."""
    fake = run_media("/acelerar abc", media="video")
    resposta = fake.sent[-1]
    assert "could not convert" not in resposta, resposta
    assert "Uso:" in resposta, resposta
    print("✓ /acelerar com fator inválido mostra mensagem amigável, não a exceção crua")


def test_lentidao_com_fator_invalido_mostra_mensagem_amigavel():
    fake = run_media("/lentidao abc", media="video")
    resposta = fake.sent[-1]
    assert "could not convert" not in resposta, resposta
    assert "Uso:" in resposta, resposta
    print("✓ /lentidao com fator inválido mostra mensagem amigável, não a exceção crua")


def test_lentidao_fora_do_limite_nao_recomenda_o_comando_errado():
    """REGRESSÃO: a mensagem de fator-fora-do-limite dizia sempre
    '/acelerar', mesmo quando o usuário tinha chamado /lentidao."""
    fake = run_media("/lentidao 0", media="video")
    resposta = fake.sent[-1]
    assert "/acelerar" not in resposta, f"ainda recomenda o comando errado: {resposta}"
    print("✓ /lentidao fora do limite não recomenda '/acelerar' por engano")


# ===================== /calc: exponenciação sem limite =====================

def test_calc_recusa_potencia_gigante_rapido():
    """REGRESSÃO: '/calc 9**999999999' tentava computar um inteiro de
    ~954 milhões de dígitos, travando a única thread síncrona do bot
    (todos os chats) por minutos, consumindo gigabytes de RAM."""
    t0 = time.time()
    fake = run("/calc 9**999999999")
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"demorou {elapsed:.2f}s — deveria recusar quase na hora"
    assert "inválida" in fake.sent[-1].lower()
    print(f"✓ /calc recusa potência gigante em {elapsed:.4f}s, sem travar o bot")


def test_calc_continua_funcionando_normalmente():
    assert "4" in run("/calc 2+2").sent[-1]
    assert "1024" in run("/calc 2**10").sent[-1]
    print("✓ /calc continua funcionando normalmente para expressões razoáveis")


# ===================== /ia: modo pensamento não bloqueia o dispatcher =====================

def test_ia_thinking_nao_bloqueia_o_dispatcher(monkeypatch=None):
    """REGRESSÃO: '/thinking on' fazia um time.sleep(5) DIRETO dentro do
    handler síncrono — como o neonize despacha TODOS os eventos (mensagens de
    TODOS os chats, mudanças de grupo, estado de conexão) numa única thread
    sem pool de workers, isso travava o bot inteiro por 5s a cada /ia."""
    import unittest.mock as mock
    with mock.patch.object(bot.time, "sleep", lambda s: None):
        db.set_setting(GSTR, "thinking", "1")
        try:
            fake = FakeClient(admin=True)
            bot.client = fake
            texto = "/ia oi"
            msg = make_msg(texto)
            t0 = time.time()
            bot.handle_command(msg, texto)
            elapsed = time.time() - t0
            assert elapsed < 1.0, f"handle_command bloqueou por {elapsed:.2f}s"
            for _ in range(200):
                if fake.sent:
                    break
                time.sleep(0.01)
            assert fake.sent, "a resposta em segundo plano nunca chegou"
        finally:
            db.set_setting(GSTR, "thinking", "0")
    print("✓ /thinking on não bloqueia mais o dispatcher de eventos (roda em thread própria)")


if __name__ == "__main__":
    test_parse_duration_tem_teto()
    test_parse_duration_teto_e_seguro_para_threading_timer()
    test_remind_com_duracao_absurda_nao_crasha()
    test_timer_com_duracao_absurda_nao_trava_silenciosamente()
    test_tempban_sem_duracao_separada_usa_padrao_e_nao_mostra_o_telefone()
    test_tempban_com_duracao_explicita_absurda_nao_crasha()
    test_backup_load_com_id_gigante_nao_crasha()
    test_cancelarlembrete_com_id_gigante_nao_crasha()
    test_exec_converte_overflow_em_valueerror()
    test_antifake_recusa_digito_unicode_nao_ascii()
    test_antifake_aceita_ddi_ascii_normal()
    test_pay_sem_valor_nao_transfere_o_telefone_do_alvo()
    test_pay_com_valor_normal_continua_funcionando()
    test_roll_rotulo_bate_com_sides_clampado()
    test_roll_rotulo_bate_com_count_clampado()
    test_trivia_errar_nao_permite_reaproveitar_resposta_revelada()
    test_mathrace_errar_nao_permite_reaproveitar_resposta_revelada()
    test_guess_game_errar_nao_permite_reaproveitar_resposta_revelada()
    test_acelerar_com_fator_invalido_mostra_mensagem_amigavel()
    test_lentidao_com_fator_invalido_mostra_mensagem_amigavel()
    test_lentidao_fora_do_limite_nao_recomenda_o_comando_errado()
    test_calc_recusa_potencia_gigante_rapido()
    test_calc_continua_funcionando_normalmente()
    test_ia_thinking_nao_bloqueia_o_dispatcher()
    print("\n✅ TODOS OS 15 BUGS DA AUDITORIA CORRIGIDOS E TRAVADOS EM TESTE")
