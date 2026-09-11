#!/usr/bin/env python
"""
finance_pdf_reconcile.py — Deterministische PDF↔DB-Abgleichs-Pipeline (Finance)
=================================================================================

Ersatz für fehleranfällige LLM-Extraktion: Alle Beträge/Zeiten kommen ausschliesslich
aus dem PDF-Text-Layer (PyMuPDF, koordinatenbasiert). KEIN LLM im Datenpfad.

Modes:
  inspect  <pdf> [--pages N[,M,...]]   Text-Layer-Zeilen mit Koordinaten (Kalibrierung)
  parse    --pdf-dir DIR ... --out J  Alle Statements parsen → Ground-Truth-JSON
  diff     --truth J --db D [--out]   PDF↔DB-Diskrepanz-Report pro Statement
  repair   --truth J --db D [--dry-run]
           Transaktionaler Repair (Junk löschen, fehlende Zeilen ergänzen, Saldo fixen)

Konventionen:
  - Beträge intern als Integer-Cents; + Gutschrift, − Belastung.
  - Kreditkarte: positiv = Belastung, negativ = Gutschrift; offizielle Closing-Grösse
    ist "Saldo neue Rechnung" (NICHT "Total Transaktionen").
  - Jede geparste Transaktion trägt `page` + `raw_text` als Beleg.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

DATE_ROW = re.compile(r"^\d{2}\.\d{2}\.\d{2,4}$")


def money_to_cents(s: str) -> int:
    """'3'979.00' / '128,15' / '128.15' → Integer-Cents (ohne Vorzeichen)."""
    s = s.strip().replace("'", "").replace("’", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return int((Decimal(s) * 100).to_integral_value())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_kind(pdf_path: Path) -> str:
    """'bank' (PostFinance REP_P_) | 'creditcard' (KK_Abrechnung) | 'unknown'."""
    name = pdf_path.name
    if name.startswith("KK_Abrechnung"):
        return "creditcard"
    if name.startswith("REP_P_"):
        return "bank"
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        text = (doc[0].get_text() or "")[:400]
        doc.close()
        if "Kreditkarte" in text or "Visa" in text:
            return "creditcard"
        if "Gutschrift" in text or "Lastschrift" in text or "Kontostand" in text:
            return "bank"
    except Exception:
        pass
    return "unknown"


def cmd_inspect(args: argparse.Namespace) -> int:
    """Text-Layer-Zeilen einer PDF mit x/y-Koordinaten dumpen (Kalibrierung)."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(args.pdf))
    pages = None
    if args.pages:
        pages = sorted({int(p) - 1 for p in args.pages.split(",") if p.strip()})
    for pno in range(doc.page_count):
        if pages is not None and pno not in pages:
            continue
        page = doc[pno]
        words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,wordno)
        buckets: dict[float, list] = {}
        for w in words:
            buckets.setdefault(round(w[1] / 2.5) * 2.5, []).append(w)
        print(f"\n===== PAGE {pno + 1} ({page.rect.width:.0f}x{page.rect.height:.0f}) =====")
        for y in sorted(buckets):
            ws = sorted(buckets[y], key=lambda w: w[0])
            print(f"y={y:7.1f} | " + " | ".join(f"{w[0]:6.1f}:{w[4]}" for w in ws))
    doc.close()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Datenmodel (Ground Truth)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ParsedTransaction:
    booking_date: str  # ISO yyyy-mm-dd
    value_date: str | None
    amount_cents: int  # signiert: + Gutschrift, − Belastung
    counterparty: str
    purpose: str
    page: int
    raw_text: str = ""
    currency: str = "CHF"
    kind: str = "normal"  # normal | uebertrag | payment | fee
    running_balance_cents: int | None = None  # Bank: Saldo-Spalte je Buchung


@dataclass
class ParsedStatement:
    kind: str  # bank | creditcard
    filename: str
    sha256: str
    period_start: str | None
    period_end: str | None
    opening_balance_cents: int | None
    closing_balance_cents: int | None
    currency: str = "CHF"
    transactions: list[ParsedTransaction] = field(default_factory=list)
    header_fields: dict[str, str] = field(default_factory=dict)
    checks: dict[str, str] = field(default_factory=dict)
    page_totals: dict[str, int] = field(default_factory=dict)  # CC: Seite (1-basiert) → GT-Summe in Cts
    uebertrag_rows: list[dict] = field(default_factory=list)  # CC: Übertrag-Zeilen (kumulierte Seiten-Subtotals)
    warnings: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# parse / diff / repair (implementiert nach Text-Layer-Kalibrierung)
# ─────────────────────────────────────────────────────────────────────────────


NUM_TOK = re.compile(r"^[−\-]?[0-9'’.,\s]+$")


def _is_date(t: str) -> bool:
    return bool(DATE_ROW.match(t))


def _is_num(t: str) -> bool:
    return bool(NUM_TOK.match(t)) and any(c.isdigit() for c in t)


def _iso(d: str) -> str | None:
    """'DD.MM.YY' / 'DD.MM.YYYY' → ISO-Datum, sonst None."""
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2,4})$", d or "")
    if not m:
        return None
    dd, mm, yy = m.groups()
    if len(yy) == 2:
        yy = "20" + yy
    try:
        return date(int(yy), int(mm), int(dd)).isoformat()
    except ValueError:
        return None


def _amount(tokens: list[str]) -> int | None:
    """Eventuell in mehrere Tokens zerlegten Betrag zu signierten Cents."""
    if not tokens:
        return None
    s = "".join(tokens)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return int((Decimal(s) * 100).to_integral_value())
    except Exception:
        return None


def page_rows(page) -> list[list[tuple[float, float, str]]]:
    """Seitenzeilen als [(x0, x1, Wort), ...] (y-Nähe-Clustering, sortiert nach x).

    Wörter einer logischen Zeile können in y bis ~1 pt abweichen (unterschiedliche
    Font-Baselines); ein fester y-Bucket würde solche Zeilen aufteilen und damit
    Betrag und Datum trennen (beobachtet: y=653.7 / y=653.8). Getrennte Zeilen sind
    dagegen immer >= 8 pt auseinander, daher ist der Clustering-Abstand sicher.
    """
    words = page.get_text("words")
    clusters: list[list] = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if clusters and w[1] - min(x[1] for x in clusters[-1]) <= _ROW_MERGE_PT:
            clusters[-1].append(w)
        else:
            clusters.append([w])
    rows = []
    for ws in clusters:
        ws.sort(key=lambda w: w[0])
        rows.append([(float(w[0]), float(w[2]), w[4]) for w in ws])
    return rows


def _bank_zone(x: float) -> str:
    """Spalten-Zonen einer Bankauszug-Zeile (aus Header-Positionen kalibriert)."""
    if x < 100:
        return "date"
    if x < 300:
        return "text"
    if x < 375:
        return "guts"
    if x < 445:
        return "last"
    if x < 510:
        return "valuta"
    return "saldo"


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

_DATE_ZONE_X = 100.0
_TEXT_ZONE = (100.0, 300.0)
_GUTS_ZONE = (300.0, 375.0)
_LAST_ZONE = (375.0, 445.0)
_VALUTA_ZONE = (445.0, 510.0)
_SALDO_ZONE = (510.0, 9999.0)
_CC_AMT_X = 494.0
_CC_DETAIL_X = 139.0
# Wörter einer logischen Zeile weichen in y maximal ~1 pt ab (unterschiedliche
# Font-Baselines); benachbarte, getrennte Zeilen sind >= 8 pt auseinander
# (Detailblock-Abstand 10 pt). 3.0 pt ist der sichere Clustering-Abstand.
_ROW_MERGE_PT = 3.0


def _zone_value(toks: list[tuple[float, float, str]], zone: tuple[float, float]) -> str:
    """Zonen-Tokens (page_rows: (x0, x1, Wort)) in x-Reihenfolge zu einer Zahl verbinden
    (PostFinance zerlegt Beträge über benachbarte Tokens, z. B. '5' + '134.08')."""
    parts = [t[2] for t in toks if zone[0] <= t[0] < zone[1]]
    return "".join(parts)


def _signed_cents(text: str) -> int | None:
    """Signierter CHF-Betrag → Integer-Cents; ungültig → None.

    Erlaubt: '-' und U+2212 '−', Apostroph-Tausendertrenner, 'dd.mm.yy' u. Ä. → None.
    """
    s = (text or "").strip().replace("'", "").replace("’", "").replace(" ", "")
    neg = s.startswith("-") or s.startswith("−")
    s = s.lstrip("-−")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        cents = int((Decimal(s) * 100).to_integral_value())
    except Exception:
        return None
    return -cents if neg else cents


def _is_money(text: str) -> bool:
    return _signed_cents(text) is not None
def parse_bank(path: Path, warnings: list[str]) -> ParsedStatement:
    """PostFinance-Kontoauszug parsen (Gutschrift +, Lastschrift −, Saldo-Kette)."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    ps = ParsedStatement(
        kind="bank",
        filename=path.name,
        sha256=sha256_file(path),
        period_start=None,
        period_end=None,
        opening_balance_cents=None,
        closing_balance_cents=None,
    )
    head = doc[0].get_text()
    m = re.search(r"Kontoauszug\s+(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})", head)
    if m:
        ps.period_start, ps.period_end = _iso(m.group(1)), _iso(m.group(2))
    else:
        warnings.append("bank: Rechnungsperiode im Header nicht gefunden")
    m = re.search(r"IBAN\s+(CH\d{2}(?:\s?\d){13,21})", head)
    if m:
        iban = re.sub(r"\s+", "", m.group(1))
        if len(iban) >= 13 and iban[2:].isdigit():
            ps.header_fields["iban"] = iban
    else:
        warnings.append("bank: IBAN im Header nicht gefunden")
    m = re.search(r"Datum:\s*(\d{2}\.\d{2}\.\d{4})", head)
    if m:
        ps.header_fields["statement_date"] = _iso(m.group(1)) or m.group(1)
    m = re.search(r"REP_P_(CH\d+)_", path.name)
    if m:
        iban_pdf = ps.header_fields.get("iban")
        if iban_pdf and iban_pdf != m.group(1):
            warnings.append(f"bank: IBAN-Abweichung PDF={iban_pdf} vs Filename={m.group(1)}")
        elif not iban_pdf:
            ps.header_fields["iban"] = m.group(1)

    pending: ParsedTransaction | None = None
    for page_no, page in enumerate(doc):
        for toks in page_rows(page):
            if not toks:
                continue
            first = toks[0]
            first_date = _iso(first[2]) if first[0] < _DATE_ZONE_X else None
            text_toks = [t for t in toks if _TEXT_ZONE[0] <= t[0] < _TEXT_ZONE[1]]
            words_all = [t[2] for t in toks]
            saldo_raw = _zone_value(toks, _SALDO_ZONE)
            saldo = _signed_cents(saldo_raw) if saldo_raw else None

            # Kontostand-/Total-Zeilen (Eröffnung, Schluss, Total).
            # Layout-Varianz: 'Kontostand' kann in der linken Spalte (x<100) stehen,
            # Datum und/oder Total-Label können in derselben Zeile vorkommen.
            if "Kontostand" in words_all or "Total" in words_all:
                if "Total" in words_all:
                    gc = _signed_cents(_zone_value(toks, _GUTS_ZONE))
                    lc = _signed_cents(_zone_value(toks, _LAST_ZONE))
                    if gc is not None:
                        ps.header_fields["total_gutschrift_cents"] = str(abs(gc))
                    if lc is not None:
                        ps.header_fields["total_lastschrift_cents"] = str(-abs(lc))
                if "Kontostand" in words_all:
                    if saldo is not None:
                        row_date = next((_iso(t[2]) for t in toks if _iso(t[2])), None)
                        if ps.opening_balance_cents is None and row_date is not None:
                            ps.opening_balance_cents = saldo
                        else:
                            ps.closing_balance_cents = saldo
                    else:
                        warnings.append(f"bank: Kontostand-Zeile ohne gültigen Betrag (page {page_no + 1})")
                pending = None
                continue
            if not first_date:
                # (a) Buchungszeile ohne Datum in der Datums-Spalte (z. B. RETOURE,
                #     GIRO INTERNATIONAL): Betrag + Valuta/Saldo vorhanden →
                #     eigene Transaktion, Buchungstag = vorheriger Buchungstag.
                g0 = _signed_cents(_zone_value(toks, _GUTS_ZONE))
                l0 = _signed_cents(_zone_value(toks, _LAST_ZONE))
                valuta0 = _zone_value(toks, _VALUTA_ZONE)
                if (g0 is not None or l0 is not None) and (
                    _iso(valuta0) is not None or saldo is not None
                ):
                    prev = ps.transactions[-1] if ps.transactions else None
                    if prev is None:
                        warnings.append(
                            f"bank: Buchungszeile ohne Datum und ohne Vorbu (page {page_no + 1})"
                        )
                        continue
                    text0 = [t for t in toks if _TEXT_ZONE[0] <= t[0] < _TEXT_ZONE[1]]
                    ps.transactions.append(
                        ParsedTransaction(
                            booking_date=prev.booking_date,
                            value_date=_iso(valuta0) or prev.value_date,
                            amount_cents=g0 if g0 is not None else -l0,
                            counterparty=text0[1][2] if len(text0) > 1 else "",
                            purpose=" ".join(t[2] for t in toks if _TEXT_ZONE[0] <= t[0] < _VALUTA_ZONE[0]),
                            page=page_no + 1,
                            raw_text=" ".join(t[2] for t in toks),
                            kind="normal",
                            running_balance_cents=saldo,
                        )
                    )
                    pending = ps.transactions[-1]
                    continue
                # (b) Verwaiste Saldo-Zeile (nur Betrag rechts) → laufender Saldo
                #     der letzten Buchung.
                if saldo is not None and pending is not None and pending.running_balance_cents is None:
                    pending.running_balance_cents = saldo
                    continue
                # (c) Fortsetzungszeile (Zweizeiler) oder Sektions-Header ('Transaktionen')
                if pending is not None:
                    frag = " ".join(t[2] for t in toks if _TEXT_ZONE[0] <= t[0] < _VALUTA_ZONE[0])
                    if frag and not NUM_TOK.match(frag):
                        pending.purpose = f"{pending.purpose} {frag}".strip() if pending.purpose else frag
                continue
            # Transaktionszeile
            g = _signed_cents(_zone_value(toks, _GUTS_ZONE))
            l = _signed_cents(_zone_value(toks, _LAST_ZONE))
            if g is None and l is None:
                warnings.append(f"bank: Transaktionszeile ohne Betrag (page {page_no + 1}): {first[2]!r}")
                pending = None
                continue
            amount = g if g is not None else -l
            value_date = None
            for t in toks:
                if _VALUTA_ZONE[0] <= t[0] < _VALUTA_ZONE[1]:
                    value_date = _iso(t[2])
                    if value_date:
                        break
            purpose = " ".join(t[2] for t in text_toks)
            tx = ParsedTransaction(
                booking_date=first_date,
                value_date=value_date,
                amount_cents=amount,
                counterparty=text_toks[1][2] if len(text_toks) > 1 else "",
                purpose=purpose,
                page=page_no + 1,
                raw_text=" ".join(t[2] for t in toks),
                kind="normal",
                running_balance_cents=saldo,
            )
            ps.transactions.append(tx)
            pending = tx
    if ps.opening_balance_cents is None:
        warnings.append("bank: Eröffnungssaldo (Kontostand) nicht gefunden")
    if ps.closing_balance_cents is None:
        warnings.append("bank: Schlussaldo (Kontostand) nicht gefunden")
    # Selbstprüfung: Eröffnung + Σ Transaktionen = Schluss (Toleranz 2 Cts, Token-Brüche)
    if ps.opening_balance_cents is not None and ps.closing_balance_cents is not None and ps.transactions:
        sum_tx = sum(t.amount_cents for t in ps.transactions)
        d = ps.opening_balance_cents + sum_tx - ps.closing_balance_cents
        if abs(d) <= 2:
            ps.checks["bank_balance_chain"] = "ok"
        else:
            ps.checks["bank_balance_chain"] = f"mismatch diff_cents={d}"
            warnings.append(f"bank: Saldo-Kette inkonsistent (diff {d} Cts)")
    doc.close()
    return ps


