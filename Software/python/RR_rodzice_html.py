#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import csv
import html
import argparse
import hashlib
from datetime import datetime


DEFAULT_VERIFY_CSV = "Weryfikacja_RR_wplaty_per_nazwisko.csv"
DEFAULT_OUT_DIR = None  # domyślnie: <katalog verify-csv>/RR_rodzice


# -------------------------
# Utils
# -------------------------
def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    mapping = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o",
        "ś": "s", "ż": "z", "ź": "z",
        "ä": "a", "ö": "o", "ü": "u", "ß": "ss",
    }
    for a, b in mapping.items():
        text = text.replace(a, b)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "nazwisko"


def parse_class_labels(children_field: str):
    """
    Wejście (jak w CSV), np:
      "Jan (1A), Ala (1A)"
      "Ola (Zajączki), ..."
    Zwraca unikalne etykiety z nawiasów: ["1A"] / ["Zajączki"] / ...
    """
    s = children_field or ""
    labels = re.findall(r"\(([^)]+)\)", s)
    out, seen = [], set()
    for lab in labels:
        lab = lab.strip()
        if not lab:
            continue
        key = lab.lower()
        if key not in seen:
            seen.add(key)
            out.append(lab)
    return out


def tokenize_filename(surname: str, salt: str | None, token_len: int = 8) -> str:
    """
    Jeśli podasz --salt, nazwa pliku będzie miała klucz/hash:
      kowalski-1a2b3c4d.html
    Jeśli nie podasz --salt:
      kowalski.html
    """
    base = slugify(surname)
    if not salt:
        return f"{base}.html"
    h = hashlib.sha1((salt + "|" + surname).encode("utf-8", errors="ignore")).hexdigest()[:token_len]
    return f"{base}-{h}.html"


def read_verify_csv(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Nie znaleziono pliku CSV: {path}")

    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)

        required = [
            "Nazwisko",
            "Dzieci (klasa/grupa)",
            "Oczekiwana suma",
            "Wykryta suma (po nazwisku)",
            "Różnica",
            "Wpłaty RR (ID)",
        ]
        fieldnames = r.fieldnames or []
        for k in required:
            if k not in fieldnames:
                raise ValueError(f"CSV nie ma wymaganej kolumny: {k}. Jest: {fieldnames}")

        for row in r:
            if not (row.get("Nazwisko") or "").strip():
                continue
            rows.append({k: (row.get(k) or "").strip() for k in required})

    return rows


def _parse_payments_field(raw: str):
    """
    Obsługuje formaty w kolumnie "Wpłaty RR (ID)":

    1) Nowy format:
       "ID | kwota | data; ID2 | kwota2 | data2"
       - separator wpisów: ';'
       - separator pól: '|'

    2) Stary format:
       "ID1, ID2, ID3" albo "ID1;ID2" albo pojedynczy "ID"

    Zwraca listę krotek: [(id, kwota, data), ...]
    """
    s = (raw or "").strip()
    if not s:
        return []

    if "|" in s:
        payments = []
        for entry in s.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            parts = [p.strip() for p in entry.split("|")]
            pid = parts[0] if len(parts) >= 1 else ""
            amount = parts[1] if len(parts) >= 2 else ""
            date = parts[2] if len(parts) >= 3 else ""
            if pid:
                payments.append((pid, amount, date))
        if payments:
            return payments

    ids = re.split(r"[;,]\s*", s)
    ids = [i.strip() for i in ids if i.strip()]
    return [(pid, "", "") for pid in ids]


def to_float(x: str):
    try:
        return float((x or "").replace(",", "."))
    except Exception:
        return None


def status_from_diff(diff_str: str):
    d = to_float(diff_str)
    if d is None:
        return ("brak danych", "muted", "—")
    if abs(d) <= 0.01:
        return ("OK (rozliczone)", "ok", "✓")
    if d > 0:
        return ("Nadpłata", "warn", "↑")
    return ("Niedopłata", "bad", "!")


