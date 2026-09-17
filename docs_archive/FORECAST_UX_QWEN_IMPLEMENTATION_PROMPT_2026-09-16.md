<!-- last-verified: 2026-09-16 -->
# Arbeitsauftrag an Qwen3.8 27B: Finance-Forecast fachlich und ergonomisch ueberarbeiten

Diese gesamte Datei ist der uebergebbare Implementierungs-Prompt. Die Bestandsanalyse ist abgeschlossen; die unten beschriebenen Aenderungen sind noch NICHT implementiert. Code und API-Signaturen vor jeder Etappe erneut lesen, da der Arbeitsstand parallel weiterentwickelt wird.

## 1. Auftrag und Arbeitsmodus

Du arbeitest im Repository `C:\Users\bot6`. Verbessere den Forecast-Subtab im Finance-Tab zu einem nachvollziehbaren, vom Nutzer aktiv pflegbaren Liquiditaets- und Planungstool. Implementiere die unten stehenden Arbeitspakete nacheinander. Liefere nicht nur eine weitere Analyse oder ein Mockup.

Originalauftrag des Nutzers:
> der Forecast-Subtab im Financetab ist nicht Sota und bietet auch zuwenig Eingriffsmöglicheiten z.B. für Korrekturen, Einfügungen etc. für den User. Insgesamt ist die UX nicht gut. Analysiere alles darin und erstell für mein anderes LLM (Qwen3.8 27B) einen Plan, den es dann abarbeiten kann. Gib mir diesen Plan dann als Prompt, den ich ihm übergeben kann.

Produktziel: Der Nutzer kann eine falsche Erkennung berichtigen, eine fehlende Einnahme oder Ausgabe eintragen, einen einzelnen Termin verschieben, eine Serie aendern/pausieren/beenden und Szenarien vergleichen. Jede Aenderung wirkt nachvollziehbar auf Kalender, Monatswerte und Liquiditaet. Importierte Bankfakten bleiben dabei unveraendert.

SOTA bedeutet hier messbare Korrektheit, gute Bedienbarkeit, erklaerbare Annahmen und gepruefte Prognosequalitaet. Es bedeutet NICHT automatisch mehr ML, ein LLM fuer Arithmetik oder einen unbewiesenen Vorsprung gegenueber anderen Produkten.

