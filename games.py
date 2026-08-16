"""Lógica pura dos jogos e brincadeiras (testável)."""
import datetime
import hashlib
import random
import re
import unicodedata

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


# ═══════════════════════════ v4: NOVOS JOGOS ═══════════════════════════
# Tudo aqui é lógica pura: recebe entrada, devolve texto/estrutura.
# Os handlers do bot.py só fazem a ponte com o chat.

# ---------- normalização de respostas ----------
_ARTIGOS = {"o", "a", "os", "as", "um", "uma", "uns", "umas"}


def normalize(text) -> str:
    """Minúsculas, sem acento e sem pontuação — pra comparar respostas.

    O usuário digita no celular: pode mandar "O Rei Leao", "o rei leão!!" ou
    "  REI LEÃO  ". Tudo isso vira "rei leao".
    """
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text).lower().strip()
    palavras = [p for p in text.split() if p]
    # tira artigo só do começo ("o rei leao" == "rei leao")
    if len(palavras) > 1 and palavras[0] in _ARTIGOS:
        palavras = palavras[1:]
    return " ".join(palavras)


def _compact(text) -> str:
    """Igual a normalize(), mas sem espaço nenhum (pra grafias coladas)."""
    return normalize(text).replace(" ", "")


def _same_answer(guess, expected) -> bool:
    return bool(_compact(guess)) and _compact(guess) == _compact(expected)


def _daily_rng(*partes) -> random.Random:
    """Gerador determinístico por dia: mesmo dia + mesma entrada = mesmo sorteio."""
    hoje = datetime.date.today().isoformat()
    semente = "|".join([hoje] + [str(p) for p in partes])
    return random.Random(semente)


# ---------- piadas, charadas, conselhos, trava-línguas ----------
JOKES = [
    "O que o pato disse pra pata? Vem quá! 🦆",
    "Por que o livro de matemática está triste? Porque tem muitos problemas. 📚",
    "O que o zero disse pro oito? Belo cinto! 0️⃣8️⃣",
    "Qual é o cúmulo da paciência? Escrever a história do mundo num grão de arroz. 🍚",
    "O que a impressora falou pra outra? Essa folha é sua ou é impressão minha? 🖨️",
    "Por que a plantinha não usa computador? Porque ela tem medo do vírus. 🌱",
    "O que o tomate foi fazer no banco? Virar molho. 🍅",
    "Qual o animal mais antigo do mundo? A zebra, porque é preto e branco. 🦓",
    "O que o pão disse pra manteiga? Você me completa. 🍞",
    "Por que o computador foi ao médico? Estava com vírus e sem defesa. 💻",
    "Como se chama o cachorro que gosta de tirar foto? Focinho. 🐶",
    "O que a lua disse pro sol? Você é tão brilhante que me ofusca. 🌙",
    "Qual é a fruta mais engraçada? A laranja, porque é uma piada azeda. 🍊",
    "O que o café falou pro leite? Vamos misturar as ideias? ☕",
]

RIDDLES = [
    {"q": "O que é, o que é: tem dentes mas não morde?", "a": "pente"},
    {"q": "O que é, o que é: quanto mais se tira, maior fica?", "a": "buraco"},
    {"q": "O que é, o que é: anda com os pés na cabeça?", "a": "piolho"},
    {"q": "O que é, o que é: cai em pé e corre deitado?", "a": "chuva"},
    {"q": "O que é, o que é: tem coroa mas não é rei, tem espinho mas não é peixe?",
     "a": "abacaxi"},
    {"q": "O que é, o que é: tem cidades mas não tem casas, tem rios mas não tem água?",
     "a": "mapa"},
    {"q": "O que é, o que é: está sempre na sua frente mas você nunca vê?",
     "a": "futuro"},
    {"q": "O que é, o que é: tem agulhas mas não costura?", "a": "relogio"},
    {"q": "O que é, o que é: quanto mais você anda, mais ela fica para trás?",
     "a": "estrada"},
    {"q": "O que é, o que é: passa a vida deitado mas nunca dorme?", "a": "tapete"},
    {"q": "O que é, o que é: tem folhas mas não é árvore, tem capa mas não é super-herói?",
     "a": "livro"},
    {"q": "O que é, o que é: fala em todas as línguas mas nunca estudou?", "a": "eco"},
]

