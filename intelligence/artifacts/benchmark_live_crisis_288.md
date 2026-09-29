# Live benchmark — crisis, 288 ticks

| policy | service_level | served L | unmet L | allocations | failures | responses | p95 ms |
|---|---|---|---|---|---|---|---|
| none | 0.275332 | 85900.0 | 226086.665 | 0 | 0 | {} | None |
| heuristic | 0.99886 | 311631.015 | 355.65 | 44 | 0 | {'201': 44} | 13.5 |
| lp | 1.0 | 311986.665 | 0.0 | 271 | 0 | {'201': 271} | 120.2 |