def fmt_pln(value: str) -> str:
    """
    Nie zmieniamy liczby (stringu) — tylko dopisujemy PLN.
    """
    v = (value or "").strip()
    if not v:
        return ""
    return f"{html.escape(v)} PLN"


# -------------------------
# HTML
# -------------------------
def _payments_list_html(payments: list[tuple[str, str, str]]) -> str:
    if not payments:
        return "<p class='muted'>Brak wykrytych wpłat w tym okresie.</p>"

    items = []
    for pid, amount, date in payments:
        parts = [f"<code>{html.escape(pid)}</code>"]
        if amount:
            parts.append(f"<span class='pmeta'>— {html.escape(amount)} PLN</span>")
        if date:
            parts.append(f"<span class='pmeta'>({html.escape(date)})</span>")
        items.append("<li>" + " ".join(parts) + "</li>")
    return "<ul class='plist'>" + "".join(items) + "</ul>"


def html_page_for_family(row: dict, title_suffix: str = "") -> str:
    nazwisko = row["Nazwisko"]
    dzieci = row["Dzieci (klasa/grupa)"]
    ocz = row["Oczekiwana suma"]
    wyk = row["Wykryta suma (po nazwisku)"]
    diff = row["Różnica"]
    wplaty_raw = row["Wpłaty RR (ID)"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    status_text, status_cls, status_icon = status_from_diff(diff)

    payments = _parse_payments_field(wplaty_raw)
    payments_html = _payments_list_html(payments)

    dzieci_html = html.escape(dzieci) if dzieci else '<span class="muted">brak danych</span>'

    # Uwaga: sekcja 💳 NA SAMYM DOLE (zgodnie z prośbą)
    html_out = (
        "<!DOCTYPE html>\n"
        "<html lang='pl'>\n"
        "<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
        f"<title>RR – {html.escape(nazwisko)}{html.escape(title_suffix)}</title>\n"
        "<style>\n"
        ":root{--bg:#0b1220;--card:#0f1a2e;--card2:#0d1729;--text:#e9eef8;--muted:#a7b3c8;"
        "--line:rgba(255,255,255,.10);--ok:#2fd3b4;--warn:#ffcc66;--bad:#ff6b9a;--info:#5aa7ff;}\n"
        "html,body{background:linear-gradient(180deg,#071023 0%,#050b16 100%);color:var(--text);"
        "font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;}\n"
        ".wrap{max-width:920px;margin:0 auto;padding:22px 16px 40px;}\n"
        ".top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px;}\n"
        ".brand{font-weight:800;letter-spacing:.2px;}\n"
        ".time{color:var(--muted);font-size:13px;}\n"
        ".hero{background:radial-gradient(1200px 400px at 10% 0%,rgba(90,167,255,.25),transparent 60%),"
        "linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));"
        "border:1px solid var(--line);border-radius:18px;padding:18px 18px 14px;box-shadow:0 18px 60px rgba(0,0,0,.35);}\n"
        ".hrow{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap;}\n"
        "h1{margin:0;font-size:26px;line-height:1.2;}\n"
        ".subtitle{margin-top:6px;color:var(--muted);font-size:14px;}\n"

        ".status{min-width:280px;flex:0 0 auto;border-radius:18px;padding:14px;border:1px solid var(--line);"
        "background:rgba(255,255,255,.04);}\n"
        ".status .lbl{color:var(--muted);font-size:12px;margin-bottom:8px;letter-spacing:.28px;text-transform:uppercase;}\n"
        ".pill{display:flex;align-items:center;gap:12px;border-radius:16px;padding:16px 16px;font-weight:900;"
        "font-size:20px;line-height:1.1;box-shadow:0 14px 34px rgba(0,0,0,.35);}\n"
        ".pill .ico{width:40px;height:40px;border-radius:14px;display:grid;place-items:center;"
        "font-size:20px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.14);}\n"
        ".pill.ok{background:linear-gradient(90deg,rgba(47,211,180,.28),rgba(47,211,180,.06));"
        "border:1px solid rgba(47,211,180,.55);box-shadow:0 16px 40px rgba(47,211,180,.14),0 14px 34px rgba(0,0,0,.35);}\n"
        ".pill.warn{background:linear-gradient(90deg,rgba(255,204,102,.28),rgba(255,204,102,.06));"
        "border:1px solid rgba(255,204,102,.55);box-shadow:0 16px 40px rgba(255,204,102,.14),0 14px 34px rgba(0,0,0,.35);}\n"
        ".pill.bad{background:linear-gradient(90deg,rgba(255,107,154,.28),rgba(255,107,154,.06));"
        "border:1px solid rgba(255,107,154,.60);box-shadow:0 16px 40px rgba(255,107,154,.16),0 14px 34px rgba(0,0,0,.35);}\n"
        ".pill.muted{background:linear-gradient(90deg,rgba(167,179,200,.20),rgba(167,179,200,.05));"
        "border:1px solid rgba(167,179,200,.30);}\n"

        ".grid{display:grid;grid-template-columns:1fr;gap:12px;margin-top:14px;}\n"
        "@media(min-width:820px){.grid{grid-template-columns:1fr 1fr;}}\n"
        ".card{background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.02));"
        "border:1px solid var(--line);border-radius:18px;padding:16px;}\n"
        ".card h2{margin:0 0 10px 0;font-size:16px;letter-spacing:.2px;}\n"
        ".kv{display:grid;grid-template-columns:1fr;gap:10px;}\n"
        ".row{background:rgba(0,0,0,.18);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:12px;}\n"
        ".k{color:var(--muted);font-size:12px;margin-bottom:4px;}\n"
        ".v{font-size:18px;font-weight:780;}\n"
        ".note{color:var(--muted);font-size:13px;line-height:1.5;margin-top:10px;}\n"
        "code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;"
        "background:rgba(0,0,0,.25);padding:2px 8px;border-radius:10px;border:1px solid rgba(255,255,255,.10);}\n"
        ".muted{color:var(--muted);}\n"
        ".plist{margin:8px 0 0 18px;}\n"
        ".plist li{margin:6px 0;}\n"
        ".pmeta{color:var(--muted);font-size:13px;margin-left:6px;}\n"
        ".payinfo{margin-top:14px;border-radius:18px;padding:16px;border:1px solid rgba(90,167,255,.35);"
        "background:radial-gradient(900px 260px at 10% 0%,rgba(90,167,255,.28),transparent 60%),"
        "linear-gradient(180deg,rgba(90,167,255,.10),rgba(255,255,255,.02));}\n"
        ".payinfo h2{display:flex;align-items:center;gap:10px;margin:0 0 10px 0;font-size:16px;}\n"
        ".badge{display:inline-flex;align-items:center;gap:8px;background:rgba(0,0,0,.22);"
        "border:1px solid rgba(255,255,255,.10);border-radius:999px;padding:6px 10px;font-size:13px;}\n"
        ".iban{font-weight:900;letter-spacing:.8px;font-size:16px;display:block;margin-top:6px;}\n"
        ".warnline{margin-top:10px;color:#ffd1dc;font-size:13px;}\n"
        ".footer{margin-top:14px;color:var(--muted);font-size:12px;}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<div class='wrap'>\n"
        "<div class='top'>\n"
        "<div class='brand'>Rada Rodziców • raport dla rodziców</div>\n"
        f"<div class='time'>Wygenerowano: <b>{html.escape(now)}</b></div>\n"
        "</div>\n"

        "<div class='hero'>\n"
        "<div class='hrow'>\n"
        "<div>\n"
        f"<h1>Rodzina: {html.escape(nazwisko)}</h1>\n"
        "<div class='subtitle'>Strona informacyjna dot. rozliczeń RR (na podstawie raportu bankowego).</div>\n"
        "</div>\n"
        "<div class='status'>\n"
        "<div class='lbl'>Status rozliczenia</div>\n"
        f"<div class='pill {status_cls}'>"
        f"<div class='ico'>{html.escape(status_icon)}</div>"
        f"<div>{html.escape(status_text)}</div>"
        "</div>\n"
        "</div>\n"
        "</div>\n"

        "<div class='grid'>\n"
        "<div class='card'>\n"
        "<h2>Dzieci</h2>\n"
        f"<div class='row'><div class='v' style='font-size:16px;font-weight:700;'>{dzieci_html}</div></div>\n"
        "<div class='note'>Jeśli dane są niepełne: sprawdź tytuł przelewu (imię/nazwisko dziecka, klasa/grupa, „RR”).</div>\n"
        "</div>\n"

        "<div class='card'>\n"
        "<h2>Podsumowanie</h2>\n"
        "<div class='kv'>\n"
        f"<div class='row'><div class='k'>Oczekiwana suma</div><div class='v'>{fmt_pln(ocz)}</div></div>\n"
        f"<div class='row'><div class='k'>Wykryta suma wpłat</div><div class='v'>{fmt_pln(wyk)}</div></div>\n"
        f"<div class='row'><div class='k'>Różnica</div><div class='v'>{fmt_pln(diff)}</div></div>\n"
        "</div>\n"
        "</div>\n"
        "</div>\n"
        "</div>\n"

        "<div class='card' style='margin-top:14px;'>\n"
        "<h2>Wpłaty RR</h2>\n"
        f"{payments_html}\n"
        "<div class='note'>Dane pochodzą z raportu bankowego (ułatwiają identyfikację operacji).</div>\n"
        "</div>\n"

        # --- Sekcja płatności na sam dół ---
        "<div class='payinfo'>\n"
        "<h2>💳 Informacje dotyczące wpłat na Radę Rodziców</h2>\n"
        "<div class='badge'><b>Dopisek obowiązkowy:</b> Imię i Nazwisko DZIECKA Wpłata na Radę Rodziców</div>\n"
        "<div class='note'>Dla bezpieczeństwa rozliczeń zalecamy dopisek dokładnie jak wyżej (ew. „RR” jako skrót).</div>\n"
        "<div class='grid' style='margin-top:12px;'>\n"
        "<div class='card' style='background:rgba(0,0,0,.16);'>\n"
        "<h2>Przedszkole — numer konta</h2>\n"
        "<span class='iban'>16 8436 0003 0000 0000 1456 0002</span>\n"
        "</div>\n"
        "<div class='card' style='background:rgba(0,0,0,.16);'>\n"
        "<h2>Szkoła — numer konta</h2>\n"
        "<span class='iban'>24 8436 0003 0000 0100 1456 0003</span>\n"
        "</div>\n"
        "</div>\n"
        "<div class='warnline'><b>Uwaga:</b> brak dopisku może utrudnić przypisanie wpłaty do rodziny.</div>\n"
        "</div>\n"

        "<div class='footer'>Wersja informacyjna dla rodziców.</div>\n"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )

    return html_out


def write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def make_index_page(title: str, items: list[tuple[str, str]]):
    rows = "\n".join(
        f'<li><a href="{html.escape(href)}">{html.escape(label)}</a></li>'
        for label, href in items
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "<!DOCTYPE html>\n"
        "<html lang='pl'>\n"
        "<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
        f"<title>{html.escape(title)}</title>\n"
        "<style>\n"
        "body { font-family: Arial, sans-serif; margin: 20px; }\n"
        "h1 { margin: 0 0 8px 0; }\n"
        ".muted { color: #666; font-size: 12px; }\n"
        "ul { line-height: 1.7; }\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"<p class='muted'>Wygenerowano: <b>{html.escape(now)}</b></p>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        "<ul>\n"
        f"{rows}\n"
        "</ul>\n"
        "</body>\n"
        "</html>\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-csv", default=DEFAULT_VERIFY_CSV,
                    help="CSV: Weryfikacja_RR_wplaty_per_nazwisko.csv")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                    help="Katalog wyjściowy (domyślnie RR_rodzice)")
    ap.add_argument("--base-url", default="",
                    help="Bazowy URL (np. https://RR-ZSP.github.io/Rozliczenia). Jeśli podasz, CSV z linkami będzie miał pełne URL.")
    ap.add_argument("--salt", default="",
                    help="Sekret do tokenów w nazwach plików (zalecane). Np. --salt 2025")
    args = ap.parse_args()

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(args.verify_csv)), "RR_rodzice")

    print("DEBUG: cwd =", os.getcwd())
    print("DEBUG: verify-csv =", os.path.abspath(args.verify_csv))
    print("DEBUG: out-dir =", os.path.abspath(out_dir))

    rows = read_verify_csv(args.verify_csv)
    print(f"DEBUG: rekordów w CSV: {len(rows)}")

    # out_dir wyliczony wyżej
    rodzice_dir = os.path.join(out_dir, "rodzice")
    klasy_dir = os.path.join(out_dir, "klasy")

    os.makedirs(rodzice_dir, exist_ok=True)
    os.makedirs(klasy_dir, exist_ok=True)

    klasy_map: dict[str, list[tuple[str, str]]] = {}
    link_rows = []

    salt = args.salt.strip() or None

    for row in rows:
        nazwisko = row["Nazwisko"]
        dzieci = row["Dzieci (klasa/grupa)"]

        filename = tokenize_filename(nazwisko, salt, token_len=8)

        # RELATYWNY link w obrębie katalogu RR_rodzice
        rel_href = f"rodzice/{filename}"

        # ABSOLUTNY link (do CSV do wysyłki) tylko jeśli podasz --base-url
        abs_href = (args.base_url.rstrip("/") + "/" + rel_href) if args.base_url else rel_href

        # strona rodzica
        page = html_page_for_family(row)
        write_text(os.path.join(rodzice_dir, filename), page)

        # podział wg klas/grup (może być kilka)
        labels = parse_class_labels(dzieci)
        if not labels:
            labels = ["(brak klasy/grupy)"]

        for lab in labels:
            # indeks klas jest w: RR_rodzice/klasy/<slug>/index.html
            # link do rodzica: ../../rodzice/<plik>
            klasy_map.setdefault(lab, []).append((nazwisko, f"../../{rel_href}"))

        link_rows.append({
            "Nazwisko": nazwisko,
            "Klasy/Grupy": "; ".join(labels),
            "Link": abs_href,
        })

    # indeksy klas
    klasy_index_items = []
    for lab in sorted(klasy_map.keys(), key=lambda x: x.lower()):
        safe_dir = slugify(lab)
        idx_path = os.path.join(klasy_dir, safe_dir, "index.html")
        items = sorted(klasy_map[lab], key=lambda t: t[0].lower())
        write_text(idx_path, make_index_page(f"RR – {lab}", items))
        klasy_index_items.append((lab, f"klasy/{safe_dir}/index.html"))

    # główny index
    write_text(os.path.join(out_dir, "index.html"),
               make_index_page("RR – wybierz klasę / grupę", klasy_index_items))

    # CSV z linkami dla rodziców
    links_csv_path = os.path.join(out_dir, "Linki_dla_rodzicow.csv")
    with open(links_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Nazwisko", "Klasy/Grupy", "Link"])
        w.writeheader()
        for r in sorted(link_rows, key=lambda x: x["Nazwisko"].lower()):
            w.writerow(r)

    print("OK.")
    print("- Wygenerowano strony:", os.path.abspath(rodzice_dir))
    print("- Indeksy klas:", os.path.abspath(klasy_dir))
    print("- Start:", os.path.abspath(os.path.join(out_dir, "index.html")))
    print("- Linki do wysyłki:", os.path.abspath(links_csv_path))
    if salt:
        print(f"- Tokeny w nazwach plików: WŁĄCZONE (salt='{salt}')")
    else:
        print("- Tokeny w nazwach plików: WYŁĄCZONE (brak --salt)")


if __name__ == "__main__":
    main()
