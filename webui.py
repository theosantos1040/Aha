"""Servidor web leve de pareamento (QR ou código de 6-8 dígitos).

Usado quando o bot roda num host sem terminal interativo (Render, VPS
headless etc.): em vez de imprimir o QR/código no console, expõe uma
página HTML simples que mostra o QR (atualizado automaticamente) e um
formulário para gerar o código de pareamento a partir do número.

Não usa Flask nem nenhuma dependência extra — só `http.server` da stdlib.
"""
import base64
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

_lock = threading.Lock()
_state = {
    "qr_png": None,      # bytes do PNG do QR atual (ou None)
    "code": None,        # código de pareamento já formatado (ou None)
    "connected": False,  # já pareado com o WhatsApp?
    "error": None,
    "requesting": False,
}
_bot_name = ["ThzyxBoTS"]
_pair_callback = None  # func(numero: str) chamada quando o form é enviado


def set_qr(png_bytes: bytes):
    with _lock:
        _state["qr_png"] = png_bytes
        _state["code"] = None
        _state["error"] = None


def set_code(code: str):
    with _lock:
        _state["code"] = code
        _state["qr_png"] = None
        _state["error"] = None
        _state["requesting"] = False


def set_connected():
    with _lock:
        _state["connected"] = True
        _state["qr_png"] = None
        _state["code"] = None


def set_error(msg: str):
    with _lock:
        _state["error"] = msg
        _state["requesting"] = False


def revoke_code():
    """Cancela/revoga o código atual e limpa o estado (volta pra tela de input)."""
    with _lock:
        _state["code"] = None
        _state["error"] = None


