"""ThzyxBoTS - Bot de WhatsApp com IA (OpenRouter) e +40 comandos.

Transporte: neonize (binding do whatsmeow, WhatsApp multidevice).
Execute com:  python run.py   (escaneie o QR com o WhatsApp)
"""
import json
import os
import random
import re
import sys
import threading
import time

import neonize.proto.Neonize_pb2 as N
from neonize.client import NewClient
from neonize.events import (
    ClientOutdatedEv,
    ConnectedEv,
    ConnectFailureEv,
    DisconnectedEv,
    GroupInfoEv,
    LoggedOutEv,
    MessageEv,
    PairStatusEv,
    StreamErrorEv,
    TemporaryBanEv,
)
from neonize.exc import PairPhoneError
from neonize.utils.enum import MediaType, MediaTypeToMMS, ParticipantChange, VoteType
from neonize.utils.jid import build_jid, Jid2String

import config
import database as db
import games
import media
import services
import tiktok
import utils
import webui
from ai import (
    AIError,
    chat as ai_chat,
    generate_image as ai_generate_image,
    search as ai_search,
    transcribe as ai_transcribe,
    vision as ai_vision,
)

START_TIME = time.time()
client = NewClient(config.SESSION_DB)

# estado em memória para jogos interativos por chat
_active_games = {}  # chat_str -> dict
# histórico recente de mensagens p/ /clear (chat_str -> deque[(sender_str, msg_id)])
from collections import deque, defaultdict

_recent_msgs = defaultdict(lambda: deque(maxlen=300))
# contador de avisos de mute p/ evitar spam (chave (chat,sender) -> int)
_mute_warns = defaultdict(int)
# antispam: últimas mensagens por usuário (chave (chat,sender) -> (texto, repeticoes))
_spam_track = {}
# texto recente por id de mensagem p/ /snipe (chat_str -> {msg_id: (sender, texto)})
_msg_text = defaultdict(lambda: deque(maxlen=200))
# última mensagem apagada por chat p/ /snipe (chat_str -> (sender, texto))
_last_deleted = {}


LINK_RE = re.compile(
    r"(https?://|www\.|chat\.whatsapp\.com/|t\.me/|wa\.me/|discord\.gg/)", re.IGNORECASE
)
PROFANITY_RE = re.compile(
    r"\b(?:caralh\w*|porr\w*|merd\w*|fdp|puta\w*|vai\s+se\s+foder|"
    r"bucet\w*|cuzao|cuzão|arromb\w*)\b",
    re.IGNORECASE,
)


# ===================== helpers =====================
def get_text(message) -> str:
    m = message.Message
    if m.conversation:
        return m.conversation
    if m.extendedTextMessage.text:
        return m.extendedTextMessage.text
    if m.imageMessage.caption:
        return m.imageMessage.caption
    if m.videoMessage.caption:
        return m.videoMessage.caption
    return ""


def get_mentions(message):
    """Menções da mensagem — inclusive quando ela é uma FOTO/VÍDEO com legenda.

    Nesse caso o mentionedJID fica no contextInfo do imageMessage/videoMessage,
    não do extendedTextMessage.
    """
    m = message.Message
    for sub in (m.extendedTextMessage, m.imageMessage, m.videoMessage):
        try:
            jids = list(sub.contextInfo.mentionedJID)
            if jids:
                return jids
        except Exception:
            continue
    return []


def parse_jid(jid_str: str):
    user, _, server = jid_str.partition("@")
    return build_jid(user, server or "s.whatsapp.net")


_MEDIA_MAP = {
    "image": ("imageMessage", MediaType.MediaImage, MediaTypeToMMS.MediaImage),
    "video": ("videoMessage", MediaType.MediaVideo, MediaTypeToMMS.MediaVideo),
    "audio": ("audioMessage", MediaType.MediaAudio, MediaTypeToMMS.MediaAudio),
    # Figurinhas usam o mesmo pipeline criptográfico de imagens no WhatsApp.
    "sticker": ("stickerMessage", MediaType.MediaImage, MediaTypeToMMS.MediaImage),
}


def _media_kind(inner) -> str:
    """Descobre o tipo de mídia presente numa Message interna (waE2E)."""
    if inner.stickerMessage.mediaKey:
        return "sticker"
    if inner.videoMessage.mediaKey:
        return "video"
    if inner.imageMessage.mediaKey:
        return "image"
    if inner.audioMessage.mediaKey:
        return "audio"
    return ""


def _download_sub(inner, kind: str) -> bytes:
    """Baixa a mídia extraindo os campos do sub-objeto (robusto p/ citadas)."""
    field, mtype, mms = _MEDIA_MAP[kind]
    sub = getattr(inner, field)
    return client.download_media_with_path(
        sub.directPath,
        sub.fileEncSHA256,
        sub.fileSHA256,
        sub.mediaKey,
        sub.fileLength,
        mtype,
        mms,
    )


def get_media(message):
    """Retorna (bytes, tipo) da mídia da própria mensagem ou da mensagem citada.

    Usa download_media_with_path em vez de download_any para evitar erros de
    wire-format ao baixar mídia de mensagens citadas. Funciona tanto enviando
    a mídia com /fg|/va na legenda quanto respondendo a uma mídia.
    """
    inner = message.Message
    kind = _media_kind(inner)
    if kind:
        return _download_sub(inner, kind), kind
    # tenta a mensagem citada (respondida)
    quoted = inner.extendedTextMessage.contextInfo.quotedMessage
    qkind = _media_kind(quoted)
    if qkind:
        return _download_sub(quoted, qkind), qkind
    return None, ""


def get_quoted_text(message) -> str:
    """Texto da mensagem citada, usado pelos comandos de IA."""
    try:
        quoted = message.Message.extendedTextMessage.contextInfo.quotedMessage
    except Exception:
        return ""
    for value in (
        quoted.conversation,
        quoted.extendedTextMessage.text,
        quoted.imageMessage.caption,
        quoted.videoMessage.caption,
    ):
        if value:
            return value.strip()
    return ""


def short_jid(jid_str: str) -> str:
    return jid_str.split("@")[0].split(":")[0]


def revoke(chat, sender_str: str, msg_id: str) -> bool:
    """Apaga (revoga) uma mensagem. Exige que o bot seja admin do grupo."""
    try:
        client.revoke_message(chat, parse_jid(sender_str), msg_id)
        return True
    except Exception:
        return False


def audit(chat, chat_str: str, actor: str, action: str, detail: str = ""):
    """Registra ação no log de auditoria e envia ao canal de logs (se houver)."""
    try:
        db.log_action(chat_str, actor, action, detail)
    except Exception:
        pass
    log_ch = db.get_setting(chat_str, "logchannel")
    if log_ch:
        try:
            when = time.strftime("%d/%m %H:%M")
            client.send_message(
                parse_jid(log_ch),
                f"📋 *LOG* [{when}]\n👤 {short_jid(actor)}\n⚙️ {action}\n{detail}".strip(),
            )
        except Exception:
            pass


def is_group_admin(chat, sender_str: str) -> bool:
    try:
        info = client.get_group_info(chat)
    except Exception:
        return False
    for p in info.Participants:
        if Jid2String(p.JID).split("@")[0] == short_jid(sender_str):
            return p.IsAdmin or p.IsSuperAdmin
    return short_jid(sender_str) in config.OWNERS


# Telefone do próprio bot, para detectar quando ele é mencionado.
_bot_phone = [""]


def _me_phone(refresh: bool = False) -> str:
    if _bot_phone[0] and not refresh:
        return _bot_phone[0]
    try:
        me = client.get_me()
        _bot_phone[0] = me.JID.User or ""
    except Exception:
        _bot_phone[0] = ""
    return _bot_phone[0]


def bot_foi_mencionado(message) -> bool:
    """True se o próprio bot está entre os mencionados da mensagem."""
    eu = _me_phone()
    if not eu:
        return False
    return any(short_jid(m) == eu for m in get_mentions(message))


class Ctx:
    def __init__(self, message, command, args):
        self.msg = message
        self.command = command
        self.args = args
        self.parts = args.split()
        src = message.Info.MessageSource
        self.chat = src.Chat
        self.chat_str = Jid2String(src.Chat)
        self.sender = src.Sender
        self.sender_str = Jid2String(src.Sender)
        self.is_group = src.IsGroup
        self.mentions = get_mentions(message)
        self.prefix = db.get_prefix(self.chat_str)

    def reply(self, text):
        try:
            client.reply_message(text, self.msg, to=self.chat)
        except Exception:
            client.send_message(self.chat, text)

    def send(self, text):
        client.send_message(self.chat, text)

    def target_jid_str(self):
        """JID alvo: menção, ou primeiro argumento numérico."""
        if self.mentions:
            return self.mentions[0]
        for p in self.parts:
            digits = "".join(ch for ch in p if ch.isdigit())
            if len(digits) >= 8:
                return f"{digits}@s.whatsapp.net"
        return None

    def require_admin(self):
        if not self.is_group:
            self.reply("❌ Esse comando só funciona em grupos.")
            return False
        if not is_group_admin(self.chat, self.sender_str):
            self.reply("🚫 Apenas administradores podem usar esse comando.")
            return False
        return True


# ===================== ADMIN =====================
def cmd_ttkvd(ctx):
    if not ctx.args:
        return ctx.reply("Uso: /ttkvd <link do vídeo do TikTok>")
    ctx.reply("⏳ Baixando o vídeo do TikTok...")
    try:
        video, title = tiktok.fetch_video(ctx.args.strip())
    except tiktok.TikTokError as exc:
        return ctx.reply(f"❌ {exc}")
    try:
        client.send_video(ctx.chat, video, caption=f"🎬 {title}")
    except Exception as exc:
        # neonize usa ffprobe (pacote ffmpeg) para enviar vídeo; se faltar,
        # envia como documento para o usuário ainda receber o arquivo.
        msg = str(exc)
        if "ffprobe" in msg or "ffmpeg" in msg or "No such file" in msg:
            try:
                client.send_document(
                    ctx.chat, video, filename="tiktok.mp4",
                    mimetype="video/mp4", caption=f"🎬 {title}\n_(instale ffmpeg p/ enviar como vídeo: pkg install ffmpeg -y)_",
                )
                return
            except Exception as exc2:
                msg = str(exc2)
        ctx.reply(f"❌ Erro ao enviar o vídeo: {msg}")


def cmd_ban(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Marque alguém. Uso: /ban @usuario")
    db.add_ban(ctx.chat_str, short_jid(target))
    try:
        client.update_group_participants(ctx.chat, [parse_jid(target)], ParticipantChange.REMOVE)
        ctx.reply(f"🔨 @{short_jid(target)} foi *banido* permanentemente.")
    except Exception as exc:
        ctx.reply(f"⚠️ Adicionado à banlist, mas não consegui remover agora: {exc}")


def cmd_unban(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Marque alguém. Uso: /unban @usuario")
    phone = short_jid(target)
    db.remove_ban(ctx.chat_str, phone)
    ctx.reply(f"✅ @{phone} removido da *banlist*. Pode voltar ao grupo.")


def cmd_kick(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Marque alguém. Uso: /kick @usuario")
    try:
        client.update_group_participants(ctx.chat, [parse_jid(target)], ParticipantChange.REMOVE)
        ctx.reply(f"👢 @{short_jid(target)} foi expulso. Pode voltar com convite.")
    except Exception as exc:
        ctx.reply(f"❌ Não consegui expulsar: {exc}")


def cmd_mute(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /mute @usuario")
    phone = short_jid(target)
    db.add_role(ctx.chat_str, phone, "muted")
    _mute_warns[(ctx.chat_str, phone)] = 0  # reseta avisos
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "MUTE", f"alvo: {phone}")
    ctx.reply(
        f"🔇 @{phone} foi *silenciado*. As mensagens dele serão apagadas "
        "automaticamente.\n_(o bot precisa ser admin para apagar)_"
    )


def cmd_unmute(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /unmute @usuario")
    phone = short_jid(target)
    db.remove_role(ctx.chat_str, phone, "muted")
    _mute_warns.pop((ctx.chat_str, phone), None)
    _spam_track.pop((ctx.chat_str, phone), None)
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "UNMUTE", f"alvo: {phone}")
    ctx.reply(f"🔊 @{phone} foi dessilenciado.")


def cmd_clear(ctx):
    if not ctx.require_admin():
        return
    qty = 50
    if ctx.parts and ctx.parts[0].isdigit():
        qty = max(1, min(int(ctx.parts[0]), 300))
    recent = _recent_msgs.get(ctx.chat_str)
    if not recent:
        return ctx.reply("🧹 Não há mensagens recentes registradas para apagar.")
    apagadas = 0
    # apaga das mais recentes para as mais antigas
    for sender_str, msg_id in list(recent)[-qty:][::-1]:
        if revoke(ctx.chat, sender_str, msg_id):
            apagadas += 1
        recent.remove((sender_str, msg_id)) if (sender_str, msg_id) in recent else None
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "CLEAR", f"{apagadas} mensagens")
    ctx.reply(
        f"🧹 Apaguei *{apagadas}* mensagem(ns) (admins e usuários).\n"
        "_Se nada sumiu, confirme que o bot é admin do grupo._"
    )


def cmd_lock(ctx):
    if not ctx.require_admin():
        return
    try:
        client.set_group_announce(ctx.chat, True)
        ctx.reply("🔒 Grupo bloqueado: somente administradores podem enviar mensagens.")
    except Exception as exc:
        ctx.reply(f"❌ Erro: {exc}")


def cmd_unlock(ctx):
    if not ctx.require_admin():
        return
    try:
        client.set_group_announce(ctx.chat, False)
        ctx.reply("🔓 Grupo desbloqueado: todos podem enviar mensagens.")
    except Exception as exc:
        ctx.reply(f"❌ Erro: {exc}")


def cmd_warn(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /warn @usuario [motivo]")
    reason = " ".join(p for p in ctx.parts if not p.startswith("@")) or "Sem motivo"
    total = db.add_warn(ctx.chat_str, short_jid(target), reason, ctx.sender_str)
    ctx.reply(f"⚠️ Advertência aplicada a @{short_jid(target)}.\nMotivo: {reason}\nTotal: {total} advertência(s).")


def cmd_checkwarns(ctx):
    target = ctx.target_jid_str() or ctx.sender_str
    warns = db.get_warns(ctx.chat_str, short_jid(target))
    if not warns:
        return ctx.reply(f"✅ @{short_jid(target)} não possui advertências.")
    lines = [f"⚠️ *Advertências de @{short_jid(target)}* ({len(warns)}):"]
    for i, w in enumerate(warns, 1):
        when = time.strftime("%d/%m/%Y", time.localtime(w["ts"]))
        lines.append(f"{i}. {w['reason']} — {when}")
    ctx.reply("\n".join(lines))


def cmd_setprefix(ctx):
    if not ctx.require_admin():
        return
    if not ctx.args:
        return ctx.reply(f"Prefixo atual: `{ctx.prefix}`\nUso: /setprefix <novo>")
    new = ctx.parts[0]
    db.set_prefix(ctx.chat_str, new)
    ctx.reply(f"✅ Prefixo alterado para `{new}`")


def cmd_addrole(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    roles = [p for p in ctx.parts if not p.startswith("@") and not p.lstrip("+").isdigit()]
    if not target or not roles:
        return ctx.reply("Uso: /addrole @usuario <cargo>")
    role = roles[0]
    if role.lower() in ("admin", "administrador"):
        try:
            client.update_group_participants(ctx.chat, [parse_jid(target)], ParticipantChange.PROMOTE)
        except Exception:
            pass
    db.add_role(ctx.chat_str, short_jid(target), role)
    ctx.reply(f"🎖️ Cargo *{role}* concedido a @{short_jid(target)}.")


def cmd_removerole(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    roles = [p for p in ctx.parts if not p.startswith("@") and not p.lstrip("+").isdigit()]
    if not target or not roles:
        return ctx.reply("Uso: /removerole @usuario <cargo>")
    role = roles[0]
    if role.lower() in ("admin", "administrador"):
        try:
            client.update_group_participants(ctx.chat, [parse_jid(target)], ParticipantChange.DEMOTE)
        except Exception:
            pass
    db.remove_role(ctx.chat_str, short_jid(target), role)
    ctx.reply(f"➖ Cargo *{role}* removido de @{short_jid(target)}.")


def cmd_slowmode(ctx):
    if not ctx.require_admin():
        return
    secs = ctx.parts[0] if ctx.parts else "0"
    ctx.reply(
        f"🐢 Slowmode definido para {secs}s (controle do bot).\n"
        "_Obs.: o WhatsApp não tem slowmode nativo; o bot apenas registra o limite._"
    )


def cmd_announce(ctx):
    if not ctx.require_admin():
        return
    if not ctx.args:
        return ctx.reply("Uso: /announce <mensagem>")
    ctx.send(f"📢 *COMUNICADO OFICIAL*\n\n{ctx.args}\n\n— _{config.BOT_NAME}_")


def cmd_nuke(ctx):
    if not ctx.require_admin():
        return
    ctx.reply(
        "💣 O WhatsApp não permite clonar/recriar um chat via API (diferente do Discord). "
        "Para limpar tudo, use 'Limpar conversa' no app ou recrie o grupo."
    )


def cmd_welcome(ctx):
    if not ctx.require_admin():
        return
    arg = (ctx.parts[0].lower() if ctx.parts else "")
    if arg in ("on", "ativar", "ligar", "sim"):
        db.set_setting(ctx.chat_str, "welcome", "1")
        ctx.reply(f"💖 Boas-vindas *ativadas*! {config.DECO_TOP}\nNovos membros serão recebidos com foto e mensagem fofa.")
    elif arg in ("off", "desativar", "desligar", "nao", "não"):
        db.set_setting(ctx.chat_str, "welcome", "0")
        ctx.reply("🚪 Boas-vindas *desativadas*.")
    else:
        status = "ativadas ✅" if db.get_setting(ctx.chat_str, "welcome") == "1" else "desativadas ❌"
        ctx.reply(f"Uso: /welcome on  |  /welcome off\nStatus atual: {status}")


def _toggle(ctx, key: str, nome: str, emoji: str):
    """Helper genérico para comandos liga/desliga (anti*, maintenance)."""
    if not ctx.require_admin():
        return
    arg = (ctx.parts[0].lower() if ctx.parts else "")
    if arg in ("on", "ativar", "ligar", "sim"):
        db.set_setting(ctx.chat_str, key, "1")
        audit(ctx.chat, ctx.chat_str, ctx.sender_str, key.upper(), "ativado")
        ctx.reply(f"{emoji} *{nome}* ativado ✅")
    elif arg in ("off", "desativar", "desligar", "nao", "não"):
        db.set_setting(ctx.chat_str, key, "0")
        audit(ctx.chat, ctx.chat_str, ctx.sender_str, key.upper(), "desativado")
        ctx.reply(f"{emoji} *{nome}* desativado ❌")
    else:
        st = "ativado ✅" if db.get_setting(ctx.chat_str, key) == "1" else "desativado ❌"
        ctx.reply(f"Uso: /{key} on | off\n{nome}: {st}")


def cmd_antibot(ctx):
    _toggle(ctx, "antibot", "Antibot (bloqueia bots não autorizados)", "🛡️")


def cmd_antilink(ctx):
    _toggle(ctx, "antilink", "Antilink (apaga links/convites)", "🔗")


def cmd_antispam(ctx):
    _toggle(ctx, "antispam", "Antispam (pune mensagens repetidas)", "🚯")


def cmd_maintenance(ctx):
    _toggle(ctx, "maintenance", "Modo manutenção (só admins usam o bot)", "🛠️")


def cmd_setlogs(ctx):
    if not ctx.require_admin():
        return
    # define o canal de logs: se mencionou/citou um grupo use; senão o próprio chat
    target = None
    if ctx.parts:
        digits = "".join(c for c in ctx.args if c.isdigit() or c == "-")
        if digits:
            target = ctx.args.strip()
    if not target:
        target = ctx.chat_str  # usa o chat atual como canal de logs
    db.set_setting(ctx.chat_str, "logchannel", target)
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "SETLOGS", target)
    ctx.reply(f"📋 Canal de logs definido: `{short_jid(target)}`\nReceberá ações de auditoria em tempo real.")


def cmd_whitelist_add(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /whitelist-add @usuario (ou número/id)")
    db.whitelist_add(ctx.chat_str, short_jid(target))
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "WHITELIST_ADD", short_jid(target))
    ctx.reply(f"⭐ @{short_jid(target)} adicionado à *whitelist* (isento da automoderação).")


def cmd_whitelist_remove(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /whitelist-remove @usuario (ou número/id)")
    db.whitelist_remove(ctx.chat_str, short_jid(target))
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "WHITELIST_REMOVE", short_jid(target))
    ctx.reply(f"➖ @{short_jid(target)} removido da whitelist.")


def cmd_backup_create(ctx):
    if not ctx.require_admin():
        return
    chat_str = ctx.chat_str
    snapshot = {
        "chat": chat_str,
        "ts": int(time.time()),
        "settings": {k: db.get_setting(chat_str, k) for k in
                     ["welcome", "antibot", "antilink", "antispam", "maintenance",
                      "logchannel", ]},
        "prefix": db.get_prefix(chat_str),
    }
    # estrutura do grupo (participantes/admins), se possível
    try:
        info = client.get_group_info(ctx.chat)
        snapshot["group_name"] = info.GroupName.Name
        snapshot["participants"] = [
            {"jid": short_jid(Jid2String(p.JID)), "admin": bool(p.IsAdmin or p.IsSuperAdmin)}
            for p in info.Participants
        ]
    except Exception:
        pass
    data = json.dumps(snapshot, ensure_ascii=False, indent=2)
    bid = db.backup_save(chat_str, data)
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "BACKUP_CREATE", f"#{bid}")
    # envia o arquivo de backup
    try:
        client.send_document(ctx.chat, data.encode("utf-8"),
                             filename=f"backup_{bid}.json", mimetype="application/json",
                             caption=f"💾 Backup #{bid} criado. Restaure com /backup-load {bid}")
    except Exception:
        ctx.reply(f"💾 Backup *#{bid}* criado e salvo. Use /backup-load {bid} para restaurar.")


def cmd_backup_load(ctx):
    if not ctx.require_admin():
        return
    if not ctx.parts or not ctx.parts[0].isdigit():
        return ctx.reply("Uso: /backup-load <id_backup>")
    bid = int(ctx.parts[0])
    row = db.backup_get(ctx.chat_str, bid)
    if not row:
        return ctx.reply(f"❌ Backup #{bid} não encontrado neste grupo.")
    try:
        snap = json.loads(row["data"])
    except Exception:
        return ctx.reply("❌ Backup corrompido.")
    # restaura configurações (bot-level)
    for k, v in (snap.get("settings") or {}).items():
        if v is not None:
            db.set_setting(ctx.chat_str, k, v)
    if snap.get("prefix"):
        db.set_prefix(ctx.chat_str, snap["prefix"])
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "BACKUP_LOAD", f"#{bid}")
    ctx.reply(
        f"♻️ Backup *#{bid}* restaurado (configurações e prefixo).\n"
        "_Cargos nativos do WhatsApp precisam ser reaplicados manualmente pela API._"
    )


def cmd_auditlog(ctx):
    if not ctx.require_admin():
        return
    qty = 10
    if ctx.parts and ctx.parts[0].isdigit():
        qty = max(1, min(int(ctx.parts[0]), 30))
    logs = db.get_auditlog(ctx.chat_str, qty)
    if not logs:
        return ctx.reply("📋 Nenhuma ação registrada ainda.")
    lines = [f"📋 *Últimas {len(logs)} ações de auditoria:*"]
    for lg in logs:
        when = time.strftime("%d/%m %H:%M", time.localtime(lg["ts"]))
        lines.append(f"• [{when}] {short_jid(lg['actor'])} → *{lg['action']}* {lg['detail']}")
    ctx.reply("\n".join(lines))


# ===================== GERAIS / UTILITÁRIOS =====================
def _ai_settings(chat_str):
    """Lê as configurações de IA do grupo (modelo, modo, nome, bio, thinking)."""
    return {
        "model": db.get_setting(chat_str, "aimodel") or config.DEFAULT_AI_MODEL,
        "mode": db.get_setting(chat_str, "iamode") or config.DEFAULT_AI_MODE,
        "name": db.get_setting(chat_str, "ainame") or config.BOT_NAME,
        "bio": db.get_setting(chat_str, "aibio") or "",
        "thinking": db.get_setting(chat_str, "thinking") == "1",
    }


def cmd_ia(ctx):
    if not ctx.args:
        modelos = ", ".join(config.AI_MODELS.keys())
        return ctx.reply(
            f"🤖 *{config.BOT_NAME}*\nUso: /IA <pergunta>\n"
            f"Escolher modelo na hora: /IA [{modelos}] <pergunta>\n"
            f"Ajustes do grupo: /aimodel /iamode /thinking /aistatus"
        )
    cfg = _ai_settings(ctx.chat_str)
    parts = ctx.args.split(maxsplit=1)
    model_key = cfg["model"]
    prompt = ctx.args
    if parts[0].lower() in config.AI_MODELS and len(parts) > 1:
        model_key = parts[0].lower()
        prompt = parts[1]
    try:
        client.send_chat_presence(ctx.chat, 0, 0)  # "digitando"
    except Exception:
        pass
    # modo pensamento: mostra o raciocínio antes da resposta
    if cfg["thinking"]:
        ctx.reply("🧠 Analisando a mensagem...")
        time.sleep(5)
        ctx.reply("🧠 Entendi sua pergunta. Gerando a melhor resposta...")
    try:
        answer = ai_chat(prompt, model_key, mode=cfg["mode"],
                         name=cfg["name"], bio=cfg["bio"] or None)
        deco = f"𓊆ྀི {cfg['name']} ❤︎𓊇 ◡̈"
        ctx.reply(f"{deco} ({model_key})\n{config.DECO_LINE}\n\n{answer}")
    except AIError as exc:
        ctx.reply(f"❌ IA indisponível: {exc}")


# ─────────── IA: visão, imagem e pesquisa ───────────
def _analisar_midia(ctx, prompt: str):
    """Baixa a mídia (foto/vídeo, enviada ou citada) e manda para a IA de visão."""
    try:
        data, kind = get_media(ctx.msg)
    except Exception as exc:
        return ctx.reply(f"❌ Não consegui baixar a mídia: {exc}")
    if not data:
        return ctx.reply(
            "🖼️ Envie uma *foto* (ou responda a uma) junto com o comando.\n"
            "Ex: mande a imagem com a legenda `/analiseia o que é isso?`"
        )

    if kind == "video":
        try:
            data = media.video_frame(data)   # visão analisa imagem, não vídeo
        except media.MediaError as exc:
            return ctx.reply(f"❌ {exc}")
        except Exception as exc:
            return ctx.reply(f"❌ Erro ao ler o vídeo: {exc}")
    elif kind != "image":
        return ctx.reply("❌ Só consigo analisar *imagens* e *vídeos*.")

    cfg = _ai_settings(ctx.chat_str)
    try:
        client.send_chat_presence(ctx.chat, 0, 0)
    except Exception:
        pass
    try:
        resposta = ai_vision(prompt, data, mode=cfg["mode"],
                             name=cfg["name"], bio=cfg["bio"] or None)
        deco = f"𓊆ྀི {cfg['name']} ❤︎𓊇 ◡̈"
        ctx.reply(f"{deco} 👁️ *análise*\n{config.DECO_LINE}\n\n{resposta}")
    except AIError as exc:
        ctx.reply(f"❌ Não consegui analisar: {exc}")


def cmd_analiseia(ctx):
    """Analisa uma foto/vídeo com IA de visão."""
    _analisar_midia(ctx, ctx.args.strip() or "Descreva esta imagem em detalhes, em português.")


def cmd_gerarimagem(ctx):
    if not ctx.args.strip():
        return ctx.reply("🎨 Uso: /gerarimagem <descrição>\nEx: /gerarimagem um gato astronauta")
    ctx.reply("🎨 Gerando a imagem… isso pode levar alguns segundos.")
    try:
        img = ai_generate_image(ctx.args.strip())
    except AIError as exc:
        return ctx.reply(f"❌ {exc}")
    except Exception as exc:
        return ctx.reply(f"❌ Erro ao gerar imagem: {exc}")
    try:
        client.send_image(ctx.chat, img, caption=f"🎨 {ctx.args.strip()[:200]}")
    except Exception as exc:
        ctx.reply(f"❌ Gerei a imagem mas não consegui enviar: {exc}")


def cmd_pesquisa(ctx):
    if not ctx.args.strip():
        return ctx.reply("🔎 Uso: /pesquisa <assunto>\nEx: /pesquisa como funciona o PIX")
    try:
        client.send_chat_presence(ctx.chat, 0, 0)
    except Exception:
        pass
    try:
        resposta = ai_search(ctx.args.strip())
        ctx.reply(f"🔎 *Pesquisa:* {ctx.args.strip()[:80]}\n{config.DECO_LINE}\n\n{resposta}")
    except AIError as exc:
        ctx.reply(f"❌ Pesquisa indisponível: {exc}")


# ─────────── IA: configurações avançadas ───────────
def cmd_iamode(ctx):
    if not ctx.is_group:
        return ctx.reply("Use em um grupo.")
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores* mudam a personalidade da IA.")
    arg = ctx.args.strip().lower()
    if arg not in config.AI_MODES:
        modos = " / ".join(config.AI_MODES)
        return ctx.reply(
            f"💙 *Personalidade da IA*\nUso: /iamode <{modos}>\n\n"
            "• carinhosa — amigável e fofa 🥰\n"
            "• zoeira — divertida e brincalhona 😆\n"
            "• sincera — direta e objetiva 🎯"
        )
    db.set_setting(ctx.chat_str, "iamode", arg)
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "iamode", arg)
    ctx.reply(f"✅ Personalidade da IA definida para *{arg}*.")


def cmd_aimodel(ctx):
    if not ctx.is_group:
        return ctx.reply("Use em um grupo.")
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só o *dono/admin* do grupo escolhe o modelo.")
    arg = ctx.args.strip().lower()
    if arg not in config.AI_MODELS:
        modelos = " / ".join(config.AI_MODELS)
        return ctx.reply(
            f"🧠 *Modelo da IA*\nUso: /aimodel <{modelos}>\n"
            "_gemini usa o Google Gemma (Gemini gratuito não existe no OpenRouter)._"
        )
    db.set_setting(ctx.chat_str, "aimodel", arg)
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "aimodel", arg)
    ctx.reply(f"✅ Modelo da IA definido para *{arg}* (`{config.AI_MODELS[arg]}`).")


