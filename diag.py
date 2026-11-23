"""Diagnóstico do ThzyxBoTS — descobre por que a chave/IA não funciona.

Rode:  python diag.py
Ele NÃO mostra sua chave inteira (mascara), só o suficiente p/ depurar.
"""
import os
import sys


def mask(key: str) -> str:
    if not key:
        return "(vazio)"
    if len(key) <= 14:
        return key[:4] + "..." + f"(len={len(key)})"
    return f"{key[:10]}...{key[-4:]} (len={len(key)})"


def show_raw_env_line():
    """Mostra a linha da chave em repr, revelando caracteres invisíveis."""
    import config
    paths = config._candidate_env_paths()
    print("\n[2] Procurando arquivo .env:")
    found_any = False
    for p in paths:
        exists = os.path.exists(p)
        print(f"    - {p}  ->  {'EXISTE ✅' if exists else 'não existe'}")
        if exists and not found_any:
            found_any = True
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
                if data[:3] == b"\xef\xbb\xbf":
                    print("      ⚠️  Este arquivo tem BOM (UTF-8 com assinatura).")
                for raw in data.splitlines():
                    try:
                        line = raw.decode("utf-8", "replace")
                    except Exception:
                        line = str(raw)
                    if "OPENROUTER_API_KEY" in line:
                        # mascara o valor mas mostra estrutura/caracteres ocultos
                        key, _, val = line.partition("=")
                        print(f"      Linha encontrada (repr): {key!r} = {mask(val.strip())}")
                        print(f"      Bytes crus da linha: {raw[:40]!r}...")
            except Exception as e:
                print(f"      erro ao ler: {e}")
    if not found_any:
        print("    ❌ NENHUM .env encontrado! Crie com: cp .env.example .env")


def test_api(key: str):
    print("\n[4] Teste AO VIVO da API OpenRouter:")
    if not key:
        print("    ⏭️  Sem chave para testar.")
        return
    try:
        import requests
    except ImportError:
        print("    ❌ módulo 'requests' não instalado: pip install requests")
        return
    # 4a) chave válida? (lista de modelos)
    try:
        r = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"}, timeout=20,
        )
        print(f"    GET /models  ->  HTTP {r.status_code}")
        if r.status_code == 401:
            print("    ❌ 401 = CHAVE INVÁLIDA/REVOGADA. Gere outra em https://openrouter.ai/keys")
            return
    except Exception as e:
        print(f"    ❌ erro de rede: {e}")
        print("    (No Termux verifique sua conexão / dados móveis / Wi-Fi)")
        return
    # 4b) consegue gerar resposta? (chat completion real)
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "openai/gpt-oss-120b:free",
                  "messages": [{"role": "user", "content": "diga oi"}],
                  "max_tokens": 20},
            timeout=60,
        )
        print(f"    POST /chat/completions  ->  HTTP {r.status_code}")
        body = r.json()
        if r.status_code == 200 and body.get("choices"):
            txt = body["choices"][0]["message"].get("content") or \
                  body["choices"][0]["message"].get("reasoning") or ""
            print(f"    ✅ RESPOSTA DA IA: {txt[:80]!r}")
            print("\n🎉 A API E A CHAVE ESTÃO FUNCIONANDO! O bot deve responder normalmente.")
        elif r.status_code == 402:
            print("    ❌ 402 = sem créditos/limite. Veja https://openrouter.ai/credits")
        else:
            print(f"    ⚠️  resposta inesperada: {str(body)[:300]}")
    except Exception as e:
        print(f"    ❌ erro: {e}")


def main():
    print("=" * 55)
    print("   DIAGNÓSTICO ThzyxBoTS")
    print("=" * 55)
    print(f"\n[1] Ambiente:")
    print(f"    Python: {sys.version.split()[0]}")
    print(f"    Diretório atual (cwd): {os.getcwd()}")
    print(f"    Pasta do projeto: {os.path.dirname(os.path.abspath(__file__))}")
    try:
        import dotenv  # noqa
        print("    python-dotenv: instalado ✅")
    except ImportError:
        print("    python-dotenv: NÃO instalado (ok, usamos parser manual)")

    show_raw_env_line()

    import config
    print(f"\n[3] Resultado do carregamento:")
    print(f"    Carregado de: {config.ENV_LOADED_FROM or 'NENHUM ARQUIVO'}")
    print(f"    OPENROUTER_API_KEY: {mask(config.OPENROUTER_API_KEY)}")
    if config.OPENROUTER_API_KEY and not config.OPENROUTER_API_KEY.startswith("sk-or"):
        print("    ⚠️  A chave NÃO começa com 'sk-or' — provavelmente está errada/incompleta.")
    if config.OPENROUTER_API_KEY == "sk-or-v1-COLOQUE_SUA_CHAVE_AQUI":
        print("    ❌ Você ainda não trocou o valor de exemplo pela sua chave real!")

    test_api(config.OPENROUTER_API_KEY)

    print("\n[5] Dependências de mídia (/ttkvd, /fg, /va):")
    import shutil
    for tool in ("ffmpeg", "ffprobe"):
        ok = shutil.which(tool)
        print(f"    {tool}: {'OK ✅' if ok else 'AUSENTE ❌ -> pkg install ffmpeg -y'}")
    try:
        import magic  # noqa
        print("    libmagic (python-magic): OK ✅")
    except Exception:
        print("    libmagic: AUSENTE ❌ -> pkg install libmagic file -y")
    print("\n" + "=" * 55)


if __name__ == "__main__":
    main()