ADVICES = [
    "Beba um copo de água agora. Seu corpo agradece. 💧",
    "Se algo leva menos de dois minutos, faça agora e não anote na lista.",
    "Dormir bem hoje resolve metade dos problemas de amanhã. 😴",
    "Antes de responder no calor da emoção, respire fundo três vezes.",
    "Comece pequeno: cinco minutos por dia viram um hábito em um mês.",
    "Guarde uma parte do que ganha, mesmo que seja pouquinho. 🪙",
    "Ligue pra alguém da família só pra saber como está. 📞",
    "Feito é melhor que perfeito — depois você melhora.",
    "Anote suas ideias: memória é boa até a hora que você precisa dela. 📝",
    "Caminhar 20 minutos por dia é o remédio mais barato que existe. 🚶",
    "Não compare o seu capítulo 1 com o capítulo 20 dos outros.",
    "Arrume um cantinho da casa hoje. Ambiente limpo, cabeça leve. 🧹",
    "Diga obrigado de verdade a quem te ajudou hoje. 🙏",
    "Se está travado, explique o problema em voz alta. A resposta costuma aparecer.",
]

TONGUE_TWISTERS = [
    "O rato roeu a roupa do rei de Roma.",
    "Três pratos de trigo para três tigres tristes.",
    "A aranha arranha a rã, a rã arranha a aranha.",
    "O peito do pé de Pedro é preto.",
    "Um tigre, dois tigres, três tigres, correndo atrás do trigo.",
    "Sabia que o sabiá sabia assobiar?",
    "Bagre branco, branco bagre.",
    "O doce perguntou pro doce qual é o doce mais doce.",
    "Num ninho de mafagafos, cinco mafagafinhos há.",
    "Se a Aliança não alinhasse, a aliança não desalinhava.",
    "A vaca malhada foi molhada por outra vaca molhada e malhada.",
    "Pedro pediu para Paulo pintar a porta de púrpura.",
]


def joke() -> str:
    """Uma piada limpa, pronta pro grupo da família."""
    return random.choice(JOKES)


def new_riddle() -> dict:
    """Charada: {"q": pergunta, "a": resposta}."""
    return dict(random.choice(RIDDLES))


def advice() -> str:
    """Um conselho curto do dia a dia."""
    return random.choice(ADVICES)


def tongue_twister() -> str:
    """Um trava-língua clássico."""
    return random.choice(TONGUE_TWISTERS)


# ---------- interações sociais (abraço, tapa, elogio...) ----------
SOCIAL_ACTIONS = {
    "abraco": [
        "🤗 {actor} deu um abraço apertado em {target}!",
        "🫂 {actor} abraçou {target} bem forte. Que fofura!",
        "🤗 {actor} correu e pulou no colo de {target} pra um abraço.",
        "💕 {actor} envolveu {target} num abraço de urso.",
    ],
    "tapa": [
        "🖐️ {actor} deu um tapinha de brincadeira em {target}!",
        "😅 {actor} acertou um tapa com travesseiro em {target}.",
        "🖐️ {actor} deu um tapa no ar e errou {target} feio.",
        "🪶 {actor} bateu em {target} com uma peninha. Doeu zero.",
    ],
    "elogio": [
        "🌟 {actor} disse que {target} tem o melhor sorriso do grupo!",
        "👏 {actor} elogiou {target}: pessoa gente boa demais!",
        "💐 {actor} avisou que {target} deixa o dia de todo mundo melhor.",
        "🏅 {actor} declarou {target} o mais prestativo daqui.",
    ],
    "cutucar": [
        "👉 {actor} cutucou {target}. Acorda!",
        "😜 {actor} ficou cutucando {target} sem parar.",
    ],
    "beliscar": [
        "🤏 {actor} deu um belisquinho em {target}.",
        "😆 {actor} beliscou {target} e saiu correndo.",
    ],
    "cafune": [
        "💆 {actor} fez um cafuné em {target}. Puro relaxamento.",
        "😌 {actor} passou a mão na cabeça de {target}. Que carinho!",
    ],
    "highfive": [
        "🙌 {actor} bateu um toca aqui com {target}!",
        "✋ {actor} e {target} fizeram o high five mais alto do grupo.",
    ],
    "danca": [
        "💃 {actor} chamou {target} pra dançar!",
        "🕺 {actor} e {target} arrasaram na pista.",
    ],
    "cafe": [
        "☕ {actor} pagou um café pra {target}.",
        "☕ {actor} trouxe café fresquinho pra {target}. Que gentileza!",
    ],
    "bolo": [
        "🍰 {actor} dividiu um pedaço de bolo com {target}.",
        "🎂 {actor} guardou a fatia maior pra {target}.",
    ],
}