def cmd_thinking(ctx):
    if not ctx.is_group:
        return ctx.reply("Use em um grupo.")
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores* mudam o modo pensamento.")
    arg = ctx.args.strip().lower()
    if arg not in ("on", "off"):
        return ctx.reply("🔍 *Modo Pensamento*\nUso: /thinking on | off")
    db.set_setting(ctx.chat_str, "thinking", "1" if arg == "on" else "0")
    ctx.reply(f"✅ Modo pensamento *{'ATIVADO ⏳' if arg == 'on' else 'desativado'}*.")


def cmd_aisetname(ctx):
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores* mudam o nome da IA.")
    if not ctx.args.strip():
        return ctx.reply("Uso: /aisetname <novo nome>")
    db.set_setting(ctx.chat_str, "ainame", ctx.args.strip()[:40])
    ctx.reply(f"✅ Agora a IA se chama *{ctx.args.strip()[:40]}* neste grupo.")


def cmd_aisetbio(ctx):
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores* mudam a bio da IA.")
    if not ctx.args.strip():
        return ctx.reply("Uso: /aisetbio <descrição da personalidade>")
    db.set_setting(ctx.chat_str, "aibio", ctx.args.strip()[:300])
    ctx.reply("✅ Bio/descrição da IA atualizada.")


def cmd_aisetavatar(ctx):
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores*.")
    # A API do WhatsApp não deixa o bot ter "foto" própria por grupo;
    # registramos a preferência de forma honesta.
    ctx.reply(
        "🖼️ A foto do bot é a do número conectado (WhatsApp não tem avatar "
        "por grupo). Troque a foto do perfil do WhatsApp do bot diretamente."
    )


def cmd_aichannel(ctx):
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores*.")
    db.set_setting(ctx.chat_str, "aichannel", ctx.chat_str)
    ctx.reply("✅ Este grupo foi definido como canal da IA.")


def cmd_aireset(ctx):
    if not is_group_admin(ctx.chat, ctx.sender_str):
        return ctx.reply("🔒 Só *administradores* podem resetar a IA.")
    for k in ("aimodel", "iamode", "ainame", "aibio", "thinking", "aichannel"):
        db.set_setting(ctx.chat_str, k, "")
    ctx.reply("♻️ Configurações da IA restauradas ao padrão.")


def cmd_aistatus(ctx):
    cfg = _ai_settings(ctx.chat_str)
    owner = db.get_setting(ctx.chat_str, "aiowner") or short_jid(ctx.sender_str)
    ch = db.get_setting(ctx.chat_str, "aichannel")
    ctx.reply(
        f"🤖 *IA Ativa*\n{config.DECO_LINE}\n"
        f"• Nome: *{cfg['name']}*\n"
        f"• Modelo: *{cfg['model']}* (`{config.AI_MODELS.get(cfg['model'],'?')}`)\n"
        f"• Personalidade: *{cfg['mode']}*\n"
        f"• Pensamento: *{'ON ⏳' if cfg['thinking'] else 'OFF'}*\n"
        f"• Canal: {'este grupo' if ch else 'qualquer chat'}\n"
        f"• Bio: {cfg['bio'] or '—'}\n"
        "Sistema funcionando normalmente. ✅"
    )


def cmd_ping(ctx):
    t0 = time.time()
    import requests
    api_ms = "?"
    try:
        r = requests.get("https://openrouter.ai/api/v1/models",
                         headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                         timeout=15)
        api_ms = f"{int((time.time() - t0) * 1000)}ms ({r.status_code})"
    except Exception:
        api_ms = "indisponível"
    ctx.reply(f"🏓 Pong!\n⏱️ Bot: {int((time.time() - t0) * 1000)}ms\n🌐 API OpenRouter: {api_ms}")


def cmd_help(ctx):
    p = ctx.prefix
    help_text = (
        f"{config.DECO_TOP}\n"
        f"📜 *{config.BOT_NAME} — Menu Completo*\n"
        f"{config.DECO_LINE}\n\n"
        f"👮 *Administração*\n"
        f"└─ {p}ban {p}unban {p}kick {p}mute {p}unmute {p}warn {p}checkwarns\n"
        f"   {p}lock {p}unlock {p}announce {p}clear {p}nuke {p}ttkvd\n"
        f"   {p}welcome {p}addrole {p}removerole {p}slowmode\n\n"
        f"🛡️ *Segurança & Moderação*\n"
        f"└─ {p}antibot {p}antilink {p}antispam {p}setlogs\n"
        f"   {p}whitelist-add {p}whitelist-remove {p}auditlog\n\n"
        f"⚙️ *Configurações Globais*\n"
        f"└─ {p}setprefix {p}maintenance {p}backup-create {p}backup-load\n\n"
        f"🛠️ *Gerais & Utilitários*\n"
        f"└─ {p}IA {p}ping {p}help {p}userinfo {p}serverinfo {p}avatar\n"
        f"   {p}fg {p}va {p}calc {p}weather {p}translate {p}remind {p}poll\n"
        f"   {p}afk {p}invite {p}uptime {p}report {p}suggest {p}level\n"
        f"   {p}leaderboard {p}daily {p}balance {p}pay\n\n"
        f"🤖 *IA Avançada*\n"
        f"└─ {p}iamode {p}aimodel {p}thinking {p}aisetname {p}aisetbio\n"
        f"   {p}aichannel {p}aireset {p}aistatus\n\n"
        f"👁️ *IA: visão, imagem e pesquisa*\n"
        f"└─ {p}analiseia — analisa uma FOTO/VÍDEO (ou marque o bot na foto)\n"
        f"   {p}gerarimagem — cria imagem a partir de texto\n"
        f"   {p}pesquisa — pesquisa organizada sobre um assunto\n\n"
        f"👑 *Admin PRO (v3.1)*\n"
        f"└─ {p}giverole {p}temprole {p}tempban {p}softban {p}massrole\n"
        f"   {p}createrole {p}deleterole {p}setwelcome {p}setbye {p}autorole\n"
        f"   {p}setmodlog {p}logs {p}backupserver {p}restorebackup\n\n"
        f"🛠️ *Gerais PRO (v3.1)*\n"
        f"└─ {p}qr {p}shorturl {p}password {p}meme {p}quote {p}fact {p}crypto\n"
        f"   {p}timer {p}countdown {p}stopwatch {p}convert {p}emojify {p}snipe\n"
        f"   {p}banner {p}roleinfo {p}membercount {p}randomuser {p}randomnumber\n"
        f"   {p}choose {p}reverse {p}sayembed\n\n"
        f"🎮 *Jogos (originais)*\n"
        f"└─ {p}coinflip {p}jokenpo {p}8ball {p}roll {p}tictactoe\n"
        f"   {p}trivia {p}hangman {p}akinator {p}russianroulette {p}ship\n\n"
        f"🎲 *Jogos PRO (v3.1)*\n"
        f"└─ {p}slot {p}blackjack {p}roulette {p}crash {p}higherlower\n"
        f"   {p}guessnumber {p}mathrace {p}guessflag {p}guesspokemon {p}guessanime\n"
        f"   {p}wordchain {p}wouldyourather {p}truth {p}dare {p}battle {p}duel\n"
        f"   {p}bossfight {p}arena {p}treasurehunt {p}heist {p}escape {p}dungeon\n"
        f"   {p}tower {p}fishing {p}mining {p}hunt {p}petbattle {p}dragonhunt\n"
        f"   {p}farm {p}race {p}parkour {p}coinwar {p}poker {p}mafia {p}detective\n"
        f"   {p}spy {p}infected {p}zombie {p}kingdom {p}hotpotato\n\n"
        f"🆕 *v4 — Mídia e IA*\n"
        f"└─ {p}toimg {p}togif {p}fgpack {p}girar {p}espelhar {p}pb {p}blur\n"
        f"   {p}pixelar {p}recortar {p}acelerar {p}lentidao {p}reverter\n"
        f"   {p}resumir {p}corrigir {p}explicar {p}codigo {p}transcrever\n"
        f"   {p}legenda {p}ocr {p}sentimento {p}ideias {p}historia {p}debater\n\n"
        f"🆕 *v4 — Moderação e utilidades*\n"
        f"└─ {p}promover {p}rebaixar {p}marcartodos {p}hidetag {p}delwarn\n"
        f"   {p}listamutados {p}listabanidos {p}antipalavrao {p}blacklist\n"
        f"   {p}antifake {p}antimidia {p}inativos {p}lembretes {p}cancelarlembrete\n"
        f"   {p}cep {p}ddd {p}moeda {p}wiki {p}dicionario {p}imc {p}idade\n"
        f"   {p}regra3 {p}base64 {p}hash {p}horario\n\n"
        f"🆕 *v4 — Economia, social e jogos*\n"
        f"└─ {p}perfil {p}loja {p}comprar {p}inventario {p}trabalhar {p}roubar\n"
        f"   {p}apostar {p}banco {p}depositar {p}sacar {p}casar {p}abraco\n"
        f"   {p}tapa {p}elogiar {p}sorteio {p}casal {p}piada {p}charada\n"
        f"   {p}conselho {p}travalingua {p}top {p}anagrama {p}cacapalavras\n"
        f"   {p}emojiquiz {p}enigma {p}bingo {p}soletrar {p}sequencia\n"
        f"   {p}campominado {p}desafio\n\n"
        f"✨ *Destaques:*\n"
        f"• {p}IA [chatgpt|nex|glm|gemini] <pergunta> — converse com a IA 🤖\n"
        f"• {p}aimodel / {p}iamode / {p}thinking — personalize a IA do grupo\n"
        f"• {p}fg — vídeo/imagem vira figurinha 🖼️\n"
        f"• {p}va — vídeo vira áudio 🎵\n\n"
        f"{config.DECO_LINE}\n"
        f"_Prefixo: {p} | {config.DECO_NAME}_"
    )
    ctx.reply(help_text)


def cmd_userinfo(ctx):
    target = ctx.target_jid_str() or ctx.sender_str
    roles = db.get_roles(ctx.chat_str, short_jid(target))
    warns = db.get_warns_count(ctx.chat_str, short_jid(target))
    ctx.reply(
        f"👤 *Informações do usuário*\n"
        f"📱 Número: @{short_jid(target)}\n"
        f"🎖️ Cargos: {', '.join(roles) if roles else 'nenhum'}\n"
        f"⚠️ Advertências: {warns}\n"
        f"💰 Saldo: {db.get_balance(target)} moedas\n"
        f"⭐ XP: {db.get_xp(target)}"
    )


def cmd_serverinfo(ctx):
    if not ctx.is_group:
        return ctx.reply("❌ Esse comando só funciona em grupos.")
    try:
        info = client.get_group_info(ctx.chat)
    except Exception as exc:
        return ctx.reply(f"❌ Erro: {exc}")
    admins = sum(1 for p in info.Participants if p.IsAdmin or p.IsSuperAdmin)
    created = time.strftime("%d/%m/%Y", time.localtime(info.GroupCreated)) if info.GroupCreated else "?"
    ctx.reply(
        f"🏠 *Informações do grupo*\n"
        f"📛 Nome: {info.GroupName.Name}\n"
        f"👥 Membros: {len(info.Participants)}\n"
        f"👮 Admins: {admins}\n"
        f"🔒 Bloqueado: {'sim' if info.GroupAnnounce.IsAnnounce else 'não'}\n"
        f"📅 Criado em: {created}"
    )


def cmd_avatar(ctx):
    target = ctx.target_jid_str() or ctx.sender_str
    try:
        pic = client.get_profile_picture(parse_jid(target))
        if pic and pic.URL:
            import requests
            img = requests.get(pic.URL, timeout=30).content
            client.send_image(ctx.chat, img, caption=f"🖼️ Avatar de @{short_jid(target)}")
        else:
            ctx.reply("❌ Esse usuário não tem foto de perfil ou ela é privada.")
    except Exception as exc:
        ctx.reply(f"❌ Não consegui obter o avatar: {exc}")


def cmd_fg(ctx):
    """Cria figurinha a partir de imagem ou vídeo (enviado ou citado).

    Convertemos nós mesmos para WebP e enviamos com passthrough=True, evitando
    o pipeline interno do neonize (que exige ffprobe/webpmux/libwebp e quebra
    no Termux). Imagem nem precisa de ffmpeg (usa Pillow).
    """
    try:
        data, kind = get_media(ctx.msg)
    except Exception as exc:
        return ctx.reply(f"❌ Não consegui baixar a mídia: {exc}")
    if not data or kind not in ("image", "video"):
        return ctx.reply(
            "🖼️ Envie uma *imagem* ou *vídeo* com a legenda /fg, "
            "ou responda a uma mídia com /fg."
        )
    ctx.reply("✨ Criando sua figurinha...")
    try:
        if kind == "image":
            webp = media.image_to_sticker(data)
        else:
            webp = media.video_to_sticker(data)
        pack_name, author = db.resolve_sticker_pack(
            ctx.sender_str, ctx.chat_str, config.BOT_NAME, config.BOT_NAME
        )
        client.send_sticker(
            ctx.chat, webp, passthrough=True,
            packname=pack_name, name=author,
        )
    except media.MediaError as exc:
        ctx.reply(f"❌ {exc}")
    except Exception as exc:
        ctx.reply(f"❌ Erro ao criar figurinha: {exc}")


