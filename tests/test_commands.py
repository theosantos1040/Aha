"""Testa os handlers de comando com um cliente WhatsApp FALSO.

Constrói mensagens proto reais do neonize e injeta um cliente mock em
bot.client, exercitando o roteamento e a maioria dos comandos offline.
"""
import os
import sys
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
        return b"FAKE_MEDIA_BYTES" * 100

    def send_sticker(self, to, file, **kw):
        self.actions.append(("sticker", len(file)))

    def send_audio(self, to, file, ptt=False, **kw):
        self.actions.append(("audio", len(file)))


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
    # /fg com imagem -> deve criar sticker
    fg_img = run_media("/fg", "image")
    assert any(a[0] == "sticker" for a in fg_img.actions), fg_img.sent
    # /fg com vídeo -> sticker animado
    fg_vid = run_media("/fg", "video")
    assert any(a[0] == "sticker" for a in fg_vid.actions)
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


def test_poll_afk():
    assert any(a[0] == "poll" for a in run("/poll Cor? | Azul | Verde").actions)
    assert "AFK" in run("/afk almoçando").sent[0]
    assert run("/suggest mais jogos").sent
    assert run("/report 5511888888888 flood").sent
    assert run("/remind 10m beber agua").sent
    print("✓ poll/afk/suggest/report/remind")


if __name__ == "__main__":
    test_utility_commands()
    test_economy_levels()
    test_admin_commands()
    test_games()
    test_media_welcome()
    test_poll_afk()
    print("\n✅ TODOS OS COMANDOS TESTADOS COM SUCESSO")
