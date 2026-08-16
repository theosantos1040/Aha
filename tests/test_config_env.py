"""Regressões de carregamento seguro do .env."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


def test_clean_value_remove_comentario_inline():
    assert config._clean_value("hf_abc # comentário") == "hf_abc"
    assert config._clean_value("'hf_abc # faz parte do valor'") == (
        "hf_abc # faz parte do valor"
    )


def test_variavel_do_host_tem_prioridade_sobre_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=hf_do_arquivo\n", encoding="utf-8")
    monkeypatch.setattr(config, "_candidate_env_paths", lambda: [str(env_file)])
    monkeypatch.setenv("HF_TOKEN", "hf_injetada_pelo_host")

    config._load_env_manual()

    assert os.environ["HF_TOKEN"] == "hf_injetada_pelo_host"


def test_dotenv_e_usado_quando_host_nao_definiu(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=hf_do_arquivo # comentário\n", encoding="utf-8")
    monkeypatch.setattr(config, "_candidate_env_paths", lambda: [str(env_file)])
    monkeypatch.delenv("HF_TOKEN", raising=False)

    config._load_env_manual()

    assert os.environ["HF_TOKEN"] == "hf_do_arquivo"