def cmd_va(ctx):
    """Converte um vídeo em áudio (mp3)."""
    try:
        data, kind = get_media(ctx.msg)
    except Exception as exc:
        return ctx.reply(f"❌ Não consegui baixar a mídia: {exc}")
    if not data or kind != "video":
        return ctx.reply(
            "🎵 Envie um *vídeo* com a legenda /va, ou responda a um vídeo com /va."
        )
    ctx.reply("🎧 Convertendo vídeo em áudio...")
    try:
        audio = media.video_to_audio(data)
        client.send_audio(ctx.chat, audio, ptt=False)
    except media.MediaError as exc:
        ctx.reply(f"❌ {exc}")
    except Exception as exc:
        ctx.reply(f"❌ Erro ao converter: {exc}")


def cmd_calc(ctx):
    if not ctx.args:
        return ctx.reply("Uso: /calc <expressão>  (ex.: /calc 2+2*5)")
    try:
        ctx.reply(f"🧮 {ctx.args} = *{utils.safe_calc(ctx.args)}*")
    except Exception:
        ctx.reply("❌ Expressão inválida.")


def cmd_weather(ctx):
    if not ctx.args:
        return ctx.reply("Uso: /weather <cidade>")
    ctx.reply(services.weather(ctx.args.strip()))


def cmd_translate(ctx):
    if len(ctx.parts) < 2:
        return ctx.reply("Uso: /translate <idioma> <texto>  (ex.: /translate en Olá mundo)")
    target = ctx.parts[0]
    text = ctx.args.split(maxsplit=1)[1]
    ctx.reply(f"🌐 ({target}): {services.translate(text, target)}")


def cmd_remind(ctx):
    if len(ctx.parts) < 2:
        return ctx.reply("Uso: /remind <tempo> <mensagem>  (ex.: /remind 10m beber água)")
    secs = utils.parse_duration(ctx.parts[0])
    if secs <= 0:
        return ctx.reply("⏰ Tempo inválido. Use 10m, 1h, 2d...")
    text = ctx.args.split(maxsplit=1)[1]
    db.add_reminder(ctx.chat_str, ctx.sender_str, text, int(time.time()) + secs)
    ctx.reply(f"⏰ Lembrete agendado em {utils.human_uptime(secs)}: {text}")


def cmd_poll(ctx):
    if "|" not in ctx.args:
        return ctx.reply("Uso: /poll Pergunta | opção1 | opção2 | ...")
    pieces = [s.strip() for s in ctx.args.split("|") if s.strip()]
    if len(pieces) < 3:
        return ctx.reply("Informe uma pergunta e ao menos 2 opções.")
    question, options = pieces[0], pieces[1:12]
    try:
        poll = client.build_poll_vote_creation(question, options, VoteType.SINGLE)
        client.send_message(ctx.chat, poll)
    except Exception as exc:
        ctx.reply(f"❌ Erro ao criar enquete: {exc}")


def cmd_afk(ctx):
    reason = ctx.args.strip() or "Ausente"
    db.set_afk(ctx.sender_str, reason)
    ctx.reply(f"😴 Modo AFK ativado: {reason}\nAvisarei quem te mencionar.")


def cmd_invite(ctx):
    if not ctx.is_group:
        return ctx.reply("❌ Esse comando só funciona em grupos.")
    try:
        link = client.get_group_invite_link(ctx.chat)
        ctx.reply(f"🔗 Link de convite do grupo:\n{link}")
    except Exception as exc:
        ctx.reply(f"❌ Não consegui gerar o link (preciso ser admin): {exc}")


def cmd_uptime(ctx):
    ctx.reply(f"⏱️ Online há *{utils.human_uptime(time.time() - START_TIME)}*")


def cmd_report(ctx):
    target = ctx.target_jid_str()
    reason = " ".join(p for p in ctx.parts if not p.startswith("@")) or "Sem motivo"
    if not target:
        return ctx.reply("Uso: /report @usuario <motivo>")
    db.add_report(ctx.chat_str, ctx.sender_str, short_jid(target), reason)
    ctx.reply("🔕 Denúncia enviada silenciosamente para a moderação. Obrigado!")


def cmd_suggest(ctx):
    if not ctx.args:
        return ctx.reply("Uso: /suggest <sua ideia>")
    sid = db.add_suggestion(ctx.chat_str, ctx.sender_str, ctx.args)
    ctx.send(f"💡 *Sugestão #{sid}*\n{ctx.args}\n\nReaja com 👍 ou 👎")


def cmd_level(ctx):
    target = ctx.target_jid_str() or ctx.sender_str
    xp = db.get_xp(target)
    lvl, cur, need = db.level_from_xp(xp)
    ctx.reply(
        f"⭐ *Nível de @{short_jid(target)}*\n"
        f"Nível: {lvl}\nXP total: {xp}\n"
        f"Progresso: {utils.progress_bar(cur, need)} {cur}/{need}"
    )


def cmd_leaderboard(ctx):
    top = db.leaderboard(10)
    if not top:
        return ctx.reply("📊 Ainda não há dados de ranqueamento.")
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    lines = ["🏆 *Ranking de Atividade*"]
    for i, row in enumerate(top):
        lvl, _, _ = db.level_from_xp(row["xp"])
        lines.append(f"{medals[i]} @{short_jid(row['jid'])} — Nv.{lvl} ({row['xp']} XP)")
    ctx.reply("\n".join(lines))


def cmd_daily(ctx):
    ok, val = db.claim_daily(ctx.sender_str)
    if ok:
        ctx.reply(f"🎁 Você resgatou *{val} moedas*!\nSaldo: {db.get_balance(ctx.sender_str)}")
    else:
        ctx.reply(f"⏳ Já resgatou hoje. Volte em {utils.human_uptime(val)}.")


def cmd_balance(ctx):
    target = ctx.target_jid_str() or ctx.sender_str
    ctx.reply(f"💰 Saldo de @{short_jid(target)}: *{db.get_balance(target)} moedas*")


def cmd_pay(ctx):
    target = ctx.target_jid_str()
    nums = [int("".join(c for c in p if c.isdigit())) for p in ctx.parts
            if p.isdigit()]
    if not target or not nums:
        return ctx.reply("Uso: /pay @usuario <valor>")
    amount = nums[-1]
    ok, msg = db.transfer(ctx.sender_str, target, amount)
    if ok:
        ctx.reply(f"💸 Você transferiu *{amount} moedas* para @{short_jid(target)}.")
    else:
        ctx.reply(f"❌ {msg}")


# ===================== JOGOS =====================
def cmd_coinflip(ctx):
    ctx.reply(f"🪙 {games.coinflip()}!")


def cmd_jokenpo(ctx):
    res = games.jokenpo(ctx.args)
    ctx.reply(res or "Uso: /jokenpo <pedra|papel|tesoura>")


def cmd_8ball(ctx):
    if not ctx.args:
        return ctx.reply("Uso: /8ball <pergunta>")
    ctx.reply(f"🎱 {games.eightball()}")


def cmd_roll(ctx):
    sides, count = 6, 1
    if ctx.parts:
        if "d" in ctx.parts[0].lower():  # formato 2d20
            c, _, s = ctx.parts[0].lower().partition("d")
            count = int(c) if c.isdigit() else 1
            sides = int(s) if s.isdigit() else 6
        elif ctx.parts[0].isdigit():
            sides = int(ctx.parts[0])
    rolls, total = games.roll(sides, count)
    ctx.reply(f"🎲 Rolagem ({count}d{sides}): {rolls} = *{total}*")


def cmd_ship(ctx):
    if len(ctx.mentions) >= 2:
        a, b = short_jid(ctx.mentions[0]), short_jid(ctx.mentions[1])
    elif len(ctx.parts) >= 2:
        a, b = ctx.parts[0], ctx.parts[1]
    else:
        return ctx.reply("Uso: /ship nome1 nome2  (ou marque 2 pessoas)")
    pct, emoji = games.ship(a, b)
    ctx.reply(f"💘 *Shippometro*\n{a} 💕 {b}\n{emoji} Compatibilidade: *{pct}%*\n{utils.progress_bar(pct, 100)}")


def cmd_russianroulette(ctx):
    if games.russian_roulette():
        db.add_role(ctx.chat_str, short_jid(ctx.sender_str), "muted")
        ctx.reply("🔫💥 BANG! Você perdeu e foi silenciado (pelo bot)!")
    else:
        ctx.reply("🔫😅 Click! O tambor estava vazio. Você sobreviveu!")


def cmd_tictactoe(ctx):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == "ttt":
        if not ctx.parts or not ctx.parts[0].isdigit():
            return ctx.reply("Envie /tictactoe <1-9> para jogar.")
        pos = int(ctx.parts[0]) - 1
        if pos < 0 or pos > 8 or g["board"][pos] != " ":
            return ctx.reply("Casa inválida ou ocupada.")
        g["board"][pos] = "❌"
        if games.ttt_winner(g["board"]) is None:
            empty = [i for i, c in enumerate(g["board"]) if c == " "]
            g["board"][random.choice(empty)] = "⭕"
        w = games.ttt_winner(g["board"])
        board_str = games.ttt_render(g["board"])
        if w == "❌":
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply(f"{board_str}\n\n🎉 Você venceu!")
        if w == "⭕":
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply(f"{board_str}\n\n🤖 Eu venci!")
        if w == "draw":
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply(f"{board_str}\n\n🤝 Empate!")
        return ctx.reply(f"{board_str}\n\nSua vez: /tictactoe <1-9>")
    _active_games[ctx.chat_str] = {"type": "ttt", "board": [" "] * 9}
    ctx.reply("🎮 *Jogo da Velha* (você ❌ vs bot ⭕)\n" +
              games.ttt_render([" "] * 9) + "\n\nJogue com /tictactoe <1-9>")


def cmd_trivia(ctx):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == "trivia":
        if ctx.args.strip().lower() == g["answer"].lower():
            db.add_balance(ctx.sender_str, 50)
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply("✅ Correto! +50 moedas 🎉")
        return ctx.reply(f"❌ Errado! A resposta era: *{g['answer']}*")
    q = games.new_trivia()
    _active_games[ctx.chat_str] = {"type": "trivia", "answer": q["a"]}
    ctx.reply(f"🧠 *Trivia!*\n{q['q']}\n\nResponda com /trivia <resposta>")


def cmd_hangman(ctx):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == "hangman":
        guess = ctx.args.strip().lower()
        if not guess:
            return ctx.reply(f"Forca: {games.hangman_display(g['word'], g['guessed'])}\nTente /hangman <letra>")
        if len(guess) > 1:  # tentar palavra inteira
            if guess == g["word"]:
                _active_games.pop(ctx.chat_str, None)
                return ctx.reply(f"🎉 Acertou! A palavra era *{g['word']}*. +30 moedas")
            g["errors"] += 1
        else:
            g["guessed"].add(guess)
            if guess not in g["word"]:
                g["errors"] += 1
        if games.hangman_won(g["word"], g["guessed"]):
            db.add_balance(ctx.sender_str, 30)
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply(f"🎉 Você venceu! Palavra: *{g['word']}*. +30 moedas")
        if g["errors"] >= 6:
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply(f"💀 Você perdeu! A palavra era *{g['word']}*.")
        return ctx.reply(
            f"🔤 {games.hangman_display(g['word'], g['guessed'])}\n"
            f"Erros: {g['errors']}/6\nTente /hangman <letra>"
        )
    word = games.new_hangman_word()
    _active_games[ctx.chat_str] = {"type": "hangman", "word": word, "guessed": set(), "errors": 0}
    ctx.reply(f"🪢 *Jogo da Forca*\n{games.hangman_display(word, set())}\n\nTente /hangman <letra>")


def cmd_akinator(ctx):
    ctx.reply(
        f"🔮 *Akinator*\nPense em um personagem...\n"
        f"Meu palpite é: *{games.akinator_guess()}*!\n"
        "_(versão simplificada — gênio em treinamento 😉)_"
    )


# ===================== v3.1 PRO: HELPERS =====================
import pro

# cronômetros ativos por (chat, usuário) -> timestamp de início
_stopwatches = {}


def _schedule(secs, fn):
    """Agenda uma ação futura em uma thread DAEMON (não segura o processo)."""
    t = threading.Timer(secs, fn)
    t.daemon = True
    t.start()
    return t


def _group_phones(ctx):
    """Lista de telefones (string) dos participantes do grupo."""
    try:
        info = client.get_group_info(ctx.chat)
        return [short_jid(Jid2String(p.JID)) for p in info.Participants]
    except Exception:
        return []


def _na(ctx, recurso):
    """Resposta honesta para recursos que a API do WhatsApp não suporta."""
    ctx.reply(
        f"⚠️ *{recurso}* não existe no WhatsApp como no Discord — a API não "
        "oferece esse recurso. Comando registrado para compatibilidade."
    )


# ===================== v3.1 PRO: ADMIN (25) =====================
def cmd_giverole(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    role = ctx.parts[-1] if len(ctx.parts) >= 2 else ""
    if not target or not role:
        return ctx.reply("Uso: /giverole @user <cargo>")
    db.add_role(ctx.chat_str, short_jid(target), role)
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "giverole", f"{short_jid(target)}={role}")
    ctx.reply(f"✅ Cargo *{role}* dado a @{short_jid(target)}.")


