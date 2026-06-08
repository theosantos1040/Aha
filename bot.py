"""ThzyxBoTS - Bot de WhatsApp com IA (OpenRouter) e +40 comandos.

Transporte: neonize (binding do whatsmeow, WhatsApp multidevice).
Execute com:  python run.py   (escaneie o QR com o WhatsApp)
"""
import random
import threading
import time

from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.enum import ParticipantChange, VoteType
from neonize.utils.jid import build_jid, Jid2String

import config
import database as db
import games
import services
import tiktok
import utils
from ai import AIError, chat as ai_chat

START_TIME = time.time()
client = NewClient(config.SESSION_DB)

# estado em memória para jogos interativos por chat
_active_games = {}  # chat_str -> dict


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
    try:
        return list(message.Message.extendedTextMessage.contextInfo.mentionedJID)
    except Exception:
        return []


def parse_jid(jid_str: str):
    user, _, server = jid_str.partition("@")
    return build_jid(user, server or "s.whatsapp.net")


def short_jid(jid_str: str) -> str:
    return jid_str.split("@")[0].split(":")[0]


def is_group_admin(chat, sender_str: str) -> bool:
    try:
        info = client.get_group_info(chat)
    except Exception:
        return False
    for p in info.Participants:
        if Jid2String(p.JID).split("@")[0] == short_jid(sender_str):
            return p.IsAdmin or p.IsSuperAdmin
    return short_jid(sender_str) in config.OWNERS


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
        ctx.reply(f"❌ Erro ao enviar o vídeo: {exc}")


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
        return ctx.reply("Uso: /mute @usuario [tempo]")
    db.add_role(ctx.chat_str, short_jid(target), "muted")
    ctx.reply(
        f"🔇 @{short_jid(target)} foi silenciado pelo bot.\n"
        "_Obs.: o WhatsApp não permite impedir o envio por terceiros; o bot registra "
        "o silenciamento. Use /lock para travar o grupo só para admins._"
    )


def cmd_unmute(ctx):
    if not ctx.require_admin():
        return
    target = ctx.target_jid_str()
    if not target:
        return ctx.reply("Uso: /unmute @usuario")
    db.remove_role(ctx.chat_str, short_jid(target), "muted")
    ctx.reply(f"🔊 @{short_jid(target)} foi dessilenciado.")


