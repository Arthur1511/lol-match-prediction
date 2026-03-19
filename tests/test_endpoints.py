"""
Teste de validação dos endpoints corrigidos para CHALLENGER/GRANDMASTER/MASTER.

Este teste verifica se os endpoints corretos estão sendo usados para cada tier.
"""

import sys
from pathlib import Path

# Adicionar o diretório raiz ao path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

from collector.riot_api_collector import RiotAPICollector


def test_endpoint_selection():
    """Verifica se os endpoints corretos são selecionados por tier."""

    collector = RiotAPICollector()

    # Testar que a configuração tem todos os endpoints necessários
    endpoints = collector.config["riot_api"]["endpoints"]

    required_endpoints = [
        "league_entries",  # DIAMOND e abaixo
        "challenger_league",  # CHALLENGER
        "grandmaster_league",  # GRANDMASTER
        "master_league",  # MASTER
    ]

    for endpoint in required_endpoints:
        assert endpoint in endpoints, (
            f"Endpoint {endpoint} não encontrado na configuração"
        )

    print("✓ Todos os endpoints necessários estão configurados")

    # Verificar os valores
    assert (
        endpoints["challenger_league"]
        == "/lol/league/v4/challengerleagues/by-queue/{queue}"
    )
    assert (
        endpoints["grandmaster_league"]
        == "/lol/league/v4/grandmasterleagues/by-queue/{queue}"
    )
    assert endpoints["master_league"] == "/lol/league/v4/masterleagues/by-queue/{queue}"
    assert (
        endpoints["league_entries"]
        == "/lol/league/v4/entries/{queue}/{tier}/{division}"
    )

    print("✓ Endpoints configurados corretamente:")
    print(f"  - CHALLENGER:   {endpoints['challenger_league']}")
    print(f"  - GRANDMASTER:  {endpoints['grandmaster_league']}")
    print(f"  - MASTER:       {endpoints['master_league']}")
    print(f"  - DIAMOND+:     {endpoints['league_entries']}")

    return True


if __name__ == "__main__":
    try:
        test_endpoint_selection()
        print("\n✅ Validação completa - endpoints corrigidos!")
    except Exception as e:
        print(f"\n❌ Erro na validação: {e}")
        raise
