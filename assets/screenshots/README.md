# Screenshots (Public Launch)

Dieser Ordner nimmt die Launch-Screenshots auf, die in der `README.md`
referenziert werden (Sektion „Screenshots").

## Erwartete Dateien

| Datei | Inhalt |
|---|---|
| `chat.png` | Chat-Tab, 2–3 Beispiel-Nachrichten (DE), Stream-UI sichtbar |
| `finance.png` | Finance-Tab: Query + Ergebnis-Chart, Disclaimer sichtbar |
| `wellbeing.png` | Wellbeing-Tab: Session-View, Disclaimer sichtbar |
| `settings.png` | Settings-Tab: GPU-Platzierung + Modell-Ansicht (ohne lokale Pfade) |
| `rag.png` *(optional)* | Dokumente-Tab mit 1–2 Dummy-Dokumenten |
| `chat_live.gif` *(optional, nur mit expliziter Freigabe)* | Live-LLM-Response, ≤ 10 s, ≤ 8 MB |

## Aufnahme-Regeln

1. **Local-Only-Modus:** App mit `$env:APP_LOCAL_ONLY = "1"` starten.
2. **Nur Demo-Daten:** fiktive Finanzen (z. B. „1.250 € Gehalt, 320 € Miete"),
   neutrales Wellbeing-Session-Beispiel. **Keine echten Finanzdaten, keine echten
   Wellbeing-Daten, keine Namen, keine IDs, keine Kontonummern.**
3. **Auflösung:** 1600×900, Browser-Zoom 100 %, Theme konsistent über alle Bilder.
4. **PII-Sweep vor dem Commit:** Jedes Bild visuell prüfen (Pfade, User-IDs,
   Beträge, Zeitstempel mit echter Uhrzeit).
5. **Größe:** PNGs < 1 MB (ggf. komprimieren), GIF ≤ 8 MB.

## Status

- [ ] `chat.png`
- [ ] `finance.png`
- [ ] `wellbeing.png`
- [ ] `settings.png`
- [ ] `rag.png` (optional)
- [ ] `chat_live.gif` (optional, nur mit expliziter Freigabe)
