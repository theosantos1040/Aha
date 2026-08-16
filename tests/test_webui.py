"""Testa a página de pareamento (webui): validade do código, QR e revogação.

O bug que impedia parear por código: o whatsmeow renova o QR a cada ~20s e
`set_qr()` apagava o código da tela junto — o usuário via o código sumir antes
de conseguir digitá-lo no celular.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui


def _reset():
    with webui._lock:
        webui._state.update({
            "qr_png": None, "qr_time": None, "code": None, "code_time": None,
            "connected": False, "error": None, "requesting": False,
        })


def test_qr_nao_apaga_codigo():
    """REGRESSÃO: renovar o QR não pode derrubar o código de pareamento."""
    _reset()
    webui.set_code("ABCD-1234")
    assert webui._state["code"] == "ABCD-1234"

    # whatsmeow renova o QR várias vezes durante a mesma conexão
    for _ in range(5):
        webui.set_qr(b"\x89PNG-falso")

    assert webui._state["code"] == "ABCD-1234", "o QR apagou o código (bug antigo)"
    assert webui.code_is_live()
    print("✓ QR renova sem apagar o código de pareamento")


def test_codigo_expira_com_a_conexao():
    """O código só vale enquanto a conexão que o emitiu viver."""
    _reset()
    webui.set_code("WXYZ-9999")
    assert webui.code_is_live()

    # simula um código emitido há mais tempo que o TTL
    with webui._lock:
        webui._state["code_time"] = time.time() - (webui.CODE_TTL + 1)
    assert not webui.code_is_live(), "código velho deveria estar expirado"

    webui.expire_code()
    assert webui._state["code"] is None
    print("✓ código expira junto com a conexão (CODE_TTL)")


def test_revoke_chama_callback():
    """Revogar limpa a tela e avisa o bot para parar a renovação automática."""
    _reset()
    chamado = []
    webui._revoke_callback = lambda: chamado.append(True)
    try:
        webui.set_code("AAAA-1111")
        webui.revoke_code()
        assert webui._state["code"] is None
        assert not webui._state["requesting"]
        assert chamado, "revoke não avisou o bot"
    finally:
        webui._revoke_callback = None
    print("✓ revogar limpa o código e desliga a renovação automática")


def test_status_json_expira_sozinho():
    """A página recebe code_age e o código morto some sozinho do status."""
    _reset()
    server = webui.start("TesteBot", 0, lambda n: None)
    port = server.server_address[1]
    try:
        webui.set_code("BBBB-2222")
        s = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json").read())
        assert s["code"] == "BBBB-2222"
        assert s["code_age"] is not None

        # envelhece o código além do TTL: o status deve devolvê-lo como None
        with webui._lock:
            webui._state["code_time"] = time.time() - (webui.CODE_TTL + 1)
        s = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json").read())
        assert s["code"] is None, "status.json devolveu código expirado"
    finally:
        server.shutdown()
        _reset()
    print("✓ status.json envelhece e descarta o código sozinho")


if __name__ == "__main__":
    test_qr_nao_apaga_codigo()
    test_codigo_expira_com_a_conexao()
    test_revoke_chama_callback()
    test_status_json_expira_sozinho()
    print("\n✅ WEBUI OK")
