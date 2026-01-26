#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import csv
import html
import hashlib
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

import argparse
import shutil
from datetime import datetime
from pathlib import Path

OUTPUT_HTML = "raport_operacji.html"
OUTPUT_HTML_SHORT = "raport_operacji_short.html"

DZIECI_TXT = "ListaDzieciZSP.txt"

OUTPUT_CSV_RR = "Podsumowanie_RR.csv"
OUTPUT_HTML_RR_VERIFY = "Weryfikacja_RR.html"

RR_MANUAL_CSV = "RR_manual.csv"


# -------------------------
# Output folder: <script_dir>/ID_YYYY-MM-DD_HH-MM-SS[/_N]
# -------------------------
def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def make_run_output_dir(base_dir: Path | None = None) -> Path:
    base = Path(base_dir) if base_dir else _script_dir()
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = f"ID_{ts}"
    p = base / stem
    i = 1
    while p.exists():
        p = base / f"{stem}_{i}"
        i += 1
    p.mkdir(parents=True, exist_ok=False)
    return p


def _copy_optional(src: str, dst: Path):
    if not src:
        return
    sp = Path(src).expanduser().resolve()
    if not sp.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku: {sp}")
    dst.write_bytes(sp.read_bytes())



# -------------------------
# Helpers
# -------------------------

def simplify_name(s: str) -> str:
    s = (s or "").lower()
    mapping = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o",
        "ś": "s", "ż": "z", "ź": "z",
        "ä": "a", "ö": "o", "ü": "u", "ß": "ss",
    }
    for src, dst in mapping.items():
        s = s.replace(src, dst)
    return s


def family_surname_key(nazwisko: str) -> str:
    n = simplify_name((nazwisko or "").strip())
    if len(n) > 3:
        n = n[:-1]
    return n


def _parse_yyyy_mm_dd(s: str):
    if not s:
        return None
    s = str(s).strip()
    s = re.split(r"[T ]", s, maxsplit=1)[0].strip()
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.match(r"^(\d{1,2})[-./](\d{1,2})[-./](\d{4})$", s)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _estimate_semesters_from_ops(operacje_rr) -> int:
    # heurystyka: jeśli są miesiące jesienne i wiosenne -> 2 semestry, inaczej 1
    months = set()
    for op in operacje_rr:
        dt = op.get("dk") or op.get("dop") or ""
        parsed = _parse_yyyy_mm_dd(dt)
        if not parsed:
            continue
        months.add(parsed[1])
    has_autumn = any(m in months for m in (9, 10, 11, 12))
    has_spring = any(m in months for m in (1, 2, 3, 4, 5, 6))
    return 2 if (has_autumn and has_spring) else 1


def _estimate_months_from_ops(operacje_rr) -> int:
    # heurystyka: liczba unikalnych (rok, miesiąc) w danych RR
    ym = set()
    for op in operacje_rr:
        dt = op.get("dk") or op.get("dop") or ""
        parsed = _parse_yyyy_mm_dd(dt)
        if not parsed:
            continue
        y, m, _ = parsed
        ym.add((y, m))
    return len(ym)


def parse_kw(kw_str: str) -> Decimal:
    if not kw_str:
        return Decimal("0")
    kw_str = kw_str.strip().replace(" ", "").replace(",", ".")
    try:
        return Decimal(kw_str)
    except InvalidOperation:
        return Decimal("0")


def znajdz_nrb(root):
    for konto in root.iter("KONTO"):
        nrb = konto.findtext("NRB")
        if not nrb:
            saldo = konto.find("SALDO")
            if saldo is not None:
                nrb = saldo.findtext("NRB")
        if nrb:
            return nrb.strip()
    return "NIEZNANY"


def nazwa_konta(nrb: str) -> str:
    n = (nrb or "").replace(" ", "")
    if n == "16843600030000000014560002":
        return "Przedszkole"
    return "Szkoła"


def _make_op_id(op: dict) -> str:
    key = "|".join([
        (op.get("nrb") or "").replace(" ", ""),
        op.get("dk") or "",
        op.get("dop") or "",
        op.get("nd") or "",
        op.get("td") or "",
        str(op.get("kwota") or ""),
        op.get("l0") or "",
        op.get("l1") or "",
        op.get("l5") or "",
        op.get("opis") or "",
        op.get("plik") or "",
    ])
    h = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:10].upper()
    return f"TR-{h}"


# -------------------------
# XML -> operations
# -------------------------

def wczytaj_operacje_z_pliku(xml_path):
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f"Błąd parsowania XML w pliku {xml_path}: {e}")
        return []

    root = tree.getroot()
    nrb = znajdz_nrb(root)
    operacje = []

    for operacje_node in root.iter("OPERACJE"):
        for op_node in operacje_node.findall("OPERACJA"):

            def txt(tag):
                node = op_node.find(tag)
                return node.text.strip() if node is not None and node.text else ""

            op_code = txt("OP")
            dk = txt("DK")
            dop = txt("DOP")
            nd = txt("ND")
            td = txt("TD")
            l0 = txt("L0")
            l1 = txt("L1")
            l2 = txt("L2")
            l3 = txt("L3")
            l4 = txt("L4")
            l5 = txt("L5")
            l6 = txt("L6")
            l7 = txt("L7")
            opis = txt("OPISDOK")
            spo = txt("SPO")
            dzl = txt("DZL")
            kwota = parse_kw(txt("KW"))

            parts = [p.strip() for p in (l5, l6, l7) if p and p.strip()]
            if parts:
                l5 = " ".join(parts)

            if op_code == "M":
                typ = "wpłata"
            elif op_code == "W":
                typ = "obciążenie"
            else:
                typ = "inna"

            d = {
                "nrb": nrb,
                "plik": os.path.basename(xml_path),
                "op": op_code,
                "dk": dk,
                "dop": dop,
                "nd": nd,
                "td": td,
                "l0": l0,
                "l1": l1,
                "l2": l2,
                "l3": l3,
                "l4": l4,
                "l5": l5,
                "opis": opis,
                "kwota": kwota,
                "typ": typ,
                "spo": spo,
                "dzl": dzl,
            }
            d["id"] = _make_op_id(d)
            operacje.append(d)

    return operacje



