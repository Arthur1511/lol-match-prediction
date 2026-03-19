"""
Teste para validar que a API key está sendo enviada corretamente nas requisições.
"""

import sys
from pathlib import Path

# Adicionar o diretório raiz ao path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from collector.riot_api_collector import RiotAPICollector


def test_api_key_in_config():
    """Verifica se a API key está sendo carregada do .env"""

    collector = RiotAPICollector()

    # Verificar que a API key foi carregada no config
    api_key = collector.config["riot_api"]["api_key"]
    assert api_key is not None, "API key é None"
    assert api_key.startswith("RGAPI-"), (
        f"API key não começa com RGAPI-: {api_key[:10]}"
    )

    print(f"✓ API Key carregada: {api_key[:15]}...")
    print(f"✓ API Key será enviada no header X-Riot-Token")

    return True


def test_api_key_in_session():
    """Verifica que a session será criada com header X-Riot-Token"""

    import inspect

    from collector.riot_api_collector import RiotAPICollector

    # Verificar que run_collection usa headers na session
    source = inspect.getsource(RiotAPICollector.run_collection)

    assert "X-Riot-Token" in source, (
        "Header X-Riot-Token não encontrado em run_collection"
    )
    assert "ClientSession(headers=headers)" in source, (
        "Session não está sendo criada com headers"
    )

    print("✓ Session será criada com header X-Riot-Token")
    print("✓ Todas as requisições incluirão a API key automaticamente")

    return True


if __name__ == "__main__":
    try:
        print("=" * 60)
        print("Testando uso da API Key")
        print("=" * 60)
        print()

        test_api_key_in_config()
        print()
        test_api_key_in_session()

        print()
        print("=" * 60)
        print("✅ API Key está configurada corretamente!")
        print("=" * 60)
        print()
        print("📝 Resumo:")
        print("  1. API key é lida do .env (RIOT_API_KEY)")
        print("  2. Armazenada em config['riot_api']['api_key']")
        print("  3. Adicionada ao header X-Riot-Token na session")
        print("  4. Todas as requisições HTTP incluem a key automaticamente")

    except Exception as e:
        print(f"\n❌ Erro na validação: {e}")
        raise
