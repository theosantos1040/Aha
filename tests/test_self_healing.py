"""Testa o vigia de sobrevivência (bot._supervisor / _should_be_healthy).

Contexto do bug que isso previne: a thread de reconexão (_connect_loop) só
tinha proteção contra falha DENTRO de client.connect() (via _run_connect).
Um erro inesperado no resto do laço matava a thread em silêncio — o processo
continuava de pé, o /health respondia 200 do mesmo jeito, mas o WhatsApp
nunca mais reconectava sozinho. A única saída era reiniciar o serviço à mão.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DB", "/tmp/test_self_healing.sqlite3")
if os.path.exists("/tmp/test_self_healing.sqlite3"):
    os.remove("/tmp/test_self_healing.sqlite3")

import config
config.DATA_DB = "/tmp/test_self_healing.sqlite3"
import database
database.init("/tmp/test_self_healing.sqlite3")

import bot
import webui


# ===================== _should_be_healthy (função pura) =====================

def test_conectado_e_sempre_saudavel():
    assert bot._should_be_healthy(conectado=True, parado_ha=99999, reincidencias=999)
    print("✓ conectado=True é sempre saudável, não importa o resto")


def test_desconectado_mas_recente_e_saudavel():
    assert bot._should_be_healthy(conectado=False, parado_ha=5, reincidencias=0)
    print("✓ desconectado há pouco tempo (ainda tentando) é saudável")


def test_desconectado_ha_muito_tempo_e_insalubre():
    assert not bot._should_be_healthy(
        conectado=False, parado_ha=bot.LIMITE_PARADO + 1, reincidencias=0
    )
    print("✓ desconectado além do LIMITE_PARADO fica insalubre")


def test_no_limiar_exato_ainda_saudavel():
    assert bot._should_be_healthy(
        conectado=False, parado_ha=bot.LIMITE_PARADO - 1, reincidencias=0
    )
    print("✓ um segundo antes do limite ainda está saudável")


def test_crash_loop_e_insalubre_mesmo_com_progresso_recente():
    """Reincidências demais na janela = insalubre, mesmo com parado_ha baixo
    (a thread pode morrer de novo segundos depois de cada revive)."""
    assert not bot._should_be_healthy(
        conectado=False, parado_ha=1, reincidencias=bot.MAX_REVIVES_JANELA + 1
    )
    print("✓ crash-loop (muitos revives) é insalubre mesmo com progresso recente")


def test_poucas_reincidencias_nao_derruba_saude():
    assert bot._should_be_healthy(
        conectado=False, parado_ha=1, reincidencias=bot.MAX_REVIVES_JANELA
    )
    print("✓ reincidências dentro do limite não derrubam a saúde sozinhas")


# ===================== _mark_alive =====================

def test_mark_alive_atualiza_timestamp():
    with bot._conn_lock:
        bot._conn_state["last_ok"] = 0
    bot._mark_alive()
    with bot._conn_lock:
        recente = time.time() - bot._conn_state["last_ok"]
    assert recente < 2, f"last_ok não foi atualizado (recente={recente})"
    print("✓ _mark_alive() atualiza o timestamp de progresso")


# ===================== integração: connect_web + vigia =====================

class _FakeClientInstavel:
    """connect() 'cai' rápido sempre — simula reconexões seguidas."""

    is_connected = False

    class event:
        @staticmethod
        def qr(cb):
            pass

    def connect(self):
        time.sleep(0.05)


class _ClienteTravaParaSempre:
    """connect() nunca retorna nem levanta.

    connect_web() nunca "termina" — a thread de reconexão que ela sobe
    continua rodando pro resto do processo de teste, mesmo depois que o
    teste retorna. Sem travar bot.client assim no fim do teste, essa thread
    fantasma seguiria chamando webui.set_error()/expire_code() (via
    _run_connect -> _on_connect_error, sempre que client.connect() falhasse)
    e corromperia o webui._state compartilhado com test_webui.py, que roda
    depois em ordem alfabética."""

    is_connected = False

    class event:
        @staticmethod
        def qr(cb):
            pass

    def connect(self):
        while True:
            time.sleep(3600)


def test_vigia_revive_thread_morta_e_processo_continua_vivo():
    """Se a thread de reconexão morrer, o vigia sobe uma nova sozinho — sem
    precisar reiniciar o container."""
    orig_interval = bot.SUPERVISOR_INTERVAL_S
    bot.client = _FakeClientInstavel()
    bot.SUPERVISOR_INTERVAL_S = 0.05  # não esperar 30s de verdade no teste
    with bot._conn_lock:
        bot._conn_state["restarts"] = []
        bot._conn_state["last_ok"] = time.time()
        bot._conn_state["loop_thread"] = None

    os.environ["PORT"] = "0"
    try:
        threading.Thread(
            target=bot.connect_web, args=("",), daemon=True
        ).start()

        # espera a primeira thread de reconexão subir
        deadline = time.time() + 3
        while time.time() < deadline:
            with bot._conn_lock:
                if bot._conn_state["loop_thread"] is not None:
                    break
            time.sleep(0.02)
        with bot._conn_lock:
            original = bot._conn_state["loop_thread"]
        assert original is not None, "connect_web não subiu a thread de reconexão"

        # simula a thread morrendo de um jeito que _run_connect não protege
        # (ex.: uma exceção no resto do laço) — troca por uma já finalizada
        morta = threading.Thread(target=lambda: None)
        morta.start()
        morta.join()
        with bot._conn_lock:
            bot._conn_state["loop_thread"] = morta

        # dá tempo do vigia perceber e reviver (intervalo encurtado p/ teste)
        deadline = time.time() + 3
        revivida = False
        while time.time() < deadline:
            with bot._conn_lock:
                atual = bot._conn_state["loop_thread"]
            if atual is not morta and atual is not None and atual.is_alive():
                revivida = True
                break
            time.sleep(0.02)

        assert revivida, "o vigia não revivou a thread morta"
        with bot._conn_lock:
            assert len(bot._conn_state["restarts"]) >= 1, "revive não foi contabilizado"
        print("✓ vigia revive a thread de reconexão sozinho, sem restart do container")
    finally:
        # connect_web() nunca retorna — a(s) thread(s) que ela subiu continuam
        # rodando pro resto do processo de teste. Trava o client num que nunca
        # responde, em vez de restaurar o original: assim a próxima chamada a
        # client.connect() bloqueia para sempre e a thread nunca mais chama
        # webui.set_error()/expire_code(), que corromperiam o webui._state
        # usado por outros arquivos de teste (test_webui.py, entre outros).
        bot.client = _ClienteTravaParaSempre()
        bot.SUPERVISOR_INTERVAL_S = orig_interval


def test_revives_antigos_somem_mesmo_sem_a_thread_morrer_de_novo():
    """REGRESSÃO: a poda dos revives só rodava dentro do `if morta`. Depois
    que a thread se estabilizava (parava de cair), `morta` nunca mais era
    True — a lista de revives antigos ficava presa acima do limite pra
    sempre, e o /health continuava recusando um problema que já tinha se
    resolvido sozinho minutos antes."""
    orig_interval = bot.SUPERVISOR_INTERVAL_S
    bot.client = _ClienteTravaParaSempre()  # thread sobe e nunca mais cai
    bot.SUPERVISOR_INTERVAL_S = 0.03
    with bot._conn_lock:
        # revives "antigos" (fora da janela) — antes do fix, ficavam presos
        # na contagem pra sempre por não terem sido podados
        agora = time.time()
        bot._conn_state["restarts"] = [
            agora - bot.JANELA_CRASH - 10 for _ in range(bot.MAX_REVIVES_JANELA + 2)
        ]
        bot._conn_state["last_ok"] = agora
        bot._conn_state["loop_thread"] = None
    with webui._lock:
        webui._state["connected"] = False
        webui._state["healthy"] = True

    os.environ["PORT"] = "0"
    try:
        threading.Thread(target=bot.connect_web, args=("",), daemon=True).start()

        # dá tempo pra thread subir e o vigia rodar alguns ciclos (sem
        # nenhuma morte nova — a thread trava para sempre em connect())
        deadline = time.time() + 3
        podado = False
        while time.time() < deadline:
            with bot._conn_lock:
                if len(bot._conn_state["restarts"]) <= bot.MAX_REVIVES_JANELA:
                    podado = True
                    break
            time.sleep(0.02)

        assert podado, "revives antigos não foram podados sem a thread morrer de novo"
        with webui._lock:
            healthy = webui._state["healthy"]
        assert healthy is True, "health deveria se recuperar depois da poda"
        print("✓ revives antigos são podados mesmo sem a thread morrer de novo")
    finally:
        bot.client = _ClienteTravaParaSempre()
        bot.SUPERVISOR_INTERVAL_S = orig_interval


def test_crash_loop_persistente_derruba_o_health():
    """Se a thread continuar morrendo repetidas vezes (não um erro isolado),
    o /health deve acusar problema — é o sinal para o Render reiniciar o
    container inteiro, como último recurso."""
    orig_interval = bot.SUPERVISOR_INTERVAL_S
    bot.client = _FakeClientInstavel()
    bot.SUPERVISOR_INTERVAL_S = 0.03
    with bot._conn_lock:
        bot._conn_state["restarts"] = []
        bot._conn_state["last_ok"] = time.time()
        bot._conn_state["loop_thread"] = None
    with webui._lock:
        webui._state["connected"] = False
        webui._state["healthy"] = True

    os.environ["PORT"] = "0"
    try:
        threading.Thread(
            target=bot.connect_web, args=("",), daemon=True
        ).start()

        deadline = time.time() + 3
        while time.time() < deadline:
            with bot._conn_lock:
                if bot._conn_state["loop_thread"] is not None:
                    break
            time.sleep(0.02)

        # mata a thread MAX_REVIVES_JANELA+1 vezes, sempre rápido o
        # suficiente para o vigia não conseguir "descansar" entre uma
        # revivida e a próxima morte simulada
        for _ in range(bot.MAX_REVIVES_JANELA + 2):
            morta = threading.Thread(target=lambda: None)
            morta.start()
            morta.join()
            with bot._conn_lock:
                bot._conn_state["loop_thread"] = morta
            time.sleep(bot.SUPERVISOR_INTERVAL_S * 2)

        # mais um ciclo do vigia pra ele recalcular saudavel com o total
        time.sleep(bot.SUPERVISOR_INTERVAL_S * 3)

        with webui._lock:
            healthy = webui._state["healthy"]
        assert healthy is False, "crash-loop persistente deveria derrubar /health"
        print("✓ crash-loop persistente derruba /health (aciona restart do container)")
    finally:
        # connect_web() nunca retorna — a(s) thread(s) que ela subiu continuam
        # rodando pro resto do processo de teste. Trava o client num que nunca
        # responde, em vez de restaurar o original: assim a próxima chamada a
        # client.connect() bloqueia para sempre e a thread nunca mais chama
        # webui.set_error()/expire_code(), que corromperiam o webui._state
        # usado por outros arquivos de teste (test_webui.py, entre outros).
        bot.client = _ClienteTravaParaSempre()
        bot.SUPERVISOR_INTERVAL_S = orig_interval
        with webui._lock:
            webui._state["healthy"] = True


if __name__ == "__main__":
    test_conectado_e_sempre_saudavel()
    test_desconectado_mas_recente_e_saudavel()
    test_desconectado_ha_muito_tempo_e_insalubre()
    test_no_limiar_exato_ainda_saudavel()
    test_crash_loop_e_insalubre_mesmo_com_progresso_recente()
    test_poucas_reincidencias_nao_derruba_saude()
    test_mark_alive_atualiza_timestamp()
    test_vigia_revive_thread_morta_e_processo_continua_vivo()
    test_revives_antigos_somem_mesmo_sem_a_thread_morrer_de_novo()
    test_crash_loop_persistente_derruba_o_health()
    print("\n✅ VIGIA DE SOBREVIVÊNCIA OK")