def cmd_clear(ctx):
    if not ctx.require_admin():
        return
    ctx.reply(
        "🧹 O WhatsApp só permite apagar mensagens *do próprio bot*. "
        "Mensagens de outros membros não podem ser apagadas via API. "
        "Para limpar o histórico, use a função nativa do app."
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


# ===================== GERAIS / UTILITÁRIOS =====================
def cmd_ia(ctx):
    if not ctx.args:
        modelos = ", ".join(config.AI_MODELS.keys())
        return ctx.reply(
            f"🤖 *{config.BOT_NAME}*\nUso: /IA <pergunta>\n"
            f"Escolher modelo: /IA [{modelos}] <pergunta>\n"
            f"Ex.: /IA chatgpt Quem descobriu o Brasil?"
        )
    parts = ctx.args.split(maxsplit=1)
    model_key = config.DEFAULT_AI_MODEL
    prompt = ctx.args
    if parts[0].lower() in config.AI_MODELS and len(parts) > 1:
        model_key = parts[0].lower()
        prompt = parts[1]
    try:
        client.send_chat_presence(ctx.chat, 0, 0)  # "digitando"
    except Exception:
        pass
    try:
        answer = ai_chat(prompt, model_key)
        ctx.reply(f"🤖 *{config.BOT_NAME}* ({model_key}):\n\n{answer}")
    except AIError as exc:
        ctx.reply(f"❌ IA indisponível: {exc}")


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
        f"📜 *{config.BOT_NAME} — Menu Completo de Comandos*\n\n"
        f"👮 *Administração (17)*\n"
        f"└─ {p}ban {p}unban {p}kick {p}mute {p}unmute {p}warn {p}checkwarns\n"
        f"   {p}lock {p}unlock {p}announce {p}clear {p}nuke {p}ttkvd\n"
        f"   {p}setprefix {p}addrole {p}removerole {p}slowmode\n\n"
        f"🛠️ *Gerais & Utilitários (21)*\n"
        f"└─ {p}IA {p}ping {p}help {p}userinfo {p}serverinfo {p}avatar\n"
        f"   {p}calc {p}weather {p}translate {p}remind {p}poll {p}afk\n"
        f"   {p}invite {p}uptime {p}report {p}suggest {p}level\n"
        f"   {p}leaderboard {p}daily {p}balance {p}pay\n\n"
        f"🎮 *Jogos & Brincadeiras (10)*\n"
        f"└─ {p}coinflip {p}jokenpo {p}8ball {p}roll {p}tictactoe\n"
        f"   {p}trivia {p}hangman {p}akinator {p}russianroulette {p}ship\n\n"
        f"*IA: /IA [chatgpt|nex|glm] <pergunta>*\n"
        f"_Prefixo: {p} | Modelos: {', '.join(config.AI_MODELS)}_"
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


# ===================== ROTEADOR =====================
COMMANDS = {
    "ttkvd": cmd_ttkvd, "ban": cmd_ban, "unban": cmd_unban, "kick": cmd_kick, "mute": cmd_mute,
    "unmute": cmd_unmute, "clear": cmd_clear, "lock": cmd_lock, "unlock": cmd_unlock,
    "warn": cmd_warn, "checkwarns": cmd_checkwarns, "setprefix": cmd_setprefix,
    "addrole": cmd_addrole, "removerole": cmd_removerole, "slowmode": cmd_slowmode,
    "announce": cmd_announce, "nuke": cmd_nuke,
    "ia": cmd_ia, "ping": cmd_ping, "help": cmd_help, "userinfo": cmd_userinfo,
    "serverinfo": cmd_serverinfo, "avatar": cmd_avatar, "calc": cmd_calc,
    "weather": cmd_weather, "translate": cmd_translate, "remind": cmd_remind,
    "poll": cmd_poll, "afk": cmd_afk, "invite": cmd_invite, "uptime": cmd_uptime,
    "report": cmd_report, "suggest": cmd_suggest, "level": cmd_level,
    "leaderboard": cmd_leaderboard, "daily": cmd_daily, "balance": cmd_balance, "pay": cmd_pay,
    "coinflip": cmd_coinflip, "jokenpo": cmd_jokenpo, "8ball": cmd_8ball, "roll": cmd_roll,
    "tictactoe": cmd_tictactoe, "trivia": cmd_trivia, "hangman": cmd_hangman,
    "akinator": cmd_akinator, "russianroulette": cmd_russianroulette, "ship": cmd_ship,
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
    ctx = Ctx(message, command, args)
    try:
        handler(ctx)
    except Exception as exc:  # nunca derrubar o listener
        try:
            ctx.reply(f"⚠️ Erro ao executar /{command}: {exc}")
        except Exception:
            pass


# ===================== EVENTOS =====================
@client.event(ConnectedEv)
def on_connected(_, __):
    print(f"✅ {config.BOT_NAME} conectado ao WhatsApp!")


@client.event(PairStatusEv)
def on_pair(_, message):
    print(f"🔗 Pareado como: {message.ID.User}")


@client.event(MessageEv)
def on_message(_, message):
    src = message.Info.MessageSource
    if src.IsFromMe:
        return
    text = get_text(message)
    if not text:
        return

    chat_str = Jid2String(src.Chat)
    sender_str = Jid2String(src.Sender)

    # XP por mensagem
    try:
        db.add_xp(sender_str)
    except Exception:
        pass

    # auto-remover banidos
    try:
        if src.IsGroup and db.is_banned(chat_str, short_jid(sender_str)):
            client.update_group_participants(src.Chat, [src.Sender], ParticipantChange.REMOVE)
            return
    except Exception:
        pass

    # voltar de AFK
    try:
        if db.get_afk(sender_str):
            db.clear_afk(sender_str)
            client.send_message(src.Chat, f"👋 Bem-vindo de volta, @{short_jid(sender_str)}! AFK removido.")
    except Exception:
        pass

    # avisar menções AFK
    try:
        for m in get_mentions(message):
            af = db.get_afk(m)
            if af:
                client.send_message(src.Chat, f"💤 @{short_jid(m)} está AFK: {af['reason']}")
    except Exception:
        pass

    prefix = db.get_prefix(chat_str)
    if text.startswith(prefix):
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


def main():
    db.init()
    if not config.OPENROUTER_API_KEY:
        print("⚠️  OPENROUTER_API_KEY não definida — o comando /IA ficará indisponível.")
    threading.Thread(target=reminder_loop, daemon=True).start()
    print(f"🚀 Iniciando {config.BOT_NAME}... escaneie o QR code que vai aparecer.")
    client.connect()


if __name__ == "__main__":
    main()