def wczytaj_wszystkie_operacje_z_katalogu(xml_dir="."):
    xml_dir = os.fspath(xml_dir)
    try:
        xml_files = [f for f in os.listdir(xml_dir) if f.lower().endswith(".xml")]
    except FileNotFoundError:
        print(f"Nie znaleziono katalogu XML: {xml_dir}")
        return []
    if not xml_files:
        print(f"Brak plików XML w katalogu: {xml_dir}")
        return []
    wszystkie = []
    for xf in xml_files:
        wszystkie.extend(wczytaj_operacje_z_pliku(os.path.join(xml_dir, xf)))
    return wszystkie


def policz_statystyki_dzienne(operacje):
    from collections import defaultdict
    stats = defaultdict(lambda: {"w": Decimal("0"), "o": Decimal("0")})
    for op in operacje:
        date = op.get("dk") or op.get("dop")
        if not date:
            continue
        if op.get("typ") == "wpłata":
            stats[date]["w"] += op.get("kwota", Decimal("0"))
        elif op.get("typ") == "obciążenie":
            stats[date]["o"] += op.get("kwota", Decimal("0"))

    if not stats:
        return [], [], []
    dates = sorted(stats.keys())
    wplaty = [float(stats[d]["w"]) for d in dates]
    obciaz = [float(stats[d]["o"]) for d in dates]
    return dates, wplaty, obciaz


# -------------------------
# RR manual CSV
# -------------------------

def wczytaj_rr_manual_ids(path: str = RR_MANUAL_CSV) -> set:
    ids = set()
    if not os.path.exists(path):
        return ids
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            first = f.readline()
            f.seek(0)
            if "ID" in (first or "").upper():
                r = csv.DictReader(f)
                if r.fieldnames and "ID" in r.fieldnames:
                    for row in r:
                        x = (row.get("ID") or "").strip()
                        if x:
                            ids.add(x)
                    return ids
            for line in f:
                x = (line or "").strip()
                if x and x.upper() != "ID":
                    # jeśli ktoś zapisał CSV z przecinkiem – bierz 1 kolumnę
                    x = x.split(",")[0].strip()
                    if x and x.upper() != "ID":
                        ids.add(x)
    except Exception as e:
        print(f"Nie udało się wczytać {path}: {e}")
    return ids


# -------------------------
# RR detection
# -------------------------

def czy_rr_auto(typ: str, jednostka: str, kwota: Decimal, opis_rr: str, opis_full: str) -> bool:
    if typ != "wpłata":
        return False

    text_all_raw = f"{opis_rr or ''} {opis_full or ''}".lower()
    text_all_norm = simplify_name(text_all_raw)
    if ("przeksiegowanie" in text_all_norm) or ("korekta" in text_all_norm):
        return False

    unit = (jednostka or "").lower()
    text = (opis_rr or opis_full or "").lower()

    def eq(a: Decimal, b: Decimal) -> bool:
        return abs(a - b) <= Decimal("0.01")

    kw = kwota

    if "przedszkole" in unit:
        kw_match = (kw % Decimal("35.00") == 0) and (kw <= Decimal("350.00"))
    elif "szkoła" in unit or "szkola" in unit:
        kw_match = eq(kw, Decimal("125.00")) or eq(kw, Decimal("250.00"))
    else:
        kw_match = False

    opis_match = (
        "rada" in text or "radę" in text or "rade" in text or
        "rr" in text or "składka" in text or
        text.startswith("rr") or text.endswith("rr")
    )

    return bool(opis_match or kw_match)


def czy_rr(typ: str, jednostka: str, kwota: Decimal, opis_rr: str, opis_full: str, opid: str, rr_manual_ids: set) -> bool:
    if opid and opid in rr_manual_ids:
        return True
    return czy_rr_auto(typ, jednostka, kwota, opis_rr, opis_full)


# -------------------------
# Children list
# -------------------------

def wczytaj_liste_dzieci(txt_path):
    dzieci = []
    current_label = None
    current_type = "klasa"
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            for line in f:
                s = (line or "").strip()
                if not s:
                    continue
                su = s.upper()
                if su.startswith("KLASA"):
                    current_label = s[5:].strip()
                    current_type = "klasa"
                    continue
                if su.startswith("GRUPA"):
                    current_label = s[6:].strip()
                    current_type = "grupa"
                    continue
                parts = s.split()
                if len(parts) < 2:
                    continue
                imie = parts[-1]
                nazwisko = " ".join(parts[:-1])
                dzieci.append({"klasa": current_label or "", "nazwisko": nazwisko, "imie": imie, "rodzaj": current_type})
    except FileNotFoundError:
        print(f"Nie znaleziono pliku z listą dzieci: {txt_path}")
        return []
    except Exception as e:
        print(f"Błąd czytania listy dzieci {txt_path}: {e}")
        return []
    print(f"Wczytano listę dzieci z pliku {txt_path}, liczba dzieci: {len(dzieci)}")
    return dzieci


# -------------------------
# Report HTML (operacje)
# -------------------------

