"""Testa os handlers de comando com um cliente WhatsApp FALSO.

Constrói mensagens proto reais do neonize e injeta um cliente mock em
bot.client, exercitando o roteamento e a maioria dos comandos offline.
"""
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DB", "/tmp/test_cmds.sqlite3")
if os.path.exists("/tmp/test_cmds.sqlite3"):
    os.remove("/tmp/test_cmds.sqlite3")

import neonize.proto.Neonize_pb2 as N
from neonize.utils.jid import build_jid

import config
config.DATA_DB = "/tmp/test_cmds.sqlite3"
import database
database.init("/tmp/test_cmds.sqlite3")

import bot

GROUP = build_jid("123456789-987654321", "g.us")
SENDER = build_jid("5511999999999")
OTHER = build_jid("5511888888888")


def _real_png():
    """PNG real pequeno para os testes de figurinha (image_to_sticker)."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 150, 240)).save(buf, "PNG")
    return buf.getvalue()


class FakeClient:
    def __init__(self, admin=True):
        self.sent = []
        self.actions = []
        self.admin = admin

    def send_message(self, to, message, **kw):
        self.sent.append(str(message))

    def reply_message(self, message, quoted, to=None, **kw):
        self.sent.append(str(message))

    def send_video(self, to, file, caption=None, **kw):
        self.actions.append(("video", len(file) if hasattr(file, "__len__") else 0, caption))

    def send_image(self, to, file, caption=None, **kw):
        self.actions.append(("image", caption))

    def send_chat_presence(self, *a, **k):
        pass

    def get_group_info(self, jid):
        gi = N.GroupInfo()
        gi.GroupName.Name = "Grupo de Teste"
        gi.GroupCreated = 1600000000
        gi.GroupAnnounce.IsAnnounce = False
        p = gi.Participants.add()
        p.JID.CopyFrom(SENDER)
        p.IsAdmin = self.admin
        p2 = gi.Participants.add()
        p2.JID.CopyFrom(OTHER)
        return gi

    def get_group_invite_link(self, jid, revoke=False):
        return "https://chat.whatsapp.com/FAKEINVITE"

    def get_profile_picture(self, jid, *a, **k):
        ppi = N.ProfilePictureInfo()
        ppi.URL = ""
        return ppi

    def update_group_participants(self, jid, parts, action):
        self.actions.append(("participants", action))
        return []

    def set_group_announce(self, jid, announce):
        self.actions.append(("announce", announce))

    def build_poll_vote_creation(self, name, options, vt, quoted=None):
        self.actions.append(("poll", name, options))
        return N.Message()

    def download_any(self, message, path=None):
        return _real_png()

    def download_media_with_path(self, direct_path, enc, fhash, mkey, flen, mtype, mms):
        return _real_png()

    def send_sticker(self, to, file, **kw):
        self.actions.append(("sticker", len(file)))

    def send_audio(self, to, file, ptt=False, **kw):
        self.actions.append(("audio", len(file)))

    def send_document(self, to, file, filename=None, mimetype=None, caption=None, **kw):
        self.actions.append(("document", filename))

    def revoke_message(self, chat, sender, message_id):
        self.actions.append(("revoke", message_id))
        return None


def make_msg(text, sender=SENDER, mentions=None, is_group=True):
    msg = N.Message()
    msg.Info.MessageSource.Chat.CopyFrom(GROUP if is_group else sender)
    msg.Info.MessageSource.Sender.CopyFrom(sender)
    msg.Info.MessageSource.IsGroup = is_group
    msg.Info.MessageSource.IsFromMe = False
    msg.Info.ID = "ABC123"
    if mentions:
        msg.Message.extendedTextMessage.text = text
        for m in mentions:
            msg.Message.extendedTextMessage.contextInfo.mentionedJID.append(m)
    else:
        msg.Message.conversation = text
    return msg


def make_media_msg(text, media="video"):
    msg = N.Message()
    msg.Info.MessageSource.Chat.CopyFrom(GROUP)
    msg.Info.MessageSource.Sender.CopyFrom(SENDER)
    msg.Info.MessageSource.IsGroup = True
    msg.Info.ID = "MEDIA1"
    if media == "video":
        msg.Message.videoMessage.mediaKey = b"k" * 32
        msg.Message.videoMessage.caption = text
    elif media == "image":
        msg.Message.imageMessage.mediaKey = b"k" * 32
        msg.Message.imageMessage.caption = text
    return msg


def run(text, admin=True, mentions=None, is_group=True):
    fake = FakeClient(admin=admin)
    bot.client = fake
    msg = make_msg(text, mentions=mentions, is_group=is_group)
    bot.handle_command(msg, text)
    return fake


def run_media(text, media="video"):
    fake = FakeClient(admin=True)
    bot.client = fake
    msg = make_media_msg(text, media)
    bot.handle_command(msg, text)
    return fake


def run_event(text, sender=OTHER, msg_id="MID1"):
    """Dispara on_message (para testar mute/antilink/antispam/manutenção)."""
    fake = FakeClient(admin=True)  # SENDER é admin; OTHER não é
    bot.client = fake
    msg = N.Message()
    msg.Info.MessageSource.Chat.CopyFrom(GROUP)
    msg.Info.MessageSource.Sender.CopyFrom(sender)
    msg.Info.MessageSource.IsGroup = True
    msg.Info.MessageSource.IsFromMe = False
    msg.Info.ID = msg_id
    msg.Message.conversation = text
    bot.handle_message(msg)
    return fake


def test_utility_commands():
    assert "4" in run("/calc 2+2").sent[0]
    assert "Pong" in run("/ping").sent[0]
    assert "Menu" in run("/help").sent[0]
    assert "Online" in run("/uptime").sent[0]
    assert "Clima" in run("/weather Lisboa").sent[0]
    assert run("/userinfo").sent
    assert "Grupo de Teste" in run("/serverinfo").sent[0]
    assert "FAKEINVITE" in run("/invite").sent[0]
    print("✓ utilitários")


def test_economy_levels():
    assert "moedas" in run("/balance").sent[0]
    out = run("/daily").sent[0]
    assert "moedas" in out or "resgat" in out
    assert "Nível" in run("/level").sent[0]
    assert run("/leaderboard").sent
    # pay para outro usuário
    assert run("/pay 5511888888888 10", mentions=None).sent
    print("✓ economia/níveis")


def test_admin_commands():
    # como admin
    assert "bloqueado" in run("/lock").sent[0].lower()
    assert run("/lock").actions == [("announce", True)]
    assert run("/unlock").actions == [("announce", False)]
    assert "Comunicado".lower() in run("/announce Olá a todos").sent[0].lower()
    # warn + checkwarns
    w = run("/warn 5511888888888 spam")
    assert "Advertência" in w.sent[0]
    assert "Advertências" in run("/checkwarns 5511888888888").sent[0]
    # ban + unban
    run("/ban 5511888888888")
    assert database.is_banned(bot.Jid2String(GROUP), "5511888888888")
    unb = run("/unban 5511888888888")
    assert "banlist" in unb.sent[0]
    assert not database.is_banned(bot.Jid2String(GROUP), "5511888888888")
    # kick chama update_group_participants
    assert any(a[0] == "participants" for a in run("/kick 5511888888888").actions)
    # setprefix
    assert run("/setprefix !").sent
    database.set_prefix(bot.Jid2String(GROUP), "/")  # restaura
    # bloqueio para não-admin
    denied = run("/lock", admin=False)
    assert "administradores" in denied.sent[0].lower()
    print("✓ administração + bloqueio de não-admin")


def test_games():
    assert run("/coinflip").sent
    assert run("/jokenpo pedra").sent
    assert run("/8ball vou passar?").sent
    assert run("/roll 2d20").sent
    assert "%" in run("/ship ana bia").sent[0]
    assert run("/russianroulette").sent
    assert run("/akinator").sent
    # jogos com estado
    bot._active_games.clear()
    assert "Velha" in run("/tictactoe").sent[0]
    assert run("/tictactoe 1").sent  # jogada
    bot._active_games.clear()
    assert "Trivia" in run("/trivia").sent[0]
    bot._active_games.clear()
    assert "Forca" in run("/hangman").sent[0]
    assert run("/hangman a").sent
    print("✓ jogos")


def test_media_welcome():
    # /fg com imagem -> deve criar sticker (PIL, sem ffmpeg)
    fg_img = run_media("/fg", "image")
    assert any(a[0] == "sticker" for a in fg_img.actions), fg_img.sent
    # /fg com vídeo -> sticker animado se houver ffmpeg+libwebp; senão msg clara
    fg_vid = run_media("/fg", "video")
    out_vid = " ".join(fg_vid.sent).lower()
    assert any(a[0] == "sticker" for a in fg_vid.actions) or "ffmpeg" in out_vid or "libwebp" in out_vid
    # /fg sem mídia -> instruções
    assert "imagem" in run("/fg").sent[0].lower()
    # /va com vídeo -> tenta converter (sem ffmpeg dá msg de ffmpeg)
    va = run_media("/va", "video")
    out = " ".join(va.sent)
    assert ("ffmpeg" in out.lower()) or any(a[0] == "audio" for a in va.actions)
    # /va sem vídeo
    assert "vídeo" in run("/va").sent[0].lower()
    # /welcome on/off
    assert "ativadas" in run("/welcome on").sent[0]
    assert bot.db.get_setting(bot.Jid2String(GROUP), "welcome") == "1"
    assert "desativadas" in run("/welcome off").sent[0]
    print("✓ figurinha/áudio/boas-vindas")


def test_clear_and_mute():
    gstr = bot.Jid2String(GROUP)
    # popula histórico recente e testa /clear
    bot._recent_msgs[gstr].clear()
    for i in range(5):
        bot._recent_msgs[gstr].append((bot.Jid2String(OTHER), f"M{i}"))
    c = run("/clear 3")
    revokes = [a for a in c.actions if a[0] == "revoke"]
    assert len(revokes) == 3, revokes
    assert "Apaguei" in c.sent[-1]
    # MUTE: silencia OTHER, depois mensagem do OTHER deve ser revogada + aviso
    run("/mute 5511888888888")
    assert "muted" in bot.db.get_roles(gstr, "5511888888888")
    ev = run_event("oi pessoal", sender=OTHER, msg_id="MUTE_MSG")
    assert any(a[0] == "revoke" for a in ev.actions), ev.actions
    assert any("silenciado" in s for s in ev.sent), ev.sent
    # aviso limitado a 3x
    for i in range(5):
        run_event("spam", sender=OTHER, msg_id=f"M{i}")
    total_avisos = bot._mute_warns[(gstr, "5511888888888")]
    assert total_avisos == 3, total_avisos
    run("/unmute 5511888888888")
    assert "muted" not in bot.db.get_roles(gstr, "5511888888888")
    print("✓ clear + mute (apaga msgs + aviso 3x)")


def test_security():
    gstr = bot.Jid2String(GROUP)
    # ANTILINK
    assert "ativado" in run("/antilink on").sent[0]
    ev = run_event("entra nesse https://golpe.com/x", sender=OTHER, msg_id="L1")
    assert any(a[0] == "revoke" for a in ev.actions), ev.actions
    run("/antilink off")
    # admin/whitelist não é punido
    bot.db.whitelist_add(gstr, "5511888888888")
    assert bot.db.is_whitelisted(gstr, "5511888888888")
    run("/antilink on")
    ev2 = run_event("olha http://site.com", sender=OTHER, msg_id="L2")
    assert not any(a[0] == "revoke" for a in ev2.actions), "whitelist deveria isentar"
    bot.db.whitelist_remove(gstr, "5511888888888")
    run("/antilink off")
    # ANTISPAM: 4 mensagens iguais -> mute
    run("/antispam on")
    bot._spam_track.clear()
    last = None
    for i in range(4):
        last = run_event("mesma msg repetida", sender=OTHER, msg_id=f"S{i}")
    assert "muted" in bot.db.get_roles(gstr, "5511888888888"), "antispam deveria silenciar"
    run("/unmute 5511888888888")
    run("/antispam off")
    # toggles simples
    assert "ativado" in run("/antibot on").sent[0]
    run("/antibot off")
    assert run("/setlogs").sent
    assert bot.db.get_setting(gstr, "logchannel")
    print("✓ segurança (antilink/antispam/antibot/whitelist/setlogs)")


def test_global_tools():
    gstr = bot.Jid2String(GROUP)
    # maintenance bloqueia não-admin
    run("/maintenance on")
    blocked = run_event("/ping", sender=OTHER, msg_id="MNT1")
    assert any("manutenção" in s for s in blocked.sent), blocked.sent
    run("/maintenance off")
    # backup create/load
    bc = run("/backup-create")
    assert any(a[0] == "document" for a in bc.actions) or bc.sent
    bl = run("/backup-load 1")
    assert any(("restaurado" in s or "não encontrado" in s) for s in bl.sent), bl.sent
    # auditlog mostra ações
    al = run("/auditlog 5")
    assert any("auditoria" in s.lower() for s in al.sent), al.sent
    print("✓ ferramentas globais (maintenance/backup/auditlog)")


def test_antibot_event():
    gstr = bot.Jid2String(GROUP)
    run("/antibot on")
    fake = FakeClient(admin=True)
    bot.client = fake
    ev = N.GroupInfoEvent()
    ev.JID.CopyFrom(GROUP)
    ev.Sender.CopyFrom(OTHER)          # quem adicionou NÃO é admin
    newbie = ev.Join.add()
    newbie.CopyFrom(build_jid("5511777777777"))
    bot.handle_group_change(ev)
    assert any(a[0] == "participants" for a in fake.actions), "antibot deveria remover"
    run("/antibot off")
    print("✓ antibot (remove entrada não autorizada)")


def test_poll_afk():
    assert any(a[0] == "poll" for a in run("/poll Cor? | Azul | Verde").actions)
    assert "AFK" in run("/afk almoçando").sent[0]
    assert run("/suggest mais jogos").sent
    assert run("/report 5511888888888 flood").sent
    assert run("/remind 10m beber agua").sent
    print("✓ poll/afk/suggest/report/remind")


def test_ia_config():
    gstr = bot.Jid2String(GROUP)
    # personalidade
    assert "zoeira" in run("/iamode zoeira").sent[0].lower()
    assert bot.db.get_setting(gstr, "iamode") == "zoeira"
    # modelo (inclui gemini)
    assert run("/aimodel gemini").sent
    assert bot.db.get_setting(gstr, "aimodel") == "gemini"
    assert "gemini" in run("/aimodel xpto").sent[0].lower()  # inválido -> ajuda
    # thinking
    assert run("/thinking on").sent
    assert bot.db.get_setting(gstr, "thinking") == "1"
    run("/thinking off")
    # nome/bio
    assert run("/aisetname Thzyx AI").sent
    assert bot.db.get_setting(gstr, "ainame") == "Thzyx AI"
    assert run("/aisetbio assistente do grupo").sent
    # status mostra tudo
    st = run("/aistatus").sent[0]
    assert "Thzyx AI" in st and "zoeira" in st and "gemini" in st
    # reset
    run("/aireset")
    assert not bot.db.get_setting(gstr, "ainame")
    # bloqueio de não-admin
    assert "admin" in run("/iamode zoeira", admin=False).sent[0].lower()
    print("✓ IA avançada (iamode/aimodel/thinking/aisetname/bio/status/reset)")


def test_pro_admin():
    gstr = bot.Jid2String(GROUP)
    assert "mod" in run("/giverole 5511888888888 mod").sent[0]
    assert "muted" not in bot.db.get_roles(gstr, "5511888888888")
    assert "mod" in bot.db.get_roles(gstr, "5511888888888")
    assert run("/massrole vip").sent
    assert run("/createrole staff").sent
    assert "staff" in (bot.db.get_setting(gstr, "customroles") or "")
    assert run("/deleterole staff").sent
    assert run("/setwelcome Bem-vindo @user!").sent
    assert bot.db.get_setting(gstr, "welcometext")
    assert run("/setbye Tchau @user").sent
    assert run("/autorole membro").sent
    assert bot.db.get_setting(gstr, "autorole") == "membro"
    run("/autorole off")
    assert run("/setmodlog").sent
    assert run("/softban 5511888888888").sent
    # tempban registra na banlist
    run("/tempban 5511888888888 1h")
    assert bot.db.is_banned(gstr, "5511888888888")
    bot.db.remove_ban(gstr, "5511888888888")
    # comandos honestos (não suportados no WhatsApp)
    assert "WhatsApp" in run("/nickname x").sent[0] or "não" in run("/createchannel x").sent[0].lower()
    print("✓ admin PRO (giverole/massrole/roles/welcome/bye/autorole/tempban/softban)")


def test_pro_general():
    gstr = bot.Jid2String(GROUP)
    assert "Senha" in run("/password 20").sent[0]
    assert run("/quote").sent and run("/fact").sent
    conv = run("/convert 10 km mi").sent[0]
    assert "km" in conv and "mi" in conv
    assert run("/emojify oi").sent
    assert run("/reverse abc").sent[0].endswith("cba")
    assert "escolho" in run("/choose a | b | c").sent[0].lower()
    assert run("/randomnumber 1 10").sent
    assert "2" in run("/membercount").sent[0]
    assert run("/randomuser").sent
    assert run("/sayembed Aviso | Olá").sent
    assert run("/roleinfo 5511999999999").sent
    assert run("/stopwatch").sent  # inicia
    assert "parado" in run("/stopwatch").sent[0].lower()  # para
    # snipe: simula mensagem apagada
    bot._last_deleted[gstr] = (bot.Jid2String(OTHER), "mensagem secreta")
    assert "secreta" in run("/snipe").sent[0]
    print("✓ gerais PRO (password/convert/emojify/reverse/choose/membercount/snipe...)")


def test_pro_games():
    bot._active_games.clear()
    # single-shot (random) — só não pode crashar e deve responder
    one_shot = [
        "/slot", "/blackjack", "/roulette vermelho", "/crash", "/memory",
        "/wouldyourather", "/neverhaveiever", "/truth", "/dare", "/battle 5511888888888",
        "/duel 5511888888888", "/bossfight", "/arena", "/treasurehunt", "/heist",
        "/escape", "/labyrinth", "/dungeon", "/tower", "/fishing", "/mining",
        "/hunt", "/petbattle", "/dragonhunt", "/farm", "/race", "/parkour",
        "/coinwar", "/poker", "/mafia", "/detective", "/spy", "/infected",
        "/murdermystery", "/zombie", "/survivor", "/kingdom", "/hotpotato",
    ]
    for cmd in one_shot:
        assert run(cmd).sent, f"{cmd} não respondeu"
    # stateful: inicia e responde
    bot._active_games.clear()
    assert "número" in run("/guessnumber").sent[0].lower()
    assert run("/guessnumber 25").sent
    bot._active_games.clear()
    assert run("/mathrace").sent
    bot._active_games.clear()
    assert run("/higherlower").sent
    assert run("/higherlower maior").sent
    bot._active_games.clear()
    for g in ("/guessflag", "/guesspokemon", "/guessanime", "/wordchain"):
        bot._active_games.clear()
        assert run(g).sent, g
    print("✓ jogos PRO (50: cassino, RPG, sociais, adivinhação)")


def test_ia_vision_image_search():
    """Visão (/analiseia), geração de imagem e pesquisa — com a IA mockada."""
    import ai as ai_mod

    chamadas = {}

    def fake_vision(prompt, image_bytes, mode=None, name=None, bio=None):
        chamadas["vision"] = {"prompt": prompt, "bytes": len(image_bytes)}
        return "Vejo um quadrado azul."

    def fake_search(q):
        chamadas["search"] = q
        return "Resultado da pesquisa."

    def fake_gen(p):
        chamadas["image"] = p
        return _real_png()

    orig = (bot.ai_vision, bot.ai_search, bot.ai_generate_image)
    orig_cooldown = bot.IMAGE_COOLDOWN_SECONDS
    bot.IMAGE_COOLDOWN_SECONDS = 0
    bot._image_cooldowns.clear()
    bot.ai_vision, bot.ai_search, bot.ai_generate_image = fake_vision, fake_search, fake_gen
    try:
        # --- /analiseia com IMAGEM ---
        f = run_media("/analiseia o que é isso?", media="image")
        assert f.sent, "/analiseia não respondeu"
        assert "Vejo um quadrado azul" in f.sent[-1]
        assert chamadas["vision"]["prompt"] == "o que é isso?"
        assert chamadas["vision"]["bytes"] > 0

        # --- /analiseia sem argumento usa prompt padrão ---
        chamadas.clear()
        run_media("/analiseia", media="image")
        assert "Descreva" in chamadas["vision"]["prompt"]

        # --- /analiseia sem mídia orienta o usuário ---
        f = run("/analiseia")
        assert "foto" in f.sent[-1].lower()

        # --- aliases ---
        for alias in ("/analisar", "/vision"):
            assert run_media(f"{alias} teste", media="image").sent, alias

        # --- /pesquisa ---
        f = run("/pesquisa como funciona o PIX")
        assert "Resultado da pesquisa" in f.sent[-1]
        assert chamadas["search"] == "como funciona o PIX"
        assert "Uso:" in run("/pesquisa").sent[0]

        # --- /gerarimagem ---
        f = run("/gerarimagem um gato astronauta")
        for _ in range(100):
            if any(action[0] == "image" for action in f.actions):
                break
            time.sleep(0.01)
        assert chamadas["image"] == "um gato astronauta"
        assert ("image", "🎨 um gato astronauta") in f.actions
        assert "Uso:" in run("/gerarimagem").sent[0]

        # --- erro da IA vira mensagem amigável, não crash ---
        def boom(*a, **k):
            raise ai_mod.AIError("sem créditos")
        bot.ai_generate_image = boom
        f = run("/gerarimagem x")
        for _ in range(100):
            if any("sem créditos" in message for message in f.sent):
                break
            time.sleep(0.01)
        assert "sem créditos" in f.sent[-1]
    finally:
        bot.ai_vision, bot.ai_search, bot.ai_generate_image = orig
        bot.IMAGE_COOLDOWN_SECONDS = orig_cooldown
        bot._image_cooldowns.clear()
    print("✓ IA visão/imagem/pesquisa (/analiseia /gerarimagem /pesquisa)")


def test_gerarimagem_aplica_cooldown_sem_bloquear_handler():
    gate = threading.Event()
    started = threading.Event()

    def slow_gen(prompt):
        started.set()
        gate.wait(timeout=2)
        return _real_png()

    original = bot.ai_generate_image
    bot.ai_generate_image = slow_gen
    bot._image_cooldowns.clear()
    try:
        first = run("/gerarimagem primeira")
        assert started.wait(timeout=1), "worker não iniciou"

        second = run("/gerarimagem segunda")
        assert any("Aguarde" in message for message in second.sent)
        assert not any(action[0] == "image" for action in second.actions)

        gate.set()
        for _ in range(100):
            if any(action[0] == "image" for action in first.actions):
                break
            time.sleep(0.01)
        assert any(action[0] == "image" for action in first.actions)
    finally:
        gate.set()
        bot.ai_generate_image = original
        bot._image_cooldowns.clear()


def test_gerarimagem_libera_slot_se_thread_nao_iniciar(monkeypatch):
    real_thread = bot.threading.Thread

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("sem thread")

    bot._image_cooldowns.clear()
    monkeypatch.setattr(bot.threading, "Thread", BrokenThread)
    failed = run("/gerarimagem teste")
    assert any("Não consegui iniciar" in message for message in failed.sent)

    # Se o rollback falhar, a próxima tentativa cairá no cooldown ou em lotação.
    monkeypatch.setattr(bot.threading, "Thread", real_thread)
    original = bot.ai_generate_image
    bot.ai_generate_image = lambda prompt: _real_png()
    try:
        success = run("/gerarimagem teste 2")
        for _ in range(100):
            if any(action[0] == "image" for action in success.actions):
                break
            time.sleep(0.01)
        assert any(action[0] == "image" for action in success.actions)
    finally:
        bot.ai_generate_image = original
        bot._image_cooldowns.clear()


def test_vision_on_mention():
    """Mencionar o bot junto com uma foto dispara a visão automaticamente."""
    chamadas = {}

    def fake_vision(prompt, image_bytes, mode=None, name=None, bio=None):
        chamadas["prompt"] = prompt
        return "Análise automática."

    orig_vision = bot.ai_vision
    orig_phone = bot._bot_phone[0]
    bot.ai_vision = fake_vision
    bot._bot_phone[0] = "5599999999999"  # finge que o bot é esse número

    # Isola o teste do estado de moderação deixado por testes anteriores:
    # se SENDER estiver silenciado/banido, handle_message retorna ANTES de
    # chegar na visão (era a causa da instabilidade deste teste).
    gstr = bot.Jid2String(GROUP)
    phone = bot.short_jid(bot.Jid2String(SENDER))
    bot.db.remove_role(gstr, phone, "muted")
    bot.db.remove_ban(gstr, phone)
    bot.db.set_setting(gstr, "antispam", "0")
    bot.db.set_setting(gstr, "antilink", "0")
    bot._spam_track.pop((gstr, phone), None)
    try:
        fake = FakeClient(admin=True)
        bot.client = fake
        # foto COM legenda mencionando o bot
        msg = make_media_msg("o que tem aqui?", media="image")
        msg.Message.imageMessage.contextInfo.mentionedJID.append(
            "5599999999999@s.whatsapp.net"
        )
        bot.handle_message(msg)
        assert chamadas.get("prompt") == "o que tem aqui?", (
            f"visão não disparou na menção | chamadas={chamadas} "
            f"| sent={fake.sent} | bot_phone={bot._bot_phone[0]!r} "
            f"| kind={bot._media_kind(msg.Message)!r} "
            f"| mencoes={bot.get_mentions(msg)}"
        )
        # `any` em vez de [-1]: threads de fundo de comandos anteriores
        # (ex.: /reaction) podem escrever neste mesmo FakeClient
        assert any("Análise automática" in s for s in fake.sent), fake.sent

        # foto SEM menção não deve disparar visão
        chamadas.clear()
        fake2 = FakeClient(admin=True)
        bot.client = fake2
        bot.handle_message(make_media_msg("foto qualquer", media="image"))
        assert "prompt" not in chamadas, "visão disparou sem menção ao bot"
    finally:
        bot.ai_vision = orig_vision
        bot._bot_phone[0] = orig_phone
    print("✓ visão automática ao marcar o bot numa foto")


def test_is_from_me_commands():
    """Dono (IsFromMe=True) consegue usar comandos; mensagens comuns são ignoradas."""
    def _make_from_me(text, has_prefix=True):
        msg = N.Message()
        msg.Info.MessageSource.Chat.CopyFrom(GROUP)
        msg.Info.MessageSource.Sender.CopyFrom(SENDER)
        msg.Info.MessageSource.IsGroup = True
        msg.Info.MessageSource.IsFromMe = True
        msg.Info.ID = "OWNER1"
        msg.Message.conversation = text
        return msg

    fake = FakeClient(admin=True)
    bot.client = fake

    # Comando com prefixo → deve ser processado
    bot.handle_message(_make_from_me("/ping"))
    assert any("Pong" in s for s in fake.sent), "IsFromMe + /ping não respondeu"

    # Mensagem sem prefixo → deve ser ignorada
    sent_before = len(fake.sent)
    bot.handle_message(_make_from_me("oi tudo bem"))
    assert len(fake.sent) == sent_before, "IsFromMe sem prefixo não deveria responder"

    print("✓ IsFromMe: dono usa /ping; mensagem sem prefixo é ignorada")


if __name__ == "__main__":
    test_utility_commands()
    test_economy_levels()
    test_admin_commands()
    test_games()
    test_media_welcome()
    test_clear_and_mute()
    test_security()
    test_global_tools()
    test_antibot_event()
    test_poll_afk()
    test_ia_config()
    test_pro_admin()
    test_pro_general()
    test_pro_games()
    test_ia_vision_image_search()
    test_vision_on_mention()
    test_is_from_me_commands()
    print("\n✅ TODOS OS COMANDOS TESTADOS COM SUCESSO")
