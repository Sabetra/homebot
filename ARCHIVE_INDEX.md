<!-- last-verified: 2026-09-01 -->
# Archive Index

This file defines the canonical archive structure for historical material.

## Archive Roles

1. docs_archive/
- Historical markdown reports that are not part of active operational docs.
- Legacy gui-related reports and completed mission-style summaries.

2. docs_archive/analysis_artifacts/
- Generated analysis artifacts and cleanup outputs.
- Includes consolidated historical reports moved from old archive locations.

3. archive_obsolete_20260213/
- Obsolete code, migration snippets, and one-off utilities retained for fallback only.

4. archive_old_analysis/
- Deprecated location kept only as pointer for compatibility.

## Retention Policy

- Active documentation belongs in docs/ and top-level runtime docs.
- Historical reports belong in docs_archive/.
- Generated analysis outputs belong in docs_archive/analysis_artifacts/.
- Obsolete code/tooling snapshots remain in archive_obsolete_20260213/.

## Archivierte Workdocs (2026-09-01 / 2026-09-04)

| Datei | Inhalt |
|-------|--------|
| `docs_archive/WORKDOC_SESSION2_IMPORT_REFERENCE_CHECKLIST_20260831.md` | Session-2-Rename (Wellbeing-Repositionierung): verifizierte Import-/Referenz-Checkliste, Triage kritisch/unkritisch, C1–C8-Status, Rollback-Referenzen — finalisiert 2026-09-01 (C6/C7/C8 ausgeführt) |
| `docs_archive/WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` | Scope-B-Rename: Exekutions- & Rollback-Plan, finale Scope-Entscheidung 2026-09-01 (§14: C6 `care_goals` inkl. DB-Migration, C7 `wellbeing_ui.*`, C8 `wellbeing_session_interface.py`, Tier-C-Residual-Risiken) |
| `docs_archive/WORKDOC_LEGAL_DSGVO_PSYCH_20260831.md` | Legal & Compliance CH/EU/DE + Mental-Health-Positionierung (Public Launch): Screening-Instrumente (Option A umgesetzt), Scope-B-Rename B-Phase (ausgeführt, Belege in Scope-B §14), User-facing Content-Neupositionierung — finalisiert 2026-09-01 |
| `docs_archive/WORKDOC_HOMEBOT_RENAME_20260904.md` | Bot6→Homebot-Release (2026-09-04): selektiver aktiver Rename (Code, ENV, Pfade, User-Agent), Live-Migration `bot6_dbs`→`homebot_dbs` (Marker + MOVE, verlustfrei), Commit-Reword + Force-Push (Sabetra-Ident), pip-audit strict (18 Advisories in 3 Pkgs), DoD 8/8 ✅ — abgeschlossen 2026-09-04 |
