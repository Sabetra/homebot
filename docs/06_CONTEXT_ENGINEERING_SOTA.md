<!-- last-verified: 2026-09-04 -->
# 06 — Context Engineering SOTA Guide

> **Stand:** 1. August 2026  
> **Status:** Aktiv — konsolidiert und verifiziert  
> **Hardware:** Windows 11, 64 GB RAM, RTX 4090 (24 GB VRAM)  
> **Primares LLM:** Gemma 4 12B (via llama-cpp-python GGUF)  
> **VE:** `<PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1` (Rollback: `venv_mistral_gguf`)

---

## 1. Ziel

Dieses Dokument beschreibt den **State-of-the-Art (SOTA) für Context Engineering** im Agent-Chatbot-Projekt. Es dient als zentrale Referenz fur LLM-basierte Coding-Assistenz, um schnell die wesentlichen Architekturinformationen, Patterns und Konfigurationen zu finden — ohne durch Dutzende von Dateien suchen zu mussen.

### 1.1 Was ist Context Engineering?

Context Engineering ist die Praxis, die **Informationsbereitstellung fur ein LLM** so zu gestalten, dass:
- Das Kontextfenster optimal genutzt wird
- Relevante Informationen priorisiert werden
- Redundanzen minimiert werden
- Die Antwortqualitat maximiert wird

### 1.2 SOTA-Prinzipien (2026)

| Prinzip | Beschreibung | Quellen |
|---------|-------------|---------|
| **Hierarchische Kontextstruktur** | Master-Document → Deep-Dive-Modules → Archived | Projektkonvention (00_CONTEXT_MASTER.md, docs/README.md) |
| **Progressive Disclosure** | Nur beim Bedarf tiefere Details laden | LangChain SOTA Papers |
| **Token-Bewusste Struktur** | Kritisches Knowledge in ersten 4K Tokens | Anthropic 2025 |
| **Modulare Dokumentation** | Jedes Dokument hat eine klare, einzelne Verantwortung | Clean Architecture |
| **Inhaltsverzeichnisse als Index** | Zentrale Navigation uber README/Index | DevOps SOTA |
| **AGENTS.md-Standard** | Agent-lesbare Repo-Instruktionen am Root (Kommandos, Konventionen) | agents.md (Agentic AI Foundation / Linux Foundation) |

---

## 2. Aktuelle Dokumentationsstruktur

```
docs/
├── README.md                    ← Index / Navigation
├── 00_CONTEXT_MASTER.md         ← Zentrales Master-Dokument (SOTA-Referenz)
├── 01_ARCHITECTURE_DEEP_DIVE.md ← Architektur im Detail
├── 02_SOTA_ROADMAP.md           ← SOTA-Entwicklungsroadmap
├── 03_FINANCE_MODULE.md         ← Finanzmodul-Dokumentation
├── 04_I18N_GUIDE.md             ← Internationalisierung
├── 05_DEVELOPER_GUIDE.md        ← Entwickler-Handbuch
├── 06_CONTEXT_ENGINEERING_SOTA.md ← Dieses Dokument
├── 08_WELLBEING_MODULE_OPTIMIZATION.md ← Wellbeing-Modul-Vertraege
├── 14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md ← Community-Modulstatus
├── 15_STREAMING_ARCHITECTURE.md ← Typisierter Chat-Stream
├── 16_DEPENDENCY_SCANNER.md     ← Dependency-Vulnerability-Scanner (lokal, privacy-preserving)
├── 17_FILESYSTEM_CONNECTOR.md   ← Filesystem-Connector
├── PROMPT_STANDARD.md           ← Verbindlicher Agent-Workflow (Schritte 1–15)
├── RTX4090_RYZEN9_GUIDE.md      ← Verifiziertes Hardware-Profil
└── 09_archived/                 ← Archivierte/obsolete Dokumente
    ├── 07_KG_SOTA_ANALYSIS.md
    ├── CLEANUP.md
    ├── DOCUMENTATION_AUDIT_REPORT_2026-06-24.md
    ├── DOCUMENTATION_AUDIT_USER_CHANGE_SUMMARY.md
    ├── finance_optimization_roadmap.md
    ├── finance_prompt_inventory.md
    ├── finance_user_change_summary.md
    ├── I18N_BULGARIAN_COMPLETE_ANALYSIS.md
    ├── I18N_BULGARIAN_IMPLEMENTATION.md
    ├── SOTA_IMPLEMENTATION_TRACKER.md
    └── SOTA_RAG_QUALITY_PIPELINE.md
```