def parse_credit_card(path: Path, warnings: list[str]) -> ParsedStatement:
    """PostFinance-Kreditkarten-Abrechnung parsen.

    Sign-Konvention: PDF zeigt Belastungen positiv / Gutschriften negativ;
    DB-Konvention (GT) = −PDF. Offizieller Schlussaldo = 'Saldo neue Rechnung'.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    ps = ParsedStatement(
        kind="creditcard",
        filename=path.name,
        sha256=sha256_file(path),
        period_start=None,
        period_end=None,
        opening_balance_cents=None,
        closing_balance_cents=None,
    )
    head = doc[0].get_text()
    # Kartenkonto: 4×4-Ziffern-Gruppe in der Nähe des Labels. Die Nummer kann vor
    # oder nach dem Label stehen (layout-abhängig). WICHTIG: Das Label taucht auch
    # in Transaktionsdetails auf ("Online Ladung Kartenkonto") — daher erst die
    # saubere "Label direkt vor Nummer"-Variante versuchen, dann ±80-Fenster-Scan.
    card = None
    m = re.search(r"Kartenkonto\s+((?:\d{4}\s+){3}\d{4})", head)
    if m:
        card = re.sub(r"\s+", "", m.group(1))
    if card is None:
        for m in re.finditer(r"Kartenkonto", head):
            window = head[max(0, m.start() - 80): m.end() + 80]
            mm = re.search(r"((?:\d{4}\s+){3}\d{4})", window)
            if mm:
                card = re.sub(r"\s+", "", mm.group(1))
                break
    if card:
        ps.header_fields["card_number"] = card
    else:
        warnings.append("cc: Kartenkonto im Header nicht gefunden")
    m = re.search(r"Rechnungsdatum", head)
    if m:
        window = head[max(0, m.start() - 40): m.end() + 40]
        mm = re.search(r"(\d{2}\.\d{2}\.\d{2,4})", window)
        if mm:
            ps.header_fields["statement_date"] = _iso(mm.group(1)) or mm.group(1)
    # Rechnungsperiode: Datums-Paar in der Nähe des Labels (neueres Layout:
    # Datum über dem Label, Trenner Bindestrich/Leerzeichen — Reihenfolge der
    # Text-Spans ist nicht garantiert).
    m = re.search(r"Rechnungsperiode", head)
    if m:
        window = head[max(0, m.start() - 120): m.end() + 120]
        pairs = re.findall(r"(\d{2}\.\d{2}\.\d{2,4})[^\d]{0,12}(\d{2}\.\d{2}\.\d{2,4})", window)
        if pairs:
            ps.period_start, ps.period_end = _iso(pairs[-1][0]), _iso(pairs[-1][1])
    if ps.period_start is None or ps.period_end is None:
        warnings.append("cc: Rechnungsperiode im Header nicht gefunden")
    m = re.search(r"KK_Abrechnung_P_(\d+)", path.name)
    if m:
        fn = m.group(1)
        card_pdf = ps.header_fields.get("card_number")
        if card_pdf and not (card_pdf.endswith(fn) or fn.endswith(card_pdf)):
            warnings.append(f"cc: Kartennummer-Abweichung PDF={card_pdf} vs Filename={fn}")
        elif not card_pdf:
            ps.header_fields["card_number"] = fn

    section: str | None = None
    section_starts: list[int] = []  # Seiten, an denen eine 'Transaktionen'-Sektion begann
    expect_summary = False
    summary_bands: list[dict] = []
    pending: ParsedTransaction | None = None
    for page_no, page in enumerate(doc):
        for toks in page_rows(page):
            if not toks:
                continue
            first = toks[0]
            first_date = _iso(first[2]) if first[0] < _DATE_ZONE_X else None
            words = [t[2] for t in toks]
            text_head = first[2]

            if expect_summary:
                vals = [(x, t) for x, _x1, t in toks if _is_money(t)]
                if vals and summary_bands:
                    for x, t in vals:
                        band = next((b for b in reversed(summary_bands) if b["x0"] <= x), summary_bands[0])
                        val = _signed_cents(t)
                        if val is not None:
                            ps.header_fields[band["key"]] = str(val)
                    if "saldo_letzte_cents" in ps.header_fields:
                        ps.opening_balance_cents = int(ps.header_fields["saldo_letzte_cents"])
                    if "saldo_neue_cents" in ps.header_fields:
                        ps.closing_balance_cents = int(ps.header_fields["saldo_neue_cents"])
                    expect_summary = False
                pending = None
                continue
            # Summary-Labelzeile: 'Saldo … neue … Rechnung' + 'Total …' + ggf.
            # 'Kumulierter Bonus'. Die Beträge stehen in der Folgezeile; die
            # Zuordnung erfolgt über die X-Bänder der Labelgruppen (die Spalten-
            # Positionen variieren zwischen Abrechnungsjahren).
            if (
                "Saldo" in words
                and "neue" in words
                and "Rechnung" in words
                and not any(_is_money(t) for _x, _x1, t in toks)
            ):
                groups: list[dict] = []
                cur: dict | None = None
                for x, _x1, w in toks:
                    if w in ("Saldo", "Total", "Kumulierter"):
                        cur = {"x0": x, "words": [w]}
                        groups.append(cur)
                    elif cur is not None:
                        cur["words"].append(w)
                for g in groups:
                    ws = " ".join(g["words"])
                    if "Zahlungen" in ws:
                        g["key"] = "total_zahlungen_cents"
                    elif "Transaktionen" in ws:
                        g["key"] = "total_transaktionen_cents"
                    elif "Kumulierter" in ws:
                        g["key"] = "bonus_kumuliert_cents"
                    elif "neue" in ws:
                        g["key"] = "saldo_neue_cents"
                    else:
                        g["key"] = "saldo_letzte_cents"
                groups.sort(key=lambda g: g["x0"])
                summary_bands = groups
                expect_summary = True
                pending = None
                continue
            if text_head == "Ihre" and "Zahlungen" in words:
                section = "payments"
                pending = None
                continue
            if text_head == "Transaktionen":
                section = "transactions"
                section_starts.append(page_no + 1)
                pending = None
                continue
            if text_head == "Übertrag":
                # Seiten-Subtotal (kumuliert): 'von Seite N' = Σ Seiten 1..N,
                # 'auf Seite N' = Σ Seiten 1..N−1. Keine echte Transaktion.
                amt_raw = next((t for x, _x1, t in reversed(toks) if x >= _CC_AMT_X and _is_money(t)), None)
                val = _signed_cents(amt_raw) if amt_raw is not None else None
                mv = re.search(r"Seite\s+(\d+)", " ".join(words))
                n = int(mv.group(1)) if mv else None
                ps.uebertrag_rows.append(
                    {"page": page_no + 1, "seite": n, "von": "von" in words, "value_cents": val}
                )
                pending = None
                continue
            if text_head == "Total":
                amt_raw = next((t for x, _x1, t in reversed(toks) if x >= _CC_AMT_X and _is_money(t)), None)
                val = _signed_cents(amt_raw) if amt_raw is not None else None
                if val is not None:
                    ps.header_fields[f"total_row_{section}_cents"] = str(-val)
                pending = None
                continue
            if not first_date:
                # Fussnote (z.B. '¹ Gutschriften werden mit einem Minus …') und
                # 'Seite X von Y' stehen links (x<120) und sind keine Buchungen —
                # niemals an pending anhängen.
                if first[2] in ("¹", "²", "³", "*") or first[0] < 120:
                    pending = None
                    continue
                if pending is not None:
                    frag = " ".join(t[2] for t in toks if _CC_DETAIL_X <= t[0] < _CC_AMT_X)
                    if frag:
                        pending.purpose = f"{pending.purpose} {frag}".strip() if pending.purpose else frag
                continue
            if section not in ("payments", "transactions"):
                warnings.append(f"cc: Transaktionszeile außerhalb einer Sektion (page {page_no + 1}): {first[2]!r}")
                pending = None
                continue
            amt_tok = next((t for x, _x1, t in reversed(toks) if x >= _CC_AMT_X and _is_money(t)), None)
            if amt_tok is None:
                warnings.append(f"cc: Transaktionszeile ohne Betrag (page {page_no + 1}): {first[2]!r}")
                pending = None
                continue
            amount = -_signed_cents(amt_tok)  # GT-Konvention = −PDF-Anzeige
            date_vals = [_iso(t[2]) for t in toks if t[0] < _CC_DETAIL_X]
            value_date = next((d for d in date_vals[1:] if d), None)
            detail = " ".join(t[2] for t in toks if _CC_DETAIL_X <= t[0] < _CC_AMT_X)
            tx = ParsedTransaction(
                booking_date=first_date,
                value_date=value_date,
                amount_cents=amount,
                counterparty=detail.split(" ", 1)[0] if detail else "",
                purpose=detail,
                running_balance_cents=None,
                kind="payment" if section == "payments" else "normal",
                page=page_no + 1,
                raw_text=" ".join(t[2] for t in toks),
            )
            ps.transactions.append(tx)
            if section == "transactions":
                key = str(page_no + 1)
                ps.page_totals[key] = ps.page_totals.get(key, 0) + tx.amount_cents
            pending = tx
    if ps.opening_balance_cents is None:
        warnings.append("cc: Saldo letzte Rechnung (Eröffnung) nicht gefunden")
    if ps.closing_balance_cents is None:
        warnings.append("cc: Saldo neue Rechnung (Schluss) nicht gefunden")
    # Selbstprüfung 1: 'Total Transaktionen' = Summe aller Transaktionszeilen
    tot = ps.header_fields.get("total_transaktionen_cents")
    if tot is not None:
        sum_tx = sum(t.amount_cents for t in ps.transactions if t.kind != "payment")
        # PDF-Total ist positiv (Kredite), GT-Total negativ (Vorzeichen-Konvention) →
        # konsistent, wenn sich beide zu Null ergänzen.
        d = int(tot) + sum_tx
        if abs(d) <= 2:
            ps.checks["cc_total_transaktionen"] = "ok"
        else:
            ps.checks["cc_total_transaktionen"] = f"mismatch diff_cents={d}"
            warnings.append(f"cc: 'Total Transaktionen' weicht ab um {d} Cts")
    # Selbstprüfung 2: Übertrag-Zeilen = kumulierte Seiten-Summen
    pages_sorted = sorted(ps.page_totals.items(), key=lambda kv: int(kv[0]))
    for row in ps.uebertrag_rows:
        v = row["value_cents"]
        n = row["seite"]
        if v is None or n is None or n < 1:
            continue
        span = n if row["von"] else (n - 1)
        if span < 1:
            continue
        start = max((s for s in section_starts if s <= span), default=1)
        cum_gt = sum(vv for kk, vv in pages_sorted if start <= int(kk) <= span)
        d = v + cum_gt  # v PDF-positiv, cum_gt GT-negativ → 0 bei Konsistenz
        key = f"uebertrag_page{row['page']}_seite{n}"
        if abs(d) <= 2:
            ps.checks[key] = "ok"
        else:
            ps.checks[key] = f"mismatch diff_cents={d}"
            warnings.append(f"cc: Übertrag (Seite {n}, page {row['page']}) weicht ab um {d} Cts")
    doc.close()
    return ps


def cmd_parse(args: argparse.Namespace) -> int:
    """Alle PDFs der Verzeichnisse parsen → Ground-Truth-JSON."""
    statements: list[ParsedStatement] = []
    errors: list[dict[str, str]] = []
    seen: set[Path] = set()
    for d in args.pdf_dir:
        root = Path(d)
        if not root.is_dir():
            errors.append({"path": str(root), "error": "Verzeichnis nicht gefunden"})
            continue
        for pdf in sorted(root.rglob("*.pdf")):
            if pdf in seen:
                continue
            seen.add(pdf)
            warnings: list[str] = []
            try:
                kind = detect_kind(pdf)
                if kind == "bank":
                    ps = parse_bank(pdf, warnings)
                elif kind == "creditcard":
                    ps = parse_credit_card(pdf, warnings)
                else:
                    w_b: list[str] = []
                    w_c: list[str] = []
                    cands = (parse_bank(pdf, w_b), parse_credit_card(pdf, w_c))
                    ps = max(cands, key=lambda s: (len(s.transactions), s.closing_balance_cents is not None))
                    warnings = w_b if ps is cands[0] else w_c
                    warnings.insert(0, f"kind=unknown -> heuristisch als {ps.kind} geparst")
                ps.warnings = warnings
            except Exception as exc:  # deterministisch erfassen, nicht schlucken
                errors.append({"path": str(pdf), "error": f"{type(exc).__name__}: {exc}"})
                continue
            statements.append(ps)
    out = {
        "generated_by": "scripts/finance_pdf_reconcile.py (parse)",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "statement_count": len(statements),
        "statements": [asdict(s) for s in statements],
        "errors": errors,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"parse: {len(statements)} Statements, {len(errors)} Fehler -> {out_path}")
    for s in statements:
        o = f"{s.opening_balance_cents / 100:,.2f}" if s.opening_balance_cents is not None else "?"
        c = f"{s.closing_balance_cents / 100:,.2f}" if s.closing_balance_cents is not None else "?"
        print(
            f"  [{s.kind:11s}] {s.filename:56s} {s.period_start or '?'} - {s.period_end or '?'} "
            f"tx={len(s.transactions):3d}  opening={o:>14s}  closing={c:>14s}  warnings={len(s.warnings)}"
        )
        for w in s.warnings:
            print(f"    WARN  {w}")
    for e in errors:
        print(f"    ERROR {e['path']}: {e['error']}")
    return 0 if not errors else 1


def _norm_cp(s: str | None) -> str:
    """Gegenpartei-Normalisierung zum Paaren (Kleinbuchstaben-Ziffern, erste 16 Zeichen)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())[:16]


