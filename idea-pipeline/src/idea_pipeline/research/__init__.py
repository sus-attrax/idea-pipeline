"""Research layer: fetch external data to fill in market_size, prevalence, etc.

Tier-based funnel (set in Step 6-8):
- T0: vault only, no research
- T1: web search (Claude API)
- T2: web + structured sources (Statista, Destatis, Eurostat)
- T3: + reports, deep competitor analysis
"""