_SOCIAL_PADRAO = [
    "✨ {actor} mandou uma energia boa pra {target}!",
    "🙂 {actor} lembrou de {target} agora há pouco.",
]


def social_interaction(kind: str, actor: str, target: str, seed=None) -> str:
    """Frase pronta de interação social entre duas pessoas.

    `kind` é o tipo (abraco, tapa, elogio...); `actor` e `target` já vêm
    formatados pelo bot (ex.: "@5511999..."). `seed` deixa o sorteio
    reproduzível quando informado.
    """
    frases = SOCIAL_ACTIONS.get(str(kind).lower().strip(), _SOCIAL_PADRAO)
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(frases).format(actor=actor, target=target)


# ---------- casal do dia / top membros ----------
def couple_of_day(phones):
    """Sorteia o casal do dia (determinístico: mesmo dia = mesmo casal).

    Recebe a lista de telefones do grupo e devolve a tupla (a, b).
    Levanta ValueError quando não dá pra formar um par — o bot já trata isso.
    """
    unicos = sorted({str(p).strip() for p in (phones or []) if str(p).strip()})
    if len(unicos) < 2:
        raise ValueError("Preciso de pelo menos 2 pessoas no grupo pra sortear o casal do dia.")
    rng = _daily_rng("casal", ",".join(unicos))
    a, b = rng.sample(unicos, 2)
    return a, b


def top_members(entries, limit: int = 5):
    """Ordena (telefone, pontuação) do maior pro menor e corta no top `limit`.

    Aguenta lista vazia, valores nulos e pontuação em texto.
    """
    limpos = []
    for item in entries or []:
        try:
            phone, score = item
        except (TypeError, ValueError):
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = 0.0
        limpos.append((str(phone), score))
    limpos.sort(key=lambda x: (-x[1], x[0]))
    return limpos[:max(0, limit)]


# ---------- desafio diário ----------
DAILY_CHALLENGES = [
    "Mande uma foto do seu café da manhã no grupo ☕",
    "Elogie três pessoas do grupo hoje 🌟",
    "Conte uma piada nova pra galera 😂",
    "Mande um áudio contando como foi seu dia 🎙️",
    "Beba 2 litros de água hoje 💧",
    "Caminhe 20 minutos e conte pro grupo 🚶",
    "Poste uma foto do céu de hoje 🌤️",
    "Diga bom dia pra todo mundo antes das 9h 🌅",
    "Ganhe uma partida de /jokenpo contra o bot ✊",
    "Acerte uma charada com /charada 🧩",
    "Descubra a palavra de um /anagrama 🔀",
    "Arrume um cantinho da casa e mande a foto 🧹",
    "Mande uma música que você está ouvindo 🎵",
    "Ligue pra alguém da família só pra conversar 📞",
    "Escreva um agradecimento pra alguém do grupo 🙏",
]

DAILY_REWARDS = [50, 75, 100, 120, 150]


def daily_challenge(user_id: str) -> dict:
    """Desafio do dia de um usuário — determinístico dentro do mesmo dia.

    Devolve {"date": "AAAA-MM-DD", "challenge": texto, "reward": moedas}.
    """
    rng = _daily_rng("desafio", user_id)
    return {
        "date": datetime.date.today().isoformat(),
        "challenge": rng.choice(DAILY_CHALLENGES),
        "reward": rng.choice(DAILY_REWARDS),
    }


# ---------- anagrama ----------
ANAGRAM_WORDS = [
    ("banana", "Uma fruta amarela que o macaco adora 🍌"),
    ("cachorro", "Melhor amigo do homem 🐶"),
    ("janela", "Você abre pra entrar ar na sala 🪟"),
    ("girassol", "Flor amarela que segue o sol 🌻"),
    ("bicicleta", "Tem duas rodas e pedais 🚲"),
    ("chocolate", "Doce feito de cacau 🍫"),
    ("professor", "Trabalha na escola ensinando 👩‍🏫"),
    ("geladeira", "Eletrodoméstico que gela a comida ❄️"),
    ("computador", "Máquina de teclado e tela 💻"),
    ("borboleta", "Era lagarta e ganhou asas 🦋"),
    ("guarda-chuva", "Te salva na chuva ☔"),
    ("travesseiro", "Onde a cabeça descansa 🛏️"),
    ("pipoca", "Estoura na panela e vai pro cinema 🍿"),
    ("sorvete", "Gelado e doce, derrete no calor 🍦"),
    ("caderno", "Cheio de folhas pra escrever 📓"),
    ("elefante", "Animal enorme de tromba 🐘"),
]