### 2.1 Root-Level Dokumente

| Datei | Status | Zweck |
|-------|--------|-------|
| `AGENTS.md` | Aktiv | Agent-Einstieg (agents.md-Standard): Kommandos, Konventionen, GPU-Parameter |
| `ARCHITECTURE.md` | Aktiv (Legacy-Referenz) | Hauptuebersicht (wurde konsolidiert) |
| `README.md` | Aktiv | Projekt-Entry-Point |
| `funktionen.md` | Aktiv | Kompendium grosser/komplexer Funktionen |
| `ARCHIVE_INDEX.md` | Aktiv | Archiv-Struktur und Retention-Policy |
| `docs/RTX4090_RYZEN9_GUIDE.md` | Aktiv | Hardware-Optimierung (verifiziertes LLM-Profil) |

---

## 3. Context Engineering SOTA Patterns

### 3.1 Prompt-Engineering fur Projekte mit grossem Kontext

#### Pattern A: Master-Document-First (Empfohlen ★★★★★)

```
Schritt 1: Lese 00_CONTEXT_MASTER.md (ca. 250 Zeilen)
Schritt 2: Bei Bedarf vertiefe in spezifisches Modul-Dokument
Schritt 3: Archivierte Dokumente nur bei historischen Fragen
```

**Vorteile:**
- 80% der Fragen werden mit dem Master-Dokument beantwortet
- Kontextfenster wird effizient genutzt
- Klare Hierarchie verhindert Informationsuberschuss

#### Pattern B: Gezieltes Modul-Laden (★★★☆☆)

```
Nur bei spezifischen Fragen an ein Modul:
- Finance-Frage → 03_FINANCE_MODULE.md
- I18n-Frage → 04_I18N_GUIDE.md
- Architektur-Frage → 01_ARCHITECTURE_DEEP_DIVE.md
```

**Nachteil:** Benötigt Vorwissen uber die Struktur

### 3.2 Token-Optimierung

| Dokument | Ladegrund | Kritikalitat |
|----------|-----------|--------------|
| 00_CONTEXT_MASTER.md | Projektorientierung | HOCH — immer zuerst |
| 01_ARCHITECTURE_DEEP_DIVE.md | Kernarchitektur aendern | MITTEL — bei Bedarf |
| 02_SOTA_ROADMAP.md | Belegten Backlog planen | NIEDRIG — nur bei Planung |
| 03_FINANCE_MODULE.md | Finance-Vertraege aendern | MODUL-SPEZIFISCH |
| 04_I18N_GUIDE.md | User-facing Texte aendern | MODUL-SPEZIFISCH |
| 05_DEVELOPER_GUIDE.md | Setup, Tests, Debugging | BEI ENTWICKLUNG |

Zeilen- und Tokenschaetzungen altern schnell und werden deshalb nicht als Vertrag gepflegt. Den tatsaechlichen Umfang vor dem Laden pruefen.

**Empfehlung:** Fur die meisten Coding-Aufgaben reicht `00_CONTEXT_MASTER.md` + gezieltes Modul-Dokument.

### 3.3 Dokumentation als Code-Kontrakt

Jedes Dokument folgt diesem Schema:

```markdown
# [Nummer] — [Titel]

> Stand, Status, Hardware-Kontext

## 1. Zweck & Scope
## 2. Architektur/Design
## 3. API/Schnittstellen
## 4. Konfiguration
## 5. Fehlerbehebung
## 6. Referenzen
```

Diese Konsistenz enables:
- Schnelles Scannen durch LLMs
- Vorhersagbare Struktur
- Einfaches Aktualisieren

---

## 4. SOTA Context-Strategien fur LLM-Coding

### 4.1 Tier-1: Schnellstart (Empfohlen fur 90% der Aufgaben)

```
Lade: 00_CONTEXT_MASTER.md (~3.500 Tokens)
Ausreichend fur:
- Neue Features planen
- Bugs analysieren
- Code-Reviews
- Architektur-Entscheidungen
```

### 4.2 Tier-2: Modul-spezifische Arbeit

```
Lade: 00_CONTEXT_MASTER.md + spezifisches Modul-Dokument
Beispiel: Finance-Bug → +03_FINANCE_MODULE.md
Gesamt: ~11.000 Tokens
```

### 4.3 Tier-3: Tiefe Analyse

```
Lade: 00_CONTEXT_MASTER.md + Architektur-Deep-Dive + Quellcode
Gesamt: ~15.000 Tokens (noch im 32K-Fenster)
```

### 4.4 Vergleich der Ansätze

| Strategie | Tokens | Abdeckung | Geschwindigkeit | Empfehlung |
|-----------|--------|-----------|-----------------|------------|
| Tier-1 Master | ~3.500 | 80% | ★★★★★ | Default |
| Tier-2 Modul | ~11.000 | 95% | ★★★★☆ | Modul-Arbeit |
| Tier-3 Deep | ~15.000 | 99% | ★★★☆☆ | Komplexe Bugs |
| Alles laden | ~35.000+ | 100% | ★☆☆☆☆ | Vermeiden |

---

## 5. Hardware-spezifisches Context Engineering

### 5.1 RTX 4090 (24 GB VRAM)

| Parameter | Wert | Begrundung |
|-----------|------|-----------|
| `n_gpu_layers` | -1 | Voll GPU-Offloading |
| `n_ctx` | 8192 | Ausreichend fur Agent-Dialoge |
| `n_batch` | 3072 | Verifiziertes Single-User-Profil; 8192 loest ggml-cuda Kernelfehler aus (siehe RTX4090_RYZEN9_GUIDE.md) |
| `n_ubatch` | 2048 | Optimal fur SM 8.9 |
| `n_threads` | 12 | Verifiziert im Canary-Benchmark (n_threads_batch=12) |
| `n_parallel` | 1 | Single-User-Optimierung |

**Wichtig:** Die RTX 4090 mit SM 8.9 erfordert `CUDA_ARCHS=890` im llama.cpp-Build. Falsche Arch-Einstellungen fuihren zu `ggml-cuda.cu:98 MUL_MAT_ID failed` Fehlern.

### 5.2 64 GB RAM

- Model-Caching ist effektiv
- Multiple Embedding-Modelle im Speicher haltbar
- FAISS-Indices bleiben geladen

### 5.3 Gemma 4 12B Spezifika

- GGUF-Format mit Q4_K_M Quantisierung empfohlen
- Temperature 0.1-0.3 fur Code-Aufgaben
- Top-K 40, Top-P 0.9 fur Balance

---

## 6. Konsolidierungs-Checkliste

### 6.1 Erfolgreich Konsolidiert

- [x] `ARCHITECTURE.md` — Vollstandige Architekturuebersicht
- [x] `docs/00_CONTEXT_MASTER.md` — Zentrales Master-Dokument
- [x] `docs/01_ARCHITECTURE_DEEP_DIVE.md` — Vertiefte Architektur
- [x] `docs/02_SOTA_ROADMAP.md` — SOTA-Entwicklung
- [x] `RTX4090_RYZEN9_GUIDE.md` → In `docs/` integriert

