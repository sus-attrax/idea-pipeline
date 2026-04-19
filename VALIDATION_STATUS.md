# Idea Pipeline — Validation Status

> **Zweck dieses Dokuments:** Gesprächsgrundlage für eine Evaluation des bisherigen Stands.
> Enthält den aktuellen Datenstand, die Pipeline-Architektur, bekannte Schwachstellen und offene Fragen.
> Erstellt: 2026-04-19

---

## Was wurde bisher gebaut?

Eine automatisierte Bewertungspipeline für Geschäftsideen, die in einem Obsidian-Vault als Markdown-Dateien gespeichert sind. Die Pipeline läuft auf einem Server und ist über dieses Git-Repo zugänglich.

### Datenstruktur

Der Vault enthält drei Note-Typen:
- **IdeeNote** — eine Geschäftsidee (142 Stück)
- **ChanceNote** — ein Problem/Opportunitätsfeld (361 Stück), verknüpft mit Ideen
- **WissenNote** — ein persönlicher Wissensbereich (37 Stück), verknüpft mit Ideen

Jede Idee wird über drei Dimensionen bewertet:
1. **Chance-Score** — Qualität der verknüpften Problembereiche (wie groß, dringend, verbreitet ist das Problem?)
2. **Wissen-Score** — Überlappung mit vorhandenem Expertenwissen (Bioreaktoren, Mikrobiologie, etc.)
3. **Intrinsic-Score** — manuell gesetzte persönliche Einschätzung (Impact, Schwierigkeit, Innovativität, Zeiteinsatz)

Diese drei werden gewichtet kombiniert: `0.40 × chance + 0.25 × wissen + 0.35 × intrinsic`

---

## Aktueller Datenstand: 142 Ideen

### Research-Tiefe pro Idee

| Tier | Tool | Ideen | Was wurde gesammelt |
|------|------|------:|---------------------|
| T1 | Tavily (Web-Snippets) | 142 | 4 Markt-Scores (1–6) aus Snippet-Extraktion via Haiku |
| T2 | Claude Sonnet + Web Search | 48 | 4 Markt-Scores + Fließtext-Narrative (3–6 Sätze mit $-Zahlen, CAGR, Konkurrenten) |
| T3 | Perplexity sonar-pro | 0 | *wartet auf API-Key* |
| T4 | Firecrawl (vollständige Seiten) | 3 | 4 Markt-Scores + tiefes Narrative (Statista/Fortune Business Insights etc.) |
| T5 | Autonomer 3-Loop (Claude) | 0 | Gegenargumente, Wettbewerber mit Zahlen, Markteintrittsbarrieren |

**Die 4 Markt-Scores (alle 1=best, 6=worst):**
- `market_size` — globale Marktgröße (1 = >$10B, 6 = <$10M)
- `market_potential` — Wachstumsrate/CAGR (1 = >20%/Jahr, 6 = stagnierend)
- `prevalence` — Verbreitung des Problems (1 = Millionen betroffen, 6 = Randfall)
- `market_awareness` — Bekanntheit des Problems (1 = etabliertes Problem, 6 = unbekannt)

**Wo die Texte zu finden sind:**
- T2-Narrative für 45 Ideen: `reports/t2_review_2026-04-19.md`
- T4-Narrative für 3 Ideen (microbial_omega3, High_performance_mulch, balkongarten): ebenfalls in `reports/t2_review_2026-04-19.md`

---

## Top 15 Leaderboard (aktueller Stand)

| # | Idee | Score | Tier | mSz | mPot | prev | mAw | intr |
|---|------|------:|:----:|:---:|:----:|:----:|:---:|:----:|
| 1 | microbial_omega3 | 5.385 | T2 | 2 | 2 | 1 | 1 | 5.6 |
| 2 | High_performance_mulch | 5.273 | T2 | 3 | 2 | 1 | 2 | 5.4 |
| 3 | balkongarten | 4.927 | T2 | 2 | 2 | 1 | 2 | 5.6 |
| 4 | doorframe_fittness | 4.702 | T2 | 3 | 2 | 1 | 2 | 5.4 |
| 5 | crop_and_soil_specific_mycorrhizal_fertilizer | 3.831 | T1 | 2 | 1 | 2 | 2 | 2.8 |
| 6 | durre_resilienz_vorhersage | 3.739 | T2 | 3 | 1 | 2 | 4 | 2.6 |
| 7 | phosphor_effizienz_predictor | 3.737 | T2 | 3 | 2 | 2 | 3 | 2.4 |
| 8 | boden_restaurations_erfolgs_predictor | 3.722 | T2 | 3 | 2 | 2 | 3 | 2.4 |
| 9 | anti_pilz_peptid_generator | 3.647 | T2 | 1 | 3 | 1 | 2 | 2.6 |
| 10 | natural_house_climat_systems | 3.553 | T2 | 2 | 2 | 1 | 3 | 2.6 |
| 11 | microbial_battelfield_remediation | 3.533 | T2 | 2 | 1 | 2 | 3 | 2.8 |
| 12 | crop_flavor_predictor | 3.533 | T2 | 3 | 1 | 3 | 5 | 2.6 |
| 13 | holistic_fitnessstudio | 3.518 | T2 | 2 | 2 | 1 | 3 | 2.6 |
| 14 | influencer | 3.499 | T2 | 1 | 1 | 1 | 2 | 3.0 |
| 15 | abwasser_mikrobiom_alarm | 3.497 | T2 | 2 | 1 | 2 | 3 | 2.8 |