def new_anagram() -> dict:
    """Anagrama novo: {"word", "scrambled", "hint"}."""
    word, hint = random.choice(ANAGRAM_WORDS)
    letras = list(word)
    embaralhada = word
    for _ in range(20):
        random.shuffle(letras)
        embaralhada = "".join(letras)
        if embaralhada != word:
            break
    return {"word": word, "scrambled": embaralhada.upper(), "hint": hint}


def check_anagram(guess: str, word: str) -> bool:
    """Confere a resposta do anagrama (ignora caixa, acento e espaços)."""
    return _same_answer(guess, word)


# ---------- quiz de emoji (filmes) ----------
EMOJI_QUIZ = [
    {"q": "🦁👑", "a": "O Rei Leão", "hint": "Desenho da Disney na savana"},
    {"q": "🧊❄️👭", "a": "Frozen", "hint": "Duas irmãs e um boneco de neve"},
    {"q": "🐟🔍🌊", "a": "Procurando Nemo", "hint": "Um peixinho palhaço perdido"},
    {"q": "🕷️🧑‍🎤🕸️", "a": "Homem-Aranha", "hint": "Herói que solta teia"},
    {"q": "🚗⚡🏁", "a": "Carros", "hint": "Animação de corrida da Pixar"},
    {"q": "🤖🌱🚀", "a": "Wall-E", "hint": "Robô que limpa a Terra"},
    {"q": "🎈🏠👴", "a": "Up Altas Aventuras", "hint": "Casa voando com balões"},
    {"q": "🐭🍝👨‍🍳", "a": "Ratatouille", "hint": "Um rato que vira chef em Paris"},
    {"q": "🦖🏝️🧬", "a": "Jurassic Park", "hint": "Parque com dinossauros"},
    {"q": "🧙‍♂️💍🌋", "a": "O Senhor dos Anéis", "hint": "Um anel e um vulcão"},
    {"q": "🐝🎬🍯", "a": "Bee Movie", "hint": "Uma abelha que processa os humanos"},
    {"q": "🧸👦🚀", "a": "Toy Story", "hint": "Brinquedos que ganham vida"},
    {"q": "🐼🥋🍜", "a": "Kung Fu Panda", "hint": "Um panda que aprende kung fu"},
    {"q": "👗🕛👠", "a": "Cinderela", "hint": "Sapatinho de cristal à meia-noite"},
]


def new_emoji_quiz() -> dict:
    """Quiz de emojis: {"q": emojis, "a": filme, "hint": dica}."""
    return dict(random.choice(EMOJI_QUIZ))


def check_emoji_quiz(guess: str, answer: str) -> bool:
    """Confere o filme (ignora caixa, acento, pontuação e artigo inicial)."""
    return _same_answer(guess, answer)


# ---------- enigma de lógica ----------
LOGIC_PUZZLES = [
    {"q": "Um trem elétrico vai de norte a sul. Para que lado vai a fumaça?",
     "a": "nenhum, trem eletrico nao solta fumaca"},
    {"q": "Se você está em terceiro lugar numa corrida e ultrapassa o segundo, "
           "em que lugar você fica?", "a": "segundo"},
    {"q": "Uma pessoa tem 2 moedas que somam 30 centavos e uma delas não é de 25. "
           "Quais são?", "a": "25 e 5"},
    {"q": "Quantos meses do ano têm 28 dias?", "a": "todos"},
    {"q": "Um fazendeiro tem 17 ovelhas e todas morrem menos 9. Quantas sobraram?",
     "a": "9"},
    {"q": "O que pesa mais: 1 kg de algodão ou 1 kg de ferro?",
     "a": "os dois pesam igual"},
    {"q": "Você acende um fósforo num quarto escuro com vela, lampião e lareira. "
           "O que acende primeiro?", "a": "o fosforo"},
    {"q": "Duas mães e duas filhas foram ao cinema e compraram 3 ingressos. "
           "Como isso é possível?", "a": "eram avo, mae e filha"},
    {"q": "Se um tijolo pesa 1 kg mais meio tijolo, quanto pesa o tijolo inteiro?",
     "a": "2 kg"},
    {"q": "Um caracol sobe 3 metros de dia e escorrega 2 à noite num poço de 5 metros. "
           "Em quantos dias ele sai?", "a": "3"},
]


def new_logic_puzzle() -> dict:
    """Enigma de lógica: {"q": pergunta, "a": resposta}."""
    return dict(random.choice(LOGIC_PUZZLES))


