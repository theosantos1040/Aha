"""Smoke test: dispara TODOS os comandos registrados com um cliente falso.

Não valida o conteúdo da resposta — valida que nenhum comando explode por
função inexistente, assinatura errada ou erro de digitação. É a rede de
segurança para o roteador: qualquer `db.x`/`media.x`/`games.x` que não exista
aparece aqui como "⚠️ Erro ao executar".

A rede é bloqueada de propósito: comando que dependa de internet tem que
degradar com mensagem de erro, nunca travar o bot.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reaproveita o cliente falso e os construtores de mensagem do test_commands.
# Importar de lá também cuida do banco de teste (ele faz o init no import), por
# isso não configuramos DATA_DB aqui — dois inits concorrentes se atrapalhariam.
import bot
from test_commands import FakeClient, make_msg, make_media_msg

# Comandos que abrem thread/loop de contagem e poluiriam outros testes.
PULAR = {"countdown", "timer", "reaction"}

# Argumentos plausíveis para os comandos que exigem entrada.
ARGS = {
    "calc": "2+2", "roll": "2d6", "choose": "a | b", "reverse": "abc",
    "translate": "oi", "weather": "Lisboa", "password": "12",
    "randomnumber": "1 10", "convert": "10 km mi", "emojify": "oi",
    "sayembed": "T | D", "shorturl": "https://exemplo.com", "crypto": "btc",
    "roleinfo": "5511999999999", "giverole": "5511888888888 mod",
    "removerole": "5511888888888 mod", "addrole": "5511888888888 mod",
    "massrole": "vip", "createrole": "staff", "deleterole": "staff",
    "setwelcome": "Oi @user", "setbye": "Tchau @user", "autorole": "off",
    "nickname": "x", "createchannel": "x", "clonechannel": "x",
    "deletechannel": "x", "hidechannel": "x", "showchannel": "x",
    "warn": "5511888888888 motivo", "delwarn": "5511888888888",
    "checkwarns": "5511888888888", "ban": "5511888888888",
    "unban": "5511888888888", "kick": "5511888888888",
    "mute": "5511888888888", "unmute": "5511888888888",
    "tempban": "5511888888888 1h", "temprole": "5511888888888 vip 1h",
    "softban": "5511888888888", "promover": "5511888888888",
    "rebaixar": "5511888888888", "pay": "5511888888888 10",
    "roubar": "5511888888888", "battle": "5511888888888",
    "duel": "5511888888888", "ship": "5511888888888",
    "poll": "Cor? | azul | verde", "remind": "1m teste",
    "report": "spam", "suggest": "ideia", "announce": "aviso",
    "slowmode": "5", "whitelist-add": "5511888888888",
    "whitelist-remove": "5511888888888", "setprefix": "/",
    "iamode": "zoeira", "aimodel": "chatgpt", "thinking": "off",
    "aisetname": "Ana", "aisetbio": "bio", "ia": "oi",
    "pesquisa": "python", "gerarimagem": "gato", "analiseia": "o que e",
    "imc": "70 1.75", "idade": "01/01/2000", "regra3": "2 4 6",
    "base64": "texto", "hash": "texto", "horario": "Europe/Lisbon",
    "cep": "01001000", "ddd": "11", "moeda": "USD-BRL",
    "wiki": "Python", "dicionario": "casa", "apostar": "10",
    "depositar": "10", "sacar": "10", "comprar": "1",
    "casar": "5511888888888", "elogiar": "5511888888888",
    "abraco": "5511888888888", "tapa": "5511888888888",
    "top": "ativos", "blacklist": "add palavrao",
    "antimidia": "video", "antipalavrao": "on", "antifake": "on",
    "inativos": "7", "cancelarlembrete": "1", "fgpack": "Pack | Autor",
    "girar": "90", "blur": "5", "pixelar": "12", "acelerar": "2",
    "lentidao": "2", "marcartodos": "oi", "hidetag": "oi",
    "resumir": "texto longo", "corrigir": "texto", "explicar": "gravidade",
    "codigo": "ordenar lista", "ideias": "festa", "historia": "dragao",
    "debater": "redes sociais", "sentimento": "estou feliz",
    "anagrama": "casa", "emojiquiz": "resposta", "soletrar": "casa",
    "sequencia": "1 2 3", "campominado": "1 1", "guessnumber": "5",
    "mathrace": "4", "higherlower": "maior", "tictactoe": "1",
    "wordchain": "casa", "roulette": "vermelho", "jokenpo": "pedra",
    "8ball": "vou passar?", "trivia": "a", "hangman": "a",
    "akinator": "sim", "guessflag": "brasil", "guesspokemon": "pikachu",
    "guessanime": "naruto", "enigma": "resposta", "bingo": "1",
    "desafio": "", "charada": "", "sorteio": "", "casal": "",
}

MIDIA = {"fg", "va", "toimg", "togif", "girar", "espelhar", "pb", "blur",
         "pixelar", "recortar", "acelerar", "lentidao", "reverter",
         "analiseia", "analisar", "vision", "ocr", "legenda", "transcrever",
         "avatar", "banner", "ttkvd"}


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Bloqueia a rede: nenhum comando pode depender dela para não quebrar."""
    def bloqueado(*a, **k):
        raise OSError("rede bloqueada no teste")
    monkeypatch.setattr("requests.get", bloqueado)
    monkeypatch.setattr("requests.post", bloqueado)


def _rodar(nome):
    fake = FakeClient(admin=True)
    bot.client = fake
    args = ARGS.get(nome, "")
    texto = f"/{nome} {args}".strip()
    msg = make_media_msg(texto, "image") if nome in MIDIA else make_msg(texto)
    bot.handle_command(msg, texto)
    return fake


@pytest.mark.parametrize("nome", sorted(set(bot.COMMANDS) - PULAR))
def test_comando_nao_explode(nome):
    fake = _rodar(nome)
    ruins = [s for s in fake.sent if "Erro ao executar" in s]
    assert not ruins, f"/{nome} quebrou -> {ruins}"


def test_cobertura_total():
    """Garante que o smoke cobre tudo que está registrado."""
    assert len(bot.COMMANDS) >= 250, f"esperava 250+, achei {len(bot.COMMANDS)}"
