<!-- last-verified: 2026-09-07 -->
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

## Archivierte Workdocs (2026-09-01 / 2026-09-04 / 2026-09-06 / 2026-09-07 / 2026-09-15)

| Datei | Inhalt |
|-------|--------|
| `docs_archive/WORKDOC_SESSION2_IMPORT_REFERENCE_CHECKLIST_20260831.md` | Session-2-Rename (Wellbeing-Repositionierung): verifizierte Import-/Referenz-Checkliste, Triage kritisch/unkritisch, C1–C8-Status, Rollback-Referenzen — finalisiert 2026-09-01 (C6/C7/C8 ausgeführt) |
| `docs_archive/WORKDOC_SCOPE_B_RENAME_PLAN_20260831.md` | Scope-B-Rename: Exekutions- & Rollback-Plan, finale Scope-Entscheidung 2026-09-01 (§14: C6 `care_goals` inkl. DB-Migration, C7 `wellbeing_ui.*`, C8 `wellbeing_session_interface.py`, Tier-C-Residual-Risiken) |
| `docs_archive/WORKDOC_LEGAL_DSGVO_PSYCH_20260831.md` | Legal & Compliance CH/EU/DE + Mental-Health-Positionierung (Public Launch): Screening-Instrumente (Option A umgesetzt), Scope-B-Rename B-Phase (ausgeführt, Belege in Scope-B §14), User-facing Content-Neupositionierung — finalisiert 2026-09-01 |
| `docs_archive/WORKDOC_HOMEBOT_RENAME_20260904.md` | Bot6→Homebot-Release (2026-09-04): selektiver aktiver Rename (Code, ENV, Pfade, User-Agent), Live-Migration `bot6_dbs`→`homebot_dbs` (Marker + MOVE, verlustfrei), Commit-Reword + Force-Push (Sabetra-Ident), pip-audit strict (18 Advisories in 3 Pkgs), DoD 8/8 ✅ — abgeschlossen 2026-09-04 |
| `docs_archive/WORKDOC_PSYCHO_FINAL_RENAME_20260906.md` | Psycho→Wellbeing Final Rename (Scope-C): finale Residuen-Triage, P0-Broken-Ref-Fixes (provenance, KG-Factory, Adapter-Kwarg), Log-Tag-/Widget-Key-Branding, ENV-Verträge & Legacy-i18n-Keys dokumentiert behalten, 473 Git-Grep-Residuen klassifiziert, 1137/1137 Tests — ERLEDIGT 2026-09-06 |
| `docs_archive/WORKDOC_PUBLIC_LAUNCH_20260831.md` | Public Launch (2026-08-31): Repo-Hygiene, Legal/Compliance, Git-Identität, portable Pfade, DoD-Verifikation — finalisiert 2026-08-31, archiviert 2026-09-07 |
| `docs_archive/WORKDOC_PHASE_E_WELLBEING_RENAME_20260901.md` | Phase-E-Rename (Wellbeing): Verifikationsprotokoll, Rollback-Position — finalisiert 2026-09-01, archiviert 2026-09-07 |
| `docs_archive/WORKDOC_SOTA_ENRICH.md` | SOTA-Risiko-Priorisierung Dependency-Scanner (KEV/EPSS/SSVC): 100/100 Tests ✅ — finalisiert 2026-09-06, archiviert 2026-09-07 |
| `docs_archive/implementation_plan.md` | Public Launch Implementationsplan v1.0.0 (Stufen 0–5) — archiviert 2026-09-07 |
| `docs_archive/WORKDOC_trusted_channels_video.md` | Trusted-Channel-Allowlist & Video-Pipeline (yt-dlp): 32-Channel-Allowlist (UC-ID-SSoT), fail-closed Gate, Subtitle-Safety-Envelope, E2E-Verifikation, Scanner-Policy (bestehende P1 explizit akzeptiert) — finalisiert 2026-09-07, archiviert 2026-09-07 |
| `docs_archive/finance_sota_phase1_workdoc_20260912.md` | Finance SOTA Phase 1 „Monarch-Core“ (2026-09-12): Schedule-first-Hybrid (deterministische Recurring-Anker + OLS-Trend × Monats-Saisonalität + Residual-Bootstrap B=1000, fester Seed), 3 neue Prognose-Tools, UI-Tab „📈 Prognosen“, 26 monarch-core-Tests + breitere Suite 220/220, abgelehnte Varianten (5 Kategorien × 1–7) — finalisiert 2026-09-12, Index-Nachtrag 2026-09-15 |
| `docs_archive/WORKDOC_FINANCE_SOTA_PHASE2.md` | Finance SOTA Phase 2 „Sparziele“ (Goals/Sinking Funds, 2026-09-15): DB-Design-Entscheidungen (5 Kategorien × 1–7), DoD 1–11 (DoD#6 bleibt deklarierter Rest: kein Overlay-Stopp am Zielbetrag/-termin → Phase 3), 10 Goal-Tools, UI-Tab „🎯 Sparziele“ (`_render_goals_tab`), 77 i18n-Keys DE/EN/BG, Tests 43/43 + 52/52 + 57/57 + 1448/1448 Pre-Commit-Suite + Release-Gate grün — ABGESCHLOSSEN 2026-09-15 |
