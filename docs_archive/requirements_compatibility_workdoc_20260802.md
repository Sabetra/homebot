# Workdoc: Requirements-Vollstaendigkeit und Kompatibilitaet

> **Erstellt:** 2026-08-02
> **Status:** ABGESCHLOSSEN
> **Autor:** GitHub Copilot

---

## Original-Auftrag

pruefe om die <PROJEKT_ROOT>\requirements.txt vollstaendig ist und die Bibliotheken auf dem neuesten STand sind, der es ihnen dennoch erlaubt kompatibel zu sein.

Start implementation

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| Alle produktiven lokalen Features auf Windows 11 / Python 3.12 | Ungepruefte pauschale Major-Upgrades |
| Direkte Runtime- und Dev-Vertraege | Cloud-Abhaengigkeiten im Standardpfad |
| Reproduzierbarer Constraints-Lock | Kopie der historisch gewachsenen venv |
| RTX-4090-CUDA-Vertrag | Erhoehung verifizierter GPU-Parameter |

## Definition of Done

| # | Kriterium | Pruefmethode | Status |
|---|-----------|-------------|--------|
| 1 | Direkte produktive Imports sind deklariert oder bewusst optional | Importaudit + Inspektion | erfuellt |
| 2 | Windows/Python-3.12-Installation ist reproduzierbar | Frische venv + Constraints | erfuellt |
| 3 | Resolver meldet keine Konflikte | `pip check` | erfuellt |
| 4 | OpenCV-Provider sind auf derselben Version und der Upstream-Doppelprovider ist dokumentiert | Metadaten + `cv2`-Import | erfuellt |
| 5 | CUDA-faehige Torch-/llama-cpp-Baseline bleibt erhalten | Metadaten + GPU-Smoke | erfuellt |
| 6 | Fokussierte und vollstaendige Tests bestehen | Pytest / Release-Gates | erfuellt |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationsrisiko | Entscheidung |
|---|--------|-----|--------|-------------|------------|-------------|-------------|-----------------|--------------|
| A | Nur offene Mindestgrenzen | Einfach | Nicht reproduzierbar; Major-Drift | 3/7 | 2/7 | 4/7 | 5/7 | 2/7 | verworfen |
| B | Vollstaendiger Freeze der bestehenden venv | Reproduziert Altbestand | Enthaelt fremde IDE-/Analysepakete | 4/7 | 4/7 | 2/7 | 4/7 | 3/7 | verworfen |
| C | Direkte Bereichsvertraege plus getesteter Constraints-Lock | Lesbar, reproduzierbar, gestuft aktualisierbar | Zusaetzlicher Pflegeprozess | 7/7 | 7/7 | 6/7 | 6/7 | 6/7 | gewaehlt |

> **Auswahl:** Option C. Sie trennt den fachlichen Vertrag vom getesteten Windows/Python-3.12-Resolverzustand.

## Abhaengigkeiten & Stakeholder

| # | Abhaengigkeit / Stakeholder | Art | Impact | Status |
|---|---------------------------|-----|--------|--------|
| 1 | Streamlit / Watchdog | UI und Windows-Dateiwatcher | hoch | App-Smoke bestanden |
| 2 | Torch / llama-cpp-python / CUDA | RTX-4090-Inferenz | kritisch | permanent validiert |
| 3 | NumPy / SciPy / FAISS / OpenCV | Native ABI | hoch | gruppiert getestet |
| 4 | Docling / Pydantic | Dokumentpipeline | hoch | Docling 2.94 getestet |
| 5 | LangChain Core / LangGraph | Psychologische Workflows | mittel | gruppiert getestet |

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | `requirements.txt` hat lokale Nutzerergaenzungen fuer `ddgs` und `tenacity` | `git diff -- requirements.txt` |
| 2 | Ziel-venv enthaelt Streamlit 1.53.0 trotz `streamlit>=1.58.0` | installierte METADATA / Requirements |
| 3 | Ziel-venv enthaelt llama-cpp-python 0.3.20 trotz `>=0.3.23` | site-packages / Requirements |
| 4 | Torch, psutil, NetworkX, Plotly und Pydantic werden direkt produktiv importiert, aber nicht direkt deklariert | Importaudit |
| 5 | Docling/RapidOCR fordert `opencv-python`, EasyOCR fordert `opencv-python-headless`; beide Provider teilen den Namespace `cv2` | installierte METADATA + Importaudit |
| 6 | PyPI-latest ist nicht automatisch kompatibel: Docling, OpenCV, pandas und zentrale Agentpakete haben Major-/API-Risiko | PyPI-Metadaten 2026-08-02 |
| 7 | Die historische venv ist wegen Mistral-Vibe-, OpenTelemetry- und ONNX-Konflikten kein gueltiger Lock-Ursprung | `pip check` in `venv_mistral_gguf` |
| 8 | `aiosqlite` wird von aktiven DB-Modulen direkt importiert und fehlte im Vertrag | Volltest + `database/async_*.py` |
| 9 | PyArrow 24.0.0 emittierte im fokussierten pytest-Lauf eine Windows-Access-Violation; die sicherheitsbereinigte 23.0.1 lief sauber | pytest-Faulthandler + Wiederholung |
| 10 | Der finale Audit meldet nur `diskcache 5.6.3`; weder PyPI noch die Advisory nennen eine Fixversion | `pip-audit` + `pip index versions diskcache` |

## Offene Hypothesen