def _tx_brief(t: dict) -> dict:
    return {
        "booking_date": t.get("booking_date"),
        "value_date": t.get("value_date"),
        "amount_cents": t.get("amount_cents"),
        "counterparty": (t.get("counterparty") or "")[:60],
        "purpose": (t.get("purpose") or "")[:120],
    }


def _text_tokens(t: dict) -> set[str]:
    """Bedeutsame Token (≥4 Zeichen) aus Gegenpartei + Verwendungszweck (Kleinschreibung)."""
    blob = f"{t.get('counterparty') or ''} {t.get('purpose') or ''}".casefold()
    return set(re.findall(r"[a-z0-9]{4,}", blob))


def _match_transactions(truth_txs: list[dict], db_txs: list[dict]) -> dict:
    """Deterministisches Multiset-Matching PDF-Transaktionen (Truth) vs. DB-Transaktionen.

    Stufe 1: exakter Schlüssel (booking_date, value_date, amount_cents).
    Stufe 2: (booking_date, amount_cents), value_date wird ignoriert (DB hat oft NULL);
             bei mehreren Kandidaten: Text-Overlap (Gegenpartei/Zweck) als Determinierung.
    Stufe 3: gleiche Buchungsdaten + Text-Overlap ≥ 2 → Betrags-/Vorzeichenfehler.
    Rest: missing_in_db / extra_in_db.
    """
    truth_tokens = [_text_tokens(t) for t in truth_txs]
    db_tokens = [_text_tokens(d) for d in db_txs]
    db_left: list[int] = list(range(len(db_txs)))
    exact: list[dict] = []

    # Stufe 1: exakt
    truth_left: list[int] = []
    for i, t in enumerate(truth_txs):
        key = (t.get("booking_date"), t.get("value_date"), t.get("amount_cents"))
        hit = next(
            (
                j
                for j in db_left
                if (db_txs[j].get("booking_date"), db_txs[j].get("value_date"), db_txs[j].get("amount_cents")) == key
            ),
            None,
        )
        if hit is not None:
            db_left.remove(hit)
            exact.append({"stage": "exact", "truth": _tx_brief(t), "db": _tx_brief(db_txs[hit])})
        else:
            truth_left.append(i)

    # Stufe 2: Datum + Betrag (value_date ignoriert)
    still_left: list[int] = []
    for i in truth_left:
        t = truth_txs[i]
        key = (t.get("booking_date"), t.get("amount_cents"))
        cands = [j for j in db_left if (db_txs[j].get("booking_date"), db_txs[j].get("amount_cents")) == key]
        if cands:
            best = max(cands, key=lambda j: len(truth_tokens[i] & db_tokens[j]))
            db_left.remove(best)
            exact.append({"stage": "value_date_gap", "truth": _tx_brief(t), "db": _tx_brief(db_txs[best])})
        else:
            still_left.append(i)
    truth_left = still_left

    # Stufe 3: Betrag-/Vorzeichenfehler (Datum + Text-Overlap)
    mismatches: list[dict] = []
    still_truth: list[dict] = []
    for i in truth_left:
        t = truth_txs[i]
        cands = [
            j
            for j in db_left
            if db_txs[j].get("booking_date") == t.get("booking_date") and len(truth_tokens[i] & db_tokens[j]) >= 2
        ]
        if cands:
            best = max(cands, key=lambda j: len(truth_tokens[i] & db_tokens[j]))
            d = db_txs[best]
            db_left.remove(best)
            typ = "sign_flip" if d.get("amount_cents") == -t.get("amount_cents") else "wrong_amount"
            mismatches.append({"type": typ, "truth": _tx_brief(t), "db": _tx_brief(d)})
        else:
            still_truth.append(_tx_brief(t))
    return {
        "truth_count": len(truth_txs),
        "db_count": len(db_txs),
        "exact_matches": len(exact),
        "value_date_gaps": sum(1 for e in exact if e["stage"] == "value_date_gap"),
        "exact_pairs": exact,
        "amount_mismatches": mismatches,
        "missing_in_db": still_truth,
        "extra_in_db": [_tx_brief(db_txs[j]) for j in db_left],
    }