PAGE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{bot_name} — Pareamento</title>
<style>
  body {{ font-family: system-ui, sans-serif; background:#0b141a; color:#e9edef;
         display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
  .card {{ background:#202c33; padding:32px; border-radius:16px; max-width:420px;
           width:90%; text-align:center; box-shadow:0 8px 24px rgba(0,0,0,.4); }}
  h1 {{ font-size:1.3rem; margin-bottom:4px; }}
  p.sub {{ color:#8696a0; margin-top:0; font-size:.9rem; }}
  img {{ width:220px; height:220px; border-radius:8px; background:#fff; padding:8px; margin:16px 0; }}
  .code {{ font-size:2rem; letter-spacing:.15em; font-weight:700; color:#00a884;
           margin:20px 0; background:#111b21; padding:16px; border-radius:10px; }}
  form {{ margin-top:16px; }}
  input {{ padding:10px 14px; border-radius:8px; border:1px solid #3b4a54;
           background:#2a3942; color:#e9edef; font-size:1rem; width:65%; }}
  button {{ padding:10px 16px; border-radius:8px; border:none; background:#00a884;
            color:#fff; font-weight:600; cursor:pointer; margin-left:6px; }}
  button:hover {{ background:#02906f; }}
  button.secondary {{ background:#3b4a54; }}
  button.secondary:hover {{ background:#4a5a64; }}
  .code-actions {{ margin-top:16px; }}
  .err {{ color:#f15c6d; margin-top:12px; font-size:.85rem; }}
  .steps {{ text-align:left; color:#8696a0; font-size:.8rem; margin-top:16px; line-height:1.5; }}
  .hidden {{ display:none; }}
</style>
</head>
<body>
  <div class="card" id="card">
    <div id="connectedView" class="hidden">
      <h1>✅ Conectado!</h1>
      <p class="sub">{bot_name} já está pareado com o WhatsApp.</p>
    </div>
    <div id="pairView">
      <h1>🔗 Parear {bot_name}</h1>
      <p class="sub">Escaneie o QR ou peça um código pelo número.</p>
      <img id="qrImg" class="hidden" alt="QR Code">
      <p class="sub hidden" id="qrHint">WhatsApp &gt; Aparelhos conectados &gt; Conectar um aparelho</p>
      <div class="code hidden" id="codeBox"></div>
      <p class="sub hidden" id="codeHint">WhatsApp &gt; Aparelhos conectados &gt; Conectar com número de telefone</p>
      <div class="code-actions hidden" id="codeActions">
        <button type="button" id="revokeBtn" class="secondary">Revogar código</button>
      </div>
      <form id="pairForm">
        <input id="numberInput" name="number" placeholder="Ex: 5511999999999"
               required pattern="[0-9]+" inputmode="numeric" autocomplete="off">
        <button type="submit" id="pairBtn">Gerar código</button>
      </form>
      <p class="steps" id="stepsText">Digite DDI+DDD+número (só dígitos) para receber um
        código, ou aguarde o QR Code acima.</p>
      <p class="err hidden" id="errText"></p>
    </div>
  </div>
  <script>
    const form = document.getElementById('pairForm');
    form.addEventListener('submit', async (e) => {{
      e.preventDefault();
      const btn = document.getElementById('pairBtn');
      btn.disabled = true;
      btn.textContent = 'Enviando…';
      try {{
        await fetch('/pair', {{ method: 'POST', body: new URLSearchParams(new FormData(form)) }});
      }} catch (err) {{ /* próximo tick mostra o erro, se houver */ }}
      setTimeout(() => {{ btn.disabled = false; btn.textContent = 'Gerar código'; }}, 3000);
    }});

    document.getElementById('revokeBtn').addEventListener('click', async () => {{
      try {{
        await fetch('/revoke', {{ method: 'POST' }});
      }} catch (err) {{ /* próximo tick mostra o estado, se houver */ }}
    }});

    let lastQr = null;
    // Só troca o que realmente mudou — NUNCA recria o <input>, senão o
    // teclado do celular fecha e o usuário perde o que estava digitando.
    async function tick() {{
      let s;
      try {{ s = await (await fetch('/status.json')).json(); }} catch (e) {{ return; }}

      if (s.connected) {{
        document.getElementById('connectedView').classList.remove('hidden');
        document.getElementById('pairView').classList.add('hidden');
        return;
      }}

      const qrImg = document.getElementById('qrImg');
      if (s.qr && s.qr !== lastQr) {{
        qrImg.src = 'data:image/png;base64,' + s.qr;
        lastQr = s.qr;
      }}
      qrImg.classList.toggle('hidden', !s.qr);
      document.getElementById('qrHint').classList.toggle('hidden', !s.qr);

      const codeBox = document.getElementById('codeBox');
      if (s.code) codeBox.textContent = s.code;
      codeBox.classList.toggle('hidden', !s.code);
      document.getElementById('codeHint').classList.toggle('hidden', !s.code);
      document.getElementById('codeActions').classList.toggle('hidden', !s.code);
      form.classList.toggle('hidden', !!s.code);
      document.getElementById('stepsText').classList.toggle('hidden', !!s.code);

      const errText = document.getElementById('errText');
      if (s.error) {{
        errText.textContent = '⚠️ ' + s.error;
        errText.classList.remove('hidden');
      }} else {{
        errText.classList.add('hidden');
      }}
    }}
    tick();
    setInterval(tick, 2500);
  </script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silencia log de acesso no stdout

    def _send(self, code, content_type, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.format(bot_name=html.escape(_bot_name[0])).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        elif self.path == "/status.json":
            with _lock:
                payload = {
                    "connected": _state["connected"],
                    "qr": base64.b64encode(_state["qr_png"]).decode() if _state["qr_png"] else None,
                    "code": _state["code"],
                    "error": _state["error"],
                }
            self._send(200, "application/json", json.dumps(payload).encode("utf-8"))
        elif self.path == "/health":
            self._send(200, "text/plain", b"ok")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if self.path == "/revoke":
            revoke_code()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path != "/pair":
            self._send(404, "text/plain", b"not found")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="ignore")
        fields = parse_qs(raw)
        number = "".join(c for c in fields.get("number", [""])[0] if c.isdigit())
        with _lock:
            already = _state["requesting"]
            if number:
                _state["requesting"] = True
        if number and not already and _pair_callback:
            threading.Thread(target=_pair_callback, args=(number,), daemon=True).start()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def start(bot_name: str, port: int, pair_callback):
    """Sobe o servidor de pareamento em background (não bloqueia)."""
    global _pair_callback
    _bot_name[0] = bot_name
    _pair_callback = pair_callback
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
