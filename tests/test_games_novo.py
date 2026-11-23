"""Testes das funções novas de games.py (v4) — jogos, social e desafios."""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games


# ──────────────────────────── conteúdo simples ────────────────────────────

def test_joke_advice_travalingua():
    assert isinstance(games.joke(), str) and games.joke().strip()
    assert isinstance(games.advice(), str) and games.advice().strip()
    assert isinstance(games.tongue_twister(), str) and games.tongue_twister().strip()
    # todo o conteúdo é texto não vazio
    for lista in (games.JOKES, games.ADVICES, games.TONGUE_TWISTERS):
        assert lista and all(isinstance(x, str) and x.strip() for x in lista)


def test_new_riddle_e_enigma_tem_q_e_a():
    for _ in range(20):
        charada = games.new_riddle()
        assert set(charada) >= {"q", "a"}
        assert charada["q"].strip() and charada["a"].strip()
        enigma = games.new_logic_puzzle()
        assert set(enigma) >= {"q", "a"}
        assert enigma["q"].strip() and enigma["a"].strip()


def test_social_interaction_formata_e_respeita_seed():
    frase = games.social_interaction("abraco", "@111", "@222", seed="x")
    assert "@111" in frase and "@222" in frase
    assert "{" not in frase
    # mesma seed = mesma frase
    assert frase == games.social_interaction("abraco", "@111", "@222", seed="x")
    # tipo desconhecido não quebra
    generico = games.social_interaction("nao-existe", "@111", "@222", seed="y")
    assert "@111" in generico and "@222" in generico
    for kind in games.SOCIAL_ACTIONS:
        texto = games.social_interaction(kind, "@a", "@b", seed="z")
        assert "@a" in texto and "@b" in texto


# ──────────────────────────── normalização ────────────────────────────

def test_normalize_ignora_acento_caixa_e_pontuacao():
    assert games.normalize("  O Rei LEÃO!! ") == "rei leao"
    assert games.normalize("Exceção") == "excecao"
    assert games.normalize("") == ""


# ──────────────────────────── anagrama ────────────────────────────

def test_new_anagram_tem_chaves_do_bot():
    for _ in range(20):
        item = games.new_anagram()
        assert set(item) >= {"word", "scrambled", "hint"}
        assert sorted(item["scrambled"].lower()) == sorted(item["word"].lower())
        assert item["hint"].strip()


def test_check_anagram_tolerante():
    assert games.check_anagram("BANANA", "banana")
    assert games.check_anagram("  banana  ", "banana")
    assert games.check_anagram("BORBOLÉTA", "borboleta")  # acento errado
    assert games.check_anagram("Guarda Chuva", "guarda-chuva")
    assert not games.check_anagram("abacaxi", "banana")
    assert not games.check_anagram("", "banana")


# ──────────────────────────── emoji quiz ────────────────────────────

def test_new_emoji_quiz_tem_q_a_hint():
    for _ in range(20):
        item = games.new_emoji_quiz()
        assert set(item) >= {"q", "a", "hint"}
        assert item["q"].strip() and item["a"].strip() and item["hint"].strip()


def test_check_emoji_quiz_tolerante():
    assert games.check_emoji_quiz("o rei leao", "O Rei Leão")
    assert games.check_emoji_quiz("REI LEÃO", "O Rei Leão")
    assert games.check_emoji_quiz("  Homem Aranha ", "Homem-Aranha")
    assert not games.check_emoji_quiz("frozen", "O Rei Leão")


# ──────────────────────────── soletrar ────────────────────────────

def test_new_spelling_e_check():
    item = games.new_spelling()
    assert set(item) >= {"word", "hint"}
    assert games.check_spelling(item["word"], item["word"])
    assert games.check_spelling("  EXCECAO ", "exceção")  # sem acento e em caixa alta
    assert games.check_spelling("Privilégio", "privilégio")
    assert not games.check_spelling("excessao", "exceção")
    assert not games.check_spelling("", "exceção")


# ──────────────────────────── sequência ────────────────────────────