Arbeitsregeln:
1. Lies zuerst `docs/00_CONTEXT_MASTER.md`, dann `AGENTS.md`, `docs/PROMPT_STANDARD.md` und `docs/03_FINANCE_MODULE.md` Abschnitte 18-20. Bei i18n zusaetzlich `docs/04_I18N_GUIDE.md`. Code ist die massgebliche Quelle.
2. Arbeite ausschliesslich im Projekt-venv `venv_bot_20260802`. Kein System-Python, keine Cloud-LLMs, keine produktiven DB-Schreibtests, kein GPU-/Modellstart. Qwen ist der implementierende Assistent, keine neue Runtime-Abhaengigkeit.
3. Pruefe `git status --short` und die relevanten Diffs. Am Analysedatum existierten bereits fremde Aenderungen in Finance-Code, Schemas, Locales, Tests und Doku. Nichts davon zuruecksetzen. Insbesondere die Korrekturen vom 2026-09-16 erhalten.
4. Backups vor Bearbeitung unter `~\homebot_backups\`, NICHT im Repo. Das widerspruechliche Wort `backups/` im Standard-Prompt ist hier nicht anzuwenden. Produktive DB-Pfade ausschliesslich ueber `utils/db_path_resolver`.
5. Fuehre ein temporaeres Workdoc nach `docs/templates/WORKDOC_TEMPLATE.md`, mit diesen Paket-IDs, Entscheidungen, Hypothesen, Tests und offenen Risiken. Nach Abschluss archivieren oder entfernen. Diese Datei bleibt eine datierte Audit-/Uebergabeaufnahme, keine aktuelle Architektur-SSoT.
6. Pro Paket: lokalen Code lesen, falsifizierbare Hypothese + kontrollierenden Pfad + guenstigsten Test benennen, kleinen Regressionstest/kleine Aenderung machen, unmittelbar validieren. Nicht alle Module vorab neu schreiben. Nach jedem Python-Schreibvorgang sofort `py_compile`, danach den fokussierten Verhaltenstest; JSON sofort parsen.
7. Keine pauschalen Exception-Fallbacks, kein SQL in Renderern, keine vollstaendige Neuausgabe grosser Dateien, keine fachfremden Reparaturen. Fremde Findings separat melden. Oeffentliche APIs nur mit dokumentierter, getesteter Kompatibilitaetsstrategie aendern.
8. Bei Kontextgrenzen: Workdoc mit letzter gruener Pruefung, aktuellem Diff, naechstem Paket und Blockern aktualisieren. Nicht erneut den ganzen Forecast umbauen oder bereits gepruefte Aufgaben wiederholen.
9. Neue Dependencies vermeiden; vorhandene geeignete Bibliotheken wiederverwenden. Falls benoetigt: Lizenz-/AGPL-Kompatibilitaet pruefen, danach Lizenzinventar generieren und strikten Check ausfuehren. Kein Commit, Push oder Branch ohne Auftrag.

## 2. Verifizierter Ausgangspunkt

Quellenangaben sind Stand 2026-09-16; Symbole sind robuster als Zeilennummern.

| Bereich | Vorhandener Code und Bedeutung |
|---|---|
| UI | `finance/tab.py:626`, `_render_forecast_tab`; `:735`, `_render_forecast_bills`; `:762`, `_render_forecast_audit`; `render_finance_tab` bindet den Forecast per `st.tabs` ein. |
| Berechnung | `finance/tools.py`: `_facts_up_to` :609, `_next_due_on_or_after` :637, `_bootstrap_interval` :694, `upcoming_bills` :788, `cash_flow_forecast` :872, `_fit_currency_series`, `_project_months`, `subscription_audit` :1184, `_recurring_groups` :2709. |
| Daten | `finance/db_schema.py`: `_SCHEMA_STATEMENTS`, `_init_schema`, `list_analysis_facts` :2157, `balance_at` :2371, Goals-/Contribution-DAOs. Aktuell keine Forecast-Plan-, Override- oder Szenariotabellen im gelesenen Schema. |
| Vertrage | `agent/tool_schemas.py` :1227/:1257/:1309: drei Forecast-Lesetools. `cash_flow_forecast` liefert `results[*].months`, optional `balance`, optional `goals`. |
| Vorhandene Integration | Goals-Overlay existiert in `cash_flow_forecast(include_goals=True)`, wird im Forecast-Renderer aber nicht angefordert/angezeigt. Caps nach Restbetrag und Zieltermin existieren bereits. |
| Tests | `tests/test_finance_monarch_core.py`, `tests/test_finance_tab_regressions.py`, `tests/test_finance_goals_schema.py`, `tests/test_finance_analytics_tools.py`. Vorhandene AppTest-Muster fuer Goals und Auswertungen wiederverwenden. |
| Darstellung | `i18n/locales/de.json`, `en.json`, `bg.json`, Bereich `finance_ui.forecast`. `_format_eur` formatiert Zahlen ohne Eurozeichen; NICHT faelschlich als festen EUR-Bug beschreiben. |
| Runtime | Streamlit 1.60.0 im Projekt-venv verifiziert. `st.tabs` hat `key` und `on_change`, Default `on_change='ignore'`. Aktuelle Web-APIs trotzdem vor Nutzung lokal pruefen. |

Bereits richtig und zu erhalten: echte `balance_at`-Basis inklusive tatsaechlicher Transferwirkung; Transferausschluss aus dem historischen Cashflow-Fit; getrennte Waehrungsreihen; feste Seeds; Filter gegen zukuenftige Buchungsdaten; getestete Goals-Caps. Kein Rueckfall auf `effective_balance_at` als Bankstand.

## 3. Bestaetigte Findings

Prioritaeten: P1 = fachlich irrefuehrende Werte/Isolation, P2 = Bedienbarkeit und fehlende Steuerung. Kein Nachweis produktiver Datenkorruption durch diesen Audit.

| ID | Prio | Befund, Auswirkung, Evidenz |
|---|---|---|
| F01 | P1 | Kein echter Terminkalender: `upcoming_bills` erzeugt exakt einen naechsten Termin je Gruppe. Auch bei 180 Tagen ist `total_in_window` nur deren Summe. `_next_due_on_or_after` behandelt alles monatlich; quartalsweise, jaehrliche und wochenbasierte Serien werden fachlich falsch dargestellt. |
| F02 | P1 | Abo-Audit nennt den Betrag pro Zahlung `monthly_cost` und multipliziert ihn mit 12. Synthetische Zahlungen 120 am 15.05. und 15.08. ergeben 120/Monat und 1.440/Jahr, naechster Termin 15.09. Eine bestaetigte Quartalsserie muss 40/Monat und 480/Jahr sowie 15.11. liefern. Zwei Beobachtungen allein beweisen dabei keinen Rhythmus. |
| F03 | P1 | `upcoming_bills`/`subscription_audit` sammeln Detailhistorie nur per Empfaengername und Vorzeichen, ohne Waehrungsfilter. Gleicher Empfaenger: CHF 30 am 03.01./03.02., EUR 90 am 20.01./20.02. Beide Serien bekommen 12.03.; beim CHF-Audit stehen letzter Betrag 90 und letzter Tag 20.02. `list_analysis_facts` gibt zudem keine Konto-ID zurueck, `_recurring_groups` gruppiert nur nach Waehrung/Name: gleichnamige Vertraege verschiedener Konten sind nicht getrennt identifizierbar. |
| F04 | P1 | `_recurring_groups` nimmt Absolutbetraege beider Vorzeichen. Zwei Ausgaben zu 30 plus positive Erstattung 300 ergeben als kommende Rechnung 120. Einnahme/Erstattung und Ausgabevertrag muessen getrennt behandelt werden. |
| F05 | P1 | Der unvollstaendige laufende Monat wird als voller Monat mit fehlenden Buchungen = 0 in OLS aufgenommen. Acht Gehaelter zu 5.000 am 25.01.-25.08. ergeben am 31.08. weiter 5.000; am 02.09. prognostiziert der Code 2.777,78 fuer den ersten Zukunftsmonat. Keine Warnung. Datenluecken und echte Nullmonate sind ebenfalls nicht unterscheidbar. |
| F06 | P1 | Guthaben startet am Referenztag, `months` aber erst im Folgemonat. Erwartete Zahlungen im Rest des aktuellen Monats fehlen. Die reale Startsaldo-Korrektur vom 16.09. loest diese separate Zeitluecke nicht. |
| F07 | P1 | `_bootstrap_interval` zieht StichprobenMITTEL von Residuen, nicht zukuenftige Zahlungs-/Monatsfehler. Bei 100 Residuen aus +/-100 entsteht um 1.000 ein 80%-Band [988, 1012]. `_project_months` addiert jede Monatsbandbreite nur zum vorigen PUNKT-Kontostand, ohne kumulierte Unsicherheit. Synthetische Guthabenbandbreiten bleiben ueber sechs Monate exakt 48. Das ist keine kalibrierte Mehrschritt-Liquiditaetsprognose. |
| F08 | P1 | Der Forecast nutzt eine geglaettete historische Monatsrate fuer konstante Ausgaben, nicht die Termine aus `upcoming_bills`. Gehalt wird statistisch gefittet. Aktuelle Preiswechsel, kuendigungsbedingte Enden und Einmalzahlungen bilden keinen konsistenten gemeinsamen Plan. Zwei zufaellige gleiche Ausgaben reichen fuer den deterministischen Anteil; Frequenz/Recency wird dort nicht geprueft. |
| F09 | P2 | Drei reine `st.dataframe`-Tabellen, keine Anlage/Bearbeitung/Bestaetigung/Ablehnung, kein Skip/Pause/Ende, kein Rueckgaengig, keine Szenarien und kein Drilldown zu Ursprungsbuchungen. Fehlklassifizierungen lassen sich hier nicht beheben. |
| F10 | P2 | Standardauswahl 'Alle Konten' liefert keine Balance-Kurve, selbst mit nur einem Konto. Ohne Balance existiert auch keine alternative Cashflow-Grafik. Optionen sind rohe IBANs; keine Mehrfachauswahl und keine explizite Waehrungsauswahl. |
| F11 | P2 | Chart fuegt den Startsaldo unter dem ersten Prognosemonat ein: im AppTest `['2026-10', '2026-10', '2026-11', ...]`. Startdatum, Datenstand, niedrigster Liquiditaetspunkt, Sicherheitspuffer und nachvollziehbare Treiber fehlen. Zahlen/Datumswerte sind fuer Tabellen bereits zu Strings formatiert. |
| F12 | P2 | Bills/Audit behandeln `success=False` wie 'keine Daten'. Ein Forecast-Fehler beendet den gesamten Renderer vor den beiden anderen Bereichen. Auch ohne Historie gibt es keinen Weg, einen manuellen Plan aufzubauen. |
| F13 | P2 | `include_goals` existiert im Backend, fehlt in der UI. Bankguthaben und nach Reservierungen verfuegbarer Betrag sind nicht getrennt sichtbar. Ein Overlay einfach als echten Bankabfluss anzuzeigen waere ohne Transfer-/Reservierungssemantik falsch. |
| F14 | P2 | Kein angezeigter Nachweis fuer Datenabdeckung/Guete; Kurzhistorienwarnung wird in Doku behauptet, im geprueften Kurzhistorienfall war `notes=[]`. Nicht jede Prozentzahl ist eine statistisch gedeckte Sicherheit. Backend-Notizen kommen teilweise als deutsche Freitexte direkt in die UI; Zahlenformat ist sprachunabhaengig deutsch. |
| F15 | P2 | Jeder Renderer-Durchlauf ruft Forecast, Bills und Audit separat auf; `_facts_up_to` liest historische Fakten erneut und filtert Datum erst danach. AppTest: drei Reads initial, sechs nach einem sachfremden Rerun. Haupttabs werden ohne Lazy-Gate gerendert. Laufzeitproblem ist strukturell belegt, Produktionslatenz wurde NICHT gemessen. |
| F16 | P2 | Bestehende Tests sichern teils problematische Annahmen ab, z.B. Lebensmittel als faellige Rechnung und besondere Referenztagbehandlung fuer Monatsende. Keine Forecast-Bearbeitungsklickfolgen in der gelesenen Tab-Suite; kein Forecast-Backtesting in den gelesenen Finance-Pfaden. Gruene Tests sind hier kein Qualitaetsnachweis der Vorhersage. |

Zusaetzlich vor Umsetzung gezielt pruefen, NICHT als bereits reproduzierte Fehler behandeln: Saldo ohne belegten Anfangsanker; veraltete/gekuendigte Serien; Scheinsaisonalitaet aus nur einem Jahreszyklus; Ueberextrapolation ueber 24 Monate; heutige Kategorie-/Transfer-/Planrevisionen in historischen Backtests. `balance_at` faellt bei fehlendem Anfangssaldo auf Transaktionssumme zurueck und liefert bisher keine Herkunfts-/Abdeckungsmetadaten.

## 4. Ziel-UX und Nutzerhandlungen

Baue innerhalb des bestehenden Streamlit-Designs einen Arbeitsbereich, keine Landingpage und keine neue Frontend-Plattform.

Oben: gut lesbare Kontoauswahl (Bank/Kontoname, gekuerzte IBAN; stabile interne IDs), Waehrung, Horizont, Szenario, Referenzdatum/Datenstand und klare Aktion 'Zahlung planen'. Konto- und Waehrungswechsel duerfen Entwuerfe nicht auf ein anderes Konto anwenden. Bei nur einem Konto sinnvoll vorbelegen.

Primaere Ansicht 'Ueberblick': echte Startliquiditaet mit Herkunft/Datum, niedrigster erwarteter Stand mit Datum, Abstand zum benutzerdefinierten Puffer, Netto im Zeitraum; danach Zeitachse mit Ist-/Prognosegrenze, Plan und Unsicherheitsband. Ohne belastbaren Startsaldo Cashflow anzeigen, keinen erfundenen Kontostand. Haushaltssummen nur fuer ausgewaehlte Konten derselben Waehrung.

Weitere Ansichten innerhalb des Forecasts: 'Zahlungsplan', 'Erkennungen', 'Szenarien'. Detailparameter und Prognoseguete unter 'Annahmen & Datenqualitaet'. Keine drei langen Pflicht-Tabellen untereinander und keine zweite globale Navigation bauen. Monatsdetail und Quellbuchungen per gezielter Auswahl/Detailansicht erreichbar machen.

Pflichtaktionen:
- Zahlung anlegen: Einnahme, Ausgabe, interner Transfer; einmalig oder wiederkehrend; Konto, Waehrung, Betrag, Kategorie, Empfaenger, Datum, Rhythmus, Beginn/Ende, Notiz. Transfer nur zwischen passenden Konten mit eindeutiger Waehrungssemantik.
- Erkennung pruefen: Quellen und vorgeschlagenen Rhythmus sehen; bestaetigen, Betrag/Datum/Frequenz/Typ/Kategorie/Kontozuordnung korrigieren oder ablehnen. Historische Regelmaessigkeit als 'erkannt', nicht als gesicherte Zahlungsverpflichtung darstellen.
- Eine Serie bearbeiten: explizite Reichweite 'nur dieser Termin', 'ab diesem Termin', 'ganze Serie'. Bei historischen Terminen keine stillen rueckwirkenden Aenderungen.
- Ein Vorkommen verschieben, Betrag korrigieren oder auslassen; Serie pausieren/wiederaufnehmen/beenden. 'Im Plan beendet' bedeutet NICHT beim Anbieter gekuendigt.
- Aus einem echten Buchungsbeleg eine Regel erzeugen; Quellen schreibgeschuetzt anzeigen. Kategoriepflege bestehend wiederverwenden. Fehlerhafte importierte Betraege separat ueber einen ausdruecklichen Korrekturworkflow bearbeiten, niemals durch Forecast-Speichern.
- Entwurf mit sichtbarer Auswirkung auf Baseline/Saldo pruefen, speichern oder verwerfen. Aenderungshistorie und gezieltes Rueckgaengig anbieten. Ein zweiter Speicherklick erzeugt keine zweite Zahlung.
- Geplanten Termin mit einer importierten Ist-Buchung verknuepfen/entknuepfen. 'Erledigt' ohne Bankbeleg ist hoechstens Nutzerstatus, keine erfundene Ist-Buchung und kein Eingriff in `balance_at`.
- Szenario erstellen/duplizieren/umbenennen/loeschen, zwischen Basis und Szenario wechseln. Beispiel: Gehalt ab November +200, variable Lebensmittelausgaben -10%, Einmalreparatur 600, Abo-Ende ab Datum.
- Filter nach Zeitraum, Konto, Waehrung, Typ, Kategorie, Status und Empfaenger; Zahlen numerisch sortierbar. Optionaler lokaler Export des aktiven Plans samt Annahmen/Datenstand, keine automatische Ablage privater Reports im Repo.

Interaktionen: echte Formulare mit Speichern/Abbrechen fuer fachliche Aenderungen, Inline-Editor nur fuer geeignete Felder. Tabellenposition oder uebersetzter Labeltext ist keine Identitaet. Icons mit zugaenglichem Namen/Tooltip, sichtbarer Fokus, Bedienbarkeit per Tastatur; Fehlermeldungen am Feld. Bei engen Viewports umgebrochene Controls und eine kompakte Detailansicht statt gequetschter Formularspalten. Keine dekorativen Bilder in der Finanzarbeitsflaeche, Charts dienen den realen Daten.

## 5. Architekturentscheidung und fachliche Invarianten

Bewertung 1-7, hoeher ist besser; Migrationssicherheit bedeutet weniger Risiko. Dies sind begruendete Designurteile, keine gemessenen Benchmarks.

| Option | Korrektheit | Robustheit | Wartbarkeit | Performance | Migrationssicherheit | Entscheidung |
|---|---:|---:|---:|---:|---:|---|
| A: wenige Forecast-Overrides direkt in vorhandene Monatslogik integrieren | 3 | 3 | 5 | 6 | 6 | Als kurzer Hotfix moeglich, aber kein konsistenter Termin-/Serien-/Szenariovertrag; behebt F01/F06/F08 nicht ausreichend. |
| B: separate Planpersistenz + ein gemeinsamer, reiner Projektionskern + vorhandene UI/Tool-Adapter | 6 | 6 | 6 | 5 | 5 | Gewaehlt: kleinere additive Migration, bankfaktentreu, testbar; etwas mehr Schema-/Cache-Arbeit notwendig. |

Kein Komplett-Rewrite. Ein begrenztes neues Forecast-Fachmodul ist sinnvoll, wenn damit Terminexpansion/Projektion aus dem grossen `FinanceTools`-Modul getrennt und wirklich gemeinsam genutzt wird. Keine zweite konkurrierende FinanceDB oder Kategorie-/Goals-Implementierung.

### 5.1 Persistenzvertrag

Lege vor dem SQL eine kompakte Vertragsmatrix fest. Namen sind Vorschlaege, noch keine existierenden APIs:
- Planposition/Serie: stabile ID, Konto-FK, Waehrung, Richtung/Typ, Betrag in Cents, Kategorie-FK, Titel/Empfaenger, Quelltyp, bestaetigt/abgelehnt/aktiv/pausiert/beendet, fachlich wirksame Daten, Wiederholungsdefinition, Revision und Zeitstempel.
- Rhythmus strukturiert validieren: einmalig, woechentlich, alle N Wochen, monatlich, alle N Monate, jaehrlich. Kalendermonat ist nicht 30 Tage. Zweiwoechentlich ist nicht zweimal monatlich. Urspruenglichen Ankertag separat erhalten.
- Ausnahme pro Serien-ID + ORIGINAL-Termin: verschoben, Betrag ersetzt oder ausgelassen. Verschieben aendert nicht die Identitaet; erneute Expansion erzeugt weder Dublette noch Verlust der Ausnahme.
- Quellen-/Ist-Zuordnung mit expliziten Kardinalitaeten; bei vorerst nur 1:1 Abgleich mehrdeutige oder gesplittete Treffer nicht automatisch verknuepfen. Reimport, Loeschen eines Belegs und stabile Wiederzuordnung definieren, ohne globale Reimport-Reparatur zu starten.
- Abgelehnte Erkennungen stabil speichern, damit derselbe Kandidat beim naechsten Scan nicht erneut erscheint. Fingerprint aus fachlichem Scope und Quellen, nicht Zeilenindex oder mutablem Anzeigetext; bei neuen/verwaisten Quellen einen sichtbaren Pruefstatus erzeugen.
- Szenario als benannte Menge zeitlich begrenzter Plan-/Annahmenaenderungen, mit eigener ID und Baseline-Revision. Keine Kopie oder Mutation der Ist-Transaktionen. 'In Basis uebernehmen' nur explizit mit Diff, Validierung und einem atomaren Save.
- Aenderungsjournal mit Vorher/Nachher, Revision und Nutzeraktion; Rueckgaengig als gezielte Gegenrevision. Keine globalen SQLite-Backups zur Laufzeit zurueckspielen. Bei veralteter Revision Konflikt melden statt neue Aenderungen zu ueberschreiben.

Idempotente additive Schema-Migration, notwendige Indizes, FK-/CHECK-/UNIQUE-Constraints und Transaktionen ueber alle zusammengehoerigen Schreibvorgaenge. Geld intern in Cents; Float erst an bestehenden Ausgabegrenzen. Keine Planpositionen in `transactions` einschleusen. Ein manueller Startsaldo ist eine datierte, sichtbare Prognoseannahme, keine Aenderung der Bankhistorie.

### 5.2 Gemeinsame Berechnung

Eine kanonische Folge von Planvorkommen speist Kalender, Rechnungsfenster, Audit, Tages-/Monatsaggregation, Szenarien und Charts. In jedem Beitrag Ursprung (Ist/erkannt/manuell/Szenario/variable Schaetzung/Zielreservierung) und Quell-ID mitfuehren.

Prioritaet: Bankfakten bestimmen vergangene Ist-Staende; ein verknuepftes Ist ersetzt das betreffende Planvorkommen. Fuer zukuenftige Planung gilt Szenario-Ausnahme vor manueller Ausnahme vor bestaetigter Serie vor automatischer Schaetzung. Ist-Daten werden nie durch Szenarien ersetzt. Ueberlagerungen fachlich benennen, nicht blind Summen addieren.

Doppelzaehlung verhindern: Historische Buchungen, deren Kosten durch einen expliziten Plan modelliert werden, genau einmal aus dem statistischen Rest entfernen. Eine neue Serie darf nicht zusaetzlich zu ihrem bereits gefitteten historischen Pendant erscheinen. Eine Kategorienannahme ersetzt oder skaliert ihren definierten Restanteil und addiert nicht noch einmal das Gesamtbudget.

Zeitvertrag: `balance_at(reference_date)` ist End-of-Day inklusive aller bekannten Buchungen dieses Tages. Standardprojektion beginnt am naechsten Tag und schliesst den Restmonat ein; Datum und verbleibenden Zeitraum offen anzeigen. Tagesgrenzen, Termin am Referenztag, schon gebuchte Faelligkeit und Ueberfaelligkeit explizit testen. Keine Sonderregel 'Tag <=28 heute, Tag >=29 erst Folgemonat' ohne fachlichen Statusgrund.

Tagesgenau heisst bei erkannten/manuellen Faelligkeiten echte Termine. Variable Monatsausgaben duerfen nur mit offengelegter Verteilungsannahme auf Tage verteilt werden; keine scheinexakten Tagesrisiken aus Monatsdaten versprechen. Monatswerte aus denselben Tages-/Ereignisbeitraegen ableiten. API-Erweiterungen fuer Restmonat/Tagesreihe additiv versionieren; vorhandene Monatsfelder nicht still umdeuten.

### 5.3 Transfers, Goals und Waehrungen

Ein interner Transfer veraendert den Stand des Quell- und Zielkontos, nicht externe Einnahmen/Ausgaben des Haushalts. In konsolidierter Gleichwaehrungsansicht netto null, im Einzelkonto relevant. Timing-Luecken als Geld unterwegs kennzeichnen. Kreditkartenumsatz und spaetere Kartenabrechnung nicht doppelt als Haushaltsausgabe behandeln; bestehende Settlement-/Transfervertraege lesen und erhalten. Keine automatische FX-Konversion ohne vorhandene belegte Kursdaten.

Goals wiederverwenden: bestehende Caps, Status und Faelligkeit erhalten. 'Reserviert/verfuegbar nach Sparzielen' getrennt von echtem Bankguthaben fuehren. Erst eine explizit modellierte Ueberweisung erzeugt Bankabfluss, im Haushalt ggf. Gegenbein. Dieselbe Sparrate nicht als Zielreservierung UND nochmals als Ausgabe abziehen. Bestehenden `include_goals`-API-Vertrag nicht still brechen; neue Kennzahlen/Version und Tests fuer die neue Semantik schaffen.

### 5.4 Prognosequalitaet

Training standardmaessig auf vollstaendigen abgeschlossenen Monaten mit Abdeckungsstatus; laufenden Monat separat als Ist-bis-heute/Restmonat behandeln. Fehlende Importe sind unbekannt, nicht automatisch Null. Datenstand, letzter Beleg, Anfangssaldoquelle, Vollstaendigkeit und verwendete Monate bereitstellen. Manuelle reine Planung muss auch ohne statistische Historie funktionieren.

Automatische Serienerkennung nutzt Konto, Waehrung, Richtung, Empfaenger und belegte Periodizitaet; Betragstoleranz, Preiswechsel, Ausreisser und letzte Sichtung nachvollziehbar behandeln. Zwei Buchungen erzeugen einen pruefbaren Vorschlag, keinen sicheren Vertrag. Variable wiederholte Einkaeufe nicht als feste Rechnung deklarieren. Erkennung darf einen bestaetigten Nutzerplan nicht unbemerkt zuruecksetzen.

Statistischer Rest: robuste einfache Baseline (z.B. Median/gedaempfter Trend) gegen bisherigen OLS-/Saisonalitaetsansatz vergleichen. Saisonalitaet nur mit ausreichender Historie und positiver Backtest-Evidenz, nicht bloss zwoelf gesehenen Monatsnamen. Keine neue Prophet-/ARIMA-/LLM-Pflichtbibliothek.

Unsicherheit als Prognoseintervall, nicht als beliebiges 'Konfidenzband': zukuenftige Residuen-/Cashflow-Pfade simulieren und jeden Kontostandspfad kumulieren, dann Quantile bilden. Historisch zusammengehoerige Einnahme-/Ausgabefehler bei Bedarf gemeinsam ziehen; keine ungewollte perfekte Korrelation durch identische Seed-Neustarts. Autokorrelation/kurze Historie pruefen und Grenzen melden. Reproduzierbarer Seed aus expliziter Konfiguration; gleiche Eingaben ergeben gleiche Resultate.

Rolling-Origin-Backtests mit zeitlichem Train/Test-Split, nur damals verfuegbaren Fakten/Planrevisionen. Ist-Rueckrechnung mit spaeter importierten Daten ggf. als solche ausweisen, nicht als echte damalige Vorhersage. MAE/Bias, geeignete skalierte Fehler nur bei definiertem Nenner, Intervallabdeckung und Breite nach Horizont berichten. Keine MAPE-Pflicht bei Null-/negativen Nettowerten. Keine stochastische Unsicherheit aus Nullhistorie erfinden; 'nicht schaetzbar' ist zulaessig.

## 6. Arbeitspakete in verbindlicher Reihenfolge

Jedes Paket endet mit kleiner Demo/Klickfolge oder reproduzierbarem Test, dokumentiertem Ergebnis und gruener relevanter Regression. Nicht erst die komplette Engine bauen und die Bedienbarkeit bis zum Schluss vertagen.

### AP0: Ausgangsbasis und Reproduktionen

Vorhandene Diffs lesen, Snapshot/Backups vorbereiten, Workdoc anlegen. F01-F08 und F10-F12 als fokussierte Tests mit synthetischen Daten charakterisieren. Fehlerhaftes Altverhalten als Befund dokumentieren, nicht als neue dauerhafte Soll-Assertion verewigen. Bestehende Folgemonat-/Goals-/Transfer-Vertraege festhalten. Bill-/Auditfehler von leeren Ergebnissen unterscheiden und unzureichende Prognosebasis sichtbar kennzeichnen; kein 'gueltiges Sicherheitsband' behaupten, solange F07 offen ist.

Gate: Ursachen reproduziert, Testkommandos verifiziert, produktive DB unberuehrt. Keine Produktivmigration.

### AP1: Kleine vertikale Strecke fuer manuelle Einmalplanung

Persistenzkern + reine Projektion + UI-Anlage/Bearbeitung/Loeschbestaetigung fuer eine datierte einmalige Einnahme/Ausgabe, mit Vorschau/Speichern/Verwerfen. Konto/Waehrung, Cents, Quelltyp, Version und Audit von Anfang an. Gemeinsame Ereignisaggregation einfuehren; UI und Lesetools verwenden denselben Pfad. Restmonat und korrekte Chart-Zeitachse anschliessen. Ohne Historie manuelle Planung erlauben; ohne Anfangssaldo nur Cashflow, nicht Fake-Liquiditaet.

Gate: '600 Ausgabe am gewaehlten Datum anlegen -> Vorschau -> speichern -> Rerun -> bearbeiten -> abbrechen -> erneut laden -> rueckgaengig' funktioniert; Banktabellen unveraendert, kein Doppelinsert und keine kontofremden Effekte.

### AP2: Serien, Erkennungen und Ist-Abgleich

Serienexpansion, Ausnahmen und Geltungsbereiche implementieren. Dann Erkennungskandidaten mit Quellen, Bestaetigen/Ablehnen/Korrigieren sowie Pause/Ende bauen. Kalender listet ALLE Vorkommen im Fenster. Abo-Monatsaequivalent und echte Faelligkeitssumme unterscheiden. Zahlungsbetrag/aktueller Preis, jaehrliche Rate und kalenderjahrbezogene Summe sauber beschriften. F01-F04/F08 am gemeinsamen Modell beheben, nicht nur Tabellenzahlen korrigieren.

Ist-Matching mit konservativen Kandidaten und Nutzerbestaetigung; Mehrdeutigkeit melden. Reimport darf manuelle Serie/Ausnahme nicht loeschen. Ausschluss vom statistischen Rest und stabile Quellenbindung testen. Wiederkehrende Einnahmen wie Gehalt explizit planbar machen.

Gate: bestaetigte Quartalsrechnung liefert die richtigen Vorkommen/Kosten; Skip/Verschieben aendert Kalender, Monatsnetto und Saldo exakt einmal; gleiche Empfaenger verschiedener Konten/Waehrungen bleiben isoliert.

### AP3: Belastbare Liquiditaet und statistischer Rest

F05/F06/F07 endgueltig beheben: Abdeckung, abgeschlossene Trainingsmonate, konservative Modellwahl, path-basierte Unsicherheit. Startsaldo-Herkunft und Wissensluecken abbilden. Rolling-Origin-Evaluation auf synthetischen Fixtures und optional ausdruecklich freigegebenem lokalem Snapshot; keine privaten Resultate im Repo. Liquiditaetspuffer, Tiefstpunkt und historische/erwartete Beitraege aus derselben Serie ableiten.

Gate: Gehalts-/Teilmonatsgegenprobe ohne kuenstlichen Einbruch; Backtest gegen einfache Baseline; deterministische Intervalle mit gemessener Coverage. Keine erfundene Qualitaetsgarantie, wenn Historie zu kurz ist.

### AP4: Szenarien, Goals und Transferplanung

Szenarios als isolierte Overrides auf eingefrorener/referenzierter Baseline; speichern, vergleichen, duplizieren, ruecksetzen. Delta nach Datum, Monat, Kategorie und niedrigstem Stand ausgeben. Baseline-Aenderung als Konflikt/Rebase-Bedarf sichtbar machen. Kategorienannahmen zeitlich/scopeseitig validieren; keine doppelte Anwendung.

Goals in der UI aktivierbar machen, aber Bankstand, Reservierung und frei verfuegbaren Betrag trennen. Explizite geplante Transfers mit Gegenbein und Kontosummen ergaenzen; Settlement-Doppelzaehlung verhindern. Bei Nichtverfuegbarkeit sauber kennzeichnen, kein synthetisches Gegenkonto erfinden.

Gate: Szenario aendert weder Ist noch Basis; Ziel- und Transferfaelle aus Abschnitt 7 bestehen; Zurueckwechseln liefert exakt die Baseline.

### AP5: Fertige UX, Fehlerzustaende, i18n und Laufzeit

Die bereits angeschlossenen Formulare/Views konsistent zum Zielbild ordnen. Keine Ergebnis-JSONs als Endnutzeroberflaeche. Numerische/date-typisierte Tabellen, Detailansicht der Beitraege, Status/Quellen, Tastatur und mobile Ansicht fertigstellen. Lange Namen/Waehrungen/negative Werte testen. Alle Nutzertexte und strukturierten Warncodes DE/EN/BG lokalisieren; Fehler getrennt von 'keine Termine', 'keine Historie', 'kein Kontostand' und 'keine Erkennungen'. Ein defekter Statistikzweig darf die manuelle Planung nicht blockieren.

Reruns kontrollieren: ein konsistenter Faktensnapshot pro Berechnung; Zeitraum/Konto moeglichst schon in SQL eingrenzen. Cache/Session-Ergebnisse nach Datenrevision, Planrevision, Konto-/Waehrungsscope, Stichtag, Parametern und Szenario invalidieren. Auch Import, Kategorie-, Goal- und Transferaenderung beruecksichtigen, nicht nur maximale Transaktions-ID. Keine global zwischen Nutzern/Scopes geteilten Entwuerfe.

Installierte Streamlit-API fuer `st.tabs(on_change='rerun')`/`.open` oder lokale View-Selektion/Fragments pruefen. Nur noetige Haupttab-Verdrahtung aendern, keine Navigation aller Module ungefragt ersetzen. 'Berechnen' bei teuren Parametern im Formular; eine Eingabe darf nicht den gesamten Backend-Stack mehrfach neu fitten. Vorher/Nachher-Read-Counts und Laufzeit messen; konservatives Ziel fuer synthetische 10k Fakten/24 Monate: warmer UI-Wechsel ohne Neufit, explizite Neuberechnung nach Moeglichkeit unter 2 s auf dem Zielrechner. Messaufbau nennen, keine ungepruefte Garantie.

Gate: echte AppTest-Klickfolgen, frische Daten nach Save, keine Daten-/Entwurfsuebernahme zwischen Konten oder Szenarien, visuelle Desktop-/Schmalbildpruefung und erklaerte Laufzeitmessung.

### AP6: Integration, Dokumentation und Abschluss

Vorhandene drei Lesetools auf konsistente Ergebnisse pruefen. Benoetigte optionale Parameter/Resultate in kanonischen Schemas und Konsumenten angleichen; `results[*].months`, Cents-/Ausgabeeinheiten und bestehende Defaults explizit testen. Nicht automatisch Forecast-Schreibtools fuer den autonomen Chat/ReAct freigeben. UI-Schreiben ueber validierten Service reicht. Bei dennoch noetiger Tool-Erweiterung gesamte Schema-/Dispatch-/Profil-/Planner-/Reflector-Kette gezielt pruefen, keine zweite SSoT erfinden.

Aktualisiere `docs/03_FINANCE_MODULE.md` mit tatsaechlichen Vertraegen/Grenzen, `funktionen.md` fuer grosse Funktionen und `00_CONTEXT_MASTER.md` nur fuer dauerhafte Aenderungen. Neue aktive Doku nur bei Bedarf und mit Indexeintrag. Keine frueheren 'SOTA'-Behauptungen ungeprueft fortschreiben.

Gate: Abschnitt 7 abgenommen; fokussierte und betroffene Integrationssuiten gruen, eigene Artefakte bereinigt, Diff ohne private Daten. Abschluss: sichtbare Nutzerverbesserungen, geaenderte Dateien, Tests/Messungen, Migration/Rollback, ehrlich offene Grenzen. Kein blosses 'fertig' bei nicht verdrahteten Formularen.

## 7. Abnahmekatalog

Diese Kriterien sind die Definition of Done. Neue Tests bevorzugt in vorhandene passende Suites; ein eigenes Forecast-Fachmodul darf eine gezielte neue Suite erhalten. Kein Testfile pro Einzelfall.

| ID | Akzeptanztest |
|---|---|
| T01 | Monatsserie 100, Referenz 2026-08-28, Anker 03, Fenster bis einschliesslich 2027-02-24: 6 Termine, Summe 600; identische Expansion bleibt identisch. |
| T02 | Bestaetigte Quartalsserie 120 mit letztem Termin 2026-08-15: naechste Termine 2026-11-15 und 2027-02-15, 180-Tage-Summe 240, Monatsaequivalent 40, Jahresaequivalent 480. Jaehliche/woechentliche/14-taegige Serien separat pruefen. |
| T03 | Anker 31 bleibt nach Februar im Maerz 31; Schaltjahr, Jahreswechsel, Beginn/Ende, Referenztag, Pause, Wiederaufnahme und nur-ein-Termin-Ausnahme. Monatsende nicht rekursiv auf 28 verschieben. |
| T04 | Gleicher Empfaenger CHF 30 am 03. und EUR 90 am 20. bleibt in Terminen, letztem Betrag und Quellen getrennt; zusaetzlich zwei Konten derselben Waehrung und zwei Vertraege desselben Empfaengers. |
| T05 | Zwei Ausgaben zu 30 und positive Erstattung 300 erzeugen keine Rechnung zu 120. Rueckerstattung wird korrekt klassifiziert, nicht als laufender Vertragsbetrag. |
| T06 | Acht vollstaendige Monate Gehalt 5.000, neuer Monat am Tag 2 noch ohne Gehalt: kein Nullmonat im Training; Restmonatsgehalt terminiert oder nachvollziehbar geschaetzt. Ohne genug Daten keine Scheinsicherheit. |
| T07 | Startsaldo Ende 16.09. = 1.000, einzig verbleibender Abfluss 600 am 20.09.: Ende September 400. Startpunkt Chart = 16.09., kein doppeltes Oktoberlabel; Monats-/Tages-/Kalendersummen konsistent. |
| T08 | Monatsresiduen +/-100: Prognoseintervall darf nicht auf die Streuung des Stichprobenmittels schrumpfen. Kumulierte Unsicherheit mit unabhaengigen nichtdegenerierten Fehlern waechst erwartungsgemaess; keine pauschale Monotoniepflicht fuer beliebige Modelle. Coverage bei bekannter synthetischer Verteilung mit vorab festgelegter statistischer Toleranz pruefen. |
| T09 | Zukuenftige Ist-Buchungen aendern Forecast zum frueheren Stichtag nicht; explizite ZukunftsPLAENE duerfen ihn aendern. Historische Backtests verwenden keine spaeter bestaetigten Planrevisionen als damaliges Wissen. |
| T10 | Einmalplanung: anlegen, Vorschau, speichern, neu rendern, editieren, abbrechen, loeschen mit Bestaetigung, Undo. Kein Bankfakt geaendert und keine Dublette bei Doppelklick. Auch ohne Buchungshistorie verwendbar. |
| T11 | Serie bestaetigen und manuell korrigieren; erneute Erkennung behaelt die Korrektur. Abgelehnter Kandidat bleibt abgelehnt. Ausnahme 'nur dieser Termin' aendert keinen Nachfolgetermin. |
| T12 | Historischer Vertrag + expliziter Plan + spaeter importierte Erfuellung zaehlen zusammen genau einmal. Mehrdeutige Matches erfordern Entscheidung. Entknuepfen und Reimport ergeben nachvollziehbare Status statt doppelter Belastung. |
| T13 | Konto-/Waehrungs-/Szenariowechsel: Formulardrafts, Auswahl, Vorschlaege und Caches bleiben isoliert; stabile IDs ueber Sortieren/Filtern/Sprachwechsel. Veraltete Revision erzeugt Konflikt, keine verlorene Aenderung. |
| T14 | Szenario Gehalt +200 ab November, Ausgabe 600 im Dezember, variable Kosten -10% im definierten Scope: exakte Monatdeltas; Baseline und Ist unveraendert. Loeschen/Ruecksetzen stellt Basis wieder her. |
| T15 | Interner Transfer 500: Quelle -500, Ziel +500, Haushalt netto 0 bei gleicher Waehrung. Kartenabrechnung nicht nochmals als Ausgabe; keine gemischte CHF/EUR-Summe. |
| T16 | Sparzielrate durch Restbetrag/Zieltermin begrenzt; pauschale Reservierung aendert nur verfuegbaren Betrag, nicht Bankstand. Expliziter Transfer plus Reservierung fuehrt nicht zu doppeltem wirtschaftlichem Abzug. Legacy-Overlay-Vertrag separat erhalten/testen. |
| T17 | Fehler bei Bills/Audit zeigt Fehler, nicht 'keine Daten'. Leere DB, keine Konten, fehlender Saldoanker, veralteter Import und unvollstaendige Historie separat pruefen. Manuelle Pflege bleibt soweit fachlich moeglich erreichbar. |
| T18 | DE/EN/BG: gleiche Keys/Platzhalter, lokalisierte Labels/Warnungen/Zahlendarstellung. Waehrung sichtbar; numerische Sortierung numerisch. Keine internen Fehlerspuren oder sensiblen Pfade im UI. |
| T19 | AppTest fuer reale Aktionen plus Screenshot-/Browserpruefung bei 1440px und 390px: keine ueberlappenden Labels/Buttons, sichere Formularbedienung, sinnvoller Chart, zugaengliche Aktionen. Browser nur mit synthetischer DB/isoliertem Forecast-Harness; keine Hauptapp mit automatischem Modellstart. |
| T20 | Migration auf leerer und synthetischer Alt-DB zweimal ausfuehren: identisches gueltiges Schema, Bankdaten unveraendert; FK-/CHECK-/UNIQUE-Verletzungen abgewehrt, fehlgeschlagener Save atomar zurueckgerollt. |
| T21 | Sachfremder Rerun verursacht keinen neuen Fit; relevante Ist-/Plan-/Kategorie-/Goals-/Transferaenderung invalidiert Ergebnisse. Read-Counts und Laufzeit dokumentiert. Keine veralteten Ergebnisse nach Speichern. |

## 8. Testkommandos und Nachweisgrenzen

Fokussierte Basis, aus dem Repository-Root:
```powershell
.\venv_bot_20260802\Scripts\python.exe -m pytest tests/test_finance_monarch_core.py tests/test_finance_tab_regressions.py -q --no-header -p no:cacheprovider
```

Danach, passend zu den geaenderten Flaechen:
```powershell
.\venv_bot_20260802\Scripts\python.exe -m pytest tests/test_finance_goals_schema.py tests/test_finance_analytics_tools.py tests/test_finance_chat.py tests/test_finance_structured_runtime.py tests/test_i18n_consistency.py tests/test_tool_profiles.py -q --no-header -p no:cacheprovider
```

Ergaenze neue/regressionsrelevante Dateien und angrenzende DB-/Transfer-/Settlement-Suiten nach tatsaechlichem Blast Radius; deren Dateinamen zuerst im aktuellen Repo verifizieren. Kein ungepruefter Gesamtprojektlauf mit Modell-/GPU-Loads. AppTest- und Runtime-Pruefungen ersetzen nicht automatisch visuelle Browserabnahme.

Durch die vorbereitende Analyse am 2026-09-16 ausgefuehrt:
- Erster obiger Befehl: **88 passed in 36.40s**. Keine Aussage ueber Gesamtprojekt oder nachfolgende Aenderungen.
- In-Memory-SyntheticDB-Proben mit echtem `FinanceTools`: Quartalskosten/Fenster, Waehrungsmischung, Refund, Teilmonat und Unsicherheit wie F01-F07 reproduziert. Keine produktive DB geoeffnet.
- `AppTest.from_string` mit echtem Forecast-Renderer und synthetischer DB: default 0 Charts/3 Tabellen/3 Faktenreads; sachfremder Rerun insgesamt 6 Reads; Kontowahl erzeugt Chart mit doppeltem ersten Monatslabel. Keine Bearbeitungsbuttons vorhanden.
- Standalone-AppTest meldete einmal `missing ScriptRunContext` vom MainThread (Bare-Mode-Harness), keine AppTest-Exception. Warnung nicht im Anwendungscode unterdruecken.
- Streamlit-Version und `st.tabs`-Signatur direkt im Projekt-venv gelesen.
- KEIN Live-App-Test mit echten Konten, keine produktiven Daten analysiert, keine Produktionslatenz gemessen, kein empirischer Modellvergleich/Backtest ausgefuehrt. Diese Punkte sind Implementierungs-/Abnahmearbeit, keine schon erbrachten Nachweise.

## 9. Risiken, Abhaengigkeiten und Rollback

| Risiko | Wahrscheinlichkeit / Auswirkung | Gegenmassnahme |
|---|---|---|
| Historische Kosten plus manueller Plan doppelt | hoch / hoch | Quellenbindung, gemeinsamer Restanteil, T12; kein additive-only Overlay. |
| Konten-/Waehrungsvermischung | hoch / hoch im Altpfad | Konto-ID in Fakten/Plan, getrennte Summen, T04/T13/T15. |
| Verlorene Nutzerkorrektur beim Erkennen/Reimport/Rerun | mittel / hoch | stabile IDs, Revisionen, persistierte Ausnahmen/Ablehnungen, T11-T13. |
| Falsche Sicherheit durch Statistik | hoch / hoch im Altpfad | Coverage/Backtest, Pfadintervalle, Datenqualitaetsstatus, T06/T08/T09. |
| Schema-/Legacy-Tool-Regressionsrisiko | mittel / hoch | additive Migration, API-Vertragstests, kein Big-Bang-Refactor, T20. |
| Unbedienbar durch lange Tabellen/Reruns | hoch / mittel | vertikale UI-Pakete, Formular- und Browserpruefung, T19/T21. |
| Private Finanzdaten in Export/Test/Commit | niedrig bei Isolation / sehr hoch | ausschliesslich synthetische Fixtures; private Exporte ausserhalb Repo; Status/Diff-Pruefung, keine Cloud. |

Abhaengigkeiten: FinanceDB/Import und Transfer-/Settlement-Zuordnung; `FinanceTools` und dessen Chat-/Tool-Konsumenten; Goals und Kategorien; Streamlit Session-State; DE/EN/BG; bestehende CPU-Testfixtures. Stakeholder: Nutzer als letzte Instanz fuer Korrekturen, implementierendes LLM, bestehende Finance-Workflows. Kein externer Dienst erforderlich.

Rollback: pro Paket eigene, gesicherte Dateiaenderungen selektiv zuruecknehmen, niemals fremde Diffs. Additive Plan-Tabellen bei Code-Rollback unangetastet lassen, sofern Legacy-Code sie toleriert; keine destruktive Down-Migration ohne ausdrueckliche Freigabe. Vor spaeterer produktiver Migration konsistentes lokales DB-Backup, Schema-Kompatibilitaet auf Kopie und Wiederherstellung testen. Laufzeit-Undo bezieht sich ausschliesslich auf die ausgewaehlte Planrevision, nicht auf eine globale DB-Ruecksicherung.

## 10. Fachliche Quellen

Am 2026-09-16 gelesen; sie begruenden einzelne Entwurfsentscheidungen, keinen pauschalen Marktvergleich:
- https://otexts.com/fpp3/prediction-intervals.html : Prognoseverteilungen, Mehrschrittunsicherheit, Bootstrap zukuenftiger Pfade statt Stichprobenmittel.
- https://docs.streamlit.io/develop/api-reference/layout/st.tabs : Standardtabs rechnen alle Inhalte; Lazy-Ausfuehrung ueber aktivierten Tab-State und explizites Gate. Installierte Signatur wurde verifiziert.
- https://docs.streamlit.io/develop/api-reference/data/st.data_editor : typisierte editierbare Tabellen, gesperrte Spalten, dynamische Zeilen und Session-State-Verhalten. Widget allein ersetzt keine validierte Persistenz oder stabile fachliche IDs.

Beginne mit AP0. Setze dann AP1 als vollstaendige kleine Nutzerstrecke um, bevor du die Serien-/Szenariofunktionalitaet verbreiterst. Melde nach jedem Paket knapp: erledigte Kriterien, Testergebnis, verbleibende Risiken und naechster konkreter Schritt.