| # | Hypothese | Status | Falsifizierungs-Test |
|---|-----------|--------|---------------------|
| 1 | Aktuelle Requirements reproduzieren den produktiven Umfang nicht | bestaetigt und behoben | Frische venv, Install, Imports |
| 2 | Konservative Obergrenzen plus Constraints loesen auf Python 3.12 konfliktfrei auf | bestaetigt | Resolver in frischer venv |
| 3 | Streamlit 1.60.0 ist mit App und Watcher kompatibel | bestaetigt | Finance-App-, Feedback-Tab- und Browser-Smoke |
| 4 | Docling 2.94.0 ist die kleinste Version mit allen gemeldeten Fixes und bleibt API-kompatibel | bestaetigt | fokussierte und volle Testsuite |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmassnahme | Status |
|---|--------|-------------------|------------|---------------------|--------|
| 1 | CPU-Wheel ersetzt CUDA-Build | mittel | kritisch | Native Baseline separat pinnen/installieren | gemindert und getestet |
| 2 | Major-Upgrade bricht API | hoch | hoch | Obergrenzen + gestufte Tests | Feedback-Pie angepasst; getestet |
| 3 | NumPy-/FAISS-/OpenCV-ABI-Konflikt | mittel | hoch | Gemeinsame Matrix + Import-/Index-Smoke | getestet |
| 4 | Bestehende Nutzeredits werden ueberschrieben | niedrig | hoch | Diffs lesen, Backups, kleine Patches | aktiv |
| 5 | Lock enthaelt venv-Altlasten | mittel | mittel | Lock nur aus frischer venv erzeugen | getestet |

## Sicherheits- & PII-Implikationen

| # | Aspekt | Implikation | Gegenmassnahme |
|---|--------|-------------|---------------|
| 1 | Local-only-Vertrag | Azure-Pakete im Standardpfad erweitern Cloud-Angriffsoberflaeche | In optionale Cloud-Datei verschieben |
| 2 | Dependency-Sicherheit | Veraltete Pakete koennen CVEs enthalten | Lokaler strikter Vulnerability-Scan; ein Befund ohne verfuegbaren Fix dokumentiert |
| 3 | Finance/Psycho-Daten | Keine Daten duerfen fuer den Audit versendet werden | Nur Paketmetadaten; Tests lokal |

## Aenderungen

| # | Datei | Aenderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | Dieses Workdoc | Audit- und Umsetzungssteuerung | n/a |
| 2 | `requirements.txt` | Direkte Runtime-Vertraege vervollstaendigt und begrenzt | 508 Tests bestanden |
| 3 | `requirements-native-cu124.txt` | RTX-4090-/CUDA-12.4-Wheels getrennt | CUDA und llama.cpp GPU-Offload aktiv |
| 4 | `requirements-optional-cloud.txt` | Azure OCR aus lokalem Standardpfad getrennt | Resolver-Dry-Run |
| 5 | `constraints-win-py312.txt` | Sauberer Windows/Python-3.12-Transitivlock mit Docling 2.94, PyArrow 23.0.1, Transformers 5.14.1 und pypdf 6.14.2 | frische venv, `pip check` sauber |
| 6 | `utils/embedding_singleton.py` | Umbenannte Sentence-Transformers-API verwendet | warnender Einzeltest sauber |
| 7 | `pdf_readability_checker.py` | Ungepflegtes PyPDF2 durch pypdf ersetzt | PDF-Fallback-Smoke sauber |
| 8 | `ui_tabs/feedback_tab.py` | Matplotlib-3.11-`PieContainer` und Legacy-Tupel kompatibel behandelt | fokussierte Tests und realer Feedback-Tab-Smoke |
| 9 | Startskripte und `.vscode/settings.json` | Auf `venv_bot_20260802` umgestellt | PowerShell-Parser, JSON- und Referenzcheck sauber |

## Rollback-Strategie

| Schritt | Aktion | Referenz |
|---------|--------|----------|
| 1 | Geaenderte Dependency-/Setup-Dateien aus datiertem Backup wiederherstellen | `backups/requirements_compatibility_20260802/` |
| 2 | Neu erzeugte Constraints-/Optional-Dateien entfernen | Git-Diff + Backup-Inventar |
| 3 | Produktive venv nicht fuer Resolverexperimente veraendern | Frische temporaere venv verwenden |
| 4 | Bei Laufzeitregression die Startkonfiguration aus `backups/production_venv_switch_20260802/` wiederherstellen | `venv_mistral_gguf` bleibt unveraendert vorhanden |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | Frische Python-3.12-venv + CUDA/Runtime/Dev-Install | erfolgreich | 2026-08-02 |
| 2 | `pip check` in frischer venv | keine gebrochenen Anforderungen | 2026-08-02 |
| 3 | CUDA-/ABI-Import-Smoke | RTX 4090, CUDA 12.4, llama.cpp GPU-Offload aktiv | 2026-08-02 |
| 4 | Dependency-fokussierter pytest-Lauf | 63 bestanden, nach PyArrow-Pin ohne Access-Violation | 2026-08-02 |
| 5 | Vollstaendige Testsuite nach Runtime-Fix und Umschaltung | 510 bestanden | 2026-08-02 |
| 6 | Profile-Synthesis-Fixture `--strict` | Gate bestanden, alle Raten 1.0 | 2026-08-02 |
| 7 | Finaler `pip-audit` | 1 Befund: diskcache 5.6.3, keine Fixversion verfuegbar; CUDA-Wheels nicht per PyPI zuordenbar | 2026-08-02 |
| 8 | Streamlit Finance-App auf `127.0.0.1:8502` | Health 200, Browser ohne Fehler, Finance- und Feedback-Tab gerendert | 2026-08-02 |
| 9 | Permanente venv `venv_bot_20260802` | `pip check` sauber, CUDA 12.4 und llama.cpp GPU-Offload aktiv | 2026-08-02 |

---

> Dieses Workdoc wird waehrend der Umsetzung aktualisiert und nach Abschluss entfernt oder archiviert.
