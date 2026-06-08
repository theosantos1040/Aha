"""Lógica pura dos jogos e brincadeiras (testável)."""
import hashlib
import random

# ---------- coinflip ----------
def coinflip() -> str:
    return random.choice(["🪙 Cara", "🪙 Coroa"])


# ---------- jokenpo (pedra, papel, tesoura) ----------
_JKP = {"pedra": "✊", "papel": "✋", "tesoura": "✌️"}
_BEATS = {"pedra": "tesoura", "papel": "pedra", "tesoura": "papel"}


def jokenpo(player_choice: str):
    player_choice = player_choice.strip().lower()
    if player_choice not in _JKP:
        return None
    bot_choice = random.choice(list(_JKP))
    if player_choice == bot_choice:
        result = "Empate! 🤝"
    elif _BEATS[player_choice] == bot_choice:
        result = "Você venceu! 🎉"
    else:
        result = "Eu venci! 🤖"
    return (
        f"Você: {_JKP[player_choice]} ({player_choice})\n"
        f"Bot: {_JKP[bot_choice]} ({bot_choice})\n\n{result}"
    )


# ---------- 8ball ----------
_8BALL = [
    "Sim, com certeza! ✅", "Não conte com isso. ❌", "Talvez... 🤔",
    "Definitivamente sim! 💯", "Não. 🙅", "As perspectivas são boas. 👍",
    "Pergunte novamente mais tarde. ⏳", "Melhor não te contar agora. 🤫",
    "Sinais apontam que sim. 🔮", "Muito duvidoso. 😬",
]


def eightball() -> str:
    return random.choice(_8BALL)


# ---------- roll ----------
def roll(sides: int = 6, count: int = 1):
    sides = max(2, min(sides, 1000))
    count = max(1, min(count, 20))
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls)


# ---------- ship ----------
def ship(name_a: str, name_b: str):
    key = "".join(sorted([name_a.lower().strip(), name_b.lower().strip()]))
    pct = int(hashlib.md5(key.encode()).hexdigest(), 16) % 101
    if pct < 25:
        emoji = "💔"
    elif pct < 50:
        emoji = "🙂"
    elif pct < 75:
        emoji = "❤️"
    else:
        emoji = "💞"
    return pct, emoji


# ---------- russian roulette ----------
def russian_roulette(chambers: int = 6) -> bool:
    """True = levou o tiro (perdeu)."""
    return random.randint(1, max(2, chambers)) == 1


# ---------- hangman (forca) ----------
HANGMAN_WORDS = [
    "python", "whatsapp", "computador", "internet", "teclado", "programador",
    "abacaxi", "girafa", "montanha", "oceano", "biblioteca", "chocolate",
    "futebol", "guitarra", "relampago", "estrela", "foguete", "dinossauro",
]


def new_hangman_word() -> str:
    return random.choice(HANGMAN_WORDS)


def hangman_display(word: str, guessed: set) -> str:
    return " ".join(c if c in guessed else "_" for c in word)


def hangman_won(word: str, guessed: set) -> bool:
    return all(c in guessed for c in word)


# ---------- trivia ----------
TRIVIA = [
    {"q": "Qual é o maior planeta do Sistema Solar?", "a": "jupiter"},
    {"q": "Quantos lados tem um hexágono?", "a": "6"},
    {"q": "Qual é a capital do Brasil?", "a": "brasilia"},
    {"q": "Em que ano o homem pisou na Lua?", "a": "1969"},
    {"q": "Qual o metal líquido à temperatura ambiente?", "a": "mercurio"},
    {"q": "Quantos continentes existem?", "a": "7"},
    {"q": "Qual a fórmula química da água?", "a": "h2o"},
    {"q": "Qual o maior oceano do mundo?", "a": "pacifico"},
]


def new_trivia():
    return random.choice(TRIVIA)


# ---------- tictactoe (jogo da velha) ----------
WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def ttt_render(board) -> str:
    cells = [c if c != " " else str(i + 1) for i, c in enumerate(board)]
    rows = [" | ".join(cells[i:i + 3]) for i in range(0, 9, 3)]
    return ("\n" + "-" * 9 + "\n").join(rows)


def ttt_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    if " " not in board:
        return "draw"
    return None


# ---------- akinator (simplificado) ----------
AKINATOR_GUESSES = [
    "Mario", "Sonic", "Pikachu", "Batman", "Homem-Aranha", "Goku",
    "Harry Potter", "Darth Vader", "Mickey Mouse", "Naruto",
]


def akinator_guess() -> str:
    return random.choice(AKINATOR_GUESSES)
