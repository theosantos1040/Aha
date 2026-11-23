"""Teste AO VIVO da IA contra o OpenRouter.

Garante que cada modelo configurado responde com texto não-vazio (HTTP 200).
Requer OPENROUTER_API_KEY no ambiente/.env.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai import chat


def test_all_models():
    assert config.OPENROUTER_API_KEY, "OPENROUTER_API_KEY não configurada"
    for key in config.AI_MODELS:
        print(f"→ Testando modelo '{key}' ({config.AI_MODELS[key]})...")
        ans = chat("Responda em uma palavra: funcionando?", model_key=key)
        assert ans and ans.strip(), f"modelo {key} devolveu vazio"
        print(f"   ✓ {key}: {ans[:80]!r}")
    print(f"\n✅ Todos os {len(config.AI_MODELS)} modelos responderam (HTTP 200).")


if __name__ == "__main__":
    test_all_models()
