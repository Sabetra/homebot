<!-- last-verified: 2026-08-30 -->

# 18 — Legal/Ethical Compliance für web-sourced RAG-Persistierung (2026-08-30)

> **Zweck:** Web-sourced Content (Web-Search-Snippets, `upsert_url`, Vision-URLs) wird
> in den lokalen RAG-Store persistiert — d. h. er überlebt die Session. Diese Doku
> definiert das konservative Compliance- und Retention-Modell, das diese Persistierung
> regelt, und die dazugehörigen Gates, Tests und Konfiguration.
>
> **Kontext:** Lokale, personal-use Runtime (Local-First, keine Cloud-LLM-Calls).
> Trotzdem gilt: Explizite Opt-Out-Signale einer Website (robots-Disallow,
> noindex/nofollow, no-store) werden respektiert — und alter Web-Content wird
> nach einem definierten Retentionsfenster wieder gelöscht.

---

## 1. Compliance-Modell (3 Schichten)

Implementiert in `utils/web_compliance.py` (reine Python-Stdlib, keine neuen
Abhängigkeiten, keine DB-Schema-Änderungen).

| Schicht | Quelle | Blockierend wenn |
|---------|--------|------------------|
| **robots.txt** | `<scheme>://<host>/robots.txt` | `Disallow`-Regel trifft den Pfad (RFC 9309 §3: längste passende Regel gewinnt) |
| **Response-Header** | HTTP-Header der Page | `X-Robots-Tag`/`Googlebot` mit `noindex`/`nofollow`/`noarchive`/`nosnippet`/`noimageindex`/`nocache`; `Cache-Control`/`Pragma: no-store` |
| **HTML-Meta** | `<meta name="robots" …>` / `<meta name="googlebot" …>` | gleiche Direktiven wie bei Header |

`index`/`follow`-Direktiven sind **nicht** blockierend (Default-Zustand).
Kein Header/Meta vorhanden → erlaubt.

### Entscheidungsfluss

```
decide(url, headers=None, html=None)
  → robots.txt (RobotsChecker, per-Domain-Cache)
  → Response-Header (check_response_headers)
  → HTML-Meta (check_html_meta)
  → ComplianceDecision(allowed, reasons[])
```