def generuj_html_raport_operacji(operacje, bez_danych: bool, rr_manual_ids: set):
    suma_wplat = sum((op["kwota"] for op in operacje if op["typ"] == "wpłata"), Decimal("0"))
    suma_obc = sum((op["kwota"] for op in operacje if op["typ"] == "obciążenie"), Decimal("0"))

    ops_by_nrb = {}
    for op in operacje:
        ops_by_nrb.setdefault(op["nrb"], []).append(op)

    daty_dk = [op.get("dk") for op in operacje if op.get("dk")]
    data_od = min(daty_dk) if daty_dk else ""
    data_do = max(daty_dk) if daty_dk else ""

    # HTML header
    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<title>raport_operacji</title>
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
h1, h2 { margin-bottom: 0.2em; }
.summary { margin: 10px 0 20px 0; font-size: 14px; }
table { border-collapse: collapse; width: 100%; margin-bottom: 30px; font-size: 12px; }
th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #eee; position: sticky; top: 0; z-index: 1; }
tr.wpłata { background-color: #f6fff6; }
tr.obciążenie { background-color: #fff6f6; }
tr.inna { background-color: #f0f0ff; }
.checkbox-col, .rr-col { text-align: center; }
.rr-yes { background-color: #fff2b8; font-weight: bold; }
.muted { color: #666; font-size: 12px; }
button { padding: 6px 12px; margin-right: 8px; cursor: pointer; }
tr:target { outline: 3px solid #ff9800; }
</style>
</head>
<body>
""")

    title = f"Raport operacji ({data_od} – {data_do})" if data_od and data_do else "Raport operacji"
    parts.append("<h1>" + html.escape(title) + "</h1>")
    parts.append(
        "<div class='summary'><b>Suma wpłat:</b> "
        + f"{suma_wplat:.2f}"
        + " &nbsp; | &nbsp; <b>Suma obciążeń:</b> "
        + f"{suma_obc:.2f}"
        + "</div>"
    )
    parts.append(
        "<div class='muted'>RR ręczne jest przenośne w pliku <b>"
        + html.escape(RR_MANUAL_CSV)
        + "</b>. Jeśli uruchamiasz stronę przez http.server i plik leży obok HTML – zostanie automatycznie wczytany.</div>"
    )

    parts.append("""
<div style="margin: 12px 0 18px 0;">
  <button type="button" onclick="pobierzRRManualCSV()">Eksport RR ręczne (CSV)</button>
  <span id="rr-manual-info" class="muted" style="margin-left:12px;"></span>
</div>
""")

    for nrb, ops in ops_by_nrb.items():
        jednostka = nazwa_konta(nrb)
        parts.append("<h2>Konto: " + html.escape(nrb) + " — " + html.escape(jednostka) + "</h2>")
        parts.append("<table><thead><tr>")
        parts.append("<th>ID</th><th>Typ</th><th>Data księg.</th><th>Data oper.</th><th>Kwota</th>")
        if not bez_danych:
            parts.append("<th>L0</th><th>L1</th><th>L2</th><th>L3</th><th>L4</th><th>L5</th><th>Opis</th><th>Plik</th>")
        else:
            parts.append("<th>Opis</th><th>Plik</th>")
        parts.append("<th class='rr-col'>RR</th><th class='rr-col'>RR ręczne</th>")
        parts.append("</tr></thead><tbody>")

        for op in ops:
            opid = op.get("id", "")
            is_rr_auto = czy_rr_auto(op["typ"], jednostka, op["kwota"], op.get("l5", ""), op.get("opis", ""))
            is_rr_manual = bool(opid and opid in rr_manual_ids)
            is_rr_eff = is_rr_auto or is_rr_manual

            rr_text = "RR" if is_rr_eff else ""
            rr_class = "rr-col rr-yes rr-auto-cell" if is_rr_eff else "rr-col rr-auto-cell"

            data_attr_str = (
                ' data-opid="' + html.escape(opid) + '"'
                ' data-rr_auto="' + ("1" if is_rr_auto else "0") + '"'
                ' data-rr_manual="' + ("1" if is_rr_manual else "0") + '"'
            )

            manual_checkbox = ""
            if op["typ"] == "wpłata":
                manual_checkbox = '<input type="checkbox" class="rr-manual" onchange="toggleManualRR(this)"' + (' checked' if is_rr_manual else '') + ">"

            parts.append("<tr id='" + html.escape(opid) + "' class='" + html.escape(op["typ"]) + "'" + data_attr_str + ">")
            parts.append("<td><a href='#" + html.escape(opid) + "'>" + html.escape(opid) + "</a></td>")
            parts.append("<td>" + html.escape(op["typ"]) + "</td>")
            parts.append("<td>" + html.escape(op.get("dk") or "") + "</td>")
            parts.append("<td>" + html.escape(op.get("dop") or "") + "</td>")
            parts.append("<td>" + f"{op.get('kwota', Decimal('0')):.2f}" + "</td>")

            if not bez_danych:
                parts.append("<td>" + html.escape(op.get("l0") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("l1") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("l2") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("l3") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("l4") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("l5") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("opis") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("plik") or "") + "</td>")
            else:
                parts.append("<td>" + html.escape(op.get("opis") or "") + "</td>")
                parts.append("<td>" + html.escape(op.get("plik") or "") + "</td>")

            parts.append("<td class='" + rr_class + "'>" + html.escape(rr_text) + "</td>")
            parts.append("<td class='rr-col'>" + manual_checkbox + "</td>")
            parts.append("</tr>")

        parts.append("</tbody></table>")

    # JS (bez f-stringów)
    js = """
<script>
function effectiveRR(tr) {
  const auto = (tr.getAttribute('data-rr_auto') || '0') === '1';
  const manual = (tr.getAttribute('data-rr_manual') || '0') === '1';
  return auto || manual;
}

function refreshRowRR(tr) {
  const cell = tr.querySelector('.rr-auto-cell');
  if (!cell) return;
  if (effectiveRR(tr)) {
    cell.classList.add('rr-yes');
    cell.textContent = 'RR';
  } else {
    cell.classList.remove('rr-yes');
    cell.textContent = '';
  }
}

function toggleManualRR(chk) {
  const tr = chk.closest('tr');
  if (!tr) return;
  tr.setAttribute('data-rr_manual', chk.checked ? '1' : '0');
  refreshRowRR(tr);
}

function pobierzRRManualCSV() {
  const ids = [];
  document.querySelectorAll('tr.wpłata').forEach(tr => {
    const manual = (tr.getAttribute('data-rr_manual') || '0') === '1';
    if (!manual) return;
    const id = tr.getAttribute('data-opid') || tr.id || '';
    if (id) ids.push(id);
  });
  ids.sort();
  const lines = ['ID', ...ids];
  const blob = new Blob([lines.join('\\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);

  const a = document.createElement('a');
  a.href = url;
  a.download = '__RR_MANUAL_CSV__';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  setTimeout(() => URL.revokeObjectURL(url), 500);
}

async function zaladujRRManualZPliku() {
  try {
    const resp = await fetch('__RR_MANUAL_CSV__', { cache: 'no-store' });
    if (!resp.ok) return;
    const txt = await resp.text();
    const ids = new Set();

    const lines = txt.split(/\\r?\\n/);
    for (let i = 0; i < lines.length; i++) {
      const s = (lines[i] || '').trim();
      if (!s) continue;
      if (i === 0 && s.toUpperCase().includes('ID')) continue;
      const firstCol = s.split(',')[0].trim();
      if (firstCol && firstCol.toUpperCase() !== 'ID') ids.add(firstCol);
    }

    if (!ids.size) return;

    document.querySelectorAll('tr.wpłata').forEach(tr => {
      const id = tr.getAttribute('data-opid') || tr.id || '';
      if (!id) return;
      if (!ids.has(id)) return;
      tr.setAttribute('data-rr_manual', '1');
      const chk = tr.querySelector('input.rr-manual');
      if (chk) chk.checked = true;
      refreshRowRR(tr);
    });

    const info = document.getElementById('rr-manual-info');
    if (info) info.textContent = 'Wczytano __RR_MANUAL_CSV__ (' + ids.size + ' ID)';
  } catch (e) {
    const info = document.getElementById('rr-manual-info');
    if (info) info.textContent = 'Nie udało się wczytać __RR_MANUAL_CSV__ (otwórz przez http.server)';
  }
}

zaladujRRManualZPliku();
</script>
</body></html>
"""
    js = js.replace("__RR_MANUAL_CSV__", RR_MANUAL_CSV)
    parts.append(js)

    return "".join(parts)


def generuj_html_short(operacje, rr_manual_ids: set):
    daty_dk = [op.get("dk") for op in operacje if op.get("dk")]
    data_od = min(daty_dk) if daty_dk else ""
    data_do = max(daty_dk) if daty_dk else ""
    title = f"Raport operacji (short) {data_od} – {data_do}" if data_od and data_do else "Raport operacji (short)"

    ops_by_nrb = {}
    for op in operacje:
        ops_by_nrb.setdefault(op["nrb"], []).append(op)

    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="pl"><head><meta charset="UTF-8"><title>raport_operacji_short</title>
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0 30px 0; font-size: 12px; }
th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #eee; }
tr.wpłata { background-color: #f6fff6; }
tr.obciążenie { background-color: #fff6f6; }
.rr-col { text-align: center; }
.rr-yes { background-color: #fff2b8; font-weight: bold; }
</style></head><body>
""")
    parts.append("<h1>" + html.escape(title) + "</h1>")

    for nrb, ops in ops_by_nrb.items():
        jednostka = nazwa_konta(nrb)
        parts.append("<h2>Konto: " + html.escape(nrb) + " — " + html.escape(jednostka) + "</h2>")
        parts.append("<table><thead><tr><th>ID</th><th>Typ</th><th>Data</th><th>Kwota</th><th>L1</th><th>L5</th><th class='rr-col'>RR</th></tr></thead><tbody>")
        for op in ops:
            opid = op.get("id", "")
            is_rr_eff = (opid in rr_manual_ids) or czy_rr_auto(op["typ"], jednostka, op["kwota"], op.get("l5", ""), op.get("opis", ""))
            rr_text = "RR" if is_rr_eff else ""
            rr_cls = "rr-col rr-yes" if is_rr_eff else "rr-col"
            parts.append(
                "<tr class='" + html.escape(op["typ"]) + "' id='" + html.escape(opid) + "'>"
                "<td>" + html.escape(opid) + "</td>"
                "<td>" + html.escape(op["typ"]) + "</td>"
                "<td>" + html.escape(op.get("dk") or op.get("dop") or "") + "</td>"
                "<td>" + f"{op.get('kwota', Decimal('0')):.2f}" + "</td>"
                "<td>" + html.escape(op.get("l1") or "") + "</td>"
                "<td>" + html.escape(op.get("l5") or "") + "</td>"
                "<td class='" + rr_cls + "'>" + html.escape(rr_text) + "</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    parts.append("</body></html>")
    return "".join(parts)


# -------------------------
# RR assignments and outputs
# -------------------------

def _build_rr_assignments(xml_dir: str, dzieci_txt_path: str, rr_manual_ids: set):
    dzieci = wczytaj_liste_dzieci(dzieci_txt_path)
    if not dzieci:
        return [], [], []

    wszystkie = wczytaj_wszystkie_operacje_z_katalogu(xml_dir)
    if not wszystkie:
        return dzieci, [], [[] for _ in dzieci]

    operacje_rr = []
    for op in wszystkie:
        jednostka = nazwa_konta(op["nrb"])
        if not czy_rr(op["typ"], jednostka, op.get("kwota", Decimal("0")), op.get("l5", ""), op.get("opis", ""), op.get("id", ""), rr_manual_ids):
            continue
        text_fields = " ".join([op.get("l0",""), op.get("l1",""), op.get("l2",""), op.get("l3",""), op.get("l4",""), op.get("l5",""), op.get("opis","")]).lower()
        op2 = dict(op)
        op2["jednostka"] = jednostka
        op2["_search_text"] = text_fields
        operacje_rr.append(op2)

    if not operacje_rr:
        print("Nie znaleziono żadnych operacji zakwalifikowanych jako RR (auto ani ręcznie).")
        return dzieci, [], [[] for _ in dzieci]

    print(f"Liczba operacji RR: {len(operacje_rr)}")

    przedszkole_kwoty = [Decimal(str(35*i)) for i in range(1, 11)]
    szkola_kwoty = [Decimal("125.00"), Decimal("250.00")]

    przypisane = [[] for _ in dzieci]

    for op in operacje_rr:
        t_all = op["_search_text"]
        t_all_norm = simplify_name(t_all)
        t_l5 = (op.get("l5") or "").lower()
        t_l5_norm = simplify_name(t_l5)

        def match_strength_for_child(dziecko) -> int:
            """Zwraca siłę dopasowania danych dziecka do opisu przelewu.
            3 = bardzo mocne (imię+nazwisko) w L5, 2 = mocne w całym tekście, 1 = samo nazwisko, 0 = brak.
            """
            nazwisko = dziecko["nazwisko"]
            imie = dziecko["imie"]

            nazw_l = (nazwisko or "").lower()
            im_l = (imie or "").lower()

            full = f"{nazw_l} {im_l}"
            rev = f"{im_l} {nazw_l}"

            nazw_core = nazw_l[:-1] if len(nazw_l) > 3 else nazw_l
            full_core = f"{nazw_core} {im_l}"
            rev_core = f"{im_l} {nazw_core}"

            full_norm = simplify_name(full)
            rev_norm = simplify_name(rev)
            full_core_norm = simplify_name(full_core)
            rev_core_norm = simplify_name(rev_core)
            nazw_norm = simplify_name(nazw_l)
            nazw_core_norm = simplify_name(nazw_core)

            if (full in t_l5 or rev in t_l5 or full_core in t_l5 or rev_core in t_l5 or
                full_norm in t_l5_norm or rev_norm in t_l5_norm or full_core_norm in t_l5_norm or rev_core_norm in t_l5_norm):
                return 3

            if (full in t_all or rev in t_all or full_core in t_all or rev_core in t_all or
                full_norm in t_all_norm or rev_norm in t_all_norm or full_core_norm in t_all_norm or rev_core_norm in t_all_norm):
                return 2

            if (nazw_l in t_all or nazw_core in t_all or nazw_norm in t_all_norm or nazw_core_norm in t_all_norm):
                return 1

            return 0

        kwota = op["kwota"]
        is_prz = any(abs(kwota - k) <= Decimal("0.01") for k in przedszkole_kwoty)
        is_sz = any(abs(kwota - k) <= Decimal("0.01") for k in szkola_kwoty)

        best_strength = 0
        best_i = None

        for i, dziecko in enumerate(dzieci):
            if is_prz and not is_sz:
                if dziecko.get("rodzaj") != "grupa":
                    continue
            elif is_sz and not is_prz:
                if dziecko.get("rodzaj") != "klasa":
                    continue

            strength = match_strength_for_child(dziecko)

            if strength > best_strength:
                best_strength = strength
                best_i = i
                if best_strength == 3:
                    break


        # OSTATNIA PRÓBA: jeśli nic nie pasuje wg dotychczasowych reguł (w tym filtrów kwoty),
        # to nie blokuj przypisania kwotą — spróbuj przypisać po pełnych danych dziecka w opisie przelewu.
        if best_i is None:
            best_strength2 = 0
            best_i2 = None
            for i, dziecko in enumerate(dzieci):
                s = match_strength_for_child(dziecko)

                # W fallbacku wymagamy konkretu: imię+nazwisko (siła 2 lub 3),
                # żeby uniknąć błędnych przypisań po samym nazwisku.
                if s >= 2 and s > best_strength2:
                    best_strength2 = s
                    best_i2 = i
                    if best_strength2 == 3:
                        break

            if best_i2 is not None:
                best_i = best_i2
        if best_i is not None:
            przypisane[best_i].append(op)

    return dzieci, operacje_rr, przypisane


def podsumowanie_rr(xml_dir: str, dzieci_txt_path: str, rr_manual_ids: set, out_dir: str):
    dzieci, operacje_rr, przypisane = _build_rr_assignments(xml_dir, dzieci_txt_path, rr_manual_ids)
    if not dzieci or not operacje_rr:
        return

    max_wplat = max((len(ops) for ops in przypisane), default=0)
    fieldnames = ["Klasa", "Nazwisko", "Imię"]
    for i in range(1, max_wplat + 1):
        fieldnames += [f"Data wpłaty {i}", f"Kwota {i}", f"ID wpłaty {i}"]
    fieldnames.append("Suma wpłat")

    rows = []
    last_label = None
    for i, d in enumerate(dzieci):
        label = d.get("klasa") or ""
        if last_label is not None and label != last_label:
            rows.append({fn: "" for fn in fieldnames})
        last_label = label

        ops_child = przypisane[i]
        row = {"Klasa": label, "Nazwisko": d["nazwisko"], "Imię": d["imie"]}
        for k in fieldnames:
            row.setdefault(k, "")
        if ops_child:
            suma = sum((op["kwota"] for op in ops_child), Decimal("0"))
            row["Suma wpłat"] = f"{suma:.2f}"
            for idx, op in enumerate(ops_child, start=1):
                if idx > max_wplat:
                    break
                row[f"Data wpłaty {idx}"] = op.get("dk") or op.get("dop") or ""
                row[f"Kwota {idx}"] = f"{op.get('kwota',Decimal('0')):.2f}"
                row[f"ID wpłaty {idx}"] = op.get("id") or ""
        rows.append(row)

        out_csv_path = os.path.join(out_dir, OUTPUT_CSV_RR)

    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Zapisano podsumowanie RR do pliku: {out_csv_path}")


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def generuj_weryfikacje_rr_html(xml_dir: str, dzieci_txt_path: str, rr_manual_ids: set, out_dir: str):
    dzieci, operacje_rr, przypisane = _build_rr_assignments(xml_dir, dzieci_txt_path, rr_manual_ids)
    if not dzieci:
        return
    if not operacje_rr:
        print("Brak operacji RR do weryfikacji.")
        return

    # usuń przeksięgowania
    def _is_przeksiegowanie(op):
        l5n = simplify_name((op.get("l5") or "").lower())
        return "przeksiegowanie" in l5n

    operacje_rr = [op for op in operacje_rr if not _is_przeksiegowanie(op)]

    semestry = _estimate_semesters_from_ops(operacje_rr)
    miesiace = _estimate_months_from_ops(operacje_rr)

    STAWKA_SZKOLA = Decimal("125.00")
    STAWKA_PRZEDSZKOLE_MIES = Decimal("35.00")

    # map rodzin
    surname_to_children = {}
    for idx, d in enumerate(dzieci):
        key = family_surname_key(d.get("nazwisko", ""))
        surname_to_children.setdefault(key, []).append((idx, d))

    # map nazwisko->operacje (unikalne ID)
    surname_to_ops = {}
    opid_to_surname = {}

    for child_ops, child in zip(przypisane, dzieci):
        sn_key = family_surname_key(child.get("nazwisko", ""))
        for op in child_ops:
            oid = op.get("id") or ""
            if not oid:
                continue
            if oid in opid_to_surname and opid_to_surname[oid] != sn_key:
                raise RuntimeError(f"Duplikat ID RR: {oid} -> {opid_to_surname[oid]} i {sn_key}")
            opid_to_surname[oid] = sn_key
            surname_to_ops.setdefault(sn_key, {})[oid] = op

    przypisane_ids = set(opid_to_surname.keys())
    nieprzypisane = [op for op in operacje_rr if (op.get("id") or "") not in przypisane_ids]

    # próba dopasowania po płatniku (L1)
    for op in nieprzypisane:
        oid = op.get("id") or ""
        if not oid or oid in opid_to_surname:
            continue
        l1 = (op.get("l1") or "").strip()
        payer_surname = l1.split(maxsplit=1)[0] if l1 else ""
        sn_key = family_surname_key(payer_surname)
        if sn_key and sn_key in surname_to_children:
            opid_to_surname[oid] = sn_key
            surname_to_ops.setdefault(sn_key, {})[oid] = op

    przypisane_ids = set(opid_to_surname.keys())
    nieprzypisane = [op for op in operacje_rr if (op.get("id") or "") not in przypisane_ids]

    # global stats
    licz_dzieci_sz = sum(1 for d in dzieci if d.get("rodzaj") == "klasa")
    licz_dzieci_prz = sum(1 for d in dzieci if d.get("rodzaj") == "grupa")

    ocz_sum_sz = STAWKA_SZKOLA * Decimal(licz_dzieci_sz * semestry)
    ocz_sum_prz = STAWKA_PRZEDSZKOLE_MIES * Decimal(licz_dzieci_prz * miesiace)
    ocz_sum_all = ocz_sum_sz + ocz_sum_prz

    wykon_sum_all = sum((op.get("kwota", Decimal("0")) for op in operacje_rr), Decimal("0"))
    wykon_sum_sz = sum((op["kwota"] for op in operacje_rr if (op.get("jednostka") or "") == "Szkoła"), Decimal("0"))
    wykon_sum_prz = sum((op["kwota"] for op in operacje_rr if (op.get("jednostka") or "") == "Przedszkole"), Decimal("0"))

    def row_cls(diff: Decimal) -> str:
        if abs(diff) <= Decimal("0.01"):
            return "ok"
        return "warn" if diff > 0 else "bad"

    def op_link(op):
        opid = op.get("id") or ""
        if not opid:
            return ""
        return '<a href="' + html.escape(OUTPUT_HTML) + "#" + html.escape(opid) + '">' + html.escape(opid) + "</a>"

    # BUILD HTML
    parts = []
    parts.append("""<!DOCTYPE html><html lang="pl"><head><meta charset="UTF-8">
<title>Weryfikacja RR</title>
<style>
body { font-family: Arial, sans-serif; margin: 20px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0 30px 0; font-size: 12px; }
th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #eee; }
.ok { background: #45b9ac; }
.warn { background: #67cbc1; }
.bad { background: #f498bd; }
.muted { color: #666; font-size: 12px; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; }
</style></head><body>
""")

    parts.append("<h1>Zestawienie wpłat na Radę Rodziców ZSP Mikołów</h1>")
    parts.append("<h2>Weryfikacja przypisań RR (Podsumowanie_RR)</h2>")
    parts.append(
        "<p class='muted'>"
        "<b>Heurystyka:</b><br>"
        "Szkoła – semestry = <b>" + str(semestry) + "</b> (na podstawie miesięcy w danych RR),<br>"
        "Przedszkole – miesiące = <b>" + str(miesiace) + "</b> (unikalne rok+miesiąc w danych RR).<br>"
        "<b>Stawki:</b><br>"
        "Szkoła 125 zł / semestr / dziecko,<br>"
        "Przedszkole 35 zł / miesiąc / dziecko."
        "</p>"
    )

    # Statystyki globalne
    parts.append("<h2>Statystyki globalne</h2>")
    parts.append("<table><thead><tr><th>Obszar</th><th>Dzieci</th><th>Oczekiwana suma</th><th>Wykryta suma RR</th><th>Różnica</th><th>Wykryte wpłaty RR</th></tr></thead><tbody>")

    diff_sz = wykon_sum_sz - ocz_sum_sz
    diff_prz = wykon_sum_prz - ocz_sum_prz
    diff_all = wykon_sum_all - ocz_sum_all

    parts.append("<tr class='" + row_cls(diff_sz) + "'><td>Szkoła</td><td>" + str(licz_dzieci_sz) + "</td><td>" + f"{ocz_sum_sz:.2f}" + "</td><td>" + f"{wykon_sum_sz:.2f}" + "</td><td>" + f"{diff_sz:.2f}" + "</td><td>" + str(sum(1 for op in operacje_rr if op.get("jednostka") == "Szkoła")) + "</td></tr>")
    parts.append("<tr class='" + row_cls(diff_prz) + "'><td>Przedszkole</td><td>" + str(licz_dzieci_prz) + "</td><td>" + f"{ocz_sum_prz:.2f}" + "</td><td>" + f"{wykon_sum_prz:.2f}" + "</td><td>" + f"{diff_prz:.2f}" + "</td><td>" + str(sum(1 for op in operacje_rr if op.get("jednostka") == "Przedszkole")) + "</td></tr>")
    parts.append("<tr class='" + row_cls(diff_all) + "'><td>Razem</td><td>" + str(len(dzieci)) + "</td><td>" + f"{ocz_sum_all:.2f}" + "</td><td>" + f"{wykon_sum_all:.2f}" + "</td><td>" + f"{diff_all:.2f}" + "</td><td>" + str(len(operacje_rr)) + "</td></tr>")

    parts.append("</tbody></table>")

    # Per nazwisko
    parts.append("<h2>Wpłaty RR per nazwisko (rodzeństwa)</h2>")
    parts.append("<table><thead><tr><th>Nazwisko</th><th>Dzieci (klasa/grupa)</th><th>Oczekiwana suma</th><th>Wykryta suma</th><th>Różnica</th><th>Wpłaty RR (ID)</th></tr></thead><tbody>")

    per_rows_csv = []

    for sn_key, children_list in sorted(surname_to_children.items(), key=lambda x: x[0]):
        sn_display = children_list[0][1].get("nazwisko", "")
        child_labels = []
        ocz = Decimal("0")

        for _idx, d in children_list:
            label = (d.get("klasa") or "").strip()
            child_labels.append(f"{d.get('imie','')} ({label})" if label else d.get("imie",""))
            if d.get("rodzaj") == "klasa":
                ocz += STAWKA_SZKOLA * Decimal(str(semestry))
            else:
                ocz += STAWKA_PRZEDSZKOLE_MIES * Decimal(str(miesiace))

        ops = list(surname_to_ops.get(sn_key, {}).values())
        ops_sorted = sorted(ops, key=lambda o: (o.get("dk") or o.get("dop") or "", str(o.get("kwota") or "")))
        wyk = sum((op.get("kwota", Decimal("0")) for op in ops_sorted), Decimal("0"))
        diff = wyk - ocz

        links = "<br>".join([
            op_link(op) + " <span class='muted'>(" + html.escape((op.get("dk") or op.get("dop") or "")) + " / " + f"{op.get('kwota',Decimal('0')):.2f}" + ")</span>"
            for op in ops_sorted
        ])

        parts.append(
            "<tr class='" + row_cls(diff) + "'>"
            "<td><b>" + html.escape(sn_display) + "</b></td>"
            "<td>" + html.escape(", ".join(child_labels)) + "</td>"
            "<td>" + f"{ocz:.2f}" + "</td>"
            "<td>" + f"{wyk:.2f}" + "</td>"
            "<td>" + f"{diff:.2f}" + "</td>"
            "<td class='mono'>" + links + "</td>"
            "</tr>"
        )

        wplaty_items = [f"{op.get('id','')} | {op.get('kwota',Decimal('0')):.2f} | {(op.get('dk') or op.get('dop') or '')}" for op in ops_sorted]
        per_rows_csv.append({
            "Nazwisko": sn_display,
            "Dzieci (klasa/grupa)": ", ".join(child_labels),
            "Oczekiwana suma": float(ocz),
            "Wykryta suma (po nazwisku)": float(wyk),
            "Różnica": float(diff),
            "Wpłaty RR (ID)": "; ".join(wplaty_items),
        })

    parts.append("</tbody></table>")

    # Nieprzypisane RR
    parts.append("<h2>Wpłaty RR nieprzypisane do dziecka</h2>")
    if not nieprzypisane:
        parts.append("<p class='muted'>Brak nieprzypisanych wpłat RR.</p>")
    else:
        parts.append("<table><thead><tr><th>ID</th><th>Data</th><th>Kwota</th><th>Jednostka</th><th>L1</th><th>L5</th><th>Opis</th></tr></thead><tbody>")
        for op in sorted(nieprzypisane, key=lambda o: (o.get("dk") or o.get("dop") or "", str(o.get("kwota") or ""))):
            parts.append(
                "<tr>"
                "<td class='mono'>" + op_link(op) + "</td>"
                "<td>" + html.escape(op.get("dk") or op.get("dop") or "") + "</td>"
                "<td>" + f"{op.get('kwota',Decimal('0')):.2f}" + "</td>"
                "<td>" + html.escape(op.get("jednostka") or "") + "</td>"
                "<td>" + html.escape(op.get("l1") or "") + "</td>"
                "<td>" + html.escape(op.get("l5") or "") + "</td>"
                "<td>" + html.escape(op.get("opis") or "") + "</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    # WPŁATY NIEOZNACZONE JAKO RR (NIE-RR)
    wszystkie_ops = wczytaj_wszystkie_operacje_z_katalogu(xml_dir)
    nie_rr_wplaty = []
    for op in wszystkie_ops:
        if op.get("typ") != "wpłata":
            continue
        jednostka = nazwa_konta(op.get("nrb", ""))
        if czy_rr(op.get("typ"), jednostka, op.get("kwota", Decimal("0")), op.get("l5", ""), op.get("opis", ""), op.get("id", ""), rr_manual_ids):
            continue
        nie_rr_wplaty.append(op)

    parts.append(f"<h2>WPŁATY NIEOZNACZONE JAKO RR ({len(nie_rr_wplaty)})</h2>")

    if not nie_rr_wplaty:
        parts.append("<p class='muted'>Brak wpłat NIE-RR w tym okresie.</p>")
    else:
        parts.append("<table><thead><tr><th>ID</th><th>Data</th><th>Kwota</th><th>Jednostka</th><th>NRB</th><th>L1</th><th>L5</th><th>Opis</th><th>Plik XML</th></tr></thead><tbody>")
        for op in sorted(nie_rr_wplaty, key=lambda o: (o.get("dk") or o.get("dop") or "", str(o.get("kwota") or ""))):
            opid = op.get("id") or ""
            link = '<a href="' + html.escape(OUTPUT_HTML) + "#" + html.escape(opid) + '">' + html.escape(opid) + "</a>" if opid else ""
            parts.append(
                "<tr>"
                "<td class='mono'>" + link + "</td>"
                "<td>" + html.escape(op.get("dk") or op.get("dop") or "") + "</td>"
                "<td>" + f"{(op.get('kwota') or Decimal('0')):.2f}" + "</td>"
                "<td>" + html.escape(nazwa_konta(op.get("nrb",""))) + "</td>"
                "<td class='mono'>" + html.escape(op.get("nrb") or "") + "</td>"
                "<td>" + html.escape(op.get("l1") or "") + "</td>"
                "<td>" + html.escape(op.get("l5") or "") + "</td>"
                "<td>" + html.escape(op.get("opis") or "") + "</td>"
                "<td>" + html.escape(op.get("plik") or "") + "</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    parts.append("</body></html>")

    out_verify_html_path = os.path.join(out_dir, OUTPUT_HTML_RR_VERIFY)

    with open(out_verify_html_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    print(f"Zapisano weryfikację RR do pliku: {out_verify_html_path}")

    # CSV-y weryfikacji
    base, _ = os.path.splitext(out_verify_html_path)

    # globalne
    global_rows = [
        {"Obszar": "Szkoła", "Dzieci": str(licz_dzieci_sz),
         "Oczekiwana suma": f"{ocz_sum_sz:.2f}", "Wykryta suma RR": f"{wykon_sum_sz:.2f}",
         "Różnica": f"{(wykon_sum_sz-ocz_sum_sz):.2f}", "Wykryte wpłaty RR": str(sum(1 for op in operacje_rr if op.get("jednostka") == "Szkoła"))},
        {"Obszar": "Przedszkole", "Dzieci": str(licz_dzieci_prz),
         "Oczekiwana suma": f"{ocz_sum_prz:.2f}", "Wykryta suma RR": f"{wykon_sum_prz:.2f}",
         "Różnica": f"{(wykon_sum_prz-ocz_sum_prz):.2f}", "Wykryte wpłaty RR": str(sum(1 for op in operacje_rr if op.get("jednostka") == "Przedszkole"))},
        {"Obszar": "Razem", "Dzieci": str(len(dzieci)),
         "Oczekiwana suma": f"{ocz_sum_all:.2f}", "Wykryta suma RR": f"{wykon_sum_all:.2f}",
         "Różnica": f"{(wykon_sum_all-ocz_sum_all):.2f}", "Wykryte wpłaty RR": str(len(operacje_rr))},
    ]
    _write_csv(base + "_statystyki_globalne.csv",
               ["Obszar", "Dzieci", "Oczekiwana suma", "Wykryta suma RR", "Różnica", "Wykryte wpłaty RR"],
               global_rows)
    print(f"Zapisano statystyki globalne RR do pliku: {base + '_statystyki_globalne.csv'}")

    _write_csv(base + "_wplaty_per_nazwisko.csv",
               ["Nazwisko", "Dzieci (klasa/grupa)", "Oczekiwana suma", "Wykryta suma (po nazwisku)", "Różnica", "Wpłaty RR (ID)"],
               per_rows_csv)
    print(f"Zapisano wpłaty per nazwisko do pliku: {base + '_wplaty_per_nazwisko.csv'}")

    rows_np = []
    for op in sorted(nieprzypisane, key=lambda o: (o.get("dk") or o.get("dop") or "", str(o.get("kwota") or ""))):
        rows_np.append({
            "ID": op.get("id", ""),
            "Data": op.get("dk") or op.get("dop") or "",
            "Kwota": f"{(op.get('kwota') or Decimal('0')):.2f}",
            "Jednostka": op.get("jednostka") or "",
            "L1": op.get("l1") or "",
            "L5": op.get("l5") or "",
            "Opis": op.get("opis") or "",
        })
    _write_csv(base + "_nieprzypisane.csv", ["ID","Data","Kwota","Jednostka","L1","L5","Opis"], rows_np)
    print(f"Zapisano nieprzypisane wpłaty RR do pliku: {base + '_nieprzypisane.csv'}")

    rows_nie_rr = []
    for op in sorted(nie_rr_wplaty, key=lambda o: (o.get("dk") or o.get("dop") or "", str(o.get("kwota") or ""))):
        rows_nie_rr.append({
            "ID": op.get("id",""),
            "Data": op.get("dk") or op.get("dop") or "",
            "Kwota": f"{(op.get('kwota') or Decimal('0')):.2f}",
            "Jednostka": nazwa_konta(op.get("nrb","")),
            "NRB": op.get("nrb") or "",
            "L1": op.get("l1") or "",
            "L5": op.get("l5") or "",
            "Opis": op.get("opis") or "",
            "Plik XML": op.get("plik") or "",
        })
    _write_csv(base + "_nie_RR.csv", ["ID","Data","Kwota","Jednostka","NRB","L1","L5","Opis","Plik XML"], rows_nie_rr)
    print(f"Zapisano wpłaty NIE-RR do pliku: {base + '_nie_RR.csv'}")


# -------------------------
# Main (domyślnie: --weryfikacja-rr)
# -------------------------

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser(description="ZSP – analiza XML + raporty HTML + rozliczenie RR")
    ap.add_argument("--xml-dir", default=".", help="Katalog z plikami *.xml (wejście).")
    ap.add_argument("--out-dir", default="", help="Katalog wyników. Jeśli pusty: <folder skryptu>/ID_YYYY-MM-DD_HH-MM-SS")
    ap.add_argument("--children-txt", default="", help="Ścieżka do ListaDzieciZSP.txt (wejście).")
    ap.add_argument("--rr-manual", default="", help="Ścieżka do RR_manual.csv (opcjonalnie).")

    ap.add_argument("--bez_danych", action="store_true", help="Raport operacji bez danych szczegółowych (L0..L5).")

    ap.add_argument("--raport", "--report", dest="raport", action="store_true", help="Generuj raporty operacji (HTML).")
    ap.add_argument("--weryfikacja-rr", "--rr-weryfikacja", dest="rr_verify", action="store_true", help="Generuj podsumowanie RR + weryfikację (HTML+CSV).")
    ap.add_argument("--podsumowanie-rr", "--podsumienie-rr", dest="only_rr", action="store_true", help="Generuj tylko Podsumowanie_RR.csv.")

    args, unknown = ap.parse_known_args()
    # kompatybilność: stary tryb wywołania z argumentem 'RR'
    if any((u or "").lower() == "rr" for u in unknown):
        args.only_rr = True

    # DOMYŚLNIE: raport + weryfikacja RR
    if not (args.raport or args.rr_verify or args.only_rr):
        args.raport = True
        args.rr_verify = True

    xml_dir = Path(args.xml_dir).expanduser().resolve()
    if not xml_dir.is_dir():
        raise FileNotFoundError(f"Nie znaleziono katalogu XML: {xml_dir}")

    # out_dir: zawsze w folderze skryptu (ID_...) jeśli nie podano jawnie
    if args.out_dir.strip():
        out_dir = Path(args.out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = make_run_output_dir()

    # Kopiuj pliki pomocnicze do out_dir (żeby HTML działał „przenośnie”)
    dzieci_dst = out_dir / DZIECI_TXT
    if args.children_txt.strip():
        _copy_optional(args.children_txt.strip(), dzieci_dst)
    else:
        # spróbuj w bieżącym katalogu
        cand = Path.cwd() / DZIECI_TXT
        if cand.exists():
            _copy_optional(str(cand), dzieci_dst)

    rr_manual_dst = out_dir / RR_MANUAL_CSV
    if args.rr_manual.strip():
        _copy_optional(args.rr_manual.strip(), rr_manual_dst)
    else:
        cand = Path.cwd() / RR_MANUAL_CSV
        if cand.exists():
            _copy_optional(str(cand), rr_manual_dst)

    rr_manual_ids = wczytaj_rr_manual_ids(str(rr_manual_dst))

    print(f"Katalog XML (wejście): {xml_dir}")
    print(f"Katalog wyników:      {out_dir}")

    # raport operacji
    if args.raport:
        wszystkie = wczytaj_wszystkie_operacje_z_katalogu(xml_dir)
        if wszystkie:
            html_content = generuj_html_raport_operacji(wszystkie, args.bez_danych, rr_manual_ids)
            out_html = out_dir / OUTPUT_HTML
            out_html.write_text(html_content, encoding="utf-8")
            print(f"Wygenerowano raport: {out_html}")

            html_short = generuj_html_short(wszystkie, rr_manual_ids)
            out_html_short = out_dir / OUTPUT_HTML_SHORT
            out_html_short.write_text(html_short, encoding="utf-8")
            print(f"Wygenerowano raport skrócony: {out_html_short}")

    # tylko RR (CSV)
    if args.only_rr:
        podsumowanie_rr(str(xml_dir), str(dzieci_dst), rr_manual_ids, str(out_dir))
        return

    # RR verify
    if args.rr_verify:
        podsumowanie_rr(str(xml_dir), str(dzieci_dst), rr_manual_ids, str(out_dir))
        generuj_weryfikacje_rr_html(str(xml_dir), str(dzieci_dst), rr_manual_ids, str(out_dir))
        return


if __name__ == "__main__":
    main()