def _compare_balance(field: str, tv, dv) -> dict:
    if tv is None or dv is None:
        return {"truth": tv, "db": dv, "status": "missing_data"}
    if tv == dv:
        return {"truth": tv, "db": dv, "status": "ok"}
    return {"truth": tv, "db": dv, "status": "mismatch", "diff_cents": dv - tv}


def _load_db_statements(conn: sqlite3.Connection):
    """Statements laden → (junk, db_by_period). Junk = fehlende Perioden (Teil-Auszüge)."""
    junk: list[dict] = []
    db_by_period: dict[tuple, list[sqlite3.Row]] = {}
    for r in conn.execute("select * from statements order by id"):
        if r["period_start"] is None or r["period_end"] is None:
            ntx = conn.execute(
                "select count(*) from transactions where statement_id=?", (r["id"],)
            ).fetchone()[0]
            junk.append(
                {
                    "id": r["id"],
                    "account_id": r["account_id"],
                    "source_filename": r["source_filename"],
                    "opening_balance_cents": r["opening_balance_cents"],
                    "closing_balance_cents": r["closing_balance_cents"],
                    "transaction_count": ntx,
                }
            )
        else:
            db_by_period.setdefault((r["account_id"], r["period_start"], r["period_end"]), []).append(r)
    return junk, db_by_period