`gate_persistence(context, url, headers=None, html=None)` ist die
Produktiv-Schnittstelle: loggt bei Blockade eine WARNING (mit Kontext +

### CPython-3.12-Quirks in `urllib.robotparser` (root-causiert, 2026-08-30)

`urllib.robotparser` darf für compliance-kritische Entscheidungen nicht
blind vertraut werden; zwei reale Mängel wurden gefunden und umgangen:

1. **Direktiven vor dem ersten `User-agent`-Block werden still ignoriert**
   (häufig bei kleinen Sites: „bares“ `Disallow:` ohne UA-Block).
   → `RobotsChecker._normalize_robots_text()` stellt eine `User-agent: *`-
     Gruppe vor, wenn Direktiven ohne UA-Kontext auftauchen.
2. **`Entry.allowance()` nimmt die *erste* passende Regel, nicht die längste** —
   widerspricht RFC 9309 §3 (longest-match-wins, wie auch Googles
   Interpretation). Folge: `Disallow: /` + `Allow: /public` blockte `/public`.
   → `RobotsChecker` wählt die Regel selbst: `_select_entry()` (wie CPython:
     zuerst Agent-spezifisches Entry, dann `*`-Default) +
     `_most_specific_rule()` (längster passender Pfad gewinnt,
     Gleichstand = Datei-Ordnung).

Beide Quirks haben Regressions-Tests in `tests/test_web_compliance.py`.

---

## 2. Retention-Modell (TTL-Pruning)

- **`retention_until`** — Web-sourced Records (`source_type LIKE 'web%'`)
  bekommen bei der Persistierung das ISO-8601-Metadatum
  `retention_until = now + Retentionsfenster`. Injiziert an allen
  Persistierungs-Punkten (u. a. `UnifiedRagStore`-Metadata-Pfad,
  `tools.py persist_to_rag`, Orchestrator-Snippet-Fallback).
- **Retentionsfenster** — `WEB_RETENTION_DAYS` (Default: **30 Tage**).
  `0` oder negativ = **unbegrenzt** (Feld wird nicht gesetzt); ungültige
  Werte fallen mit WARNING auf den Default zurück.
- **Pruning** — `UnifiedRagStore.prune_web_content(max_age_days=None, dry_run=False)`:
  löscht nur web-derived Records mit abgelaufenem `retention_until`
  (Fallback: `search_timestamp`/`date_stored` bei fehlendem Feld;
  Records ohne verwertbaren Zeitstempel werden **übersprungen**,
  nie blind gelöscht). Kind-Tabellen (Chunks/Evidence) werden vor dem
  Dokument gelöscht. Wird bei Pipeline-Start einmalig im Hintergrund
  ausgeführt (daemon-Thread, fail-soft) — `agent/rag_pipeline.py`.

---

## 3. Gate-Punkte (Produktivpfad)

| Modul | Stelle | Kontext-String |
|-------|--------|----------------|
| `agent/unified_rag_store.py` | `upsert_url()` | `upsert_url` |
| `agent/unified_rag_store.py` | `upsert_url_with_vision()` | `upsert_url_with_vision` |
| `agent/unified_rag_store.py` | Metadata-Pfad (web-sourced) | `retention_until`-Injektion |
| `agent/tools.py` | `persist_to_rag`-Tool | `tools.persist_to_rag` |
| `agent/rag_pipeline.py` | Snippet-Fallback-Persistierung | `rag_pipeline.snippet_fallback` |
| `agent/rag_pipeline.py` | Pipeline-Start | `prune_web_content()` (daemon) |
| `agent/orchestrator.py` | Snippet-Fallback-Persistierung | `orchestrator.snippet_fallback` |

Alle Gates: `if not web_compliance.gate_persistence(context, url): return …`
(kein Persistieren, kein Hard-Error).

---

## 4. Konfiguration (ENV)

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `WEB_COMPLIANCE_ENABLED` | `1` (aktiv) | Master-Switch; `0`/`false`/`off`/`no` deaktiviert alle Checks + Retention (Gate erlaubt dann mit `disabled`-Reason) |
| `WEB_RETENTION_DAYS` | `30` | Retentionsfenster in Tagen; `0`/negativ = unbegrenzt |

Interne Konstanten (`utils/web_compliance.py`): robots-Cache-TTL 1 h pro
Domain, Negativ-Cache 60 s (verhindert Hammerschlag bei out-of-reach
Domains), Fetch-Timeout 5 s, max. 1 MB robots.txt,
User-Agent `bot6-local-rag/1.0 (personal-use; local RAG store)`.

---

## 5. Tests

`tests/test_web_compliance.py` — **47 Tests**, vollständig hermetisch
(Netzwerk-Schicht via injizierbarem `fetcher` simuliert, **keine** echten
robots.txt-Downloads). Abgedeckt:

- `RobotsChecker`: Allow/Disallow, bare-Direktiven-Quirk, longest-match
  (Allow-Liste), UA-spezifische + Wildcard-Entries, CRLF/Whitespace/Comments,
  Nicht-HTTP-Schemes (kein Fetch), per-Domain-Cache, TTL-Expiry,
  Negativ-Cache, Fail-Open, Thread-Sicherheit (8 Threads).
- `check_response_headers` / `check_html_meta`: Blockierende Direktiven,
  Case-Insensitivität, `index`/`follow` erlaubt, `None`/leer erlaubt.
- `decide`: Aggregation mehrerer Reasons, Robots-blockiert-First,
  Disabled-Modus.
- `gate_persistence`: Block + WARNING-Log, erlaubt, leere URL, Disabled.
- Retention-Helfer: Default 30 d, Custom, `0`/negativ = unbegrenzt,
  ungültig → Default, `retention_until_iso()`, Master-Switch.

```powershell
cd <PROJEKT_ROOT>
.\venv_bot_20260802\Scripts\python.exe -m pytest tests\test_web_compliance.py -v --no-header -p no:cacheprovider
```

Stand 2026-08-30: **47/47 passed**.

---

## 6. Referenzen

| Typ | Pfad |
|-----|------|
| Implementierung | `utils/web_compliance.py` |
| Tests | `tests/test_web_compliance.py` |
| Pruning | `agent/unified_rag_store.py` (`prune_web_content`) |
| Gates | `agent/unified_rag_store.py`, `agent/tools.py`, `agent/rag_pipeline.py`, `agent/orchestrator.py` |
| Kompendium | `funktionen.md` §W |
Reasons) und gibt `False` zurück → der Caller persistiert **nicht**.
Leere/fehlende URL → Gate wird übersprungen (keine Web-URL, kein Check).

### Fail-Open (bewusst, dokumentiert)

Schlägt der robots.txt-**Fetch** fehl (Netzwerk, Timeout, HTTP-Fehler,
Parse-Fehler), wird die URL mit WARNING-Log **erlaubt**. Begründung:
Eine unerreichbare robots.txt darf die lokale Persistierung nicht brechen —
der Fail-Case wäre sonst „jede Offline-Seite wird stumm verworfen“ und
außerdem unzuverlässig. **Explizite** Opt-Out-Signale (eine erfolgreich
gelesene Disallow-Regel, noindex, no-store) blocken dagegen **hart** —
Fail-Open greift nie über ein erhaltenes Opt-Out-Signal hinweg.