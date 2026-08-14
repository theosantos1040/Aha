"""Testa o orçamento anti rate-limit dos pedidos de código de pareamento.

Cada PairPhone é um pedido real ao WhatsApp: notifica o celular e invalida o
código anterior. Pedir demais faz o WhatsApp parar de entregar o código sem
devolver erro nenhum — daí o limite e o intervalo mínimo entre pedidos.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DB", "/tmp/test_pairing.sqlite3")

import bot


def _reset_budget():
    bot._pair_count[0] = 0
    bot._pair_last_ts[0] = 0.0


def test_primeiro_pedido_passa():
    _reset_budget()
    ok, motivo = bot._pair_budget(consume=True)
    assert ok, motivo
    assert bot._pair_count[0] == 1
    print("✓ primeiro pedido de código é liberado")


def test_cooldown_bloqueia_pedido_seguido():
    """Dois pedidos em sequência: o segundo espera o cooldown."""
    _reset_budget()
    assert bot._pair_budget(consume=True)[0]

    ok, motivo = bot._pair_budget(consume=True)
    assert not ok, "cooldown não bloqueou o segundo pedido"
    assert "aguarde" in motivo.lower()
    assert bot._pair_count[0] == 1, "pedido bloqueado não pode consumir orçamento"
    print("✓ cooldown bloqueia pedidos em sequência")


def test_limite_total_de_pedidos():
    """Depois de PAIR_MAX_REQUESTS o bot para de pedir e manda usar o QR."""
    _reset_budget()
    for i in range(bot.PAIR_MAX_REQUESTS):
        bot._pair_last_ts[0] = 0.0  # ignora o cooldown p/ testar só o teto
        ok, motivo = bot._pair_budget(consume=True)
        assert ok, f"pedido {i + 1} deveria passar: {motivo}"

    bot._pair_last_ts[0] = 0.0
    ok, motivo = bot._pair_budget(consume=True)
    assert not ok, "estourou o limite e ainda liberou"
    assert "limite" in motivo.lower()
    assert "qr" in motivo.lower(), "a mensagem deveria sugerir o QR como saída"
    assert bot._pair_count[0] == bot.PAIR_MAX_REQUESTS
    print(f"✓ limite de {bot.PAIR_MAX_REQUESTS} pedidos é respeitado")


def test_consulta_nao_consome():
    """_pair_budget() sem consume=True só consulta, não gasta o orçamento."""
    _reset_budget()
    for _ in range(10):
        assert bot._pair_budget()[0]
    assert bot._pair_count[0] == 0, "consulta não pode consumir orçamento"
    print("✓ consultar o orçamento não gasta pedido")


def test_cooldown_libera_depois_do_tempo():
    _reset_budget()
    assert bot._pair_budget(consume=True)[0]
    # simula um pedido feito há mais tempo que o cooldown
    bot._pair_last_ts[0] = time.time() - (bot.PAIR_COOLDOWN + 1)
    ok, motivo = bot._pair_budget(consume=True)
    assert ok, f"cooldown deveria ter expirado: {motivo}"
    assert bot._pair_count[0] == 2
    print("✓ cooldown libera depois do tempo")


if __name__ == "__main__":
    test_primeiro_pedido_passa()
    test_cooldown_bloqueia_pedido_seguido()
    test_limite_total_de_pedidos()
    test_consulta_nao_consome()
    test_cooldown_libera_depois_do_tempo()
    _reset_budget()
    print("\n✅ ORÇAMENTO DE PAREAMENTO OK")
