"""Servidor web leve de pareamento (QR ou código de 6-8 dígitos).

Usado quando o bot roda num host sem terminal interativo (Render, VPS
headless etc.): em vez de imprimir o QR/código no console, expõe uma
página HTML simples que mostra o QR e um formulário para gerar o código
de pareamento a partir do número.

O próprio whatsmeow (por baixo do neonize) já renova o QR Code sozinho
várias vezes dentro da MESMA conexão — este módulo só precisa exibir
cada QR novo assim que `set_qr()` é chamado de novo pelo bot.

Não usa Flask nem nenhuma dependência extra — só `http.server` da stdlib.
"""
import base64
import html
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

QR_TTL = 20   # cada QR do whatsmeow vale ~20s (barra visual)
CODE_TTL = 120  # o código de pareamento só vale enquanto a conexão viver (~2 min)

_lock = threading.Lock()
_state = {
    "qr_png": None,      # bytes do PNG do QR atual (ou None)
    "qr_time": None,     # epoch em que o QR foi gerado
    "code": None,        # código de pareamento já formatado (ou None)
    "code_time": None,   # epoch em que o código foi emitido
    "connected": False,  # já pareado com o WhatsApp?
    "error": None,
    "requesting": False,
    "healthy": True,     # usado só pelo /health — ver set_health()
}
_bot_name = ["ThzyxBoTS"]
_pair_callback = None    # func(numero: str) chamada quando o form é enviado
_revoke_callback = None  # func() chamada quando o usuário revoga o código


def set_qr(png_bytes: bytes):
    """Publica um QR novo.

    NÃO mexe no código de pareamento: o whatsmeow renova o QR a cada ~20s e,
    se isso apagasse o código, ele sumiria da tela antes do usuário conseguir
    digitá-lo no celular (era exatamente por isso que parear por código nunca
    funcionava).
    """
    with _lock:
        _state["qr_png"] = png_bytes
        _state["qr_time"] = time.time()
        _state["error"] = None


def set_code(code: str):
    with _lock:
        _state["code"] = code
        _state["code_time"] = time.time()
        _state["error"] = None
        _state["requesting"] = False


def code_is_live() -> bool:
    """True se existe um código ainda dentro da validade."""
    with _lock:
        if not _state["code"] or not _state["code_time"]:
            return False
        return (time.time() - _state["code_time"]) < CODE_TTL


def expire_code():
    """Invalida o código atual — usado quando a conexão que o emitiu morreu.

    O código de pareamento do WhatsApp só vale enquanto o socket que o gerou
    estiver vivo. Deixar o código antigo na tela faria o usuário digitar um
    código morto e o pareamento falharia sem explicação.
    """
    with _lock:
        _state["code"] = None
        _state["code_time"] = None
        _state["requesting"] = False


def set_connected():
    with _lock:
        _state["connected"] = True
        _state["qr_png"] = None
        _state["qr_time"] = None
        _state["code"] = None
        _state["code_time"] = None
        _state["error"] = None


def set_error(msg: str):
    """Publica um erro COSMÉTICO — não mexe em `connected`.

    Usado por falhas do FORMULÁRIO de pareamento (número inválido, cooldown
    do orçamento anti-bloqueio, "não consegui gerar o código"...), que podem
    acontecer mesmo com o bot já conectado — ex.: alguém reenviando uma aba
    antiga da página depois que o pareamento já deu certo. Se isso derrubasse
    `connected`, a página passaria a mostrar "desconectado" para um bot que
    está funcionando normalmente. Para uma queda de conexão DE VERDADE, use
    set_disconnected().
    """
    with _lock:
        _state["error"] = msg
        _state["requesting"] = False


def set_disconnected(msg: str):
    """Publica um erro E derruba `connected` — para quando o WhatsApp caiu
    de fato (handle_disconnected, handle_stream_error, handle_logged_out...).

    Sem isso, uma queda de conexão DEPOIS de já ter pareado deixava
    `connected=True` para sempre: a página continuava mostrando "conectado"
    mesmo com o WhatsApp fora do ar, e o vigia de sobrevivência
    (bot._supervisor) usa esse mesmo campo para decidir se está tudo bem —
    ficaria cego pro problema.
    """
    with _lock:
        _state["error"] = msg
        _state["requesting"] = False
        _state["connected"] = False


def set_health(ok: bool):
    """Liga/desliga o resultado do /health.

    Usado pelo vigia de sobrevivência (bot._supervisor) como ÚLTIMO recurso:
    a reconexão em si já se recupera sozinha por dentro do processo, então só
    chega a marcar healthy=False quando isso falhar repetidamente — aí vale
    deixar o Render reiniciar o container inteiro (healthCheckPath: /health
    no render.yaml), que pode limpar algum problema que o retry interno não
    resolve (rede do container travada, etc.).
    """
    with _lock:
        _state["healthy"] = ok