def cmd_temprole(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target or len(ctx.parts) < 2:
        return ctx.reply("Uso: /temprole @user <cargo> <duração ex:10m>")
    role = ctx.parts[1] if not ctx.parts[1].startswith("@") else (ctx.parts[2] if len(ctx.parts) > 2 else "")
    secs = utils.parse_duration(ctx.parts[-1]) or 600
    phone = short_jid(target)
    db.add_role(ctx.chat_str, phone, role)
    ctx.reply(f"⏳ Cargo *{role}* dado a @{phone} por {ctx.parts[-1]}.")
    _schedule(secs, lambda: db.remove_role(ctx.chat_str, phone, role))


def cmd_tempban(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /tempban @user <duração ex:1h>")
    secs = utils.parse_duration(ctx.parts[-1]) if ctx.parts else 0
    secs = secs or 3600
    phone = short_jid(target)
    db.add_ban(ctx.chat_str, phone)
    try:
        client.update_group_participants(ctx.chat, [parse_jid(target)], ParticipantChange.REMOVE)
    except Exception:
        pass
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "tempban", f"{phone} {ctx.parts[-1]}")
    ctx.reply(f"⛔ @{phone} banido temporariamente ({ctx.parts[-1]}).")
    _schedule(secs, lambda: db.remove_ban(ctx.chat_str, phone))


def cmd_softban(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /softban @user")
    phone = short_jid(target)
    # apaga mensagens recentes do alvo e remove (sem manter na banlist)
    apagadas = 0
    for snd, mid in list(_recent_msgs.get(ctx.chat_str, [])):
        if short_jid(snd) == phone and revoke(ctx.chat, snd, mid):
            apagadas += 1
    try:
        client.update_group_participants(ctx.chat, [parse_jid(target)], ParticipantChange.REMOVE)
    except Exception:
        pass
    audit(ctx.chat, ctx.chat_str, ctx.sender_str, "softban", f"{phone} ({apagadas} msgs)")
    ctx.reply(f"🧹 @{phone} sofreu softban: removido e {apagadas} mensagens apagadas.")


def cmd_massrole(ctx):
    if not ctx.require_admin():
        return
    role = ctx.args.strip()
    if not role:
        return ctx.reply("Uso: /massrole <cargo>")
    n = 0
    for phone in _group_phones(ctx):
        db.add_role(ctx.chat_str, phone, role)
        n += 1
    ctx.reply(f"✅ Cargo *{role}* aplicado a {n} membros.")


def cmd_createrole(ctx):
    if not ctx.require_admin():
        return
    if not ctx.args.strip():
        return ctx.reply("Uso: /createrole <nome>")
    roles = set((db.get_setting(ctx.chat_str, "customroles") or "").split(",")) - {""}
    roles.add(ctx.args.strip())
    db.set_setting(ctx.chat_str, "customroles", ",".join(sorted(roles)))
    ctx.reply(f"✅ Cargo *{ctx.args.strip()}* criado.")


def cmd_deleterole(ctx):
    if not ctx.require_admin():
        return
    roles = set((db.get_setting(ctx.chat_str, "customroles") or "").split(",")) - {""}
    if ctx.args.strip() in roles:
        roles.discard(ctx.args.strip())
        db.set_setting(ctx.chat_str, "customroles", ",".join(sorted(roles)))
        return ctx.reply(f"🗑️ Cargo *{ctx.args.strip()}* apagado.")
    ctx.reply("❌ Cargo não encontrado. Veja /roleinfo")


def cmd_setwelcome(ctx):
    if not ctx.require_admin():
        return
    if not ctx.args.strip():
        return ctx.reply("Uso: /setwelcome <texto> (use @ para mencionar o novato)")
    db.set_setting(ctx.chat_str, "welcometext", ctx.args.strip())
    db.set_setting(ctx.chat_str, "welcome", "1")
    ctx.reply("✅ Mensagem de boas-vindas configurada e ativada.")


def cmd_setbye(ctx):
    if not ctx.require_admin():
        return
    if not ctx.args.strip():
        return ctx.reply("Uso: /setbye <texto de despedida>")
    db.set_setting(ctx.chat_str, "byetext", ctx.args.strip())
    db.set_setting(ctx.chat_str, "bye", "1")
    ctx.reply("✅ Mensagem de despedida configurada.")


def cmd_autorole(ctx):
    if not ctx.require_admin():
        return
    arg = ctx.args.strip()
    if arg.lower() in ("off", "desativar"):
        db.set_setting(ctx.chat_str, "autorole", "")
        return ctx.reply("✅ Autorole desativado.")
    if not arg:
        return ctx.reply("Uso: /autorole <cargo> | off")
    db.set_setting(ctx.chat_str, "autorole", arg)
    ctx.reply(f"✅ Novos membros receberão o cargo *{arg}* automaticamente.")


def cmd_setmodlog(ctx):
    if not ctx.require_admin():
        return
    db.set_setting(ctx.chat_str, "modlog", ctx.chat_str)
    ctx.reply("✅ Canal de moderação definido para este grupo.")


# ===================== v3.1 PRO: GERAIS (25) =====================
def cmd_qr(ctx):
    if not ctx.args.strip():
        return ctx.reply("Uso: /qr <texto ou link>")
    try:
        png = services.qr_png(ctx.args.strip())
        client.send_image(ctx.chat, png, caption="🔳 Seu QR Code")
    except Exception as exc:
        ctx.reply(f"❌ Não consegui gerar o QR: {exc}")


def cmd_shorturl(ctx):
    if not ctx.args.strip():
        return ctx.reply("Uso: /shorturl <link>")
    ctx.reply(f"🔗 {services.shorten_url(ctx.args.strip())}")


def cmd_password(ctx):
    n = 16
    if ctx.parts and ctx.parts[0].isdigit():
        n = int(ctx.parts[0])
    ctx.reply(f"🔐 Senha gerada:\n`{pro.gen_password(n)}`")


def cmd_meme(ctx):
    try:
        url, title = services.random_meme()
        import requests
        img = requests.get(url, timeout=30).content
        client.send_image(ctx.chat, img, caption=f"😂 {title}")
    except Exception as exc:
        ctx.reply(f"❌ Não consegui buscar um meme agora: {exc}")


def cmd_quote(ctx):
    ctx.reply(f"💬 {pro.random_from(pro.QUOTES)}")


def cmd_fact(ctx):
    ctx.reply(f"🤓 *Curiosidade:* {pro.random_from(pro.FACTS)}")


def cmd_crypto(ctx):
    coin = ctx.args.strip() or "btc"
    ctx.reply(services.crypto_price(coin))


def cmd_timer(ctx):
    if len(ctx.parts) < 1:
        return ctx.reply("Uso: /timer <duração ex:5m> [mensagem]")
    secs = utils.parse_duration(ctx.parts[0])
    if not secs:
        return ctx.reply("❌ Duração inválida. Ex.: /timer 5m")
    msg = ctx.args[len(ctx.parts[0]):].strip() or "⏰ Tempo esgotado!"
    ctx.reply(f"⏱️ Timer de {ctx.parts[0]} iniciado.")
    _schedule(secs, lambda: client.send_message(ctx.chat, f"⏰ @{short_jid(ctx.sender_str)} {msg}"))


def cmd_countdown(ctx):
    if not ctx.parts or not ctx.parts[0].isdigit():
        return ctx.reply("Uso: /countdown <segundos>")
    n = min(int(ctx.parts[0]), 10)
    ctx.reply(f"⏳ Contagem regressiva de {n}...")
    def run():
        for i in range(n, 0, -1):
            client.send_message(ctx.chat, f"{i}️⃣")
            time.sleep(1)
        client.send_message(ctx.chat, "🚀 *JÁ!*")
    threading.Thread(target=run, daemon=True).start()


def cmd_stopwatch(ctx):
    key = (ctx.chat_str, ctx.sender_str)
    now = time.time()
    if key in _stopwatches:
        elapsed = now - _stopwatches.pop(key)
        return ctx.reply(f"⏹️ Cronômetro parado: *{elapsed:.1f}s*")
    _stopwatches[key] = now
    ctx.reply("▶️ Cronômetro iniciado. Use /stopwatch de novo para parar.")


def cmd_convert(ctx):
    if len(ctx.parts) < 3:
        return ctx.reply("Uso: /convert <valor> <de> <para>\nEx.: /convert 10 km mi")
    try:
        val = float(ctx.parts[0].replace(",", "."))
        res = pro.convert_units(val, ctx.parts[1], ctx.parts[2])
        ctx.reply(f"🔄 {val} {ctx.parts[1]} = *{res:.4g} {ctx.parts[2]}*")
    except Exception as exc:
        ctx.reply(f"❌ {exc}")


def cmd_emojify(ctx):
    if not ctx.args.strip():
        return ctx.reply("Uso: /emojify <texto>")
    ctx.reply(pro.emojify(ctx.args.strip()))


def cmd_snipe(ctx):
    snipe = _last_deleted.get(ctx.chat_str)
    if not snipe:
        return ctx.reply("🤷 Nenhuma mensagem apagada recente para mostrar.")
    snd, txt = snipe
    ctx.reply(f"🔍 *Última mensagem apagada*\n👤 @{short_jid(snd)}:\n{txt}")


def cmd_banner(ctx):
    cmd_avatar(ctx)


def cmd_roleinfo(ctx):
    custom = db.get_setting(ctx.chat_str, "customroles") or "—"
    target = ctx.target_jid_str() or ctx.sender_str
    roles = db.get_roles(ctx.chat_str, short_jid(target)) or "nenhum"
    ctx.reply(
        f"🏷️ *Cargos*\nCargos do grupo: {custom}\n"
        f"@{short_jid(target)}: {roles}"
    )


def cmd_channelinfo(ctx):
    cmd_serverinfo(ctx)


def cmd_membercount(ctx):
    phones = _group_phones(ctx)
    ctx.reply(f"👥 Este grupo tem *{len(phones)}* membros.")


def cmd_randomuser(ctx):
    phones = _group_phones(ctx)
    if not phones:
        return ctx.reply("❌ Não consegui listar os membros.")
    ctx.reply(f"🎲 Usuário sorteado: @{random.choice(phones)}")


def cmd_randomnumber(ctx):
    lo, hi = 1, 100
    if len(ctx.parts) >= 2 and ctx.parts[0].lstrip("-").isdigit() and ctx.parts[1].lstrip("-").isdigit():
        lo, hi = int(ctx.parts[0]), int(ctx.parts[1])
    ctx.reply(f"🔢 Número sorteado ({lo}–{hi}): *{pro.random_number(lo, hi)}*")


def cmd_choose(ctx):
    opts = [o.strip() for o in ctx.args.split("|") if o.strip()]
    if len(opts) < 2:
        return ctx.reply("Uso: /choose opção1 | opção2 | opção3")
    ctx.reply(f"🤔 Eu escolho: *{pro.choose(opts)}*")


def cmd_reverse(ctx):
    if not ctx.args.strip():
        return ctx.reply("Uso: /reverse <texto>")
    ctx.reply(f"🔃 {pro.reverse_text(ctx.args.strip())}")


def cmd_sayembed(ctx):
    if not ctx.args.strip():
        return ctx.reply("Uso: /sayembed <título> | <mensagem>")
    parts = ctx.args.split("|", 1)
    titulo = parts[0].strip()
    corpo = parts[1].strip() if len(parts) > 1 else ""
    ctx.send(f"╭━━━ *{titulo}* ━━━╮\n{corpo}\n╰━━━━━━━━━━━━━╯")


# ===================== v3.1 PRO: JOGOS (50) =====================
def cmd_slot(ctx):
    r, mult = pro.slot_spin()
    line = " | ".join(r)
    if mult:
        gain = 50 * mult
        db.add_balance(ctx.sender_str, gain)
        ctx.reply(f"🎰 [ {line} ]\n🎉 Combinou! x{mult} → +{gain} moedas!")
    else:
        ctx.reply(f"🎰 [ {line} ]\n😢 Não foi dessa vez!")


def cmd_blackjack(ctx):
    p, d, res = pro.blackjack_round()
    ctx.reply(
        f"🃏 *Blackjack*\nVocê: {p} = {sum(p)}\nDealer: {d} = {sum(d)}\n\n*{res}*"
    )


def cmd_roulette(ctx):
    if not ctx.args.strip():
        return ctx.reply("Uso: /roulette <vermelho|preto|par|impar|número>")
    n, color, win = pro.roulette_spin(ctx.args.strip())
    res = "🎉 Você GANHOU!" if win else "😢 Você perdeu!"
    ctx.reply(f"🎡 A bola caiu no *{n}* {color}\n{res}")


def cmd_crash(ctx):
    point = pro.crash_game()
    ctx.reply(f"📈 *Crash!*\nO multiplicador estourou em *{point:.2f}x* 💥")


def cmd_higherlower(ctx):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == "hl":
        guess = ctx.args.strip().lower()
        nxt = pro.higher_lower(g["num"])
        if (guess in ("maior", "h", "+") and nxt >= g["num"]) or \
           (guess in ("menor", "l", "-") and nxt <= g["num"]):
            _active_games.pop(ctx.chat_str, None)
            db.add_balance(ctx.sender_str, 30)
            return ctx.reply(f"🔼 Era *{nxt}*. Acertou! +30 moedas 🎉")
        _active_games.pop(ctx.chat_str, None)
        return ctx.reply(f"🔽 Era *{nxt}*. Errou! 😢")
    num = pro.random_number(1, 100)
    _active_games[ctx.chat_str] = {"type": "hl", "num": num}
    ctx.reply(f"🔼🔽 *Maior ou Menor?*\nNúmero atual: *{num}*\n"
              "O próximo será maior ou menor? /higherlower maior | menor")


def cmd_guessnumber(ctx):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == "gn":
        if not ctx.parts or not ctx.parts[0].lstrip("-").isdigit():
            return ctx.reply("Digite um número: /guessnumber <n>")
        guess = int(ctx.parts[0])
        if guess == g["num"]:
            _active_games.pop(ctx.chat_str, None)
            db.add_balance(ctx.sender_str, 40)
            return ctx.reply("🎯 Acertou! +40 moedas 🎉")
        dica = "📈 maior" if guess < g["num"] else "📉 menor"
        return ctx.reply(f"❌ Não é {guess}. Tente um número {dica}.")
    num = pro.random_number(1, 50)
    _active_games[ctx.chat_str] = {"type": "gn", "num": num}
    ctx.reply("🔢 *Adivinhe o número* (1 a 50)!\nResponda: /guessnumber <n>")


def cmd_mathrace(ctx):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == "math":
        try:
            if int(ctx.args.strip()) == g["ans"]:
                _active_games.pop(ctx.chat_str, None)
                db.add_balance(ctx.sender_str, 35)
                return ctx.reply("✅ Correto! +35 moedas ⚡")
        except ValueError:
            pass
        return ctx.reply(f"❌ Errado! Era *{g['ans']}*.")
    expr, ans = pro.math_challenge()
    _active_games[ctx.chat_str] = {"type": "math", "ans": ans}
    ctx.reply(f"🧮 *Corrida Matemática!*\nQuanto é *{expr}*?\n/mathrace <resposta>")


def _guess_game(ctx, kind, emoji, titulo):
    g = _active_games.get(ctx.chat_str)
    if g and g.get("type") == kind:
        if ctx.args.strip().lower() == g["answer"]:
            _active_games.pop(ctx.chat_str, None)
            db.add_balance(ctx.sender_str, 40)
            return ctx.reply(f"✅ Isso! Era *{g['answer'].title()}*. +40 moedas 🎉")
        return ctx.reply(f"❌ Não! A resposta era *{g['answer'].title()}*.")
    hint, ans = pro.guess_new(kind if kind in ("flag", "pokemon", "anime") else "flag")
    _active_games[ctx.chat_str] = {"type": kind, "answer": ans.lower()}
    ctx.reply(f"{emoji} *{titulo}*\n{hint}\n/{ctx.command} <resposta>")


def cmd_guessflag(ctx):
    _guess_game(ctx, "flag", "🚩", "Adivinhe a Bandeira")


def cmd_guesspokemon(ctx):
    _guess_game(ctx, "pokemon", "❓", "Quem é esse Pokémon?")


def cmd_guessanime(ctx):
    _guess_game(ctx, "anime", "🎌", "Adivinhe o Anime")


def cmd_wordchain(ctx):
    g = _active_games.get(ctx.chat_str)
    word = ctx.args.strip().lower()
    if g and g.get("type") == "wc":
        if not word:
            return ctx.reply(f"🔗 Diga uma palavra que comece com *{g['last'][-1].upper()}*")
        if word[0] != g["last"][-1]:
            return ctx.reply(f"❌ Tem que começar com *{g['last'][-1].upper()}*!")
        g["last"] = word
        return ctx.reply(f"✅ Boa! Próxima começa com *{word[-1].upper()}* 🔗")
    start = ctx.args.strip().lower() or random.choice(["banana", "casa", "sol", "amor"])
    _active_games[ctx.chat_str] = {"type": "wc", "last": start}
    ctx.reply(f"🔗 *Cadeia de Palavras!*\nComeço: *{start}*\n"
              f"Próxima começa com *{start[-1].upper()}*\n/wordchain <palavra>")


def cmd_memory(ctx):
    seq = " ".join(random.choice(["🔴", "🟢", "🔵", "🟡"]) for _ in range(5))
    ctx.reply(f"🧠 *Jogo da Memória!*\nMemorize esta sequência:\n\n{seq}\n\n"
              "_(Recriação simplificada — desafie sua memória!)_")


def cmd_reaction(ctx):
    ctx.reply("⚡ *Reação Rápida!*\nQuando eu disser JÁ, mande qualquer coisa!")
    def go():
        time.sleep(random.uniform(2, 5))
        client.send_message(ctx.chat, "🟢 *JÁ!* Responda agora!")
    threading.Thread(target=go, daemon=True).start()


def cmd_wouldyourather(ctx):
    ctx.reply(f"🤔 *Você prefere?*\n{pro.random_from(pro.WOULD_YOU_RATHER)}")


def cmd_neverhaveiever(ctx):
    ctx.reply(f"🙅 *Eu nunca...*\n{pro.random_from(pro.NEVER_HAVE_I_EVER)}")


def cmd_truth(ctx):
    ctx.reply(f"🗣️ *Verdade:* {pro.random_from(pro.TRUTHS)}")


def cmd_dare(ctx):
    ctx.reply(f"🎯 *Desafio:* {pro.random_from(pro.DARES)}")


def cmd_battle(ctx):
    a = short_jid(ctx.sender_str)
    b = short_jid(ctx.target_jid_str()) if ctx.target_jid_str() else "Inimigo"
    log, winner = pro.battle(a, b)
    ctx.reply("⚔️ *Batalha RPG!*\n" + "\n".join(log[:6]) + f"\n\n🏆 Vencedor: *{winner}*!")


def cmd_duel(ctx):
    a = short_jid(ctx.sender_str)
    b = short_jid(ctx.target_jid_str()) if ctx.target_jid_str() else "Oponente"
    winner = random.choice([a, b])
    ctx.reply(f"🤺 *Duelo!*\n@{a} ⚔️ @{b}\n\n🏆 *{winner}* venceu o duelo!")


def cmd_bossfight(ctx):
    won, hp, dmg = pro.boss_fight(short_jid(ctx.sender_str))
    if won:
        db.add_balance(ctx.sender_str, 200)
        ctx.reply(f"🐲 *Chefe derrotado!*\nVida do chefe: {hp} | Seu dano: {dmg}\n+200 moedas 🎉")
    else:
        ctx.reply(f"💀 O chefe (HP {hp}) resistiu ao seu ataque de {dmg}. Tente de novo!")


def cmd_arena(ctx):
    foes = ["🗡️ Gladiador", "🛡️ Cavaleiro", "🏹 Arqueiro", "🐉 Dragão", "👹 Ogro"]
    res = random.choice(["venceu", "perdeu"])
    foe = random.choice(foes)
    if res == "venceu":
        db.add_balance(ctx.sender_str, 80)
        ctx.reply(f"🏟️ Na arena você enfrentou {foe} e *venceu*! +80 moedas 🎉")
    else:
        ctx.reply(f"🏟️ Na arena {foe} foi mais forte e você *perdeu*! 💪")


def cmd_treasurehunt(ctx):
    item, rar = pro.loot()
    ctx.reply(f"🗺️ *Caça ao Tesouro!*\nVocê encontrou: {item} _({rar})_ ✨")


def cmd_heist(ctx):
    if random.random() < 0.5:
        ganho = random.randint(100, 500)
        db.add_balance(ctx.sender_str, ganho)
        ctx.reply(f"💰 *Assalto bem-sucedido!*\nA quadrilha levou {ganho} moedas! 🤑")
    else:
        ctx.reply("🚓 *O assalto deu errado!* A polícia chegou e vocês fugiram sem nada! 😵")


def cmd_escape(ctx):
    if random.random() < 0.45:
        ctx.reply("🔓 Você decifrou o enigma e *ESCAPOU* da prisão! 🎉")
    else:
        ctx.reply("🔒 O tempo acabou... você continua preso! Tente de novo. ⛓️")


def cmd_labyrinth(ctx):
    passos = random.randint(3, 12)
    ctx.reply(f"🌀 Você explorou o labirinto por {passos} salas e encontrou a saída! 🚪✨")


def cmd_dungeon(ctx):
    ctx.reply(f"🏰 *Masmorra*\n{pro.dungeon_step()}")


def cmd_tower(ctx):
    floor, reward = pro.tower_climb()
    db.add_balance(ctx.sender_str, reward)
    ctx.reply(f"🗼 Você subiu até o andar *{floor}* da torre infinita!\n+{reward} moedas 🪙")


def cmd_fishing(ctx):
    item, val = pro.gather("fishing")
    if val:
        db.add_balance(ctx.sender_str, val)
    ctx.reply(f"🎣 Você pescou: {item}" + (f" (+{val} moedas)" if val else " (nada de valor)"))


def cmd_mining(ctx):
    item, val = pro.gather("mining")
    if val:
        db.add_balance(ctx.sender_str, val)
    ctx.reply(f"⛏️ Você minerou: {item}" + (f" (+{val} moedas)" if val else ""))


def cmd_hunt(ctx):
    item, val = pro.gather("hunt")
    if val:
        db.add_balance(ctx.sender_str, val)
    ctx.reply(f"🏹 Caçada: {item}" + (f" (+{val} moedas)" if val else ""))


def cmd_petbattle(ctx):
    pets = ["🐶 Cão", "🐱 Gato", "🐉 Dragão", "🦅 Águia", "🐢 Tartaruga"]
    a, b = random.sample(pets, 2)
    ctx.reply(f"🐾 *Batalha de Pets!*\n{a} VS {b}\n🏆 *{random.choice([a, b])}* venceu!")


def cmd_dragonhunt(ctx):
    if random.random() < 0.35:
        db.add_balance(ctx.sender_str, 300)
        ctx.reply("🐉 Você caçou o *Dragão Lendário*! Tesouro: +300 moedas 🏆🔥")
    else:
        ctx.reply("🔥 O dragão cuspiu fogo e você recuou! Ele é forte demais... 😰")


def cmd_farm(ctx):
    crops = ["🌽 Milho", "🍅 Tomate", "🥕 Cenoura", "🌾 Trigo", "🍓 Morango"]
    ganho = random.randint(20, 90)
    db.add_balance(ctx.sender_str, ganho)
    ctx.reply(f"🚜 Você colheu {random.choice(crops)} e vendeu por +{ganho} moedas! 🧺")


def cmd_race(ctx):
    racers = ["🏎️ Você", "🚗 Bot", "🏍️ Rival"]
    ctx.reply(f"🏁 *Corrida!*\n🥇 {random.choice(racers)} cruzou a linha primeiro!")


def cmd_parkour(ctx):
    score = random.randint(0, 100)
    ctx.reply(f"🤸 *Parkour!*\nVocê completou *{score}%* do percurso de obstáculos!")


def cmd_coinwar(ctx):
    a, b = random.randint(1, 100), random.randint(1, 100)
    res = "🎉 Você venceu!" if a >= b else "😢 Você perdeu!"
    ctx.reply(f"🪙 *Guerra de Moedas!*\nVocê: {a} | Inimigo: {b}\n{res}")


def cmd_poker(ctx):
    cartas = ["A♠️", "K♥️", "Q♦️", "J♣️", "10♠️", "9♥️", "8♦️"]
    mao = random.sample(cartas, 5)
    ctx.reply(f"🃏 *Poker (simplificado)*\nSua mão: {' '.join(mao)}\n"
              "_(Mostre sua mão e desafie os amigos!)_")


def _social_game(ctx, emoji, nome, papeis):
    phones = _group_phones(ctx)
    if len(phones) < 2:
        return ctx.reply(f"{emoji} *{nome}* precisa de um grupo com mais pessoas!")
    escolhido = random.choice(phones)
    papel = random.choice(papeis)
    ctx.reply(f"{emoji} *{nome}*\n🎭 @{escolhido} é o *{papel}*!\n"
              "_(versão simplificada — diversão garantida no grupo!)_")


def cmd_mafia(ctx):
    _social_game(ctx, "🕵️", "Máfia", ["Máfia 🔪", "Detetive 🔍", "Médico 💉", "Cidadão 👤"])


def cmd_detective(ctx):
    _social_game(ctx, "🔍", "Detetive", ["Culpado 😈", "Inocente 😇"])


def cmd_spy(ctx):
    _social_game(ctx, "🕶️", "Espião", ["Espião 🕶️", "Agente 🛡️"])


def cmd_infected(ctx):
    _social_game(ctx, "🧟", "Infectado", ["Infectado 🧟", "Sobrevivente 🏃"])


def cmd_murdermystery(ctx):
    _social_game(ctx, "🔪", "Mistério de Assassinato", ["Assassino 🔪", "Vítima 💀", "Investigador 🕵️"])


def cmd_zombie(ctx):
    sobreviventes = random.randint(1, 5)
    ctx.reply(f"🧟 *Sobrevivência Zumbi!*\nApós o ataque, restaram *{sobreviventes}* sobreviventes! 🏚️")


def cmd_survivor(ctx):
    _social_game(ctx, "🏝️", "Survivor", ["Eliminado ❌", "Imune ✅"])


def cmd_kingdom(ctx):
    reinos = ["🏰 Norte", "🏜️ Deserto", "🌲 Floresta", "⛰️ Montanha"]
    ctx.reply(f"👑 *Conquista de Reinos!*\nVocê conquistou: {random.choice(reinos)}! 🗡️")


def cmd_hotpotato(ctx):
    phones = _group_phones(ctx)
    if phones:
        ctx.reply(f"🥔🔥 *Batata Quente!*\n💥 Explodiu na mão de @{random.choice(phones)}!")
    else:
        ctx.reply("🥔 *Batata Quente* precisa de um grupo!")


def cmd_fastclick(ctx):
    cmd_reaction(ctx)


# comandos que o WhatsApp não suporta (registrados de forma honesta)
def cmd_nickname(ctx):
    _na(ctx, "Alterar apelido de outros (/nickname)")


def cmd_resetnick(ctx):
    _na(ctx, "Restaurar apelido (/resetnick)")


def cmd_hidechannel(ctx):
    _na(ctx, "Ocultar canal (/hidechannel)")


def cmd_showchannel(ctx):
    _na(ctx, "Mostrar canal (/showchannel)")


def cmd_clonechannel(ctx):
    _na(ctx, "Clonar canal (/clonechannel)")


def cmd_deletechannel(ctx):
    _na(ctx, "Apagar canal (/deletechannel)")


def cmd_createchannel(ctx):
    _na(ctx, "Criar canal (/createchannel)")


def cmd_boosts(ctx):
    _na(ctx, "Boosts do servidor (/boosts)")


def cmd_firstmessage(ctx):
    _na(ctx, "Primeira mensagem do canal (/firstmessage)")


def cmd_editsnipe(ctx):
    _na(ctx, "Última mensagem editada (/editsnipe)")


def cmd_guesslogo(ctx):
    ctx.reply("🏷️ *Adivinhe o Logo* exige enviar imagens de logos — em breve! "
              "Por enquanto tente /guessflag, /guesspokemon ou /guessanime 😉")


def cmd_guesssong(ctx):
    ctx.reply("🎵 *Adivinhe a Música* exige enviar áudios — em breve! "
              "Por enquanto tente /trivia ou /guessanime 😉")


# ===================== v4: MÍDIA E FIGURINHAS (12) =====================
def _ctx_media(ctx, allowed, usage):
    try:
        data, kind = get_media(ctx.msg)
    except Exception as exc:
        ctx.reply(f"❌ Não consegui baixar a mídia: {exc}")
        return None, ""
    if not data or kind not in allowed:
        ctx.reply(usage)
        return None, ""
    return data, kind


def _send_processed(ctx, payload, kind, caption=""):
    if kind == "image":
        client.send_image(ctx.chat, payload, caption=caption or None)
    elif kind == "video":
        client.send_video(ctx.chat, payload, caption=caption or None)
    else:
        client.send_audio(ctx.chat, payload, ptt=False)


def cmd_toimg(ctx):
    data, kind = _ctx_media(
        ctx, {"sticker"},
        "Uso: responda a uma *figurinha estática* com /toimg.",
    )
    if not data:
        return
    try:
        client.send_image(ctx.chat, media.sticker_to_image(data, kind), caption="🖼️ Figurinha convertida em foto.")
    except media.MediaError as exc:
        ctx.reply(f"❌ {exc}")


def cmd_togif(ctx):
    data, kind = _ctx_media(
        ctx, {"sticker"},
        "Uso: responda a uma *figurinha animada* com /togif.",
    )
    if not data:
        return
    try:
        client.send_video(ctx.chat, media.animated_sticker_to_video(data, kind), caption="🎞️ Figurinha animada convertida em vídeo.")
    except media.MediaError as exc:
        ctx.reply(f"❌ {exc}")


def cmd_fgpack(ctx):
    if "|" not in ctx.args:
        return ctx.reply("Uso: /fgpack Nome do pacote | Autor")
    name, author = (part.strip() for part in ctx.args.split("|", 1))
    if not name or not author or len(name) > 60 or len(author) > 60:
        return ctx.reply("❌ Nome e autor são obrigatórios e devem ter até 60 caracteres.")
    db.set_sticker_pack(ctx.sender_str, name, author, scope="user")
    ctx.reply(f"✅ Pacote definido:\n📦 {name}\n✍️ {author}")


def _image_operation(ctx, operation, *args):
    data, kind = _ctx_media(
        ctx, {"image", "sticker"},
        f"Uso: envie ou responda a uma imagem/figurinha com /{ctx.command}.",
    )
    if not data:
        return
    try:
        result = operation(data, kind, *args)
        client.send_image(ctx.chat, result, caption=f"✅ /{ctx.command} concluído.")
    except media.MediaError as exc:
        ctx.reply(f"❌ {exc}")


def cmd_girar(ctx):
    try:
        degrees = float(ctx.parts[0]) if ctx.parts else 90
    except ValueError:
        return ctx.reply("Uso: /girar [graus] (padrão: 90)")
    _image_operation(ctx, media.rotate_image, degrees)


def cmd_espelhar(ctx):
    _image_operation(ctx, media.mirror_image)


def cmd_pb(ctx):
    _image_operation(ctx, media.grayscale_image)


def cmd_blur(ctx):
    try:
        radius = float(ctx.parts[0]) if ctx.parts else 5
    except ValueError:
        return ctx.reply("Uso: /blur [intensidade de 0 a 100]")
    _image_operation(ctx, media.blur_image, radius)


def cmd_pixelar(ctx):
    try:
        size = int(ctx.parts[0]) if ctx.parts else 12
    except ValueError:
        return ctx.reply("Uso: /pixelar [tamanho de 2 a 256]")
    _image_operation(ctx, media.pixelate_image, size)


def cmd_recortar(ctx):
    _image_operation(ctx, media.crop_square_image)


def _speed_operation(ctx, operation, default_factor):
    data, kind = _ctx_media(
        ctx, {"audio", "video"},
        f"Uso: envie ou responda a áudio/vídeo com /{ctx.command} [fator].",
    )
    if not data:
        return
    try:
        factor = float(ctx.parts[0]) if ctx.parts else default_factor
        result = operation(data, kind, factor)
        _send_processed(ctx, result, kind, f"✅ /{ctx.command} {factor:g}x concluído.")
    except (ValueError, media.MediaError) as exc:
        ctx.reply(f"❌ {exc}")


def cmd_acelerar(ctx):
    _speed_operation(ctx, media.speed_up_media, 2)


def cmd_lentidao(ctx):
    _speed_operation(ctx, media.slow_down_media, 2)


def cmd_reverter(ctx):
    data, kind = _ctx_media(
        ctx, {"audio", "video"},
        "Uso: envie ou responda a áudio/vídeo com /reverter.",
    )
    if not data:
        return
    try:
        _send_processed(ctx, media.reverse_media(data, kind), kind, "⏪ Mídia revertida.")
    except media.MediaError as exc:
        ctx.reply(f"❌ {exc}")


# ===================== v4: IA (11) =====================
def _ai_source(ctx, usage):
    source = ctx.args.strip() or get_quoted_text(ctx.msg)
    if not source:
        ctx.reply(usage)
        return ""
    return source


def _ai_instruction(ctx, instruction, usage):
    source = _ai_source(ctx, usage)
    if not source:
        return
    try:
        client.send_chat_presence(ctx.chat, 0, 0)
    except Exception:
        pass
    try:
        answer = ai_chat(f"{instruction}\n\nCONTEÚDO:\n{source}")
        ctx.reply(answer)
    except AIError as exc:
        ctx.reply(f"❌ IA indisponível: {exc}")


def cmd_resumir(ctx):
    _ai_instruction(ctx, "Resuma em português, preservando os fatos principais e sem inventar informações.",
                    "Uso: /resumir <texto> ou responda a uma mensagem com /resumir")


def cmd_corrigir(ctx):
    _ai_instruction(ctx, "Corrija ortografia e gramática. Responda apenas com o texto corrigido.",
                    "Uso: /corrigir <texto> ou responda a uma mensagem")


def cmd_explicar(ctx):
    _ai_instruction(ctx, "Explique em linguagem muito simples, como para uma criança de 5 anos.",
                    "Uso: /explicar <assunto ou texto>")


def cmd_codigo(ctx):
    _ai_instruction(ctx, "Gere ou explique o código solicitado. Use blocos de código e mencione cuidados importantes.",
                    "Uso: /codigo <pedido ou código>")


def cmd_transcrever(ctx):
    data, kind = _ctx_media(
        ctx, {"audio"},
        "Uso: envie um áudio com a legenda /transcrever ou responda a um áudio.",
    )
    if not data:
        return
    try:
        ctx.reply("🎙️ Transcrevendo áudio...")
        ctx.reply(f"📝 *Transcrição*\n{ai_transcribe(data, language='pt')}")
    except AIError as exc:
        ctx.reply(f"❌ {exc}")


def _vision_instruction(ctx, prompt, title):
    data, kind = _ctx_media(
        ctx, {"image", "sticker"},
        f"Uso: envie ou responda a uma foto com /{ctx.command}.",
    )
    if not data:
        return
    try:
        ctx.reply(f"{title} Processando imagem...")
        ctx.reply(ai_vision(prompt, data))
    except AIError as exc:
        ctx.reply(f"❌ IA de visão indisponível: {exc}")


def cmd_legenda(ctx):
    extra = f" Contexto dado pelo usuário: {ctx.args}" if ctx.args else ""
    _vision_instruction(ctx, "Crie uma legenda curta, criativa e apropriada em português para esta imagem." + extra, "✍️")


def cmd_ocr(ctx):
    _vision_instruction(ctx, "Extraia todo o texto visível desta imagem. Preserve linhas e responda apenas com o texto; se não houver, diga isso.", "🔎")


def cmd_sentimento(ctx):
    _ai_instruction(ctx, "Analise o tom e o sentimento. Seja objetivo e aponte sinais no texto.",
                    "Uso: /sentimento <texto> ou responda a uma mensagem")


def cmd_ideias(ctx):
    _ai_instruction(ctx, "Faça um brainstorm variado com pelo menos 10 ideias práticas e concisas.",
                    "Uso: /ideias <tema>")


def cmd_historia(ctx):
    _ai_instruction(ctx, "Crie uma história original, coerente e apropriada para todas as idades.",
                    "Uso: /historia <tema>")


def cmd_debater(ctx):
    _ai_instruction(ctx, "Apresente de forma equilibrada os melhores argumentos a favor e contra, e conclua sem falsa certeza.",
                    "Uso: /debater <tema>")


# ===================== v4: MODERAÇÃO (12) =====================
def _participant_change(ctx, action, label):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply(f"Uso: /{ctx.command} @usuario")
    try:
        client.update_group_participants(ctx.chat, [parse_jid(target)], action)
        audit(ctx.chat, ctx.chat_str, ctx.sender_str, ctx.command, short_jid(target))
        ctx.reply(f"✅ @{short_jid(target)} foi {label}.")
    except Exception as exc:
        ctx.reply(f"❌ Não consegui alterar o admin. Confirme se o bot é admin: {exc}")


def cmd_promover(ctx):
    _participant_change(ctx, ParticipantChange.PROMOTE, "promovido(a) a admin")


def cmd_rebaixar(ctx):
    _participant_change(ctx, ParticipantChange.DEMOTE, "rebaixado(a) de admin")


def _tag_everyone(ctx, hidden=False):
    """Menciona todos os membros do grupo.

    send_message NÃO tem parâmetro `mentions=` — quem descobre as menções é o
    próprio neonize, varrendo `@<dígitos>` no texto (ou em `ghost_mentions`,
    que tem precedência) para preencher o contextInfo.
      * /marcartodos: os @ ficam no texto, todo mundo vê quem foi marcado.
      * /hidetag: o texto fica limpo e os @ vão só em ghost_mentions, então
        todos recebem a notificação sem virar uma parede de números.
    """
    if not ctx.require_admin():
        return
    phones = _group_phones(ctx)
    if not phones:
        return ctx.reply("❌ Não consegui obter os membros do grupo.")
    text = ctx.args.strip() or "Atenção, pessoal!"
    marcacoes = " ".join(f"@{p}" for p in phones)
    try:
        if hidden:
            client.send_message(ctx.chat, f"📢 {text}", ghost_mentions=marcacoes)
        else:
            client.send_message(ctx.chat, f"📢 {text}\n{marcacoes}")
    except Exception as exc:
        ctx.reply(f"❌ Falha ao mencionar o grupo: {exc}")


def cmd_marcartodos(ctx):
    _tag_everyone(ctx, hidden=False)


def cmd_hidetag(ctx):
    _tag_everyone(ctx, hidden=True)


def cmd_delwarn(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /delwarn @usuario [id]")
    ids = [int(p) for p in ctx.parts if p.isdigit() and len(p) < 8]
    removed = db.delete_warn(ctx.chat_str, short_jid(target), ids[-1] if ids else None)
    ctx.reply("✅ Advertência removida." if removed else "❌ Advertência não encontrada.")


def _show_jid_list(ctx, title, rows):
    if not ctx.require_admin():
        return
    if not rows:
        return ctx.reply(f"{title}\n_nenhum usuário_"
        )
    ctx.reply(title + "\n" + "\n".join(f"• @{short_jid(row)}" for row in rows[:100]))


def cmd_listamutados(ctx):
    _show_jid_list(ctx, "🔇 *Usuários silenciados*", db.list_muted(ctx.chat_str))


def cmd_listabanidos(ctx):
    _show_jid_list(ctx, "🚫 *Usuários banidos*", db.list_banned(ctx.chat_str))


def cmd_antipalavrao(ctx):
    _toggle(ctx, "antipalavrao", "Filtro de palavrões", "🤐")


def cmd_blacklist(ctx):
    if not ctx.require_admin():
        return
    action = ctx.parts[0].casefold() if ctx.parts else "list"
    value = ctx.args.split(maxsplit=1)[1].strip() if len(ctx.parts) > 1 else ""
    if action in ("add", "adicionar") and value:
        added = db.blacklist_add(ctx.chat_str, value, ctx.sender_str)
        return ctx.reply("✅ Expressão adicionada à blacklist." if added else "ℹ️ Expressão já estava na blacklist.")
    if action in ("del", "remove", "remover") and value:
        removed = db.blacklist_remove(ctx.chat_str, value)
        return ctx.reply("✅ Expressão removida." if removed else "❌ Expressão não encontrada.")
    if action in ("list", "lista"):
        words = db.blacklist_list(ctx.chat_str)
        return ctx.reply("🚫 *Blacklist*\n" + ("\n".join(f"• {w}" for w in words) if words else "_vazia_"))
    ctx.reply("Uso: /blacklist add|remove <expressão> ou /blacklist list")


def cmd_antifake(ctx):
    if not ctx.require_admin():
        return
    action = ctx.parts[0].casefold() if ctx.parts else ""
    if action in ("on", "ativar", "ligar"):
        ddi = "".join(c for c in (ctx.parts[1] if len(ctx.parts) > 1 else "55") if c.isdigit())
        if not 1 <= len(ddi) <= 3:
            return ctx.reply("Uso: /antifake on [DDI permitido] (ex.: /antifake on 55)")
        db.set_setting(ctx.chat_str, "antifake", "1")
        db.set_setting(ctx.chat_str, "antifake_ddi", ddi)
        return ctx.reply(f"🛂 Antifake ativado. Somente DDI +{ddi} poderá entrar.")
    if action in ("off", "desativar", "desligar"):
        db.set_setting(ctx.chat_str, "antifake", "0")
        return ctx.reply("🛂 Antifake desativado.")
    status = db.get_setting(ctx.chat_str, "antifake") == "1"
    ctx.reply(f"Uso: /antifake on [DDI] | off\nStatus: {'ativado' if status else 'desativado'}")


def cmd_antimidia(ctx):
    if not ctx.require_admin():
        return
    aliases = {
        "foto": "image", "imagem": "image", "image": "image",
        "video": "video", "vídeo": "video", "audio": "audio", "áudio": "audio",
        "figurinha": "sticker", "sticker": "sticker",
    }
    action = ctx.parts[0].casefold() if ctx.parts else ""
    if action in ("off", "desativar", "desligar"):
        db.set_setting(ctx.chat_str, "antimidia", "")
        return ctx.reply("🖼️ Antimídia desativado.")
    if action in ("on", "todos", "tudo"):
        blocked = {"image", "video", "audio", "sticker"}
    else:
        blocked = {aliases[p.casefold()] for p in ctx.parts if p.casefold() in aliases}
    if blocked:
        db.set_setting(ctx.chat_str, "antimidia", ",".join(sorted(blocked)))
        return ctx.reply("🚷 Antimídia bloqueando: " + ", ".join(sorted(blocked)))
    current = db.get_setting(ctx.chat_str, "antimidia", "")
    ctx.reply("Uso: /antimidia on|off ou /antimidia foto video audio figurinha\nAtual: " + (current or "desativado"))


def cmd_inativos(ctx):
    if not ctx.require_admin():
        return
    try:
        days = int(ctx.parts[0]) if ctx.parts else 30
    except ValueError:
        return ctx.reply("Uso: /inativos [dias]")
    if not 1 <= days <= 3650:
        return ctx.reply("❌ Informe entre 1 e 3650 dias.")
    cutoff = int(time.time()) - days * 86400
    phones = _group_phones(ctx)
    if not phones:
        return ctx.reply("❌ Não consegui obter os membros do grupo. Tente de novo.")
    inactive = []
    for phone in phones:
        seen = db.get_last_activity(ctx.chat_str, phone)
        if seen is None or seen < cutoff:
            label = "sem registro" if seen is None else time.strftime("%d/%m/%Y", time.localtime(seen))
            inactive.append((phone, label))
    if not inactive:
        return ctx.reply(f"✅ Todos falaram nos últimos {days} dias.")
    lines = [f"💤 *Inativos há {days}+ dias* ({len(inactive)})"]
    lines.extend(f"• @{phone} — {last}" for phone, last in inactive[:100])
    ctx.reply("\n".join(lines))


# ===================== v4: UTILIDADES (13) =====================
def cmd_lembretes(ctx):
    rows = db.list_reminders(ctx.sender_str, ctx.chat_str)
    if not rows:
        return ctx.reply("⏰ Você não tem lembretes ativos neste chat.")
    lines = ["⏰ *Seus lembretes ativos*"]
    for row in rows[:30]:
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(row["due"]))
        lines.append(f"#{row['id']} — {when} — {row['text']}")
    ctx.reply("\n".join(lines))


def cmd_cancelarlembrete(ctx):
    if not ctx.parts or not ctx.parts[0].isdigit():
        return ctx.reply("Uso: /cancelarlembrete <id> (consulte /lembretes)")
    ok = db.cancel_reminder(int(ctx.parts[0]), ctx.sender_str, ctx.chat_str)
    ctx.reply("✅ Lembrete cancelado." if ok else "❌ Lembrete ativo não encontrado ou não pertence a você.")


def cmd_cep(ctx):
    ctx.reply(services.cep(ctx.args))


def cmd_ddd(ctx):
    ctx.reply(services.ddd(ctx.args))


def cmd_moeda(ctx):
    ctx.reply(services.moeda(ctx.args))


def cmd_wiki(ctx):
    ctx.reply(services.wiki(ctx.args))


def cmd_dicionario(ctx):
    ctx.reply(services.dicionario(ctx.args))


def cmd_imc(ctx):
    if len(ctx.parts) != 2:
        return ctx.reply("Uso: /imc <peso_kg> <altura_m> (ex.: /imc 70 1.75)")
    try:
        weight = float(ctx.parts[0].replace(",", "."))
        height = float(ctx.parts[1].replace(",", "."))
        if not 20 <= weight <= 500 or not 0.8 <= height <= 2.8:
            raise ValueError
    except ValueError:
        return ctx.reply("❌ Peso ou altura inválidos.")
    value = weight / (height * height)
    category = "baixo peso" if value < 18.5 else "peso adequado" if value < 25 else "sobrepeso" if value < 30 else "obesidade"
    ctx.reply(f"⚖️ IMC: *{value:.1f}* — {category}.\n_Este cálculo é apenas informativo._")


def cmd_idade(ctx):
    from datetime import date, datetime
    raw = ctx.args.strip()
    born = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            born = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            pass
    today = date.today()
    if born is None or born > today or born.year < 1900:
        return ctx.reply("Uso: /idade DD/MM/AAAA")
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    ctx.reply(f"🎂 Idade: *{age} anos*.")


def cmd_regra3(ctx):
    if len(ctx.parts) != 3:
        return ctx.reply("Uso: /regra3 <a> <b> <c>  (a está para b assim como c está para x)")
    try:
        a, b, c = (float(p.replace(",", ".")) for p in ctx.parts)
        if a == 0:
            raise ValueError
    except ValueError:
        return ctx.reply("❌ Use três números e mantenha o primeiro diferente de zero.")
    ctx.reply(f"📐 x = *{(b * c / a):.6g}*")


def cmd_base64(ctx):
    import base64 as b64
    if len(ctx.parts) < 2:
        return ctx.reply("Uso: /base64 encode <texto> | /base64 decode <base64>")
    action, value = ctx.parts[0].casefold(), ctx.args.split(maxsplit=1)[1]
    if len(value) > 20000:
        return ctx.reply("❌ Entrada longa demais.")
    try:
        if action in ("encode", "codificar"):
            result = b64.b64encode(value.encode()).decode()
        elif action in ("decode", "decodificar"):
            result = b64.b64decode(value, validate=True).decode("utf-8")
        else:
            return ctx.reply("Ação inválida: use encode ou decode.")
        ctx.reply(f"🔐 `{result}`")
    except Exception:
        ctx.reply("❌ Base64 inválido ou texto não UTF-8.")


def cmd_hash(ctx):
    import hashlib
    if len(ctx.parts) < 2 or ctx.parts[0].casefold() not in ("md5", "sha256"):
        return ctx.reply("Uso: /hash md5|sha256 <texto>")
    algorithm = ctx.parts[0].casefold()
    value = ctx.args.split(maxsplit=1)[1].encode()
    digest = hashlib.new(algorithm, value).hexdigest()
    ctx.reply(f"#️⃣ {algorithm.upper()}: `{digest}`")


def cmd_horario(ctx):
    ctx.reply(services.horario(ctx.args))


# ===================== v4: ECONOMIA E RPG (11) =====================
def cmd_perfil(ctx):
    target = ctx.target_jid_str() or ctx.sender_str
    xp = db.get_xp(target)
    level, current, needed = db.level_from_xp(xp)
    wallet, bank = db.get_wallet_and_bank(target)
    roles = db.get_roles(ctx.chat_str, short_jid(target))
    inventory = db.get_inventory(target)
    items = sum(row["quantity"] for row in inventory)
    ctx.reply(
        f"👤 *Perfil de @{short_jid(target)}*\n"
        f"⭐ Nível {level} — {current}/{needed} XP\n"
        f"💰 Carteira: {wallet} | 🏦 Banco: {bank}\n"
        f"🎖️ Cargos: {', '.join(roles) if roles else 'nenhum'}\n"
        f"🎒 Itens: {items}"
    )


def cmd_loja(ctx):
    items = db.shop_list()
    lines = ["🛒 *Loja*"]
    lines.extend(f"• `{row['item_id']}` — {row['name']} — {row['price']} moedas\n  {row['description']}" for row in items)
    lines.append("\nCompre com /comprar <id> [quantidade]")
    ctx.reply("\n".join(lines))


def cmd_comprar(ctx):
    if not ctx.parts:
        return ctx.reply("Uso: /comprar <id> [quantidade]")
    try:
        quantity = int(ctx.parts[1]) if len(ctx.parts) > 1 else 1
    except ValueError:
        return ctx.reply("❌ Quantidade inválida.")
    # quantidade negativa venderia o item de volta, creditando moedas
    if quantity <= 0 or quantity > 1000:
        return ctx.reply("❌ Quantidade deve ser entre 1 e 1000.")
    ok, result = db.buy_item(ctx.sender_str, ctx.parts[0], quantity)
    if not ok:
        return ctx.reply(f"❌ {result}")
    ctx.reply(
        f"✅ Comprou {result['quantity']}x {result['name']} por {result['total']} moedas.\n"
        f"Saldo: {result['balance']}"
    )


def cmd_inventario(ctx):
    rows = db.get_inventory(ctx.sender_str)
    if not rows:
        return ctx.reply("🎒 Seu inventário está vazio.")
    ctx.reply("🎒 *Inventário*\n" + "\n".join(
        f"• {row['name'] or row['item_id']} x{row['quantity']}" for row in rows
    ))


def cmd_trabalhar(ctx):
    reward = random.randint(80, 180)
    ok, value = db.claim_work(ctx.sender_str, reward=reward, cooldown=3600)
    if not ok:
        return ctx.reply(f"⏳ Você já trabalhou. Volte em {utils.human_uptime(value)}.")
    jobs = ["programou um site", "ajudou numa mudança", "fez entregas", "consertou um computador"]
    ctx.reply(f"🧰 Você {random.choice(jobs)} e ganhou *{value} moedas*!\nSaldo: {db.get_balance(ctx.sender_str)}")


def cmd_roubar(ctx):
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /roubar @usuario")
    success = random.random() < 0.45
    amount = random.randint(25, 120)
    penalty = random.randint(15, 80)
    ok, result = db.rob_user(ctx.sender_str, target, amount, success=success, penalty=penalty)
    if not ok:
        if isinstance(result, int):
            return ctx.reply(f"⏳ Tente roubar novamente em {utils.human_uptime(result)}.")
        return ctx.reply(f"❌ {result}")
    if result["success"]:
        ctx.reply(f"🥷 Você roubou *{result['amount']} moedas* de @{short_jid(target)}!")
    else:
        ctx.reply(f"🚓 Você foi pego e perdeu *{result['amount']} moedas*.")


def cmd_apostar(ctx):
    if not ctx.parts or not ctx.parts[0].isdigit():
        return ctx.reply("Uso: /apostar <valor>")
    amount = int(ctx.parts[0])
    if amount <= 0:
        return ctx.reply("❌ Aposte um valor maior que zero.")
    won = random.random() < 0.48
    ok, result = db.place_bet(ctx.sender_str, amount, won, multiplier=2)
    if not ok:
        return ctx.reply(f"❌ {result}")
    if result["won"]:
        ctx.reply(f"🎰 Você venceu! Prêmio: *{result['payout']} moedas*. Saldo: {result['balance']}")
    else:
        ctx.reply(f"🎰 Você perdeu {result['stake']} moedas. Saldo: {result['balance']}")


def cmd_banco(ctx):
    wallet, bank = db.get_wallet_and_bank(ctx.sender_str)
    ctx.reply(f"🏦 *Banco*\nCarteira: {wallet} moedas\nGuardado: *{bank} moedas*")


def _money_amount(ctx, available):
    """Lê um valor em moedas do comando. Devolve None se for inválido.

    Rejeita zero e negativos: sem isso, `/sacar -1000` inverteria o sentido da
    transferência e viraria um depósito de dinheiro que o usuário não tem
    (o mesmo valia para `/depositar -1000`).
    """
    if not ctx.parts:
        return None
    if ctx.parts[0].casefold() in ("tudo", "all"):
        return available if available > 0 else None
    try:
        valor = int(ctx.parts[0])
    except ValueError:
        return None
    return valor if valor > 0 else None


def cmd_depositar(ctx):
    wallet, _ = db.get_wallet_and_bank(ctx.sender_str)
    amount = _money_amount(ctx, wallet)
    if amount is None:
        return ctx.reply("Uso: /depositar <valor|tudo>")
    ok, result = db.deposit(ctx.sender_str, amount)
    ctx.reply(
        f"✅ Depósito feito. Carteira: {result['balance']} | Banco: {result['bank']}"
        if ok else f"❌ {result}"
    )


def cmd_sacar(ctx):
    _, bank = db.get_wallet_and_bank(ctx.sender_str)
    amount = _money_amount(ctx, bank)
    if amount is None:
        return ctx.reply("Uso: /sacar <valor|tudo>")
    ok, result = db.withdraw(ctx.sender_str, amount)
    ctx.reply(
        f"✅ Saque feito. Carteira: {result['balance']} | Banco: {result['bank']}"
        if ok else f"❌ {result}"
    )


def cmd_casar(ctx):
    action = ctx.parts[0].casefold() if ctx.parts else "status"
    if action in ("aceitar", "sim"):
        ok, result = db.accept_marriage(ctx.chat_str, ctx.sender_str)
        if not ok:
            return ctx.reply(f"❌ {result}")
        return ctx.reply(f"💍 Casamento confirmado entre @{short_jid(result['jid1'])} e @{short_jid(result['jid2'])}!")
    if action in ("recusar", "nao", "não"):
        return ctx.reply("💔 Pedido recusado." if db.decline_marriage(ctx.chat_str, ctx.sender_str) else "❌ Pedido não encontrado.")
    if action in ("divorciar", "divorcio", "divórcio"):
        return ctx.reply("💔 Divórcio concluído." if db.divorce(ctx.chat_str, ctx.sender_str) else "❌ Você não está casado(a).")
    target = ctx.target_jid_str()
    if target:
        ok, result = db.propose_marriage(ctx.chat_str, ctx.sender_str, target)
        return ctx.reply(
            f"💍 @{short_jid(target)}, @{short_jid(ctx.sender_str)} pediu você em casamento! Responda /casar aceitar."
            if ok else f"❌ {result}"
        )
    row = db.get_marriage(ctx.chat_str, ctx.sender_str, include_pending=True)
    if not row:
        return ctx.reply("💍 Você está solteiro(a). Use /casar @usuario.")
    other = row["jid2"] if row["jid1"] == ctx.sender_str else row["jid1"]
    ctx.reply(f"💍 Status: {row['status']} com @{short_jid(other)}")


# ===================== v4: DIVERSÃO E SOCIAL (10) =====================
def _social_target(ctx, kind):
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply(f"Uso: /{ctx.command} @usuario")
    actor = "@" + short_jid(ctx.sender_str)
    target_label = "@" + short_jid(target)
    ctx.reply(games.social_interaction(kind, actor, target_label, seed=str(time.time_ns())))


def cmd_abraco(ctx):
    _social_target(ctx, "abraco")


def cmd_tapa(ctx):
    _social_target(ctx, "tapa")


def cmd_elogiar(ctx):
    _social_target(ctx, "elogio")


def cmd_sorteio(ctx):
    if "|" in ctx.args:
        choices = [item.strip() for item in ctx.args.split("|") if item.strip()]
    elif ctx.parts:
        # o que o usuário digitou tem prioridade sobre sortear um membro:
        # antes, "/sorteio pizza sushi" num grupo ignorava as opções
        choices = ctx.parts
    elif ctx.is_group:
        choices = ["@" + phone for phone in _group_phones(ctx)]
    else:
        choices = []
    if len(choices) < 2:
        return ctx.reply("Uso: /sorteio opção 1 | opção 2 | opção 3 (ou use no grupo)")
    ctx.reply(f"🎉 Sorteado: *{random.choice(choices)}*")


def cmd_casal(ctx):
    if not ctx.is_group:
        return ctx.reply("❌ Esse comando só funciona em grupos.")
    try:
        a, b = games.couple_of_day(_group_phones(ctx))
        ctx.reply(f"💞 *Casal do dia*\n@{a} + @{b}")
    except ValueError as exc:
        ctx.reply(f"❌ {exc}")


def cmd_piada(ctx):
    ctx.reply(f"😂 {games.joke()}")


def cmd_charada(ctx):
    item = games.new_riddle()
    ctx.reply(f"🧩 *Charada*\n{item['q']}\n\n💡 Resposta: ||{item['a']}||")


def cmd_conselho(ctx):
    ctx.reply(f"💡 {games.advice()}")


def cmd_travalingua(ctx):
    ctx.reply(f"👅 {games.tongue_twister()}")


def cmd_top(ctx):
    if not ctx.is_group:
        return ctx.reply("❌ Esse comando só funciona em grupos.")
    criterion = ctx.parts[0].casefold() if ctx.parts else "xp"
    members = _group_phones(ctx)
    if criterion in ("moedas", "saldo", "rico"):
        entries = db.scores_for_phones(members, "moedas")
        unit = "moedas"
    else:
        entries = db.scores_for_phones(members, "xp")
        unit = "XP"
    lines = [f"🏆 *Top 5 — {criterion}*"]
    lines.extend(f"{i}. @{phone} — {score:g} {unit}" for i, (phone, score) in enumerate(games.top_members(entries), 1))
    ctx.reply("\n".join(lines))


# ===================== v4: JOGOS (9) =====================
def cmd_anagrama(ctx):
    state = _active_games.get(ctx.chat_str)
    if state and state.get("type") == "anagrama" and ctx.args:
        if games.check_anagram(ctx.args, state["word"]):
            db.add_balance(ctx.sender_str, 40)
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply("✅ Acertou! +40 moedas.")
        return ctx.reply(f"❌ Ainda não. Dica: {state['hint']}")
    item = games.new_anagram()
    _active_games[ctx.chat_str] = {"type": "anagrama", **item}
    ctx.reply(f"🔀 *Anagrama:* `{item['scrambled']}`\nDica: {item['hint']}\nResponda /anagrama <palavra>")


def cmd_cacapalavras(ctx):
    puzzle = games.new_word_search()
    ctx.reply("🔎 *Caça-palavras*\n```\n" + games.render_word_search(puzzle) + "\n```\nPalavras: " + ", ".join(puzzle["words"]))


def cmd_emojiquiz(ctx):
    state = _active_games.get(ctx.chat_str)
    if state and state.get("type") == "emojiquiz" and ctx.args:
        if games.check_emoji_quiz(ctx.args, state["answer"]):
            db.add_balance(ctx.sender_str, 40)
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply("🎬 Acertou o filme! +40 moedas.")
        return ctx.reply(f"❌ Errado. Dica: {state['hint']}")
    item = games.new_emoji_quiz()
    _active_games[ctx.chat_str] = {"type": "emojiquiz", "answer": item["a"], "hint": item["hint"]}
    ctx.reply(f"🎬 *Qual é o filme?* {item['q']}\nResponda /emojiquiz <filme>")


def cmd_enigma(ctx):
    state = _active_games.get(ctx.chat_str)
    if state and state.get("type") == "enigma" and ctx.args:
        expected = re.sub(r"\W", "", state["answer"].casefold())
        answer = re.sub(r"\W", "", ctx.args.casefold())
        _active_games.pop(ctx.chat_str, None)
        if answer == expected:
            db.add_balance(ctx.sender_str, 50)
            return ctx.reply("🧠 Resposta correta! +50 moedas.")
        return ctx.reply(f"❌ A resposta era: {state['answer']}")
    item = games.new_logic_puzzle()
    _active_games[ctx.chat_str] = {"type": "enigma", "answer": item["a"]}
    ctx.reply(f"🧠 *Enigma*\n{item['q']}\nResponda /enigma <resposta>")


def cmd_bingo(ctx):
    ctx.reply("🎱 *Sua cartela de bingo*\n```\n" + games.render_bingo_card(games.new_bingo_card()) + "\n```")


def cmd_soletrar(ctx):
    state = _active_games.get(ctx.chat_str)
    if state and state.get("type") == "soletrar" and ctx.args:
        if games.check_spelling(ctx.args, state["word"]):
            db.add_balance(ctx.sender_str, 30)
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply("🔤 Correto! +30 moedas.")
        return ctx.reply("❌ Grafia incorreta. Tente de novo.")
    item = games.new_spelling()
    _active_games[ctx.chat_str] = {"type": "soletrar", "word": item["word"]}
    ctx.reply(f"🔤 *Soletre a palavra desta dica:* {item['hint']}\nResponda /soletrar <palavra>")


def cmd_sequencia(ctx):
    state = _active_games.get(ctx.chat_str)
    if state and state.get("type") == "sequencia" and ctx.args:
        if games.check_sequence(ctx.args, state["sequence"]):
            db.add_balance(ctx.sender_str, 35)
            _active_games.pop(ctx.chat_str, None)
            return ctx.reply("🧠 Sequência correta! +35 moedas.")
        _active_games.pop(ctx.chat_str, None)
        return ctx.reply("❌ Sequência incorreta.")
    sequence = games.new_sequence(random.randint(1, 5))
    _active_games[ctx.chat_str] = {"type": "sequencia", "sequence": sequence}
    ctx.reply("🧠 Memorize: `" + " ".join(map(str, sequence)) + "`\nResponda /sequencia <números>")


def cmd_campominado(ctx):
    state = _active_games.get(ctx.chat_str)
    if not state or state.get("type") != "campominado":
        board = games.new_minesweeper()
        state = {"type": "campominado", "board": board, "revealed": set()}
        _active_games[ctx.chat_str] = state
        return ctx.reply("💣 *Campo minado*\n" + games.render_minesweeper(board) + "\nJogue /campominado <linha> <coluna>")
    if len(ctx.parts) != 2 or not all(p.isdigit() for p in ctx.parts):
        return ctx.reply("Uso: /campominado <linha> <coluna>")
    try:
        result = games.minesweeper_reveal(
            state["board"], int(ctx.parts[0]) - 1, int(ctx.parts[1]) - 1, state["revealed"]
        )
    except ValueError as exc:
        return ctx.reply(f"❌ {exc}")
    if not result.get("valid", True):
        size = len(state["board"])
        return ctx.reply(f"❌ Fora do tabuleiro. Use linha e coluna de 1 a {size}.")
    state["revealed"] = result["revealed"]
    if result["hit"]:
        board = games.render_minesweeper(state["board"], state["revealed"], show_mines=True)
        _active_games.pop(ctx.chat_str, None)
        return ctx.reply("💥 Você pisou numa mina!\n" + board)
    if result["won"]:
        db.add_balance(ctx.sender_str, 100)
        _active_games.pop(ctx.chat_str, None)
        return ctx.reply("🏆 Você limpou o campo! +100 moedas.\n" + games.render_minesweeper(state["board"], state["revealed"]))
    ctx.reply(games.render_minesweeper(state["board"], state["revealed"]) + "\nJogue novamente.")


def cmd_desafio(ctx):
    item = games.daily_challenge(ctx.sender_str)
    if ctx.parts and ctx.parts[0].casefold() in ("concluir", "feito", "resgatar"):
        ok, result = db.claim_daily_challenge(
            ctx.sender_str, reward=item["reward"], challenge=item["challenge"], day=item["date"]
        )
        return ctx.reply(f"✅ Desafio concluído! +{result} moedas." if ok else f"❌ {result}")
    row = db.get_daily_challenge(
        ctx.sender_str, day=item["date"], challenge=item["challenge"], reward=item["reward"]
    )
    status = "✅ já resgatado" if row["claimed"] else "use /desafio concluir ao terminar"
    ctx.reply(f"🎯 *Desafio diário*\n{row['challenge']}\nRecompensa: {row['reward']} moedas\n_{status}_")


# ===================== ROTEADOR =====================
COMMANDS = {
    "ttkvd": cmd_ttkvd, "ban": cmd_ban, "unban": cmd_unban, "kick": cmd_kick, "mute": cmd_mute,
    "unmute": cmd_unmute, "clear": cmd_clear, "lock": cmd_lock, "unlock": cmd_unlock,
    "warn": cmd_warn, "checkwarns": cmd_checkwarns, "setprefix": cmd_setprefix,
    "addrole": cmd_addrole, "removerole": cmd_removerole, "slowmode": cmd_slowmode,
    "announce": cmd_announce, "nuke": cmd_nuke, "welcome": cmd_welcome,
    "antibot": cmd_antibot, "antilink": cmd_antilink, "antispam": cmd_antispam,
    "setlogs": cmd_setlogs, "whitelist-add": cmd_whitelist_add,
    "whitelist-remove": cmd_whitelist_remove, "maintenance": cmd_maintenance,
    "backup-create": cmd_backup_create, "backup-load": cmd_backup_load,
    "auditlog": cmd_auditlog,
    "ia": cmd_ia, "ping": cmd_ping, "help": cmd_help, "userinfo": cmd_userinfo,
    "serverinfo": cmd_serverinfo, "avatar": cmd_avatar, "fg": cmd_fg, "va": cmd_va,
    "calc": cmd_calc,
    "weather": cmd_weather, "translate": cmd_translate, "remind": cmd_remind,
    "poll": cmd_poll, "afk": cmd_afk, "invite": cmd_invite, "uptime": cmd_uptime,
    "report": cmd_report, "suggest": cmd_suggest, "level": cmd_level,
    "leaderboard": cmd_leaderboard, "daily": cmd_daily, "balance": cmd_balance, "pay": cmd_pay,
    "coinflip": cmd_coinflip, "jokenpo": cmd_jokenpo, "8ball": cmd_8ball, "roll": cmd_roll,
    "tictactoe": cmd_tictactoe, "trivia": cmd_trivia, "hangman": cmd_hangman,
    "akinator": cmd_akinator, "russianroulette": cmd_russianroulette, "ship": cmd_ship,
    # ----- IA avançada -----
    "iamode": cmd_iamode, "aimodel": cmd_aimodel, "thinking": cmd_thinking,
    "aisetname": cmd_aisetname, "aisetbio": cmd_aisetbio, "aisetavatar": cmd_aisetavatar,
    "aichannel": cmd_aichannel, "aireset": cmd_aireset, "aistatus": cmd_aistatus,
    # ----- IA: visão, geração de imagem e pesquisa -----
    "analiseia": cmd_analiseia, "analisar": cmd_analiseia, "vision": cmd_analiseia,
    "gerarimagem": cmd_gerarimagem, "imagine": cmd_gerarimagem,
    "pesquisa": cmd_pesquisa, "pesquisar": cmd_pesquisa,
    # ----- v3.1 PRO: admin -----
    "giverole": cmd_giverole, "temprole": cmd_temprole, "tempban": cmd_tempban,
    "softban": cmd_softban, "massrole": cmd_massrole, "createrole": cmd_createrole,
    "deleterole": cmd_deleterole, "setwelcome": cmd_setwelcome, "setbye": cmd_setbye,
    "autorole": cmd_autorole, "setmodlog": cmd_setmodlog, "logs": cmd_setlogs,
    "backupserver": cmd_backup_create, "restorebackup": cmd_backup_load,
    "nickname": cmd_nickname, "resetnick": cmd_resetnick, "hidechannel": cmd_hidechannel,
    "showchannel": cmd_showchannel, "clonechannel": cmd_clonechannel,
    "deletechannel": cmd_deletechannel, "createchannel": cmd_createchannel,
    # ----- v3.1 PRO: gerais -----
    "qr": cmd_qr, "shorturl": cmd_shorturl, "password": cmd_password, "meme": cmd_meme,
    "quote": cmd_quote, "fact": cmd_fact, "crypto": cmd_crypto, "timer": cmd_timer,
    "countdown": cmd_countdown, "stopwatch": cmd_stopwatch, "convert": cmd_convert,
    "emojify": cmd_emojify, "snipe": cmd_snipe, "editsnipe": cmd_editsnipe,
    "banner": cmd_banner, "roleinfo": cmd_roleinfo, "channelinfo": cmd_channelinfo,
    "membercount": cmd_membercount, "boosts": cmd_boosts, "randomuser": cmd_randomuser,
    "randomnumber": cmd_randomnumber, "choose": cmd_choose, "reverse": cmd_reverse,
    "sayembed": cmd_sayembed, "firstmessage": cmd_firstmessage,
    # ----- v3.1 PRO: jogos -----
    "slot": cmd_slot, "blackjack": cmd_blackjack, "roulette": cmd_roulette,
    "crash": cmd_crash, "higherlower": cmd_higherlower, "guessnumber": cmd_guessnumber,
    "mathrace": cmd_mathrace, "guessflag": cmd_guessflag, "guesspokemon": cmd_guesspokemon,
    "guessanime": cmd_guessanime, "wordchain": cmd_wordchain, "memory": cmd_memory,
    "reaction": cmd_reaction, "fastclick": cmd_fastclick, "wouldyourather": cmd_wouldyourather,
    "neverhaveiever": cmd_neverhaveiever, "truth": cmd_truth, "dare": cmd_dare,
    "battle": cmd_battle, "duel": cmd_duel, "bossfight": cmd_bossfight, "arena": cmd_arena,
    "treasurehunt": cmd_treasurehunt, "heist": cmd_heist, "escape": cmd_escape,
    "labyrinth": cmd_labyrinth, "dungeon": cmd_dungeon, "tower": cmd_tower,
    "fishing": cmd_fishing, "mining": cmd_mining, "hunt": cmd_hunt, "petbattle": cmd_petbattle,
    "dragonhunt": cmd_dragonhunt, "farm": cmd_farm, "race": cmd_race, "parkour": cmd_parkour,
    "coinwar": cmd_coinwar, "poker": cmd_poker, "mafia": cmd_mafia, "detective": cmd_detective,
    "spy": cmd_spy, "infected": cmd_infected, "murdermystery": cmd_murdermystery,
    "zombie": cmd_zombie, "survivor": cmd_survivor, "kingdom": cmd_kingdom,
    "hotpotato": cmd_hotpotato, "guesslogo": cmd_guesslogo, "guesssong": cmd_guesssong,
    # ----- v4: mídia e figurinhas (12) -----
    "toimg": cmd_toimg, "togif": cmd_togif, "fgpack": cmd_fgpack,
    "girar": cmd_girar, "espelhar": cmd_espelhar, "pb": cmd_pb,
    "blur": cmd_blur, "pixelar": cmd_pixelar, "recortar": cmd_recortar,
    "acelerar": cmd_acelerar, "lentidao": cmd_lentidao, "reverter": cmd_reverter,
    # ----- v4: IA (11) -----
    "resumir": cmd_resumir, "corrigir": cmd_corrigir, "explicar": cmd_explicar,
    "codigo": cmd_codigo, "transcrever": cmd_transcrever, "legenda": cmd_legenda,
    "ocr": cmd_ocr, "sentimento": cmd_sentimento, "ideias": cmd_ideias,
    "historia": cmd_historia, "debater": cmd_debater,
    # ----- v4: moderação (12) -----
    "promover": cmd_promover, "rebaixar": cmd_rebaixar,
    "marcartodos": cmd_marcartodos, "hidetag": cmd_hidetag,
    "delwarn": cmd_delwarn, "listamutados": cmd_listamutados,
    "listabanidos": cmd_listabanidos, "antipalavrao": cmd_antipalavrao,
    "blacklist": cmd_blacklist, "antifake": cmd_antifake,
    "antimidia": cmd_antimidia, "inativos": cmd_inativos,
    # ----- v4: utilidades (13) -----
    "lembretes": cmd_lembretes, "cancelarlembrete": cmd_cancelarlembrete,
    "cep": cmd_cep, "ddd": cmd_ddd, "moeda": cmd_moeda, "wiki": cmd_wiki,
    "dicionario": cmd_dicionario, "imc": cmd_imc, "idade": cmd_idade,
    "regra3": cmd_regra3, "base64": cmd_base64, "hash": cmd_hash,
    "horario": cmd_horario,
    # ----- v4: economia e RPG (11) -----
    "perfil": cmd_perfil, "loja": cmd_loja, "comprar": cmd_comprar,
    "inventario": cmd_inventario, "trabalhar": cmd_trabalhar,
    "roubar": cmd_roubar, "apostar": cmd_apostar, "banco": cmd_banco,
    "depositar": cmd_depositar, "sacar": cmd_sacar, "casar": cmd_casar,
    # ----- v4: diversão e social (10) -----
    "abraco": cmd_abraco, "tapa": cmd_tapa, "elogiar": cmd_elogiar,
    "sorteio": cmd_sorteio, "casal": cmd_casal, "piada": cmd_piada,
    "charada": cmd_charada, "conselho": cmd_conselho,
    "travalingua": cmd_travalingua, "top": cmd_top,
    # ----- v4: jogos (9) -----
    "anagrama": cmd_anagrama, "cacapalavras": cmd_cacapalavras,
    "emojiquiz": cmd_emojiquiz, "enigma": cmd_enigma, "bingo": cmd_bingo,
    "soletrar": cmd_soletrar, "sequencia": cmd_sequencia,
    "campominado": cmd_campominado, "desafio": cmd_desafio,
}


def handle_command(message, text):
    prefix = db.get_prefix(Jid2String(message.Info.MessageSource.Chat))
    body = text[len(prefix):].strip()
    if not body:
        return
    command = body.split()[0].lower()
    args = body[len(command):].strip()
    handler = COMMANDS.get(command)
    if not handler:
        return
    try:
        ctx = Ctx(message, command, args)
    except Exception as exc:
        print(f"❌ Erro ao criar contexto para /{command}: {exc}")
        return
    try:
        handler(ctx)
    except Exception as exc:  # nunca derrubar o listener
        try:
            ctx.reply(f"⚠️ Erro ao executar /{command}: {exc}")
        except Exception:
            pass


# ===================== EVENTOS =====================
def handle_connected():
    print(f"✅ {config.BOT_NAME} conectado ao WhatsApp!")
    _wa_ready.set()
    _paired.set()  # encerra o loop de reconexão de pareamento
    _me_phone(refresh=True)  # cacheia o próprio número (detectar menções)
    webui.set_connected()
    _mark_alive()


def handle_pair(message):
    print(f"🔗 Pareado como: {message.ID.User}")
    _paired.set()
    webui.set_connected()


@client.event(ConnectedEv)
def on_connected(_, __):
    handle_connected()


@client.event(PairStatusEv)
def on_pair(_, message):
    handle_pair(message)


# ---- Diagnóstico de falhas de conexão -------------------------------------
# Sem isso, PairPhone() falha com "websocket not connected" e não há como
# saber POR QUE o socket caiu (ban temporário? stream error? logout remoto?
# client desatualizado?). Esses eventos expõem a causa real do neonize.
# Lógica em funções separadas (testáveis) — o decorator @client.event()
# sempre retorna None, então não dá pra chamar o nome decorado diretamente.
def handle_connect_failure(event):
    _wa_ready.clear()
    try:
        reason_name = N.ConnectFailureReason.Name(event.Reason)
    except Exception:
        reason_name = str(event.Reason)
    reason = f"{reason_name} — {event.Message}" if event.Message else reason_name
    print(f"❌ Falha ao conectar ao WhatsApp: {reason}")
    webui.set_disconnected(f"O WhatsApp recusou a conexão: {reason}")


def handle_disconnected(event):
    _wa_ready.clear()
    print(f"🔌 Desconectado do WhatsApp (status booleano={event.status}).")
    webui.set_disconnected("Conexão com o WhatsApp caiu. Recarregue a página em alguns segundos.")


def handle_stream_error(event):
    _wa_ready.clear()
    print(f"⚠️ Erro de stream do WhatsApp: code={event.Code} raw={event.Raw}")
    webui.set_disconnected(f"Erro de stream do WhatsApp (code={event.Code}). Tente novamente em alguns instantes.")


def handle_logged_out(event):
    _wa_ready.clear()
    try:
        reason_name = N.ConnectFailureReason.Name(event.Reason)
    except Exception:
        reason_name = str(event.Reason)
    print(f"🚪 Sessão desconectada pelo WhatsApp: {reason_name} (on_connect={event.OnConnect})")
    webui.set_disconnected(
        f"O WhatsApp encerrou a sessão salva ({reason_name}). Apague o arquivo "
        f"SESSION_DB e pareie de novo do zero."
    )


def handle_temp_ban(event):
    _wa_ready.clear()
    print(f"🚫 Banimento temporário do WhatsApp: code={event.Code} expira={event.Expire}")
    webui.set_disconnected(
        f"O WhatsApp aplicou um bloqueio temporário neste número/IP (code={event.Code}). "
        f"Espere e tente de novo mais tarde."
    )


def handle_client_outdated():
    _wa_ready.clear()
    print("📛 O WhatsApp recusou a conexão: versão do cliente (neonize/whatsmeow) desatualizada.")
    webui.set_disconnected(
        "O WhatsApp recusou a conexão por versão de cliente desatualizada. "
        "Atualize o pacote neonize (pip install -U neonize) e reinicie o bot."
    )


@client.event(ConnectFailureEv)
def on_connect_failure(_, event):
    handle_connect_failure(event)


@client.event(DisconnectedEv)
def on_disconnected(_, event):
    handle_disconnected(event)


@client.event(StreamErrorEv)
def on_stream_error(_, event):
    handle_stream_error(event)


@client.event(LoggedOutEv)
def on_logged_out(_, event):
    handle_logged_out(event)


@client.event(TemporaryBanEv)
def on_temp_ban(_, event):
    handle_temp_ban(event)


@client.event(ClientOutdatedEv)
def on_client_outdated(_, __):
    handle_client_outdated()


@client.event(GroupInfoEv)
def on_group_change(_, event):
    handle_group_change(event)


def handle_group_change(event):
    """Boas-vindas (/welcome), despedida (/setbye), autorole e antibot."""
    chat = event.JID
    chat_str = Jid2String(chat)
    try:
        joined = list(event.Join)
    except Exception:
        joined = []

    # ----- DESPEDIDA (/setbye) -----
    try:
        left = list(event.Leave)
    except Exception:
        left = []
    if left and db.get_setting(chat_str, "bye") == "1":
        tpl = db.get_setting(chat_str, "byetext") or "👋 @user saiu do grupo. Até logo!"
        for member in left:
            phone = short_jid(Jid2String(member))
            msg = tpl.replace("@user", f"@{phone}") if "@user" in tpl else f"{tpl} (@{phone})"
            try:
                client.send_message(chat, msg)
            except Exception:
                pass

    if not joined:
        return

    # ----- ANTIFAKE: aceita apenas o DDI configurado -----
    if db.get_setting(chat_str, "antifake") == "1":
        allowed_ddi = db.get_setting(chat_str, "antifake_ddi", "55")
        accepted = []
        removed = []
        falhou = []
        for member in joined:
            phone = short_jid(Jid2String(member))
            if phone.startswith(allowed_ddi) or db.is_whitelisted(chat_str, phone):
                accepted.append(member)
                continue
            try:
                client.update_group_participants(chat, [member], ParticipantChange.REMOVE)
                removed.append(phone)
            except Exception:
                # Falhou a remoção (bot sem admin, por exemplo). Não anuncia como
                # removido — antes o grupo recebia "antifake removeu @fulano"
                # com o fulano ainda dentro — mas também não dá boas-vindas.
                falhou.append(phone)
        joined = accepted
        if falhou:
            try:
                client.send_message(
                    chat,
                    "⚠️ Antifake detectou número com DDI não permitido mas *não "
                    "consegui remover* — confirme se o bot é admin: "
                    + ", ".join("@" + p for p in falhou),
                )
            except Exception:
                pass
        if removed:
            audit(chat, chat_str, "antifake", "ANTIFAKE", f"removidos: {', '.join(removed)}")
            try:
                client.send_message(chat, "🛂 Antifake removeu número(s) com DDI não permitido: " +
                                    ", ".join("@" + phone for phone in removed))
            except Exception:
                pass
        if not joined:
            return

    # ----- ANTIBOT: bloqueia entradas feitas por quem não é admin -----
    if db.get_setting(chat_str, "antibot") == "1":
        try:
            adder = Jid2String(event.Sender) if event.Sender.User else ""
        except Exception:
            adder = ""
        adder_is_admin = bool(adder) and is_group_admin(chat, adder)
        if not adder_is_admin:
            removed = []
            for member in joined:
                m_str = Jid2String(member)
                if db.is_whitelisted(chat_str, short_jid(m_str)):
                    continue
                try:
                    client.update_group_participants(chat, [member], ParticipantChange.REMOVE)
                    removed.append(short_jid(m_str))
                except Exception:
                    pass
            if removed:
                audit(chat, chat_str, adder or "?", "ANTIBOT", f"removidos: {', '.join(removed)}")
                try:
                    client.send_message(chat, f"🛡️ Antibot: entrada não autorizada bloqueada ({', '.join('@'+r for r in removed)}).")
                except Exception:
                    pass
            return  # não dá boas-vindas a quem foi removido

    # ----- AUTOROLE: dá um cargo a quem entra -----
    auto = db.get_setting(chat_str, "autorole")
    if auto:
        for member in joined:
            try:
                db.add_role(chat_str, short_jid(Jid2String(member)), auto)
            except Exception:
                pass

    if db.get_setting(chat_str, "welcome") != "1":
        return
    custom = db.get_setting(chat_str, "welcometext")
    for member in joined:
        member_str = Jid2String(member)
        phone = short_jid(member_str)
        if custom:
            caption = custom.replace("@user", f"@{phone}") if "@user" in custom \
                else f"{custom}\n@{phone}"
        else:
            caption = (
                f"{config.DECO_TOP}\n"
                f"💖 *Bem-vindo(a)*, @{phone}! 🎉✨\n"
                f"{config.DECO_LINE}\n"
                f"Seja muito bem-vindo(a) ao grupo! 🥰🌸\n"
                f"Use /help para ver tudo que eu faço 🤖💕\n"
                f"{config.DECO_NAME}"
            )
        try:
            pic = client.get_profile_picture(member)
            if pic and pic.URL:
                import requests
                img = requests.get(pic.URL, timeout=30).content
                client.send_image(chat, img, caption=caption)
            else:
                client.send_message(chat, caption)
        except Exception:
            try:
                client.send_message(chat, caption)
            except Exception:
                pass


def _is_exempt(chat, chat_str, sender_str):
    """Admin ou whitelist => isento da automoderação."""
    if db.is_whitelisted(chat_str, short_jid(sender_str)):
        return True
    return is_group_admin(chat, sender_str)


@client.event(MessageEv)
def on_message(_, message):
    try:
        handle_message(message)
    except Exception as exc:
        import traceback
        print(f"❌ Erro não capturado em handle_message: {exc}")
        traceback.print_exc()


def handle_message(message):
    src = message.Info.MessageSource

    chat = src.Chat
    chat_str = Jid2String(chat)
    sender_str = Jid2String(src.Sender)
    phone = short_jid(sender_str)
    msg_id = message.Info.ID
    text = get_text(message)

    # Mensagens do próprio número: só processa se for um comando (prefixo).
    # Isso permite que o dono use o bot pelo próprio WhatsApp, mas evita
    # que o bot reaja às próprias respostas que envia ou a mensagens comuns.
    if src.IsFromMe:
        try:
            pfx = db.get_prefix(chat_str)
        except Exception:
            pfx = "/"
        if not text.startswith(pfx):
            return

    if text:
        print(f"📩 [{phone}] {text[:80]}")

    # registra a mensagem recente (para /clear) — inclui mídia
    if src.IsGroup and msg_id:
        _recent_msgs[chat_str].append((sender_str, msg_id))

    # rastreio p/ /snipe: guarda texto por id; detecta apagamento (revoke)
    try:
        revoked = message.Message.protocolMessage
        if revoked and int(revoked.type) == 0 and revoked.key.ID:
            # type 0 == REVOKE: recupera o texto que tínhamos guardado
            for mid, snd, txt in _msg_text[chat_str]:
                if mid == revoked.key.ID:
                    _last_deleted[chat_str] = (snd, txt)
                    break
        elif text:
            _msg_text[chat_str].append((msg_id, sender_str, text))
    except Exception:
        pass

    # ----- MUTE: apaga msgs do silenciado + avisa (máx. 3x) -----
    try:
        if src.IsGroup and not src.IsFromMe and "muted" in db.get_roles(chat_str, phone):
            revoke(chat, sender_str, msg_id)
            kc = (chat_str, phone)
            if _mute_warns[kc] < 3:
                _mute_warns[kc] += 1
                client.send_message(chat, f"@{phone} Você foi silenciado ‼️⚠️")
            return
    except Exception:
        pass

    # ----- ANTIMÍDIA: apaga tipos configurados (exceto admin/whitelist) -----
    try:
        kind = _media_kind(message.Message)
        blocked_media = set(filter(None, db.get_setting(chat_str, "antimidia", "").split(",")))
        if (src.IsGroup and not src.IsFromMe and kind in blocked_media
                and not _is_exempt(chat, chat_str, sender_str)):
            revoke(chat, sender_str, msg_id)
            audit(chat, chat_str, sender_str, "ANTIMIDIA", kind)
            client.send_message(chat, f"🚷 @{phone}, mídia do tipo *{kind}* não é permitida.")
            return
    except Exception:
        pass

    # ----- automoderação (antilink / antispam) -----
    if src.IsGroup and not src.IsFromMe and text and not _is_exempt(chat, chat_str, sender_str):
        # ANTILINK
        try:
            if db.get_setting(chat_str, "antilink") == "1" and LINK_RE.search(text):
                revoke(chat, sender_str, msg_id)
                audit(chat, chat_str, sender_str, "ANTILINK", "link removido")
                client.send_message(chat, f"🔗 @{phone}, links não são permitidos aqui! Mensagem removida.")
                return
        except Exception:
            pass
        # ANTIPALAVRÃO
        try:
            if db.get_setting(chat_str, "antipalavrao") == "1" and PROFANITY_RE.search(text):
                revoke(chat, sender_str, msg_id)
                audit(chat, chat_str, sender_str, "ANTIPALAVRAO", "mensagem removida")
                client.send_message(chat, f"🤐 @{phone}, mantenha o respeito no grupo.")
                return
        except Exception:
            pass
        # BLACKLIST configurável por grupo
        try:
            forbidden = db.find_blacklisted(chat_str, text)
            if forbidden:
                revoke(chat, sender_str, msg_id)
                audit(chat, chat_str, sender_str, "BLACKLIST", forbidden)
                client.send_message(chat, f"🚫 @{phone}, sua mensagem contém uma expressão proibida.")
                return
        except Exception:
            pass
        # ANTISPAM (mesma mensagem repetida)
        try:
            if db.get_setting(chat_str, "antispam") == "1":
                last_text, count = _spam_track.get((chat_str, phone), ("", 0))
                if text == last_text:
                    count += 1
                else:
                    count = 1
                _spam_track[(chat_str, phone)] = (text, count)
                if count >= 4:
                    revoke(chat, sender_str, msg_id)
                    db.add_role(chat_str, phone, "muted")
                    _mute_warns[(chat_str, phone)] = 0
                    audit(chat, chat_str, sender_str, "ANTISPAM", "silenciado por flood")
                    client.send_message(chat, f"🚯 @{phone} foi silenciado por *spam* (mensagens repetidas)!")
                    return
        except Exception:
            pass

    # atividade por grupo + XP por mensagem
    try:
        if src.IsGroup:
            db.record_activity(chat_str, phone)
    except Exception:
        pass
    try:
        db.add_xp(sender_str)
    except Exception:
        pass

    # auto-remover banidos
    try:
        if src.IsGroup and not src.IsFromMe and db.is_banned(chat_str, phone):
            client.update_group_participants(chat, [src.Sender], ParticipantChange.REMOVE)
            return
    except Exception:
        pass

    # ----- VISÃO automática: mencionou o bot junto com uma foto/vídeo -----
    # Acontece ANTES do "if not text" porque uma foto pode vir sem legenda.
    try:
        if _media_kind(message.Message) in ("image", "video") and bot_foi_mencionado(message):
            pergunta = text or "Descreva esta imagem em detalhes, em português."
            _analisar_midia(Ctx(message, "analiseia", pergunta), pergunta)
            return
    except Exception as exc:
        # não derruba a mensagem, mas também não engole o motivo em silêncio
        print(f"⚠️ Visão automática falhou: {type(exc).__name__}: {exc}")

    if not text:
        return

    # voltar de AFK
    try:
        if db.get_afk(sender_str):
            db.clear_afk(sender_str)
            client.send_message(chat, f"👋 Bem-vindo de volta, @{phone}! AFK removido.")
    except Exception:
        pass

    # avisar menções AFK
    try:
        for m in get_mentions(message):
            af = db.get_afk(m)
            if af:
                client.send_message(chat, f"💤 @{short_jid(m)} está AFK: {af['reason']}")
    except Exception:
        pass

    prefix = db.get_prefix(chat_str)
    if text.startswith(prefix):
        # ----- MODO MANUTENÇÃO: só admins -----
        if db.get_setting(chat_str, "maintenance") == "1" and src.IsGroup:
            if not _is_exempt(chat, chat_str, sender_str):
                client.send_message(chat, "🛠️ Bot em *modo manutenção*. Apenas administradores podem usá-lo agora.")
                return
        handle_command(message, text)


# ===================== SCHEDULER DE LEMBRETES =====================
def reminder_loop():
    while True:
        try:
            now = int(time.time())
            for r in db.due_reminders(now):
                try:
                    client.send_message(parse_jid(r["chat"]),
                                        f"⏰ @{short_jid(r['jid'])} lembrete: {r['text']}")
                except Exception:
                    pass
                db.mark_reminder_done(r["id"])
        except Exception:
            pass
        time.sleep(15)


def _normalize_number(raw: str) -> str:
    """Mantém apenas dígitos (formato esperado: DDI+DDD+numero, ex: 5511999999999)."""
    return "".join(c for c in (raw or "") if c.isdigit())


def _print_pair_code(code: str):
    print("\n" + "═" * 44)
    print("  🔑 CÓDIGO DE PAREAMENTO ".center(44))
    print("═" * 44)
    pretty = f"{code[:4]}-{code[4:]}" if len(code) == 8 else code
    print(f"\n        👉  {pretty}  👈\n")
    print("  No WhatsApp do celular:")
    print("  Aparelhos conectados > Conectar um aparelho")
    print("  > Conectar com número de telefone > digite o código")
    print("═" * 44 + "\n")


def _run_connect(on_error=None):
    """Roda client.connect() capturando qualquer exceção — sem isso, uma
    falha (sessão corrompida, erro de rede etc.) morre em silêncio dentro da
    thread daemon e a página/terminal fica esperando pra sempre sem dizer
    por quê."""
    try:
        client.connect()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"❌ client.connect() falhou: {exc}")
        if on_error:
            try:
                on_error(str(exc))
            except Exception:
                pass


# ---- Vigia de sobrevivência (self-healing) ---------------------------------
# Problema real que isso resolve: a thread de reconexão (_connect_loop) só
# tinha proteção contra falha DENTRO de client.connect() (via _run_connect).
# Qualquer erro inesperado no resto do laço (ex.: webui.expire_code() falhar)
# matava a thread em silêncio — o processo continuava de pé, o /health
# respondia 200 do mesmo jeito, mas o WhatsApp nunca mais reconectava sozinho.
# A única saída era reiniciar o serviço manualmente. Esse era o comportamento
# que o usuário descrevia como "o Render reiniciar" — na prática o container
# não tinha motivo pra reiniciar sozinho, então o bot ficava morto pra sempre.
_conn_lock = threading.Lock()
_conn_state = {
    "last_ok": time.time(),   # última vez que houve progresso REAL (loop
                               # iterando, QR recebido, ou conectado)
    "loop_thread": None,      # referência viva da thread de reconexão atual
    "restarts": [],           # timestamps em que o vigia teve que reviver
                               # a thread (não conta reconexões normais)
}

# Sem sinal de progresso por tanto tempo E desconectado -> algo travou de
# verdade (não é só "esperando o backoff").
LIMITE_PARADO = 900       # 15 min
# Se o vigia precisar reviver a thread mais vezes que isso dentro da janela,
# não é um erro isolado — é um problema sistêmico que o retry interno não
# resolve sozinho (ex.: alguma exceção que ocorre sempre logo no início).
JANELA_CRASH = 600        # 10 min
MAX_REVIVES_JANELA = 5
# Cadência do vigia. É uma variável de módulo (não uma constante local dentro
# de _supervisor) só para os testes conseguirem reduzi-la e não esperar 30s
# de verdade a cada checagem.
SUPERVISOR_INTERVAL_S = 30


def _mark_alive():
    with _conn_lock:
        _conn_state["last_ok"] = time.time()


def _should_be_healthy(conectado: bool, parado_ha: float, reincidencias: int) -> bool:
    """Decide o resultado do /health. Função pura — fácil de testar sem
    precisar montar threads/timers de verdade.

    _start_connect_loop() e _supervisor() (que dependem desta função) são
    definidos DENTRO de connect_web(), não aqui: eles precisam enxergar
    _connect_loop, que é uma closure aninhada em connect_web — uma função no
    nível de módulo não tem acesso a essa closure."""
    if conectado:
        return True
    if reincidencias > MAX_REVIVES_JANELA:
        return False
    return parado_ha < LIMITE_PARADO


def connect_with_paircode(number: str):
    """Conecta usando código de pareamento (em vez de QR), no terminal.

    Mesma dinâmica do modo web: o canal de QR do whatsmeow expira depois de
    ~2 min e derruba o socket, então pedimos o código no instante em que o
    evento de QR chega (conexão de pé) e reconectamos se expirar antes de
    parear.
    """
    number = _normalize_number(number)
    _wa_ready.clear()
    _paired.clear()
    ja_pedido = threading.Event()

    def _silent_qr(_, __):
        # Suprime o QR no terminal (não faz sentido no modo código), mas usa o
        # evento como sinal de "conexão pronta para PairPhone".
        _wa_ready.set()
        if _paired.is_set() or ja_pedido.is_set():
            return
        ja_pedido.set()

        def _pedir():
            # mesmo orçamento anti-bloqueio do modo web: o WhatsApp limita
            # quantos códigos aceita gerar por número num intervalo curto
            ok, motivo = _pair_budget(consume=True)
            if not ok:
                print(f"🛑 {motivo}")
                return
            print(f"📲 Solicitando código de pareamento para +{number}...")
            code, err = _try_pair_phone(number)
            if code:
                _print_pair_code(code)
            else:
                print(f"❌ Não consegui gerar o código: {err}")
                ja_pedido.clear()  # deixa tentar de novo na próxima conexão

        threading.Thread(target=_pedir, daemon=True).start()

    try:
        client.event.qr(_silent_qr)
    except Exception:
        pass

    # Nunca desiste de vez — só desacelera. Isto roda na thread PRINCIPAL
    # (chamado direto de main()), então qualquer erro que escapasse daqui sem
    # try/except derrubaria o processo inteiro; e um `return` definitivo
    # deixaria o bot morto pra sempre se o dono demorasse pra digitar o
    # código, sem nenhum vigia pra reviver (esse existe só no modo web).
    RECONEXAO_LENTA_APOS = 12
    RECONEXAO_LENTA = 300
    tentativa_pareamento = 0
    tentativa_pos_pareamento = 0
    avisou_lenta = False
    while True:
        try:
            _wa_ready.clear()
            # O código morre junto com a conexão que o emitiu, então a conexão
            # nova precisa poder emitir outro — sem isto só UM código era
            # gerado em toda a execução e o usuário esperava um já inválido.
            ja_pedido.clear()
            _run_connect()
            if not _paired.is_set():
                tentativa_pareamento += 1
                if tentativa_pareamento > RECONEXAO_LENTA_APOS:
                    if not avisou_lenta:
                        avisou_lenta = True
                        print(f"⏳ Sem parear depois de {RECONEXAO_LENTA_APOS} tentativas — "
                              f"passo a tentar a cada {RECONEXAO_LENTA}s (sem desistir).")
                    espera = RECONEXAO_LENTA
                else:
                    espera = min(3 + tentativa_pareamento * 2, 20)
                    print(f"🔄 Canal de QR expirou (socket caiu). Reconectando em {espera}s… "
                          f"[{tentativa_pareamento}/{RECONEXAO_LENTA_APOS}]")
            else:
                tentativa_pos_pareamento += 1
                espera = min(5 + tentativa_pos_pareamento * 2, 30)
                print(f"🔄 Conexão caiu (já pareado). Reconectando em {espera}s…")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"⚠️ connect_with_paircode: erro inesperado (não derruba o processo): {exc}")
            espera = 15
        time.sleep(espera)


