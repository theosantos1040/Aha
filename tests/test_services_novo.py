"""Testes das consultas novas do services: cep, ddd, moeda, wiki, dicionario, horario.

Nenhum teste toca a rede: `requests.get` é substituído por um dublê que devolve
payloads falsos. O `horario` é offline de verdade (zoneinfo), então é testado
direto. Também são testados os caminhos de entrada inválida (que não podem nem
chegar a chamar a rede) e o de erro de rede (deve virar string "❌ ...").
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services


class RespostaFalsa:
    """Imita o objeto Response do requests, só com o que o services usa."""

    def __init__(self, dados=None, status=200, texto=""):
        self._dados = dados
        self.status_code = status
        self.text = texto

    def json(self):
        if self._dados is None:
            raise ValueError("sem JSON")
        return self._dados

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fingir_get(monkeypatch, resposta, registro=None):
    """Troca services.requests.get por um dublê.

    `resposta` pode ser um RespostaFalsa ou uma função url -> RespostaFalsa.
    """
    def falso_get(url, **kwargs):
        # regra crítica do projeto: nenhuma chamada pode ficar sem timeout
        assert kwargs.get("timeout"), f"chamada sem timeout: {url}"
        if registro is not None:
            registro.append(url)
        return resposta(url) if callable(resposta) else resposta

    monkeypatch.setattr(services.requests, "get", falso_get)


def proibir_rede(monkeypatch):
    """Qualquer acesso à rede vira falha do teste."""
    def explode(url, **kwargs):
        raise AssertionError(f"não deveria ter chamado a rede: {url}")

    monkeypatch.setattr(services.requests, "get", explode)


def get_que_falha(monkeypatch):
    """Simula a rede caindo no meio da chamada."""
    def explode(url, **kwargs):
        assert kwargs.get("timeout"), f"chamada sem timeout: {url}"
        raise services.requests.ConnectionError("rede fora do ar")

    monkeypatch.setattr(services.requests, "get", explode)


# ============================== CEP ==============================

CEP_OK = {
    "cep": "01001-000", "logradouro": "Praça da Sé", "complemento": "lado ímpar",
    "bairro": "Sé", "localidade": "São Paulo", "uf": "SP", "estado": "São Paulo",
    "regiao": "Sudeste", "ddd": "11",
}


def test_cep_formata_resposta(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa(CEP_OK), urls)
    saida = services.cep("01001-000")
    assert urls == ["https://viacep.com.br/ws/01001000/json/"]
    assert "📍 *CEP 01001-000*" in saida
    assert "Praça da Sé (lado ímpar)" in saida
    assert "São Paulo - SP" in saida
    assert "Sudeste" in saida
    assert "DDD: 11" in saida
    assert not saida.startswith("❌")


def test_cep_nao_encontrado(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa({"erro": "true"}))
    saida = services.cep("99999999")
    assert saida.startswith("❌")
    assert "99999-999" in saida


@pytest.mark.parametrize("entrada", ["", "123", "abcdefgh", "0100100012", "01001-00"])
def test_cep_invalido_nao_chama_rede(monkeypatch, entrada):
    proibir_rede(monkeypatch)
    saida = services.cep(entrada)
    assert saida.startswith("❌")
    assert "8 dígitos" in saida


def test_cep_erro_de_rede(monkeypatch):
    get_que_falha(monkeypatch)
    saida = services.cep("01001000")
    assert saida.startswith("❌")
    assert "rede fora do ar" in saida


# ============================== DDD ==============================

def test_ddd_formata_resposta(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa({"state": "SP",
                                           "cities": ["SÃO PAULO", "SANTOS", "OSASCO"]}), urls)
    saida = services.ddd("11")
    assert urls == ["https://brasilapi.com.br/api/ddd/v1/11"]
    assert "📞 *DDD 11*" in saida
    assert "Estado: SP" in saida
    assert "Cidades atendidas: 3" in saida
    assert "Osasco" in saida


def test_ddd_inexistente(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa({"message": "não existe"}, status=404))
    saida = services.ddd("00")
    assert saida.startswith("❌")
    assert "não existe" in saida


@pytest.mark.parametrize("entrada", ["", "1", "123", "xx"])
def test_ddd_invalido_nao_chama_rede(monkeypatch, entrada):
    proibir_rede(monkeypatch)
    saida = services.ddd(entrada)
    assert saida.startswith("❌")
    assert "2 dígitos" in saida


def test_ddd_erro_de_rede(monkeypatch):
    get_que_falha(monkeypatch)
    assert services.ddd("11").startswith("❌")


# ============================== MOEDA ==============================

MOEDA_OK = {"USDBRL": {"code": "USD", "codein": "BRL",
                       "name": "Dólar Americano/Real Brasileiro",
                       "high": "5.44", "low": "5.40", "pctChange": "0.25",
                       "bid": "5.4321", "create_date": "2026-08-16 10:00:00"}}


def test_moeda_awesomeapi(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa(MOEDA_OK), urls)
    saida = services.moeda("USD-BRL")
    assert urls == ["https://economia.awesomeapi.com.br/json/last/USD-BRL"]
    assert "Dólar Americano/Real Brasileiro" in saida
    assert "1 USD = 5.4321 BRL" in saida
    assert "+0.25%" in saida
    assert "2026-08-16 10:00:00" in saida


def test_moeda_aceita_formatos_soltos(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa(MOEDA_OK))
    for entrada in ("usd brl", "usd/brl", "USDBRL", " Usd-Brl "):
        assert "1 USD = 5.4321 BRL" in services.moeda(entrada)


def test_moeda_cai_no_plano_b(monkeypatch):
    """AwesomeAPI com quota estourada (429) -> usa o open.er-api."""
    urls = []

    def responder(url):
        if "awesomeapi" in url:
            return RespostaFalsa({"status": 429, "code": "QuotaExceeded"}, status=429)
        return RespostaFalsa({"result": "success", "base_code": "USD",
                              "rates": {"BRL": 5.1853, "EUR": 0.86},
                              "time_last_update_utc": "Sun, 16 Aug 2026 00:02:31 +0000"})

    fingir_get(monkeypatch, responder, urls)
    saida = services.moeda("USD-BRL")
    assert any("awesomeapi" in u for u in urls)
    assert any("open.er-api.com" in u for u in urls)
    assert "💱 *USD/BRL*" in saida
    assert "1 USD = 5.1853 BRL" in saida
    assert not saida.startswith("❌")


def test_moeda_inexistente_nas_duas_apis(monkeypatch):
    def responder(url):
        if "awesomeapi" in url:
            return RespostaFalsa({"status": 404}, status=404)
        return RespostaFalsa({"result": "error", "error-type": "unsupported-code"})

    fingir_get(monkeypatch, responder)
    saida = services.moeda("ZZZ-BRL")
    assert saida.startswith("❌")
    assert "ZZZ-BRL" in saida


@pytest.mark.parametrize("entrada", ["", "USD", "US-BRL", "USD-BRLL", "1234", "USD BRL EUR"])
def test_moeda_invalida_nao_chama_rede(monkeypatch, entrada):
    proibir_rede(monkeypatch)
    saida = services.moeda(entrada)
    assert saida.startswith("❌")


def test_moeda_par_igual_nao_chama_rede(monkeypatch):
    proibir_rede(monkeypatch)
    assert services.moeda("BRL-BRL").startswith("❌")


def test_moeda_erro_de_rede(monkeypatch):
    get_que_falha(monkeypatch)
    saida = services.moeda("USD-BRL")
    assert saida.startswith("❌")


# ============================== WIKI ==============================

WIKI_OK = {
    "type": "standard", "title": "Brasil", "description": "país na América do Sul",
    "extract": "Brasil é o maior país da América do Sul.",
    "content_urls": {"desktop": {"page": "https://pt.wikipedia.org/wiki/Brasil"}},
}


def test_wiki_formata_resposta(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa(WIKI_OK), urls)
    saida = services.wiki("Brasil")
    assert urls == ["https://pt.wikipedia.org/api/rest_v1/page/summary/Brasil"]
    assert "📚 *Brasil*" in saida
    assert "país na América do Sul" in saida
    assert "maior país da América do Sul" in saida
    assert "https://pt.wikipedia.org/wiki/Brasil" in saida


def test_wiki_sanitiza_o_termo(monkeypatch):
    """O usuário não pode montar uma URL arbitrária com barras/../."""
    urls = []
    fingir_get(monkeypatch, RespostaFalsa(WIKI_OK), urls)
    services.wiki("../../w/index.php?x=1 & y")
    url = urls[0]
    assert url.startswith("https://pt.wikipedia.org/api/rest_v1/page/summary/")
    resto = url.split("/summary/", 1)[1]
    assert "/" not in resto and "?" not in resto and "&" not in resto


def test_wiki_termo_composto_vira_underline(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa(WIKI_OK), urls)
    services.wiki("  Rio de   Janeiro ")
    assert urls[0].endswith("/Rio_de_Janeiro")


def test_wiki_resumo_longo_e_cortado(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa(dict(WIKI_OK, extract="a" * 2000)))
    saida = services.wiki("Brasil")
    assert "..." in saida
    assert len(saida) < 1200


def test_wiki_desambiguacao(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa(dict(WIKI_OK, type="disambiguation")))
    saida = services.wiki("Manga")
    assert "específico" in saida


def test_wiki_nao_encontrado(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa({"status": 404}, status=404))
    saida = services.wiki("asdkjhasd")
    assert saida.startswith("❌")
    assert "asdkjhasd" in saida


def test_wiki_vazio_nao_chama_rede(monkeypatch):
    proibir_rede(monkeypatch)
    assert services.wiki("").startswith("❌")
    assert services.wiki("   ").startswith("❌")
    assert services.wiki("x" * 500).startswith("❌")


def test_wiki_erro_de_rede(monkeypatch):
    get_que_falha(monkeypatch)
    assert services.wiki("Brasil").startswith("❌")


# ============================== DICIONÁRIO ==============================

XML_LIVRO = (
    '<entry id="livro"><form><orth>Livro</orth></form>'
    "<sense><gramGrp>m.</gramGrp><def>\n"
    "Reunião de cadernos manuscritos ou impressos.\n"
    "Composição literária mais extensa que um folheto.\n"
    "</def></sense>"
    "<sense><def>\nAquilo que ensina como um livro.\n</def></sense>"
    "<etym orig=\"lat\">(Do lat. _liber_)</etym></entry>"
)


def test_dicionario_formata_resposta(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa([{"word": "livro", "xml": XML_LIVRO}]), urls)
    saida = services.dicionario("Livro")
    assert urls == ["https://api.dicionario-aberto.net/word/livro"]
    assert "📖 *Livro*" in saida
    assert "1. (m.) Reunião de cadernos manuscritos ou impressos." in saida
    assert "2. Composição literária mais extensa que um folheto." in saida
    assert "3. Aquilo que ensina como um livro." in saida
    assert "Dicionário Aberto" in saida


def test_dicionario_xml_quebrado_usa_plano_b(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa([{"xml": "<entry><def>Significado solto"}]))
    saida = services.dicionario("teste")
    assert "Significado solto" in saida
    assert not saida.startswith("❌")


def test_dicionario_palavra_sem_registro(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa([]))
    saida = services.dicionario("xyzzyqwe")
    assert saida.startswith("❌")
    assert "xyzzyqwe" in saida


def test_dicionario_404(monkeypatch):
    fingir_get(monkeypatch, RespostaFalsa(None, status=404))
    assert services.dicionario("abluble").startswith("❌")


@pytest.mark.parametrize("entrada", ["", "   ", "duas palavras", "casa123", "a/b", "x" * 60])
def test_dicionario_invalido_nao_chama_rede(monkeypatch, entrada):
    proibir_rede(monkeypatch)
    assert services.dicionario(entrada).startswith("❌")


def test_dicionario_aceita_acento_e_hifen(monkeypatch):
    urls = []
    fingir_get(monkeypatch, RespostaFalsa([{"xml": XML_LIVRO}]), urls)
    services.dicionario("guarda-chuva")
    services.dicionario("órgão")
    assert urls[0].endswith("/guarda-chuva")
    assert urls[1].endswith("/%C3%B3rg%C3%A3o")  # acento vai codificado na URL


def test_dicionario_erro_de_rede(monkeypatch):
    get_que_falha(monkeypatch)
    assert services.dicionario("casa").startswith("❌")


# ============================== HORÁRIO (offline de verdade) ==============================

def test_horario_apelido_em_portugues():
    saida = services.horario("Tóquio")
    assert "Asia/Tokyo" in saida
    assert "UTC+09:00" in saida  # o Japão não tem horário de verão
    assert "🕒" in saida and "⏰" in saida and "📅" in saida


def test_horario_utc():
    saida = services.horario("utc")
    assert "UTC+00:00" in saida


def test_horario_nome_iana():
    saida = services.horario("America/Sao_Paulo")
    assert "America/Sao_Paulo" in saida
    assert "UTC-03:00" in saida


def test_horario_nome_iana_minusculo():
    assert "Asia/Tokyo" in services.horario("asia/tokyo")


def test_horario_cidade_do_iana_sem_apelido():
    """Cidade que não está no dicionário de apelidos, mas existe no zoneinfo."""
    saida = services.horario("Kathmandu")
    assert "Asia/Kathmandu" in saida


def test_horario_data_bate_com_o_relogio():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    agora = datetime.now(ZoneInfo("Europe/Lisbon"))
    saida = services.horario("Lisboa")
    assert agora.strftime("%d/%m/%Y") in saida
    assert saida.split("⏰ ")[1][:2] == agora.strftime("%H")


def test_horario_dia_da_semana_em_portugues():
    saida = services.horario("UTC")
    dias = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
            "sexta-feira", "sábado", "domingo")
    assert any(dia in saida for dia in dias)


@pytest.mark.parametrize("entrada", ["", "   ", "lugar nenhum", "../../etc/passwd"])
def test_horario_invalido(entrada):
    assert services.horario(entrada).startswith("❌")


def test_horario_nao_usa_rede(monkeypatch):
    proibir_rede(monkeypatch)
    assert "Europe/London" in services.horario("Londres")