def test_new_sequence_tamanho_por_nivel():
    for nivel in range(1, 6):
        seq = games.new_sequence(nivel)
        assert len(seq) == nivel + 2
        assert all(isinstance(n, int) and 1 <= n <= 9 for n in seq)
    # nível fora da faixa não quebra
    assert len(games.new_sequence(0)) == 3
    assert len(games.new_sequence(99)) == 7


def test_check_sequence_aceita_varios_formatos():
    seq = [1, 2, 3, 4]
    assert games.check_sequence("1 2 3 4", seq)
    assert games.check_sequence(" 1,2,3,4 ", seq)
    assert games.check_sequence("1234", seq)
    assert not games.check_sequence("1 2 4 3", seq)
    assert not games.check_sequence("", seq)
    assert not games.check_sequence("1 2 3 4", [])


# ──────────────────────────── bingo ────────────────────────────

def test_bingo_card_e_render():
    card = games.new_bingo_card()
    assert set(card) >= {"letters", "columns"}
    assert len(card["columns"]) == 5 and all(len(col) == 5 for col in card["columns"])
    assert card["columns"][2][2] == 0  # espaço livre
    for i, col in enumerate(card["columns"]):
        for r, valor in enumerate(col):
            if (i, r) == (2, 2):
                continue
            assert i * 15 + 1 <= valor <= i * 15 + 15
        assert len(set(col)) == 5
    texto = games.render_bingo_card(card)
    linhas = texto.split("\n")
    assert linhas[0].replace(" ", "") == "BINGO"
    assert len(linhas) == 7  # cabeçalho + separador + 5 linhas
    assert all(len(l) <= 20 for l in linhas)  # cabe na tela do celular


# ──────────────────────────── caça-palavras ────────────────────────────

def _busca_na_grade(grid, word):
    """Procura a palavra nas 8 direções."""
    size = len(grid)
    direcoes = [(0, 1), (1, 0), (1, 1), (0, -1), (-1, 0), (-1, -1), (1, -1), (-1, 1)]
    for r in range(size):
        for c in range(size):
            for dr, dc in direcoes:
                fim_r, fim_c = r + dr * (len(word) - 1), c + dc * (len(word) - 1)
                if not (0 <= fim_r < size and 0 <= fim_c < size):
                    continue
                if all(grid[r + dr * i][c + dc * i] == word[i]
                       for i in range(len(word))):
                    return True
    return False


def test_word_search_contem_mesmo_as_palavras():
    for _ in range(10):
        puzzle = games.new_word_search()
        assert set(puzzle) >= {"grid", "words", "size"}
        size = puzzle["size"]
        assert len(puzzle["grid"]) == size
        assert all(len(linha) == size for linha in puzzle["grid"])
        assert all(isinstance(ch, str) and len(ch) == 1
                   for linha in puzzle["grid"] for ch in linha)
        assert len(puzzle["words"]) >= 3
        for word in puzzle["words"]:
            assert _busca_na_grade(puzzle["grid"], word), f"{word} não está na grade"


def test_render_word_search_cabe_na_tela():
    puzzle = games.new_word_search()
    texto = games.render_word_search(puzzle)
    linhas = texto.split("\n")
    assert len(linhas) == puzzle["size"]
    assert all(len(l.split()) <= 10 for l in linhas)


# ──────────────────────────── campo minado ────────────────────────────

def test_new_minesweeper_estrutura():
    board = games.new_minesweeper()
    assert len(board) == 8 and all(len(l) == 8 for l in board)
    minas = [(r, c) for r in range(8) for c in range(8) if board[r][c] == games.MINE]
    assert len(minas) == 10
    # os números batem com as minas vizinhas
    for r in range(8):
        for c in range(8):
            if board[r][c] == games.MINE:
                continue
            esperado = sum(1 for nr, nc in games._ms_neighbors(r, c, 8)
                           if board[nr][nc] == games.MINE)
            assert board[r][c] == esperado


def test_minesweeper_coordenada_invalida_nao_quebra():
    board = games.new_minesweeper()
    for row, col in [(-1, 0), (0, -1), (99, 3), (3, 99), (100, 100)]:
        res = games.minesweeper_reveal(board, row, col, set())
        assert res["valid"] is False
        assert res["hit"] is False and res["won"] is False
        assert res["revealed"] == set()