def _chain_breaks(conn: sqlite3.Connection, account_id: int, db_by_period: dict) -> list[dict]:
    rows = [
        r
        for (a, _p1, _p2), rs in db_by_period.items()
        if a == account_id
        for r in rs
    ]
    rows.sort(key=lambda r: (r["period_start"], r["period_end"], r["id"]))
    breaks = []
    for a, b in zip(rows, rows[1:]):
        if (
            a["closing_balance_cents"] is not None
            and b["opening_balance_cents"] is not None
            and a["closing_balance_cents"] != b["opening_balance_cents"]
        ):
            breaks.append(
                {
                    "closing_statement_id": a["id"],
                    "closing_period_end": a["period_end"],
                    "closing_balance_cents": a["closing_balance_cents"],
                    "opening_statement_id": b["id"],
                    "opening_period_start": b["period_start"],
                    "opening_balance_cents": b["opening_balance_cents"],
                    "diff_cents": b["opening_balance_cents"] - a["closing_balance_cents"],
                }
            )
    return breaks


def cmd_diff(args: argparse.Namespace) -> int:
    """Truth-JSON vs. Finance-DB vergleichen → deterministischer Diskrepanz-Report (read-only)."""
    with open(args.truth, encoding="utf-8") as f:
        truth = json.load(f)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    accounts = {
        a["account_type"]: a["id"] for a in conn.execute("select id, account_type from accounts")
    }
    if "checking" not in accounts or "credit_card" not in accounts:
        if len(accounts) == 1:
            only = next(iter(accounts.values()))
            accounts = {"checking": only, "credit_card": only}
        else:
            raise SystemExit(f"DB-Konten nicht eindeutig (checking/credit_card): {accounts}")
    acc_bank, acc_cc = accounts["checking"], accounts["credit_card"]

    junk, db_by_period = _load_db_statements(conn)
    matched_ids: set[int] = set()
    statements_out: list[dict] = []
    sums = {
        "matched": 0,
        "missing_in_db": 0,
        "duplicate_in_db": 0,
        "balance_opening_mismatch": 0,
        "balance_closing_mismatch": 0,
        "tx_missing_in_db": 0,
        "tx_extra_in_db": 0,
        "tx_wrong_amount": 0,
        "tx_sign_flip": 0,
    }

    for s in truth["statements"]:
        acc = acc_bank if s["kind"] == "bank" else acc_cc
        rows = db_by_period.get((acc, s["period_start"], s["period_end"]), [])
        entry: dict = {
            "kind": s["kind"],
            "filename": s["filename"],
            "period_start": s["period_start"],
            "period_end": s["period_end"],
            "truth": {
                "opening_balance_cents": s["opening_balance_cents"],
                "closing_balance_cents": s["closing_balance_cents"],
                "transaction_count": len(s["transactions"]),
            },
        }
        if not rows:
            entry["status"] = "missing_in_db"
            sums["missing_in_db"] += 1
        elif len(rows) > 1:
            entry["status"] = "duplicate_in_db"
            entry["db_statement_ids"] = [r["id"] for r in rows]
            sums["duplicate_in_db"] += 1
            row = rows[0]
        else:
            entry["status"] = "matched"
            row = rows[0]
            matched_ids.add(row["id"])
            sums["matched"] += 1

        if entry["status"] in ("matched", "duplicate_in_db"):
            db_txs = [
                {
                    "booking_date": r2["booking_date"],
                    "value_date": r2["value_date"],
                    "amount_cents": r2["amount_cents"],
                    "counterparty": r2["counterparty"],
                    "purpose": r2["purpose"],
                }
                for r2 in conn.execute(
                    "select booking_date, value_date, amount_cents, counterparty, purpose"
                    " from transactions where statement_id=? order by id",
                    (row["id"],),
                )
            ]
            entry["db_statement_id"] = row["id"]
            entry["db"] = {
                "opening_balance_cents": row["opening_balance_cents"],
                "closing_balance_cents": row["closing_balance_cents"],
                "transaction_count": len(db_txs),
            }
            entry["opening"] = _compare_balance("opening", s["opening_balance_cents"], row["opening_balance_cents"])
            entry["closing"] = _compare_balance("closing", s["closing_balance_cents"], row["closing_balance_cents"])
            if entry["opening"]["status"] == "mismatch":
                sums["balance_opening_mismatch"] += 1
            if entry["closing"]["status"] == "mismatch":
                sums["balance_closing_mismatch"] += 1
            tx_cmp = _match_transactions(s["transactions"], db_txs)
            entry["transactions"] = tx_cmp
            sums["tx_missing_in_db"] += len(tx_cmp["missing_in_db"])
            sums["tx_extra_in_db"] += len(tx_cmp["extra_in_db"])
            for m in tx_cmp["amount_mismatches"]:
                sums["tx_sign_flip" if m["type"] == "sign_flip" else "tx_wrong_amount"] += 1
        statements_out.append(entry)
    chain = {
        "bank": _chain_breaks(conn, acc_bank, db_by_period),
        "creditcard": _chain_breaks(conn, acc_cc, db_by_period),
    }
    unmatched_db = [
        {
            "id": r["id"],
            "account_id": a,
            "period_start": p1,
            "period_end": p2,
            "source_filename": r["source_filename"],
        }
        for (a, p1, p2), rs in sorted(
            db_by_period.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])
        )
        for r in rs
        if r["id"] not in matched_ids
    ]
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(args.db),
        "truth_path": str(args.truth),
        "account_map": {"bank": acc_bank, "creditcard": acc_cc},
        "summary": {
            "truth_statements": len(truth["statements"]),
            "db_statements": sum(len(v) for v in db_by_period.values()) + len(junk),
            "junk_statements": len(junk),
            **sums,
            "db_chain_breaks": len(chain["bank"]) + len(chain["creditcard"]),
        },
        "statements": statements_out,
        "junk_statements": junk,
        "db_chain_breaks": chain,
        "unmatched_db_statements": unmatched_db,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    conn.close()

    sm = out["summary"]
    print(f"diff: {sm['matched']}/{sm['truth_statements']} Statements gematcht")
    print(
        f"  fehlend in DB: {sm['missing_in_db']} | Duplikate: {sm['duplicate_in_db']}"
        f" | Junk (ohne Zeitraum): {sm['junk_statements']}"
    )
    print(f"  Saldo-Fehler: opening {sm['balance_opening_mismatch']}, closing {sm['balance_closing_mismatch']}")
    print(
        f"  Tx: fehlend {sm['tx_missing_in_db']} | extra {sm['tx_extra_in_db']}"
        f" | Betrag {sm['tx_wrong_amount']} | Vorzeichen {sm['tx_sign_flip']}"
    )
    print(f"  DB-Chain-Brüche: {sm['db_chain_breaks']}")
    if args.out:
        print(f"  Report → {args.out}")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    raise SystemExit("repair-Modus: folgt nach diff")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Deterministische PDF↔DB-Abgleichs-Pipeline (Finance)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="mode", required=True)

    pi = sub.add_parser("inspect", help="Text-Layer-Zeilen mit Koordinaten dumpen")
    pi.add_argument("pdf")
    pi.add_argument("--pages", default=None, help="kommagetrennte Seiten (1-basiert, z.B. 1,2,7)")
    pi.set_defaults(func=cmd_inspect)

    pp = sub.add_parser("parse", help="Alle PDFs eines/er Verzeichnisse parsen → Truth-JSON")
    pp.add_argument("--pdf-dir", action="append", required=True)
    pp.add_argument("--out", required=True)
    pp.set_defaults(func=cmd_parse)

    pd = sub.add_parser("diff", help="Truth-JSON vs. Finance-DB vergleichen")
    pd.add_argument("--truth", required=True)
    pd.add_argument("--db", required=True)
    pd.add_argument("--out", default=None)
    pd.set_defaults(func=cmd_diff)

    pr = sub.add_parser("repair", help="Transaktionaler Repair (nur PDF-verifizierte Zeilen)")
    pr.add_argument("--truth", required=True)
    pr.add_argument("--db", required=True)
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=cmd_repair)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