*Vollständiges Leaderboard (alle 142): `LEADERBOARD.md`*

---

## Bekannte Schwachstellen / Verzerrungen

### 1. Intrinsic-Score dominiert Top 4
Die Ideen auf Plätzen 1–4 haben `intr ≈ 5.4–5.6` (sehr hoch). Das liegt daran, dass diese Ideen **manuell gesetzte** intrinsic-Werte haben (Impact=1, Difficulty=1 etc. — also die besten möglichen Werte). Alle anderen 138 Ideen haben Default-Werte (6 = worst), weil der LLM-Schritt zur automatischen Intrinsic-Bewertung (Step 11) noch nicht implementiert ist.

**Folge:** Die Top 4 sind strukturell bevorzugt. Das Ranking ab Platz 5 ist fairer, weil es nur auf Chance/Wissen/Markt-Scores basiert.

### 2. T1-Scores sind grob
Tavily liefert nur Snippet-Fragmente. Haiku extrahiert daraus Scores ohne vollen Kontext. Bei 94 Ideen (noch auf T1) sind die Markt-Scores deshalb weniger verlässlich.

### 3. Wissen-Links noch unvollständig
129 von 142 Ideen haben Wissen-Links. 13 haben keine. Der Linking-Algorithmus hat konservativ nur sichere Matches gesetzt.

### 4. Chance-Scores kommen aus Vault-Daten
Die 361 Chancen wurden manuell oder per LLM mit Scores versehen. Die Qualität dieser Scores ist heterogen — frühe Chancen haben sorgfältigere Bewertungen als später eingepflegte.

---

## Dominante Wissensgebiete (Top 10)

| Wissensbereich | Ideen die es nutzen |
|----------------|:-------------------:|
| mikrobiologie | 71 |
| biochemie | 54 |
| oekologie | 47 |
| mycologie | 41 |
| fermentation | 38 |
| bioreactors | 28 |
| biologie | 28 |
| botanik | 24 |
| gene_engineering | 16 |
| mechatronik | 14 |

→ Das Portfolio ist stark auf Bio/Agri/Fermentation konzentriert. Nur wenige Ideen liegen außerhalb dieses Clusters (balkongarten, doorframe_fittness, holistic_fitnessstudio).

---

## Was als nächstes geplant ist

| Schritt | Was | Voraussetzung |
|---------|-----|---------------|
| T3 | Perplexity-Research für Top 10 | API-Key eintragen in `.env` |
| T4 | Firecrawl für Top 5 | Neue Firecrawl-Credits kaufen (€17/3000) |
| T5 | Autonomer Research-Loop für Top 5 | T4 abgeschlossen |
| Step 10 | Intrinsic-Scores per LLM für alle 142 Ideen | — |
| Step 11 | Re-Scoring nach Intrinsic-Update | Step 10 |

---

## Offene Fragen zur Diskussion

1. **Macht das Scoring-System Sinn?** Die Gewichtung `0.40/0.25/0.35` ist ein erster Entwurf. Sollte Markt-Research stärker gewichtet werden als persönliches Wissen?

2. **Sind die Top 4 wirklich die besten Ideen?** Oder sind sie nur bevorzugt weil sie manuell gut bewertet wurden (Intrinsic-Score)? → Kritisch diskutieren bevor T3/T4 drauf verwendet wird.

3. **Was fehlt inhaltlich?** Die Pipeline bewertet Marktgröße und persönliches Wissen, aber nicht: Wettbewerbsdichte, Regulierungs-Risiko, Time-to-Market, Capital-Intensität.

4. **Nächster konkreter Schritt:** Perplexity-Key holen und T3 für Top 10 laufen lassen — oder erst Step 10 (Intrinsic-Scores) implementieren damit das Ranking stabiler ist?