# Sinalizado quando o WhatsApp termina o handshake e está pronto para parear.
# O evento QR dispara nesse momento (é o sinal correto, não is_connected).
_wa_ready = threading.Event()


def _wait_wa_ready(timeout: float = 40) -> bool:
    """Aguarda o WhatsApp estar realmente pronto para aceitar PairPhone.

    Usar client.is_connected é insuficiente: ele vira True quando o WebSocket
    abre, mas o servidor ainda precisa concluir um handshake interno ("info
    query"). O evento QR é o sinal correto — ele dispara só após esse
    handshake, indicando que o servidor está esperando autenticação.
    """
    return _wa_ready.wait(timeout=timeout)


# Trechos de mensagem que indicam problema TRANSITÓRIO de socket (o whatsmeow usa
# literalmente "websocket not connected" / "websocket disconnected" internamente) —
# nesses casos vale a pena esperar a reconexão e tentar de novo. Qualquer outra
# PairPhoneError (número inválido, já pareado, rate limit etc.) é definitiva.
_TRANSIENT_MARKERS = ("not connected", "disconnected", "conectad", "timeout", "context deadline")


def _is_transient(msg: str) -> bool:
    m = (msg or "").lower()
    return any(marker in m for marker in _TRANSIENT_MARKERS)


# ---- Orçamento de pedidos de pareamento (anti rate-limit) -----------------
# Cada PairPhone é um pedido REAL ao WhatsApp: gera notificação no celular e
# INVALIDA o código anterior. O WhatsApp limita quantos pedidos aceita por
# número num intervalo curto — estourar isso faz ele simplesmente parar de
# entregar o código, sem erro nenhum (era o sintoma: "a mensagem não chega").
PAIR_MAX_REQUESTS = 3   # pedidos por execução do bot
PAIR_COOLDOWN = 90      # segundos mínimos entre um pedido e o seguinte