def revoke_code():
    """Cancela/revoga o código atual e volta pra tela de input."""
    with _lock:
        _state["code"] = None
        _state["code_time"] = None
        _state["error"] = None
        _state["requesting"] = False
    if _revoke_callback:
        try:
            _revoke_callback()
        except Exception:
            pass


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
  img {{ width:220px; height:220px; border-radius:8px; background:#fff;
         padding:8px; margin:12px 0 4px; display:block; margin-left:auto; margin-right:auto; }}
  .countdown-bar-wrap {{ width:220px; margin:4px auto 10px; height:6px;
                         background:#3b4a54; border-radius:3px; overflow:hidden; }}
  .countdown-bar {{ height:100%; background:#00a884; border-radius:3px;
                    transition:width 1s linear, background 0.4s; }}
  .countdown-bar.urgent {{ background:#f15c6d; }}
  .countdown-label {{ font-size:.75rem; color:#8696a0; margin-bottom:8px; }}
  .code {{ font-size:2rem; letter-spacing:.15em; font-weight:700; color:#00a884;
           margin:20px 0; background:#111b21; padding:16px; border-radius:10px; }}
  form {{ margin-top:16px; }}
  input {{ padding:10px 14px; border-radius:8px; border:1px solid #3b4a54;
           background:#2a3942; color:#e9edef; font-size:1rem; width:65%; }}
  button {{ padding:10px 16px; border-radius:8px; border:none; background:#00a884;
            color:#fff; font-weight:600; cursor:pointer; margin-left:6px; }}
  button:hover {{ background:#02906f; }}
  button.secondary {{ background:#3b4a54; margin-left:0; }}
  button.secondary:hover {{ background:#4a5a64; }}
  .code-actions {{ margin-top:16px; display:flex; gap:8px; justify-content:center; flex-wrap:wrap; }}
  .spinner {{ display:inline-block; width:40px; height:40px; border:4px solid #3b4a54;
              border-top-color:#00a884; border-radius:50%; animation:spin 0.8s linear infinite;
              margin:24px auto; }}
  @keyframes spin {{ to {{ transform:rotate(360deg) }} }}
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

      <!-- QR e barra de expiração -->
      <div id="qrSection" class="hidden">
        <img id="qrImg" alt="QR Code">
        <div class="countdown-bar-wrap">
          <div class="countdown-bar" id="countdownBar" style="width:100%"></div>
        </div>
        <p class="countdown-label" id="countdownLabel">renova sozinho em breve</p>
        <p class="sub" style="margin-top:0">
          WhatsApp &gt; Aparelhos conectados &gt; Conectar um aparelho
        </p>
      </div>

      <!-- Spinner enquanto aguarda o primeiro QR chegar -->
      <div id="refreshingView">
        <div class="spinner"></div>
        <p class="sub">Conectando ao WhatsApp…</p>
      </div>

      <!-- Código de pareamento -->
      <div class="code hidden" id="codeBox"></div>
      <p class="countdown-label hidden" id="codeCountdown"></p>
      <p class="sub hidden" id="codeHint">
        WhatsApp &gt; Aparelhos conectados &gt; Conectar com número de telefone
      </p>
      <div class="code-actions hidden" id="codeActions">
        <button type="button" id="revokeBtn" class="secondary">Revogar código</button>
      </div>

      <!-- Formulário de número -->
      <form id="pairForm">
        <input id="numberInput" name="number" placeholder="Ex: 5511999999999"
               required pattern="[0-9]+" inputmode="numeric" autocomplete="off">
        <button type="submit" id="pairBtn">Gerar código</button>
      </form>
      <p class="steps" id="stepsText">
        Digite DDI+DDD+número (só dígitos) para receber um código,
        ou aguarde o QR Code acima.
      </p>
      <p class="err hidden" id="errText"></p>
    </div>
  </div>
  <script>
    const QR_TTL = {qr_ttl};
    const CODE_TTL = {code_ttl};
    const form = document.getElementById('pairForm');

    form.addEventListener('submit', async (e) => {{
      e.preventDefault();
      const btn = document.getElementById('pairBtn');
      btn.disabled = true; btn.textContent = 'Enviando…';
      try {{ await fetch('/pair', {{ method:'POST', body: new URLSearchParams(new FormData(form)) }}); }}
      catch (err) {{}}
      setTimeout(() => {{ btn.disabled = false; btn.textContent = 'Gerar código'; }}, 3000);
    }});

    document.getElementById('revokeBtn').addEventListener('click', async () => {{
      try {{ await fetch('/revoke', {{ method:'POST' }}); }} catch (err) {{}}
    }});

    let lastQr = null;
    let countdownInterval = null;

    function startCountdown(qrAge) {{
      clearInterval(countdownInterval);
      const bar = document.getElementById('countdownBar');
      const label = document.getElementById('countdownLabel');
      let remaining = Math.max(0, QR_TTL - qrAge);

      function update() {{
        const pct = Math.max(0, (remaining / QR_TTL) * 100);
        bar.style.width = pct + '%';
        bar.classList.toggle('urgent', remaining <= 10);
        label.textContent = remaining > 0 ? 'renova em ' + remaining + 's' : 'renovando…';
        if (remaining <= 0) clearInterval(countdownInterval);
        remaining = Math.max(0, remaining - 1);
      }}
      update();
      countdownInterval = setInterval(update, 1000);
    }}

    async function tick() {{
      let s;
      try {{ s = await (await fetch('/status.json')).json(); }} catch (e) {{ return; }}

      if (s.connected) {{
        clearInterval(countdownInterval);
        document.getElementById('connectedView').classList.remove('hidden');
        document.getElementById('pairView').classList.add('hidden');
        return;
      }}

      const hasCode = !!s.code;

      // QR section — fica escondida enquanto houver um código válido na tela,
      // pra não competir com ele (o QR continua sendo renovado por baixo).
      const qrSection = document.getElementById('qrSection');
      const qrImg = document.getElementById('qrImg');
      if (s.qr && s.qr !== lastQr) {{
        qrImg.src = 'data:image/png;base64,' + s.qr;
        lastQr = s.qr;
        startCountdown(s.qr_age || 0);
      }}
      qrSection.classList.toggle('hidden', hasCode || !s.qr);

      // Spinner: só aparece antes do primeiro QR/código/erro chegar
      const waitingFirst = !s.qr && !hasCode && !s.error;
      document.getElementById('refreshingView').classList.toggle('hidden', !waitingFirst);

      // Código + validade real (o código morre junto com a conexão)
      const codeBox = document.getElementById('codeBox');
      if (hasCode) codeBox.textContent = s.code;
      codeBox.classList.toggle('hidden', !hasCode);
      document.getElementById('codeHint').classList.toggle('hidden', !hasCode);
      document.getElementById('codeActions').classList.toggle('hidden', !hasCode);

      const cd = document.getElementById('codeCountdown');
      cd.classList.toggle('hidden', !hasCode);
      if (hasCode) {{
        const left = Math.max(0, Math.round(CODE_TTL - (s.code_age || 0)));
        cd.textContent = left > 0
          ? 'válido por mais ' + left + 's — depois um novo é gerado sozinho'
          : 'expirado, gerando outro…';
      }}

      // Formulário — esconde enquanto código está visível
      form.classList.toggle('hidden', hasCode);
      document.getElementById('stepsText').classList.toggle('hidden', hasCode);

      // Erro
      const errText = document.getElementById('errText');
      if (s.error) {{
        errText.textContent = '⚠️ ' + s.error;
        errText.classList.remove('hidden');
      }} else {{
        errText.classList.add('hidden');
      }}
    }}
    tick();
    setInterval(tick, 2000);
  </script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, content_type, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.format(
                bot_name=html.escape(_bot_name[0]),
                qr_ttl=QR_TTL,
                code_ttl=CODE_TTL,
            ).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        elif self.path == "/status.json":
            # o código morre junto com a conexão que o emitiu — some sozinho
            # quando passa do tempo, em vez de deixar um código morto na tela
            if _state["code"] and not code_is_live():
                expire_code()
            with _lock:
                now = time.time()
                qr_age = round(now - _state["qr_time"], 1) if _state["qr_time"] else None
                code_age = round(now - _state["code_time"], 1) if _state["code_time"] else None
                payload = {
                    "connected": _state["connected"],
                    "qr": base64.b64encode(_state["qr_png"]).decode() if _state["qr_png"] else None,
                    "qr_age": qr_age,
                    "code": _state["code"],
                    "code_age": code_age,
                    "error": _state["error"],
                }
            self._send(200, "application/json", json.dumps(payload).encode("utf-8"))
        elif self.path == "/health":
            with _lock:
                ok = _state["healthy"]
            self._send(200 if ok else 503, "text/plain", b"ok" if ok else b"unhealthy")
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


def start(bot_name: str, port: int, pair_callback, revoke_callback=None):
    """Sobe o servidor de pareamento em background (não bloqueia)."""
    global _pair_callback, _revoke_callback
    _bot_name[0] = bot_name
    _pair_callback = pair_callback
    _revoke_callback = revoke_callback
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