# ---------- soletrar ----------
SPELLING_WORDS = [
    ("exceção", "Aquilo que foge à regra"),
    ("privilégio", "Vantagem que só alguns têm"),
    ("beneficente", "Instituição que faz o bem sem lucro"),
    ("concerto", "Apresentação musical numa sala de espetáculos"),
    ("cabeleireiro", "Profissional que corta cabelo"),
    ("meteorologia", "Ciência que estuda o tempo e o clima"),
    ("supersticioso", "Quem acredita em gato preto e sexta-feira 13"),
    ("empecilho", "Aquilo que atrapalha, um obstáculo"),
    ("beneficência", "Prática de ajudar quem precisa"),
    ("paralelepípedo", "Pedra usada para calçar ruas antigas"),
    ("hesitar", "Ficar em dúvida antes de agir"),
    ("obsessão", "Ideia fixa que não sai da cabeça"),
    ("ascensão", "Ato de subir, de se elevar"),
    ("iminente", "Que está prestes a acontecer"),
    ("bicarbonato", "Pó branco usado na cozinha e na limpeza"),
]


def new_spelling() -> dict:
    """Palavra pra soletrar: {"word": palavra, "hint": dica}."""
    word, hint = random.choice(SPELLING_WORDS)
    return {"word": word, "hint": hint}


def check_spelling(guess: str, word: str) -> bool:
    """Confere a grafia (tolerante a caixa, acento e espaços extras)."""
    return _same_answer(guess, word)