_pair_lock = threading.Lock()
_pair_count = [0]
_pair_last_ts = [0.0]


def _pair_budget(consume: bool = False):
    """Diz se podemos pedir outro código agora. (ok, motivo_da_recusa)

    Com consume=True já reserva a vaga — evita duas threads pedirem ao mesmo
    tempo (o formulário da página e a renovação automática do evento de QR).
    """
    with _pair_lock:
        if _pair_count[0] >= PAIR_MAX_REQUESTS:
            return False, (
                f"limite de {PAIR_MAX_REQUESTS} pedidos de código atingido nesta "
                f"execução. O WhatsApp bloqueia números que pedem código demais. "
                f"Use o QR Code acima, ou reinicie o serviço daqui a alguns minutos."
            )
        espera = PAIR_COOLDOWN - (time.time() - _pair_last_ts[0])
        if _pair_last_ts[0] and espera > 0:
            return False, (
                f"aguarde {int(espera)}s antes de pedir outro código "
                f"(evita bloqueio do WhatsApp por excesso de pedidos)."
            )
        if consume:
            _pair_count[0] += 1
            _pair_last_ts[0] = time.time()
            print(f"📊 Pedido de código {_pair_count[0]}/{PAIR_MAX_REQUESTS}.")
        return True, ""


def _try_pair_phone(number: str, attempts: int = 2):
    """Chama PairPhone, devolvendo (código, None) ou (None, erro_real).

    attempts é BAIXO de propósito: cada tentativa é um pedido contabilizado
    pelo WhatsApp. Só repetimos em erro transitório de socket (o handshake
    termina um pouco antes do socket de escrita ficar pronto).
    """
    last_err = "erro desconhecido"
    for i in range(attempts):
        # Espera o socket estar realmente aberto antes de cada tentativa —
        # client.is_connected reflete o estado ATUAL (diferente de _wa_ready,
        # que só sinaliza que o handshake ocorreu uma vez).
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                if client.is_connected:
                    break
            except Exception:
                pass
            time.sleep(0.3)

        try:
            code = client.PairPhone(number, True)
            if code:
                return code, None
        except PairPhoneError as exc:
            msg = str(exc) or "o WhatsApp recusou o pedido"
            last_err = msg
            if not _is_transient(msg):
                print(f"⚠️ PairPhone recusou definitivamente ({i + 1}/{attempts}): {msg}")
                break  # recusa definitiva do servidor: não adianta repetir
            print(f"⚠️ PairPhone com erro transitório ({i + 1}/{attempts}): {msg} — tentando de novo…")
        except Exception as exc:
            last_err = str(exc)
            print(f"⚠️ PairPhone falhou ({i + 1}/{attempts}): {last_err}")
        time.sleep(2)
    return None, last_err


