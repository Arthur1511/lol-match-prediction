                        Riot Games API
                              │
                              │
                       Data Collector
                              │
                              ▼
                    Cloud Storage (Landing Zone)
                        JSON (compressed)
                              │
                              ▼
                        Bronze Layer
                     Raw match data
                              │
                              ▼
                        Silver Layer
                 Structured match tables
                    (players + matches)
                              │
                              ▼
                        Gold Layer
                     Feature Engineering
                              │
                              ▼
                        ML Training
                  ┌────────────────────┐
                  │ Pre-Draft Model    │
                  │ Post-Draft Model   │
                  └────────────────────┘
                              │
                              ▼
                        Model Evaluation
                   ΔAUC (Draft Impact Study)
                              │
                              ▼
                        Monitoring
                 Patch changes / meta drift