# ---------- sequência de memória ----------
def new_sequence(level: int = 1):
    """Lista de números pra memorizar. Nível 1..5 → 3 a 7 números."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    level = max(1, min(level, 5))
    tamanho = level + 2
    return [random.randint(1, 9) for _ in range(tamanho)]


def check_sequence(guess: str, sequence) -> bool:
    """Confere a sequência digitada: aceita "1 2 3", "1,2,3" e "123"."""
    esperado = [str(n) for n in (sequence or [])]
    if not esperado:
        return False
    numeros = re.findall(r"\d+", str(guess or ""))
    if numeros == esperado:
        return True
    # tudo colado ou com separadores estranhos: compara só os dígitos
    digitos = re.sub(r"\D", "", str(guess or ""))
    return bool(digitos) and digitos == "".join(esperado)


# ---------- bingo ----------
def new_bingo_card() -> dict:
    """Cartela de bingo 5x5 (coluna B=1-15, I=16-30, ... O=61-75).

    Devolve {"letters": "BINGO", "columns": [[...], ...]} — o centro é 0,
    que a renderização mostra como espaço livre.
    """
    columns = []
    for i in range(5):
        inicio = i * 15 + 1
        columns.append(sorted(random.sample(range(inicio, inicio + 15), 5)))
    columns[2][2] = 0  # espaço livre no meio
    return {"letters": "BINGO", "columns": columns}


def render_bingo_card(card: dict) -> str:
    """Desenha a cartela em texto monoespaçado (5 colunas, cabe no celular)."""
    columns = card["columns"]
    letters = card.get("letters", "BINGO")
    linhas = ["  ".join(letters)]
    linhas.append("-" * len(linhas[0]))
    for r in range(5):
        celulas = []
        for c in range(5):
            valor = columns[c][r]
            celulas.append("★ " if not valor else f"{valor:2d}")
        linhas.append(" ".join(celulas))
    return "\n".join(linhas)


# ---------- caça-palavras ----------
WORD_SEARCH_WORDS = [
    "GATO", "LIVRO", "PRAIA", "BOLO", "VERDE", "PONTE", "FLOR", "CHUVA",
    "AMIGO", "FESTA", "NUVEM", "PEIXE", "CARRO", "PIPA", "BOLA", "CASA",
    "SOL", "LUA", "MAR", "CAFE", "PATO", "VIOLA", "TREM", "MESA",
]

_WS_DIRECTIONS = [(0, 1), (1, 0), (1, 1), (0, -1), (-1, 0)]
_WS_LETTERS = "ABCDEFGHIJLMNOPQRSTUVXZ"


def _ws_try_place(grid, word: str, size: int) -> bool:
    """Tenta encaixar a palavra na grade; devolve True se conseguiu."""
    posicoes = [(r, c, d) for r in range(size) for c in range(size)
                for d in _WS_DIRECTIONS]
    random.shuffle(posicoes)
    for r, c, (dr, dc) in posicoes:
        fim_r, fim_c = r + dr * (len(word) - 1), c + dc * (len(word) - 1)
        if not (0 <= fim_r < size and 0 <= fim_c < size):
            continue
        if all(grid[r + dr * i][c + dc * i] in (None, word[i])
               for i in range(len(word))):
            for i, letra in enumerate(word):
                grid[r + dr * i][c + dc * i] = letra
            return True
    return False


def new_word_search(size: int = 10, count: int = 5) -> dict:
    """Caça-palavras com as palavras REALMENTE escondidas na grade.

    Devolve {"grid": lista de listas, "words": palavras colocadas, "size": n}.
    """
    size = max(6, min(size, 10))  # tela do celular é estreita
    grid = [[None] * size for _ in range(size)]
    candidatas = [w for w in WORD_SEARCH_WORDS if len(w) <= size]
    random.shuffle(candidatas)
    colocadas = []
    for word in candidatas:
        if len(colocadas) >= count:
            break
        if _ws_try_place(grid, word, size):
            colocadas.append(word)
    for r in range(size):
        for c in range(size):
            if grid[r][c] is None:
                grid[r][c] = random.choice(_WS_LETTERS)
    return {"grid": grid, "words": colocadas, "size": size}


def render_word_search(puzzle: dict) -> str:
    """Desenha a grade do caça-palavras em texto monoespaçado."""
    return "\n".join(" ".join(linha) for linha in puzzle["grid"])


# ---------- campo minado ----------
MINE = -1
_MS_NUMS = ["⬛", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
_MS_HEADER = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]


def new_minesweeper(size: int = 8, mines: int = 10):
    """Tabuleiro do campo minado: matriz com MINE (-1) ou o número de vizinhas."""
    size = max(3, min(size, 8))  # no máximo 8 colunas pra não quebrar a linha
    mines = max(1, min(mines, size * size - 1))
    board = [[0] * size for _ in range(size)]
    for pos in random.sample(range(size * size), mines):
        board[pos // size][pos % size] = MINE
    for r in range(size):
        for c in range(size):
            if board[r][c] == MINE:
                continue
            board[r][c] = sum(
                1 for nr, nc in _ms_neighbors(r, c, size)
                if board[nr][nc] == MINE
            )
    return board


def _ms_neighbors(r: int, c: int, size: int):
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < size and 0 <= nc < size:
                yield nr, nc


def minesweeper_reveal(board, row: int, col: int, revealed=None) -> dict:
    """Revela uma casa. Nunca levanta exceção com coordenada inválida.

    Devolve {"revealed": conjunto novo, "hit": pisou na mina,
    "won": limpou o campo, "valid": a coordenada existia}.
    Casas sem mina vizinha abrem em cascata.
    """
    size = len(board)
    revelado = set(revealed or ())
    try:
        row, col = int(row), int(col)
    except (TypeError, ValueError):
        return {"revealed": revelado, "hit": False, "won": False, "valid": False}
    if not (0 <= row < size and 0 <= col < len(board[row])):
        return {"revealed": revelado, "hit": False, "won": False, "valid": False}
    if board[row][col] == MINE:
        revelado.add((row, col))
        return {"revealed": revelado, "hit": True, "won": False, "valid": True}
    # cascata: abre vizinhas enquanto a casa não tiver mina por perto
    fila = [(row, col)]
    while fila:
        r, c = fila.pop()
        if (r, c) in revelado:
            continue
        revelado.add((r, c))
        if board[r][c] == 0:
            fila.extend(
                (nr, nc) for nr, nc in _ms_neighbors(r, c, size)
                if (nr, nc) not in revelado and board[nr][nc] != MINE
            )
    seguras = sum(1 for lin in board for v in lin if v != MINE)
    return {
        "revealed": revelado,
        "hit": False,
        "won": len(revelado) >= seguras,
        "valid": True,
    }


def render_minesweeper(board, revealed=None, show_mines: bool = False) -> str:
    """Desenha o campo minado com emojis (cabe na tela do celular)."""
    revelado = set(revealed or ())
    size = len(board)
    linhas = ["⬜" + "".join(_MS_HEADER[:size])]
    for r in range(size):
        celulas = []
        for c in range(size):
            valor = board[r][c]
            if (r, c) in revelado:
                celulas.append("💥" if valor == MINE else _MS_NUMS[valor])
            elif show_mines and valor == MINE:
                celulas.append("💣")
            else:
                celulas.append("⬜")
        linhas.append(_MS_HEADER[r] + "".join(celulas))
    return "\n".join(linhas)