# Número aguardando pareamento. Preenchido quando o usuário envia o formulário
# mas o socket já morreu; o handler de QR consome isso assim que uma conexão
# nova fica de pé.
_pending_number = [None]
# Último número pedido — usado para RENOVAR o código automaticamente a cada
# conexão nova, já que o código do WhatsApp morre junto com o socket que o
# emitiu (~2 min). Sem isso o usuário ficaria olhando um código morto.
_last_number = [None]
# Garante um único pedido de código por conexão (o evento de QR dispara a cada
# ~20s dentro da MESMA conexão; sem esta trava pediríamos código sem parar).
_code_req = threading.Event()
# Sinalizado quando o pareamento conclui — encerra o loop de reconexão.
_paired = threading.Event()


def _publish_pair_code(number: str):
    """Pede o código ao WhatsApp e publica o resultado na página."""
    ok, motivo = _pair_budget(consume=True)
    if not ok:
        print(f"🛑 Pedido de código bloqueado localmente: {motivo}")
        webui.set_error(motivo)
        return
    code, err = _try_pair_phone(number)
    if code:
        pretty = f"{code[:4]}-{code[4:]}" if len(code) == 8 else code
        print(f"🔑 Código de pareamento gerado: {pretty}")
        webui.set_code(pretty)
    else:
        webui.set_error(f"Não consegui gerar o código: {err}")