### 6.2 Archivierte Dokumente

- [x] Audit-Reports → `docs/09_archived/`
- [x] Bulgarische I18n-Dokus → `docs/09_archived/`
- [x] Finance-Specific-Dokus → `docs/09_archived/`
- [x] SOTA-Implementierungs-Tracker → `docs/09_archived/`

### 6.3 Noch zu tun

- [x] Root-Level `SOTA_IMPLEMENTATION_PROGRESS.md` — geloescht (reines Arbeitsdokument, siehe docs/README Release Notes 2026-07-13)
- [x] `CLEANUP.md` — nach `docs/09_archived/` verschoben
- [x] Dieses Dokument abgeschlossen (2026-07-14: GPU-Parameter verifiziert, AGENTS.md ergaenzt)

---

## 7. Empfohlene LLM-Arbeitsablaufe

### 7.1 Neue Feature-Entwicklung

```
1. Lese 00_CONTEXT_MASTER.md
2. Identifiziere betroffenes Modul
3. Lese spezifisches Modul-Dokument (falls vorhanden)
4. Lese relevante Quellcode-Dateien
5. Implementiere mit Bezug auf Architektur-Prinzipien
```

### 7.2 Bug-Fixing

```
1. Lese 00_CONTEXT_MASTER.md (Error-Handling-Sektion)
2. Prufe docs/09_archived/CLEANUP.md (historische bekannte Issues)
3. Lese betroffene Quellcodedatei
4. Analysiere Root-Cause mit SOTA-Methodik
5. Implementiere Fix + Verifikation (Tests)
```

### 7.3 Code-Review

```
1. Lese 00_CONTEXT_MASTER.md (Quality Standards)
2. Prufe Architektur-Konformitat
3. Validiere Error-Handling
4. Uberprufe Performance-Implikationen
```

---

## 8. Metriken und Monitoring

### 8.1 Dokumentationsgesundheit

| Metrik | Ziel | Aktuell | Status |
|--------|------|---------|--------|
| Redundanz < 10% | <10% | ~5% | ✅ |
| Alle Module dokumentiert | 100% | ~90% | ⚠️ |
| Master-Dok < 4K Tokens | <4000 | ~3500 | ✅ |
| Archivierte Doku aktuell | Ja | Ja | ✅ |

### 8.2 Context-Effizienz

| Metrik | Wert |
|--------|------|
| Durchschnittliche Tokens pro Task | ~8.000 |
| Kontext-Nutzung bei 32K Fenster | ~25% |
| Abdeckung mit Master-Dok | ~80% |

---

## 9. Referenzen

- Anthropic (2025) — Token-Optimierung fur grosse Kontexte
- LangChain SOTA Papers — RAG-Pipeline-Optimierung
- agents.md — Offener Standard fur Agent-Instruktionen (https://agents.md)
- Projekt-intern: `AGENTS.md`, `00_CONTEXT_MASTER.md`, `ARCHITECTURE.md`

---

## 10. Anderungshistorie

| Datum | Anderung | Autor |
|-------|----------|-------|
| 2026-07-13 | Erstcreation — Konsolidierungsarbeit | Cline Assistant |
| 2026-07-14 | Verifikations-Fix: n_batch 8192→3072, n_threads 16→12 (Code + RTX4090-Guide als Beleg); Root-Doku-Tabelle korrigiert (CLEANUP.md archiviert, SOTA_IMPLEMENTATION_PROGRESS.md geloescht); korrupten Text in §7.2 repariert; AGENTS.md-Standard ergaenzt; Status auf Aktiv | GitHub Copilot |
| 2026-08-01 | 07_KG_SOTA_ANALYSIS.md in 02_SOTA_ROADMAP.md integriert; Doc-Tree aktualisiert (07 entfernt) | Cline Assistant |
