# Riot API Method Rate Limits (Coding-Agent Friendly)

This file normalizes endpoint-specific rate limits for easy implementation in collectors, clients, and throttling middleware.

## How To Read

- `window`: time period for the quota.
- `limit`: max requests allowed in that window.
- If an endpoint has multiple windows, all windows apply simultaneously.
- Apply the strictest effective throughput when designing concurrency.

## Suggested Agent Strategy

- Keep a global app limiter (for your API key tier).
- Keep per-method limiters for endpoints with stricter quotas.
- Evaluate all relevant buckets before each request.
- On `429`, use backoff and honor retry headers when available.

## Normalized Limits

### champion-v3

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/platform/v3/champion-rotations` | `30 / 10s`, `500 / 10m` |

### summoner-v4

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/summoner/v4/summoners/by-puuid/{encryptedPUUID}` | `1600 / 1m` |

### league-v4

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/league/v4/challengerleagues/by-queue/{queue}` | `30 / 10s`, `500 / 10m` |
| GET | `/lol/league/v4/grandmasterleagues/by-queue/{queue}` | `30 / 10s`, `500 / 10m` |
| GET | `/lol/league/v4/masterleagues/by-queue/{queue}` | `30 / 10s`, `500 / 10m` |
| GET | `/lol/league/v4/leagues/{leagueId}` | `500 / 10s` |
| GET | `/lol/league/v4/entries/{queue}/{tier}/{division}` | `50 / 10s` |
| GET | `/lol/league/v4/entries/by-puuid/{encryptedPUUID}` | `20000 / 10s`, `1200000 / 10m` |

### league-exp-v4

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/league-exp/v4/entries/{queue}/{tier}/{division}` | `50 / 10s` |

### clash-v1

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/clash/v1/teams/{teamId}` | `200 / 1m` |
| GET | `/lol/clash/v1/tournaments/{tournamentId}` | `10 / 1m` |
| GET | `/lol/clash/v1/tournaments/by-team/{teamId}` | `200 / 1m` |
| GET | `/lol/clash/v1/tournaments` | `10 / 1m` |
| GET | `/lol/clash/v1/players/by-puuid/{puuid}` | `20000 / 10s`, `1200000 / 10m` |

### account-v1

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}` | `1000 / 1m`, `20000 / 10s`, `1200000 / 10m` |
| GET | `/riot/account/v1/accounts/by-puuid/{puuid}` | `1000 / 1m`, `20000 / 10s`, `1200000 / 10m` |
| GET | `/riot/account/v1/region/by-game/{game}/by-puuid/{puuid}` | `20000 / 10s`, `1200000 / 10m` |

### lol-status-v4

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/status/v4/platform-data` | `20000 / 10s`, `1200000 / 10m` |

### match-v5

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/match/v5/matches/{matchId}` | `2000 / 10s` |
| GET | `/lol/match/v5/matches/by-puuid/{puuid}/ids` | `2000 / 10s` |
| GET | `/lol/match/v5/matches/{matchId}/timeline` | `2000 / 10s` |
| GET | `/lol/match/v5/matches/by-puuid/{puuid}/replays` | `20000 / 10s`, `1200000 / 10m` |

### lol-challenges-v1

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/challenges/v1/challenges/percentiles` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/challenges/v1/challenges/{challengeId}/leaderboards/by-level/{level}` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/challenges/v1/challenges/{challengeId}/percentiles` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/challenges/v1/challenges/{challengeId}/config` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/challenges/v1/player-data/{puuid}` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/challenges/v1/challenges/config` | `20000 / 10s`, `1200000 / 10m` |

### champion-mastery-v4

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/champion-mastery/v4/champion-masteries/by-puuid/{encryptedPUUID}` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/champion-mastery/v4/champion-masteries/by-puuid/{encryptedPUUID}/by-champion/{championId}` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/champion-mastery/v4/scores/by-puuid/{encryptedPUUID}` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/champion-mastery/v4/champion-masteries/by-puuid/{encryptedPUUID}/top` | `20000 / 10s`, `1200000 / 10m` |

### tournament-stub-v5

| Method | Endpoint | Limits |
|---|---|---|
| POST | `/lol/tournament-stub/v5/codes` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/tournament-stub/v5/lobby-events/by-code/{tournamentCode}` | `20000 / 10s`, `1200000 / 10m` |
| GET | `/lol/tournament-stub/v5/codes/{tournamentCode}` | `20000 / 10s`, `1200000 / 10m` |
| POST | `/lol/tournament-stub/v5/providers` | `20000 / 10s`, `1200000 / 10m` |
| POST | `/lol/tournament-stub/v5/tournaments` | `20000 / 10s`, `1200000 / 10m` |

### spectator-v5

| Method | Endpoint | Limits |
|---|---|---|
| GET | `/lol/spectator/v5/active-games/by-summoner/{encryptedPUUID}` | `20000 / 10s`, `1200000 / 10m` |

## Notes For This Repository

- Current collector architecture should continue using endpoint-aware throttling.
- Elite leagues (`CHALLENGER`, `GRANDMASTER`, `MASTER`) use dedicated endpoints and should keep separate limiter keys from standard league entries.
- Keep response caching enabled to reduce quota pressure.