def _request_pair_code(number: str):
    """Chamado pelo formulário da página web.

    O socket do WhatsApp cai sozinho quando o canal de QR do whatsmeow expira
    (ele emite alguns QRs por ~2 min e fecha o canal; nos logs isso aparece
    como "failed to read frame header: EOF"). Era exatamente por isso que dava
    "websocket not connected" quando o usuário demorava para digitar o número.

    Em vez de tentar parear num socket morto, enfileiramos o número: o loop de
    reconexão levanta uma conexão nova e o handler de QR dispara o pareamento
    assim que ela estiver de pé — que é o momento em que o whatsmeow aceita
    PairPhone.
    """
    number = _normalize_number(number)
    if not number:
        webui.set_error("Número inválido. Use DDI+DDD+número, só dígitos (ex: 5511999999999).")
        return

    # guarda o número para renovar o código sozinho a cada conexão nova
    _last_number[0] = number

    socket_vivo = _wa_ready.is_set()
    if socket_vivo:
        try:
            socket_vivo = client.is_connected
        except Exception:
            socket_vivo = False

    if socket_vivo:
        _code_req.set()
        _publish_pair_code(number)
        return

    _pending_number[0] = number
    print(f"⏳ Socket caído — número +{number} enfileirado para a próxima conexão.")
    webui.set_error(
        "Reconectando ao WhatsApp para gerar seu código… leva alguns segundos, "
        "a página atualiza sozinha."
    )


def _cancel_pair_request():
    """Chamado pelo botão 'Revogar código' da página.

    Além de limpar a tela, para a renovação automática — senão o próximo QR
    geraria um código novo logo depois do usuário ter cancelado.
    """
    _pending_number[0] = None
    _last_number[0] = None
    _code_req.set()  # impede novo pedido nesta conexão
    print("🚫 Código revogado pelo usuário — renovação automática desligada.")


def connect_web(number: str = ""):
    """Conecta expondo QR e código de pareamento numa página web (Render/VPS
    sem terminal interativo). Não bloqueia esperando input — quem decide se
    escaneia o QR ou digita o número é quem abrir a página no navegador.

    O whatsmeow renova o QR Code sozinho dentro da MESMA chamada de connect()
    — mas só por um tempo: depois de emitir alguns QRs (~2 min) ele encerra o
    canal, connect() RETORNA e o socket morre. Por isso existe o loop de
    reconexão abaixo: quando connect() retorna sem ter pareado, subimos uma
    conexão nova. Isso é seguro porque só acontece DEPOIS que a conexão morreu
    sozinha (bem diferente de matar uma conexão viva à força).
    """
    _wa_ready.clear()
    _paired.clear()
    _code_req.clear()

    # Número já conhecido (PHONE_NUMBER): enfileira ANTES de subir a conexão,
    # senão o primeiro evento de QR chega antes da atribuição e o pedido só
    # aconteceria no ciclo seguinte (~2 min depois).
    number = _normalize_number(number)
    if number:
        _pending_number[0] = number
        _last_number[0] = number
        print(f"📲 PHONE_NUMBER definido — vou gerar o código para +{number} automaticamente.")

    port = int(os.getenv("PORT", "8080"))
    webui.start(config.BOT_NAME, port, _request_pair_code, _cancel_pair_request)
    print(f"🌐 Página de pareamento em: http://0.0.0.0:{port}  (abra no navegador e escaneie o QR ou digite seu número)")

    def _on_qr(_, qr_data):
        # Disparado pelo whatsmeow a cada renovação do QR. Quando chega, a
        # conexão está de pé e o PairPhone é aceito.
        _wa_ready.set()
        _mark_alive()
        try:
            text = qr_data.decode() if isinstance(qr_data, (bytes, bytearray)) else str(qr_data)
            webui.set_qr(services.qr_png(text))
            print("📷 QR Code atualizado.")
        except Exception as exc:
            webui.set_error(f"Erro ao gerar QR: {exc}")

        # Conexão de pé: pede o código no máximo UMA vez por conexão.
        # Um pedido enfileirado (usuário mandou o número com o socket morto)
        # tem prioridade e sempre vale. Já a RENOVAÇÃO automática só acontece
        # se o código anterior realmente morreu — pedir um código novo com um
        # ainda válido na tela invalidaria justamente o que o usuário está
        # digitando, além de queimar o orçamento anti-bloqueio à toa.
        pedido = _pending_number[0]
        alvo = pedido or _last_number[0]
        if not alvo or _paired.is_set() or _code_req.is_set():
            return
        if not pedido and webui.code_is_live():
            return  # ainda há código válido na tela: não mexe
        if not _pair_budget()[0]:
            return  # sem orçamento: deixa o usuário usar o QR

        _code_req.set()
        _pending_number[0] = None
        _last_number[0] = alvo
        print(f"▶️ Conexão de pé — gerando código para +{alvo}.")
        threading.Thread(target=_publish_pair_code, args=(alvo,), daemon=True).start()

    try:
        client.event.qr(_on_qr)
    except Exception:
        pass

    def _on_connect_error(msg):
        webui.set_disconnected(
            f"Falha ao conectar ao WhatsApp: {msg}. Se isso persistir, apague o "
            f"arquivo de sessão salvo (SESSION_DB) e tente parear de novo do zero."
        )

    def _connect_loop():
        """Mantém a conexão WhatsApp viva PARA SEMPRE — nunca desiste.

        Fase 1 (antes de parear): reconecta com backoff crescente até um teto
        de RECONEXAO_LENTA_APOS tentativas; depois disso continua tentando,
        só que numa cadência bem mais espaçada (RECONEXAO_LENTA), pra não
        martelar o WhatsApp enquanto ninguém está olhando a página. Antes essa
        fase desistia de vez (`return`) depois de 12 tentativas — se o dono
        demorasse mais que ~10 minutos pra escanear/digitar o código, o bot
        morria e só voltava reiniciando o serviço à mão.

        Fase 2 (depois de parear / sessão existente): se o socket cair,
        reconecta automaticamente com backoff, sem precisar reparear.

        Todo o corpo do laço fica dentro de um try/except: um erro inesperado
        aqui (não só dentro de client.connect(), que _run_connect já protege)
        não pode matar esta thread — sem reconexão nunca mais aconteceria de
        novo sozinha. Se mesmo assim a thread morrer, o vigia (_supervisor) a
        revive.
        """
        RECONEXAO_LENTA_APOS = 12   # ~10 min de tentativas rápidas
        RECONEXAO_LENTA = 300       # depois disso, 1 tentativa a cada 5 min
        tentativa_pareamento = 0    # só cresce ANTES de parear pela 1ª vez
        tentativa_pos_pareamento = 0  # só cresce DEPOIS de já ter pareado
        avisou_lenta = False
        while True:
            try:
                _mark_alive()
                _wa_ready.clear()
                _code_req.clear()  # a conexão nova pode emitir um código novo
                _run_connect(_on_connect_error)

                if not _paired.is_set():
                    # A conexão morreu: o código emitido por ela morreu junto.
                    # Tira da tela pro usuário não digitar um código já
                    # inválido — o próximo QR gera outro automaticamente.
                    webui.expire_code()
                    tentativa_pareamento += 1
                    if tentativa_pareamento > RECONEXAO_LENTA_APOS:
                        if not avisou_lenta:
                            avisou_lenta = True
                            print(f"⏳ Sem parear depois de {RECONEXAO_LENTA_APOS} tentativas — "
                                  f"passo a tentar a cada {RECONEXAO_LENTA}s (sem desistir).")
                            webui.set_disconnected(
                                "Ainda não pareado depois de várias tentativas. Continuo "
                                "tentando em segundo plano — escaneie o QR ou peça o código "
                                "quando quiser."
                            )
                        espera = RECONEXAO_LENTA
                    else:
                        espera = min(5 + tentativa_pareamento * 5, 60)
                        print(f"🔄 Canal de QR expirou (socket caiu). Reconectando em {espera}s… "
                              f"[{tentativa_pareamento}/{RECONEXAO_LENTA_APOS}]")
                else:
                    # já estava pareado — reconecta automaticamente, sempre.
                    # O backoff cresce a cada queda e satura em 30s (não some:
                    # fica ali como cadência estável enquanto a rede insistir
                    # em cair, sem martelar o WhatsApp).
                    tentativa_pos_pareamento += 1
                    espera = min(5 + tentativa_pos_pareamento * 2, 30)
                    print(f"🔄 Conexão caiu (já pareado). Reconectando em {espera}s…")
            except Exception as exc:
                import traceback
                traceback.print_exc()
                print(f"⚠️ _connect_loop: erro inesperado (não derruba a thread): {exc}")
                espera = 15
            time.sleep(espera)

    def _start_connect_loop():
        """Sobe a thread que mantém a conexão com o WhatsApp viva.

        Usada tanto no arranque quanto pelo vigia (_supervisor, logo abaixo)
        para reviver, caso a thread anterior tenha morrido de forma
        inesperada. Aninhada aqui dentro (não no nível do módulo) porque
        precisa enxergar _connect_loop, que é uma closure local a esta
        chamada de connect_web().
        """
        t = threading.Thread(target=_connect_loop, daemon=True)
        with _conn_lock:
            _conn_state["loop_thread"] = t
        t.start()
        return t

    def _supervisor():
        """Roda em paralelo à thread de reconexão, vigiando se ela segue viva.

        Duas camadas de defesa, da mais rápida/barata pra mais lenta/cara:
          1. Reviver a thread em processo, sem precisar de restart do
             container (cobre o caso comum: um erro isolado matou a thread).
          2. Se isso não bastar (crash-loop: a thread morre de novo repetidas
             vezes), falha o /health — aí o Render reinicia o container
             inteiro (healthCheckPath já configurado no render.yaml), último
             recurso para problemas que o retry interno não resolve.
        """
        while True:
            time.sleep(SUPERVISOR_INTERVAL_S)
            try:
                with _conn_lock:
                    t = _conn_state["loop_thread"]
                morta = t is None or not t.is_alive()

                # Poda os revives velhos em TODA volta, não só quando a thread
                # acaba de morrer de novo. Sem isso, um surto passageiro (a
                # thread morre 6x numa rede instável e depois se estabiliza)
                # deixava reincidencias travado acima do limite pra sempre —
                # a poda só rodava dentro do `if morta`, que nunca mais era
                # verdadeiro depois que a thread parava de cair. O vigia
                # ficaria acusando crash-loop e derrubando o /health por um
                # problema que já tinha se resolvido sozinho minutos antes.
                agora = time.time()
                with _conn_lock:
                    _conn_state["restarts"] = [
                        ts for ts in _conn_state["restarts"] if agora - ts < JANELA_CRASH
                    ]
                    if morta:
                        _conn_state["restarts"].append(agora)
                    reincidencias = len(_conn_state["restarts"])

                if morta:
                    print(f"🛡️ Vigia: thread de reconexão morreu (revive nº {reincidencias} "
                          f"nos últimos {JANELA_CRASH // 60} min) — reiniciando.")
                    _start_connect_loop()

                conectado = False
                try:
                    with webui._lock:
                        conectado = bool(webui._state["connected"])
                except Exception:
                    pass
                with _conn_lock:
                    parado_ha = time.time() - _conn_state["last_ok"]

                saudavel = _should_be_healthy(conectado, parado_ha, reincidencias)
                webui.set_health(saudavel)
                if not saudavel:
                    print(f"🚨 Vigia: sem progresso há {int(parado_ha)}s "
                          f"(revives={reincidencias}/{JANELA_CRASH // 60}min) — "
                          f"pedindo restart do container via /health.")
            except Exception as exc:
                # o vigia nunca pode morrer — sem ele, ninguém revive nada.
                print(f"⚠️ Vigia: erro interno (ignorado, tenta de novo em 30s): {exc}")

    _start_connect_loop()
    threading.Thread(target=_supervisor, daemon=True).start()

    def _startup_watchdog():
        # Se depois de um tempo razoável nada apareceu (nem QR, nem erro, nem
        # conectado), avisa na página em vez de deixar o spinner girando pra
        # sempre sem explicação — ajuda a diagnosticar sessão corrompida,
        # rede bloqueada etc.
        time.sleep(30)
        with webui._lock:
            stuck = (
                not webui._state["qr_png"]
                and not webui._state["code"]
                and not webui._state["error"]
                and not webui._state["connected"]
            )
        if stuck:
            print("⚠️ 30s sem QR/código/erro — conexão pode estar travada.")
            webui.set_disconnected(
                "Demorando demais pra conectar ao WhatsApp (30s+ sem resposta). "
                "Verifique os logs do servidor. Se persistir, pode ser sessão "
                "corrompida — apague o arquivo SESSION_DB salvo e reinicie."
            )

    threading.Thread(target=_startup_watchdog, daemon=True).start()

    # Mantém o processo principal vivo. Não dá join() numa thread específica
    # porque o vigia (_supervisor) pode SUBSTITUIR a thread de reconexão se
    # ela morrer — todas as threads daqui são daemon, então dar join numa
    # referência antiga faria o processo inteiro encerrar assim que aquela
    # thread em particular morresse, mesmo com uma nova já rodando no lugar.
    while True:
        time.sleep(3600)



def main():
    db.init()
    if not config.OPENROUTER_API_KEY:
        print("⚠️  OPENROUTER_API_KEY não definida — o comando /IA ficará indisponível.")
    threading.Thread(target=reminder_loop, daemon=True).start()

    # método de login: código, QR (terminal) ou web (página com QR + código)
    method = os.getenv("LOGIN_METHOD", "").strip().lower()
    number = config.PHONE_NUMBER

    # sem terminal interativo (Render, VPS headless etc.) → força o método web
    if not method and not sys.stdin.isatty():
        method = "web"

    if not method:
        print(f"\n🚀 {config.BOT_NAME} — como deseja conectar?")
        print("  [1] Código de pareamento (digitar o número)  ← recomendado")
        print("  [2] QR Code")
        print("  [3] Página web (QR + código, útil em servidores)")
        try:
            choice = input("Escolha [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "1"
        method = {"2": "qr", "3": "web"}.get(choice, "code")

    if method == "web":
        connect_web(number)
    elif method.startswith("q"):
        print("📷 Gerando QR Code... escaneie com o WhatsApp.")
        while True:
            _run_connect()
            espera = 5
            print(f"🔄 Conexão caiu. Reconectando em {espera}s…")
            time.sleep(espera)
    else:
        if not number:
            try:
                number = input("📱 Digite seu número com DDI e DDD (ex: 5511999999999): ").strip()
            except (EOFError, KeyboardInterrupt):
                number = ""
        if not _normalize_number(number):
            print("❌ Número inválido. Reinicie e informe o número com DDI+DDD.")
            return
        connect_with_paircode(number)


if __name__ == "__main__":
    main()
