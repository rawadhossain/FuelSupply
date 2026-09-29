# Live benchmark — baseline, 288 ticks

| policy | service_level | served L | unmet L | allocations | failures | responses | p95 ms |
|---|---|---|---|---|---|---|---|
| none | 0.30735 | 85900.0 | 193585.957 | 0 | 0 | {} | None |
| heuristic | 1.0 | 279485.957 | 0.0 | 40 | 0 | {'201': 40} | 17.5 |
| lp | 1.0 | 279485.957 | 0.0 | 258 | 0 | {'201': 258} | 131.1 |
