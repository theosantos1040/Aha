"""Lógica pura dos comandos da v3.1 PRO (testável offline, sem WhatsApp/rede).

Tudo aqui é determinístico ou usa random — funções puras que recebem entrada
e devolvem texto/estruturas. Os handlers em bot.py só fazem a ponte com o chat.
"""
import random
import secrets
import string

# ──────────────────────────── UTILITÁRIOS ────────────────────────────

def gen_password(length: int = 16, symbols: bool = True) -> str:
    length = max(4, min(length, 64))
    alpha = string.ascii_letters + string.digits
    if symbols:
        alpha += "!@#$%&*?-_+="
    return "".join(secrets.choice(alpha) for _ in range(length))


_EMOJI_MAP = {c: chr(0x1F1E6 + ord(c) - 97) for c in string.ascii_lowercase}


def emojify(text: str) -> str:
    """Transforma letras em emojis regionais e números em emojis de teclado."""
    nums = {str(i): e for i, e in enumerate(
        ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"])}
    out = []
    for ch in text.lower():
        if ch in _EMOJI_MAP:
            out.append(_EMOJI_MAP[ch] + " ")
        elif ch in nums:
            out.append(nums[ch] + " ")
        else:
            out.append(ch)
    return "".join(out).strip()


def reverse_text(text: str) -> str:
    return text[::-1]


def choose(options: list) -> str:
    return random.choice(options) if options else ""


# conversões de unidade simples: base por categoria
_CONV = {
    "km": ("dist", 1000.0), "m": ("dist", 1.0), "cm": ("dist", 0.01),
    "mi": ("dist", 1609.34), "ft": ("dist", 0.3048), "in": ("dist", 0.0254),
    "kg": ("mass", 1000.0), "g": ("mass", 1.0), "mg": ("mass", 0.001),
    "lb": ("mass", 453.592), "oz": ("mass", 28.3495),
    "l": ("vol", 1.0), "ml": ("vol", 0.001), "gal": ("vol", 3.78541),
}


def convert_units(value: float, frm: str, to: str):
    frm, to = frm.lower(), to.lower()
    # temperatura é caso à parte
    temp = {"c", "f", "k"}
    if frm in temp and to in temp:
        c = value if frm == "c" else (value - 32) * 5 / 9 if frm == "f" else value - 273.15
        if to == "c":
            return c
        if to == "f":
            return c * 9 / 5 + 32
        return c + 273.15
    if frm not in _CONV or to not in _CONV:
        raise ValueError("unidade desconhecida")
    if _CONV[frm][0] != _CONV[to][0]:
        raise ValueError("categorias incompatíveis")
    return value * _CONV[frm][1] / _CONV[to][1]


# ──────────────────────────── CASSINO / SORTE ────────────────────────────

def slot_spin():
    reels = ["🍒", "🍋", "🍇", "🔔", "⭐", "7️⃣", "💎"]
    r = [random.choice(reels) for _ in range(3)]
    if r[0] == r[1] == r[2]:
        mult = 10 if r[0] in ("7️⃣", "💎") else 5
    elif r[0] == r[1] or r[1] == r[2]:
        mult = 2
    else:
        mult = 0
    return r, mult


def blackjack_round():
    """Mão simplificada: jogador e dealer recebem cartas; vence quem chega mais perto de 21."""
    def draw():
        return random.randint(1, 11)
    def hand():
        h = [draw(), draw()]
        while sum(h) < 17:
            h.append(draw())
        return h
    p, d = hand(), hand()
    ps, ds = sum(p), sum(d)
    if ps > 21 and ds > 21:
        res = "Empate (ambos estouraram)"
    elif ps > 21:
        res = "Dealer venceu 🃏"
    elif ds > 21:
        res = "Você venceu! 🎉"
    elif ps > ds:
        res = "Você venceu! 🎉"
    elif ds > ps:
        res = "Dealer venceu 🃏"
    else:
        res = "Empate"
    return p, d, res


def roulette_spin(bet: str):
    n = random.randint(0, 36)
    color = "🟢" if n == 0 else ("🔴" if n % 2 else "⚫")
    bet = bet.lower().strip()
    win = False
    if bet in ("vermelho", "red") and color == "🔴":
        win = True
    elif bet in ("preto", "black") and color == "⚫":
        win = True
    elif bet.isdigit() and int(bet) == n:
        win = True
    elif bet in ("par",) and n != 0 and n % 2 == 0:
        win = True
    elif bet in ("impar", "ímpar") and n % 2:
        win = True
    return n, color, win


def crash_game():
    """Multiplicador de cassino tipo 'crash': onde ele estourou."""
    point = round(1 + random.expovariate(1.0), 2)
    return max(1.0, point)


def higher_lower(prev: int):
    nxt = random.randint(1, 100)
    return nxt


# ──────────────────────────── RPG / AVENTURA ────────────────────────────

def battle(name_a: str, name_b: str):
    ha = hb = 100
    log = []
    turn = 0
    while ha > 0 and hb > 0 and turn < 12:
        dmg = random.randint(8, 22)
        if turn % 2 == 0:
            hb -= dmg
            log.append(f"⚔️ {name_a} causa {dmg} de dano ({name_b}: {max(hb,0)} HP)")
        else:
            ha -= dmg
            log.append(f"🛡️ {name_b} causa {dmg} de dano ({name_a}: {max(ha,0)} HP)")
        turn += 1
    winner = name_a if ha > hb else name_b
    return log, winner


def boss_fight(player: str):
    boss_hp = random.randint(120, 200)
    dmg = random.randint(80, 220)
    if dmg >= boss_hp:
        return True, boss_hp, dmg
    return False, boss_hp, dmg


def loot():
    items = [
        ("🗡️ Espada Lendária", "raro"), ("🪙 Saco de Moedas", "comum"),
        ("💎 Diamante", "épico"), ("🧪 Poção de Vida", "comum"),
        ("👑 Coroa Real", "lendário"), ("🛡️ Escudo de Ferro", "incomum"),
        ("🏹 Arco Élfico", "raro"), ("📜 Pergaminho Mágico", "incomum"),
        ("🪨 Pedra Comum", "lixo"), ("🔮 Orbe do Poder", "épico"),
    ]
    return random.choice(items)


def gather(activity: str):
    """Pescaria/mineração/caça: devolve (item, quantidade/valor)."""
    tables = {
        "fishing": [("🐟 Peixe", 10), ("🐠 Peixe tropical", 25), ("🦈 Tubarão", 100),
                    ("🥾 Bota velha", 0), ("🦀 Caranguejo", 15), ("🐙 Polvo", 40)],
        "mining": [("🪨 Pedra", 2), ("⛏️ Ferro", 20), ("🥇 Ouro", 60),
                   ("💎 Diamante", 150), ("💀 Caverna vazia", 0)],
        "hunt": [("🐰 Coelho", 12), ("🦌 Cervo", 45), ("🐗 Javali", 70),
                 ("🐻 Urso", 120), ("🍃 Nada encontrado", 0)],
    }
    return random.choice(tables.get(activity, tables["fishing"]))


def dungeon_step():
    events = [
        "🚪 Você abre uma porta e encontra um baú com 💰 50 moedas!",
        "👹 Um goblin aparece! Você o derrota com facilidade. (+30 XP)",
        "🕳️ Uma armadilha! Você perde 10 de vida.",
        "🧙 Um mago misterioso te dá uma poção. 🧪",
        "💀 Você encontra uma sala vazia e assustadora...",
        "🗝️ Achou uma chave dourada! Pode abrir a próxima sala.",
        "🐉 Um dragão dorme à frente. Melhor passar quietinho... 🤫",
    ]
    return random.choice(events)


def tower_climb():
    floor = random.randint(1, 100)
    reward = floor * random.randint(5, 15)
    return floor, reward


# ──────────────────────────── JOGOS SOCIAIS ────────────────────────────

WOULD_YOU_RATHER = [
    "Você prefere voar 🦅 ou ser invisível 👻?",
    "Você prefere viver sem internet 📵 ou sem música 🎵?",
    "Você prefere ter dinheiro infinito 💰 ou tempo infinito ⏳?",
    "Você prefere ler mentes 🧠 ou prever o futuro 🔮?",
    "Você prefere comer só doce 🍬 ou só salgado 🍟 pra sempre?",
    "Você prefere viajar no tempo pro passado ⏪ ou pro futuro ⏩?",
    "Você prefere falar todos os idiomas 🗣️ ou tocar todos os instrumentos 🎸?",
]

NEVER_HAVE_I_EVER = [
    "Eu nunca... fingi estar doente pra faltar 😷",
    "Eu nunca... mandei mensagem pra pessoa errada 📱",
    "Eu nunca... dormi numa aula ou reunião 😴",
    "Eu nunca... cantei no banho bem alto 🚿🎤",
    "Eu nunca... comi algo que caiu no chão 🍕",
    "Eu nunca... esqueci o nome de alguém na hora 🤔",
]

TRUTHS = [
    "Qual foi a coisa mais embaraçosa que já te aconteceu? 😳",
    "Quem foi seu primeiro amor? 💘",
    "Qual seu maior medo? 😨",
    "Qual a maior mentira que você já contou? 🤥",
    "O que você mais esconde dos seus pais? 🙈",
]

DARES = [
    "Mande um áudio cantando seu refrão favorito 🎤",
    "Troque sua foto de perfil por um emoji por 1 hora 😄",
    "Mande a última foto da sua galeria 📸",
    "Escreva uma mensagem só com emojis pros próximos 5 min 🤪",
    "Conte uma piada bem ruim 😂",
]


def random_from(lst):
    return random.choice(lst)


# ──────────────────────────── ADIVINHAÇÕES ────────────────────────────

FLAGS = [
    ("🇧🇷", "brasil"), ("🇵🇹", "portugal"), ("🇺🇸", "estados unidos"),
    ("🇯🇵", "japao"), ("🇫🇷", "franca"), ("🇩🇪", "alemanha"),
    ("🇮🇹", "italia"), ("🇪🇸", "espanha"), ("🇦🇷", "argentina"),
    ("🇨🇦", "canada"), ("🇬🇧", "reino unido"), ("🇲🇽", "mexico"),
]

POKEMON = [
    ("Um rato elétrico amarelo com bochechas vermelhas ⚡", "pikachu"),
    ("Uma planta-dinossauro com um bulbo nas costas 🌱", "bulbasaur"),
    ("Um lagarto laranja com chama no rabo 🔥", "charmander"),
    ("Uma tartaruga azul que atira água 💧", "squirtle"),
    ("Um pássaro de fogo lendário 🔥🦅", "moltres"),
]

ANIME = [
    ("Ninja loiro que quer ser Hokage 🍥", "naruto"),
    ("Caçador de demônios com irmã transformada ⚔️", "demon slayer"),
    ("Garoto que vira herói e grita 'Plus Ultra' 💪", "my hero academia"),
    ("Piratas em busca do One Piece 🏴‍☠️", "one piece"),
    ("Titãs gigantes e muralhas 🧱", "attack on titan"),
]

FACTS = [
    "🐙 Polvos têm três corações e sangue azul!",
    "🍯 O mel nunca estraga — acharam mel comestível de 3000 anos.",
    "🦒 A girafa tem o mesmo número de vértebras no pescoço que o humano: 7.",
    "🌕 Um dia em Vênus é mais longo que um ano em Vênus.",
    "🧠 Seu cérebro gera eletricidade suficiente pra acender uma lâmpada pequena.",
    "🐝 As abelhas conseguem reconhecer rostos humanos.",
    "🦈 Tubarões existem há mais tempo que as árvores.",
]

QUOTES = [
    "“A persistência realiza o impossível.” — Provérbio chinês",
    "“Seja a mudança que você quer ver no mundo.” — Gandhi",
    "“O sucesso é ir de fracasso em fracasso sem perder o entusiasmo.” — Churchill",
    "“Feito é melhor que perfeito.”",
    "“Grandes jornadas começam com um único passo.” — Lao Tzu",
]


def guess_new(kind: str):
    """Devolve (dica, resposta) para um jogo de adivinhação."""
    table = {"flag": FLAGS, "pokemon": POKEMON, "anime": ANIME}.get(kind, FLAGS)
    return random.choice(table)


def math_challenge():
    a, b = random.randint(2, 30), random.randint(2, 30)
    op = random.choice(["+", "-", "*"])
    expr = f"{a} {op} {b}"
    ans = {"+": a + b, "-": a - b, "*": a * b}[op]
    return expr, ans


def random_number(lo: int = 1, hi: int = 100) -> int:
    if lo > hi:
        lo, hi = hi, lo
    return random.randint(lo, hi)