def test_minesweeper_mina_e_cascata():
    # tabuleiro montado à mão: uma mina no canto inferior direito
    size = 4
    board = [[0] * size for _ in range(size)]
    board[3][3] = games.MINE
    for r in range(size):
        for c in range(size):
            if board[r][c] == games.MINE:
                continue
            board[r][c] = sum(1 for nr, nc in games._ms_neighbors(r, c, size)
                              if board[nr][nc] == games.MINE)
    # pisar na mina
    res = games.minesweeper_reveal(board, 3, 3, set())
    assert res["hit"] is True and res["won"] is False

    # cascata a partir do canto oposto abre todas as casas seguras
    res = games.minesweeper_reveal(board, 0, 0, set())
    assert res["hit"] is False
    assert len(res["revealed"]) == size * size - 1
    assert (3, 3) not in res["revealed"]
    assert res["won"] is True


def test_minesweeper_acumula_reveladas_e_renderiza():
    board = games.new_minesweeper()
    seguras = [(r, c) for r in range(8) for c in range(8)
               if board[r][c] != games.MINE]
    revelado = set()
    for r, c in seguras[:3]:
        res = games.minesweeper_reveal(board, r, c, revelado)
        revelado = res["revealed"]
    assert all(pos in revelado for pos in seguras[:3])

    vazio = games.render_minesweeper(board)
    assert vazio.count("💣") == 0
    parcial = games.render_minesweeper(board, revelado)
    com_minas = games.render_minesweeper(board, revelado, show_mines=True)
    assert com_minas.count("💣") == 10
    for texto in (vazio, parcial, com_minas):
        assert len(texto.split("\n")) == 9  # cabeçalho + 8 linhas


# ──────────────────────────── casal do dia / top ────────────────────────────

def test_couple_of_day_deterministico_e_listas_curtas():
    phones = ["5511111", "5522222", "5533333", "5544444"]
    a, b = games.couple_of_day(phones)
    assert a != b and a in phones and b in phones
    assert (a, b) == games.couple_of_day(list(reversed(phones)))  # ordem não importa
    with pytest.raises(ValueError):
        games.couple_of_day([])
    with pytest.raises(ValueError):
        games.couple_of_day(["5511111"])
    with pytest.raises(ValueError):
        games.couple_of_day(["5511111", "5511111"])  # duplicado = 1 pessoa
    with pytest.raises(ValueError):
        games.couple_of_day(None)


def test_top_members():
    entries = [("111", 10), ("222", 50), ("333", 30), ("444", 5),
               ("555", 99), ("666", 1)]
    top = games.top_members(entries)
    assert len(top) == 5
    assert [p for p, _ in top] == ["555", "222", "333", "111", "444"]
    assert top[0][1] == 99.0
    # listas vazia e de 1 elemento
    assert games.top_members([]) == []
    assert games.top_members(None) == []
    assert games.top_members([("111", 7)]) == [("111", 7.0)]
    # valores inválidos viram 0 e não quebram
    assert games.top_members([("111", None), ("222", "abc")]) == [
        ("111", 0.0), ("222", 0.0)]


# ──────────────────────────── desafio diário ────────────────────────────

def test_daily_challenge_deterministico_no_mesmo_dia():
    item = games.daily_challenge("5511111@s.whatsapp.net")
    assert set(item) >= {"date", "challenge", "reward"}
    assert item["date"] == datetime.date.today().isoformat()
    assert isinstance(item["reward"], int) and item["reward"] > 0
    assert item["challenge"] in games.DAILY_CHALLENGES
    # mesmo usuário, mesmo dia = mesmo desafio
    for _ in range(5):
        assert games.daily_challenge("5511111@s.whatsapp.net") == item
    # usuários diferentes normalmente recebem desafios diferentes
    outros = {games.daily_challenge(f"55{i}@s.whatsapp.net")["challenge"]
              for i in range(30)}
    assert len(outros) > 1
