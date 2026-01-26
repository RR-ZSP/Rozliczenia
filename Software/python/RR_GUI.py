#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RR GUI – połączenie skryptów:
- ZSP.py (analiza XML + raporty HTML + weryfikacja RR + CSV per nazwisko)
- RR_rodzice_html.py (generowanie stron dla rodziców na podstawie CSV per nazwisko)
+ PDF: automatyczne generowanie Linki_dla_rodzicow.pdf z Linki_dla_rodzicow.csv (wbudowane, bez dodatkowego skryptu)

Wymagania: Python 3.10+ (zalecane), standardowa biblioteka (tkinter), reportlab.
"""

from __future__ import annotations

import os
import sys
import threading
import subprocess
import webbrowser
import datetime
import csv
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# ---- PDF deps (reportlab) ----
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


APP_TITLE = "RR GUI (ZSP + RR_rodzice)"

HELP_TEXT = """\
RR GUI – instrukcja (skrót)

Ten program łączy dwa skrypty:

1) ZSP.py – czyta pliki *.xml z katalogu, generuje:
   • raport_operacji.html (pełny) + raport_operacji_short.html (skrót)
   • Podsumowanie_RR.csv
   • Weryfikacja_RR.html
   • Weryfikacja_RR_wplaty_per_nazwisko.csv  <-- tego pliku używa etap 2

2) RR_rodzice_html.py – z pliku CSV per nazwisko tworzy paczkę HTML dla rodziców:
   • <out_dir>/index.html (start)
   • <out_dir>/klasy/*/index.html (indeksy klas/grup)
   • <out_dir>/rodzice/*.html (strony rodzin)
   • <out_dir>/Linki_dla_rodzicow.csv (linki do wysyłki)

3) PDF (automatycznie po etapie 2):
   • <out_dir>/Linki_dla_rodzicow.pdf  <-- generowane z Linki_dla_rodzicow.csv

Szybki start (najczęstszy scenariusz):
A) Wskaż „Katalog roboczy (XML)” – folder, w którym leżą pliki bankowe *.xml.
B) Wskaż „Lista dzieci (TXT)” (domyślnie: ListaDzieciZSP.txt).
C) (Opcjonalnie) Wskaż „RR ręczne (CSV)” (domyślnie: RR_manual.csv).
D) Kliknij „1) Generuj raporty (ZSP)” – powstaną pliki HTML/CSV w katalogu ID_... obok skryptów.
E) Kliknij „2) Generuj strony dla rodziców” – wskaż katalog wyjściowy (np. RR_rodzice).
F) Otwórz wygenerowany „index.html” (przycisk „Otwórz index”).

Ważne uwagi:
• ZSP.py zakłada nazwy plików:
  - Lista dzieci: ListaDzieciZSP.txt
  - RR ręczne:  RR_manual.csv
  Dlatego GUI na czas uruchomienia kopiuje wskazane pliki do katalogu XML (wejście) pod tymi nazwami.
• „RR ręczne” w raporcie HTML może automatycznie wczytać się dopiero gdy raport
  jest oglądany przez prosty serwer HTTP (np. python -m http.server).
  W GUI masz przycisk „Start HTTP server” (port 8000) i „Otwórz raport przez HTTP”.

Najczęstsze błędy:
• „Brak plików XML…” – wybrałeś zły katalog roboczy.
• „Nie znaleziono pliku z listą dzieci…” – brak ListaDzieciZSP.txt (wskaż w GUI).
• Etap 2 nie działa – upewnij się, że po etapie 1 powstał plik
  Weryfikacja_RR_wplaty_per_nazwisko.csv w katalogu ID_... obok skryptów.
"""


@dataclass
class Paths:
    base_dir: Path
    zsp_py: Path
    rr_rodzice_py: Path


def make_run_output_dir(base_dir: Path) -> Path:
    """Tworzy unikalny katalog: ID_YYYY-MM-DD_HH-MM-SS (z dopiskiem _N jeśli trzeba)."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = f"ID_{ts}"
    p = (base_dir / stem).resolve()
    i = 1
    while p.exists():
        p = (base_dir / f"{stem}_{i}").resolve()
        i += 1
    p.mkdir(parents=True, exist_ok=False)
    return p


# =========================
# PDF generator (wbudowany)
# =========================
def _pdf_header_footer_factory(font_name: str, date_str: str):
    def header_footer(canvas, doc):
        canvas.saveState()
        width, height = landscape(A4)

        # Nagłówek
        canvas.setFont(font_name, 13)
        canvas.drawCentredString(
            width / 2.0,
            height - 20,
            "Wpłaty na Radę Rodziców ZSP Mikołów",
        )

        canvas.setFont(font_name, 8)
        if date_str:
            canvas.drawRightString(
                width - 25,
                height - 32,
                f"Data wygenerowania: {date_str}",
            )

        canvas.setStrokeColor(colors.grey)
        canvas.setLineWidth(0.5)
        canvas.line(20, height - 38, width - 20, height - 38)

        # Stopka
        canvas.setFont(font_name, 8)
        canvas.drawRightString(
            width - 25,
            18,
            f"Strona {canvas.getPageNumber()}",
        )
        canvas.line(20, 30, width - 20, 30)

        canvas.restoreState()
    return header_footer


def generate_links_pdf(csv_path: Path, pdf_path: Path) -> None:
    """
    Tworzy PDF z tabelą linków na podstawie CSV.
    - Podlinkowuje komórki zaczynające się od http/https.
    - Próbuje użyć DejaVuSans.ttf (PL znaki), a gdy brak – Helvetica.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Nie znaleziono pliku CSV: {csv_path}")

    # Czcionka z PL znakami (jeśli dostępna)
    font_name = "Helvetica"
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", "DejaVuSans.ttf"))
        font_name = "DejaVuSans"
    except Exception:
        font_name = "Helvetica"

    date_str = datetime.date.today().strftime("%Y-%m-%d")

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows:
        raise ValueError("Plik CSV jest pusty.")

    header = rows[0]
    body = rows[1:]

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=landscape(A4),
        leftMargin=25,
        rightMargin=25,
        topMargin=55,
        bottomMargin=40,
    )

    elements = [Spacer(1, 12)]

    styles = getSampleStyleSheet()
    link_style = styles["Normal"]
    link_style.fontName = font_name
    link_style.fontSize = 7
    link_style.textColor = colors.HexColor("#003366")

    def make_cell(value: str):
        v = value or ""
        if isinstance(v, str) and (v.startswith("http://") or v.startswith("https://")):
            return Paragraph(f'<link href="{v}">{v}</link>', link_style)
        return v

    table_data = [header]
    for row in body:
        table_data.append([make_cell(cell) for cell in row])

    col_count = max(1, len(header))
    usable_width = landscape(A4)[0] - doc.leftMargin - doc.rightMargin
    col_widths = [usable_width / col_count] * col_count

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 7),

            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),

            ("ALIGN", (0, 1), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),

            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),

            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )

    elements.append(table)
    elements.append(Spacer(1, 6))

    elements.append(
        Table(
            [[f"Dokument wygenerowany automatycznie z pliku CSV: {csv_path.name}"]],
            colWidths=[usable_width],
            style=TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 6),
            ]),
        )
    )

    hf = _pdf_header_footer_factory(font_name, date_str)
    doc.build(elements, onFirstPage=hf, onLaterPages=hf)


class App(tk.Tk):
    def __init__(self, paths: Paths):
        super().__init__()
        self.paths = paths
        self.title(APP_TITLE)
        self.minsize(920, 620)

        self.http_proc: subprocess.Popen | None = None

        self.run_dir: Path | None = None

        self._build_menu()
        self._build_ui()

    # ---------- UI ----------
    def _build_menu(self):
        m = tk.Menu(self)
        self.config(menu=m)
        help_m = tk.Menu(m, tearoff=False)
        m.add_cascade(label="Help", menu=help_m)
        help_m.add_command(label="Instrukcja", command=self._show_help)
        help_m.add_separator()
        help_m.add_command(label="O programie", command=lambda: messagebox.showinfo(
            "O programie",
            "RR GUI – prosty interfejs do uruchamiania ZSP.py oraz RR_rodzice_html.py.\n"
            "Dodatkowo: automatyczny PDF z linkami dla rodziców.\n"
            "Wymagania: Python + tkinter + reportlab."
        ))

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        # ---- Paths section
        paths_box = ttk.LabelFrame(frm, text="Pliki / katalogi")
        paths_box.pack(fill="x", **pad)

        self.var_xml_dir = tk.StringVar(value="")
        self.var_children_txt = tk.StringVar(value="")
        self.var_rr_manual_csv = tk.StringVar(value="")
        self.var_run_dir = tk.StringVar(value="")
        self.var_out_dir = tk.StringVar(value="")
        self.var_base_url = tk.StringVar(value="")
        self.var_salt = tk.StringVar(value="")

        self.var_bez_danych = tk.BooleanVar(value=False)
        self.var_run_report = tk.BooleanVar(value=True)
        self.var_run_verify = tk.BooleanVar(value=True)

        self.var_http_port = tk.StringVar(value="8000")

        def row(label, var, browse_cmd, hint=""):
            r = ttk.Frame(paths_box)
            r.pack(fill="x", padx=10, pady=4)
            ttk.Label(r, text=label, width=22).pack(side="left")
            e = ttk.Entry(r, textvariable=var)
            e.pack(side="left", fill="x", expand=True, padx=(0, 8))
            ttk.Button(r, text="Wybierz…", command=browse_cmd).pack(side="left")
            if hint:
                ttk.Label(paths_box, text=hint).pack(anchor="w", padx=10)

        row("Katalog roboczy (XML)", self.var_xml_dir, self._pick_xml_dir,
            hint="→ Tu mają leżeć pliki bankowe *.xml (WEJŚCIE). Wyniki będą w katalogu ID_... obok skryptów.")
        row("Lista dzieci (TXT)", self.var_children_txt, self._pick_children_txt,
            hint="→ Plik w formacie jak w ZSP.py (sekcje KLASA / GRUPA).")
        row("RR ręczne (CSV)", self.var_rr_manual_csv, self._pick_rr_manual_csv,
            hint="→ Opcjonalnie. Jeśli pusty, ZSP zadziała bez RR ręcznych.")

        row("Katalog wyników (ID)", self.var_run_dir, lambda: None,
            hint="→ Automatycznie tworzone obok skryptów: ID_YYYY-MM-DD_HH-MM-SS (nowe dla każdego uruchomienia).")

        row("Wyjście rodzice (dir)", self.var_out_dir, lambda: None,
            hint="→ Automatycznie: <ID...>/RR_rodzice (HTML + Linki_dla_rodzicow.pdf).")

        # ---- Options
        opt = ttk.LabelFrame(frm, text="Opcje")
        opt.pack(fill="x", **pad)

        opt_row = ttk.Frame(opt)
        opt_row.pack(fill="x", padx=10, pady=6)
        ttk.Checkbutton(opt_row, text="ZSP: --bez_danych (mniej kolumn w raporcie)",
                        variable=self.var_bez_danych).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opt_row, text="ZSP: generuj raport_operacji.html",
                        variable=self.var_run_report).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opt_row, text="ZSP: generuj weryfikację RR + CSV",
                        variable=self.var_run_verify).pack(side="left", padx=(0, 14))

        opt_row2 = ttk.Frame(opt)
        opt_row2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(opt_row2, text="Base URL (opcjonalnie):", width=22).pack(side="left")
        ttk.Entry(opt_row2, textvariable=self.var_base_url).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(opt_row2, text="Salt (opcjonalnie):", width=16).pack(side="left")
        ttk.Entry(opt_row2, textvariable=self.var_salt, width=18).pack(side="left")

        # ---- Actions
        actions = ttk.LabelFrame(frm, text="Akcje")
        actions.pack(fill="x", **pad)

        arow = ttk.Frame(actions)
        arow.pack(fill="x", padx=10, pady=8)

        ttk.Button(arow, text="1) Generuj raporty (ZSP)", command=self.run_zsp).pack(side="left", padx=(0, 8))
        ttk.Button(arow, text="2) Generuj strony dla rodziców (+PDF)", command=self.run_rr_rodzice).pack(side="left", padx=(0, 8))
        ttk.Button(arow, text="3) Uruchom 1 + 2 (+PDF)", command=self.run_all).pack(side="left", padx=(0, 8))
        ttk.Button(arow, text="Otwórz index (rodzice)", command=self.open_parent_index).pack(side="left")

        # ---- HTTP server
        http_box = ttk.LabelFrame(frm, text="Podgląd przez HTTP (dla RR_manual.csv w raporcie)")
        http_box.pack(fill="x", **pad)

        hrow = ttk.Frame(http_box)
        hrow.pack(fill="x", padx=10, pady=8)
        ttk.Label(hrow, text="Port:", width=6).pack(side="left")
        ttk.Entry(hrow, textvariable=self.var_http_port, width=8).pack(side="left", padx=(0, 8))
        ttk.Button(hrow, text="Start HTTP server", command=self.start_http).pack(side="left", padx=(0, 8))
        ttk.Button(hrow, text="Stop HTTP server", command=self.stop_http).pack(side="left", padx=(0, 8))
        ttk.Button(hrow, text="Otwórz raport przez HTTP", command=self.open_report_http).pack(side="left")
        self.lbl_http = ttk.Label(http_box, text="Serwer: (nie uruchomiony)")
        self.lbl_http.pack(anchor="w", padx=10, pady=(0, 10))

        # ---- Log
        log_box = ttk.LabelFrame(frm, text="Log")
        log_box.pack(fill="both", expand=True, **pad)

        self.log = ScrolledText(log_box, height=16, font=("Consolas", 10))
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

        self._log_line("Gotowe. Ustaw katalog XML i pliki wejściowe, potem uruchom krok 1.")

    # ---------- Helpers ----------
    def _log_line(self, s: str):
        self.log.insert("end", s.rstrip() + "\n")
        self.log.see("end")

    def _show_help(self):
        w = tk.Toplevel(self)
        w.title("Help – RR GUI")
        w.minsize(820, 560)
        t = ScrolledText(w, font=("Segoe UI", 10))
        t.pack(fill="both", expand=True, padx=10, pady=10)
        t.insert("end", HELP_TEXT)
        t.configure(state="disabled")

    def _pick_xml_dir(self):
        p = filedialog.askdirectory(title="Wybierz katalog roboczy z plikami XML")
        if p:
            self.var_xml_dir.set(p)

    def _pick_children_txt(self):
        p = filedialog.askopenfilename(
            title="Wybierz ListaDzieciZSP.txt",
            filetypes=[("TXT", "*.txt"), ("Wszystkie", "*.*")]
        )
        if p:
            self.var_children_txt.set(p)

    def _pick_rr_manual_csv(self):
        p = filedialog.askopenfilename(
            title="Wybierz RR_manual.csv (opcjonalnie)",
            filetypes=[("CSV", "*.csv"), ("Wszystkie", "*.*")]
        )
        if p:
            self.var_rr_manual_csv.set(p)

    def _pick_out_dir(self):
        p = filedialog.askdirectory(title="Wybierz katalog wyjściowy dla paczki rodzice")
        if p:
            self.var_out_dir.set(p)

    def _require(self, cond: bool, msg: str) -> bool:
        if cond:
            return True
        messagebox.showerror("Brak danych", msg)
        return False

    def _copy_if_given(self, src_path_str: str, dst_dir: Path, dst_name: str):
        src = Path(src_path_str).expanduser().resolve()
        if not src_path_str:
            return
        if not src.exists():
            raise FileNotFoundError(f"Nie znaleziono pliku: {src}")
        dst = (dst_dir / dst_name).resolve()
        dst.write_bytes(src.read_bytes())
        self._log_line(f"Skopiowano: {src} -> {dst}")

    def _run_subprocess(self, cmd: list[str], cwd: Path):
        self._log_line("CMD: " + " ".join(cmd))
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if p.stdout:
            self._log_line(p.stdout.rstrip())
        if p.stderr:
            self._log_line("[stderr] " + p.stderr.rstrip())
        if p.returncode != 0:
            raise RuntimeError(f"Komenda zakończona błędem (code={p.returncode}).")

    def _maybe_generate_pdf(self, out_dir: Path):
        """
        Jeśli istnieje Linki_dla_rodzicow.csv w out_dir, generuje Linki_dla_rodzicow.pdf.
        """
        csv_path = out_dir / "Linki_dla_rodzicow.csv"
        pdf_path = out_dir / "Linki_dla_rodzicow.pdf"
        if not csv_path.exists():
            self._log_line("PDF: pominięto (brak Linki_dla_rodzicow.csv).")
            return
        self._log_line("PDF: generowanie Linki_dla_rodzicow.pdf …")
        generate_links_pdf(csv_path, pdf_path)
        self._log_line(f"PDF: OK -> {pdf_path}")

    # ---------- Actions ----------
    def run_zsp(self):
        def job():
            try:
                xml_dir = Path(self.var_xml_dir.get()).expanduser().resolve()
                if not self._require(xml_dir.is_dir(), "Wybierz poprawny katalog roboczy (XML)."):
                    return

                children_path = self.var_children_txt.get().strip()
                if not self._require(bool(children_path), "Wskaż plik ListaDzieciZSP.txt."):
                    return

                # Nowy katalog wyników (obok skryptów): ID_YYYY-MM-DD_HH-MM-SS
                run_dir = make_run_output_dir(self.paths.base_dir)
                self.run_dir = run_dir
                self.var_run_dir.set(str(run_dir))
                self.var_out_dir.set(str((run_dir / "RR_rodzice").resolve()))

                self._log_line("=== ZSP: start ===")
                self._log_line(f"Wyniki: {run_dir}")

                # Pliki pomocnicze kopiujemy do katalogu wyników,
                # żeby całość była „przenośna”.
                self._copy_if_given(children_path, run_dir, "ListaDzieciZSP.txt")

                rr_manual_path = self.var_rr_manual_csv.get().strip()
                if rr_manual_path:
                    self._copy_if_given(rr_manual_path, run_dir, "RR_manual.csv")
                else:
                    self._log_line("RR_manual.csv: pominięto (brak pliku)")

                args = []
                if self.var_bez_danych.get():
                    args.append("--bez_danych")

                run_report = self.var_run_report.get()
                run_verify = self.var_run_verify.get()

                if run_report:
                    args += ["--raport"]
                if run_verify:
                    args += ["--weryfikacja-rr"]
                if not args:
                    raise ValueError("Zaznacz przynajmniej jedną opcję ZSP: raport lub weryfikacja RR.")

                cmd = [
                    sys.executable, str(self.paths.zsp_py),
                    "--xml-dir", str(xml_dir),
                    "--out-dir", str(run_dir),
                    "--children-txt", str((run_dir / "ListaDzieciZSP.txt").resolve()),
                ] + args

                if (run_dir / "RR_manual.csv").exists():
                    cmd += ["--rr-manual", str((run_dir / "RR_manual.csv").resolve())]

                # uruchamiamy w cwd=run_dir, żeby wszystkie pliki były w jednym miejscu
                self._run_subprocess(cmd, run_dir)

                out_verify_csv = run_dir / "Weryfikacja_RR_wplaty_per_nazwisko.csv"
                if out_verify_csv.exists():
                    self._log_line(f"OK: znaleziono {out_verify_csv.name} (do kroku 2).")
                else:
                    self._log_line("UWAGA: nie znaleziono Weryfikacja_RR_wplaty_per_nazwisko.csv – krok 2 może nie zadziałać.")

                self._log_line("=== ZSP: koniec ===\n")
            except Exception as e:
                self._log_line(f"[BŁĄD] {e}")
                messagebox.showerror("Błąd", str(e))

        threading.Thread(target=job, daemon=True).start()


    def run_rr_rodzice(self):
        def job():
            try:
                run_dir = self.run_dir or (Path(self.var_run_dir.get()).expanduser().resolve() if self.var_run_dir.get().strip() else None)
                if not run_dir or not run_dir.is_dir():
                    raise FileNotFoundError("Brak katalogu wyników (ID...). Najpierw uruchom krok 1 (ZSP).")

                verify_csv = run_dir / "Weryfikacja_RR_wplaty_per_nazwisko.csv"
                if not self._require(verify_csv.exists(), f"Nie znaleziono pliku:\n{verify_csv}\nNajpierw uruchom krok 1 (ZSP) z weryfikacją RR."):

                    return

                out_dir = (run_dir / "RR_rodzice").resolve()
                out_dir.mkdir(parents=True, exist_ok=True)
                self.var_out_dir.set(str(out_dir))

                self._log_line("=== RR_rodzice_html: start ===")
                cmd = [
                    sys.executable, str(self.paths.rr_rodzice_py),
                    "--verify-csv", str(verify_csv),
                    "--out-dir", str(out_dir),
                ]
                base_url = self.var_base_url.get().strip()
                salt = self.var_salt.get().strip()
                if base_url:
                    cmd += ["--base-url", base_url]
                if salt:
                    cmd += ["--salt", salt]

                self._run_subprocess(cmd, run_dir)

                # PDF po kroku 2
                self._maybe_generate_pdf(out_dir)

                self._log_line("=== RR_rodzice_html: koniec ===\n")

                idx = out_dir / "index.html"
                if idx.exists():
                    self._log_line(f"OK: gotowe. Start: {idx}")
                else:
                    self._log_line("UWAGA: nie znaleziono index.html w katalogu wyjściowym.")

            except Exception as e:
                self._log_line(f"[BŁĄD] {e}")
                messagebox.showerror("Błąd", str(e))

        threading.Thread(target=job, daemon=True).start()


    def run_all(self):
        def job():
            try:
                xml_dir = Path(self.var_xml_dir.get()).expanduser().resolve()
                if not self._require(xml_dir.is_dir(), "Wybierz poprawny katalog roboczy (XML)."):
                    return

                children_path = self.var_children_txt.get().strip()
                if not self._require(bool(children_path), "Wskaż plik ListaDzieciZSP.txt."):
                    return

                # Nowy katalog wyników (obok skryptów)
                run_dir = make_run_output_dir(self.paths.base_dir)
                self.run_dir = run_dir
                self.var_run_dir.set(str(run_dir))
                out_dir = (run_dir / "RR_rodzice").resolve()
                self.var_out_dir.set(str(out_dir))

                self._log_line("=== PIPELINE: 1) ZSP ===")
                self._log_line(f"Wyniki: {run_dir}")

                self._copy_if_given(children_path, run_dir, "ListaDzieciZSP.txt")

                rr_manual_path = self.var_rr_manual_csv.get().strip()
                if rr_manual_path:
                    self._copy_if_given(rr_manual_path, run_dir, "RR_manual.csv")

                args = []
                if self.var_bez_danych.get():
                    args.append("--bez_danych")

                run_report = self.var_run_report.get()
                run_verify = self.var_run_verify.get()

                if not run_verify:
                    raise ValueError("Pipeline (1+2) wymaga zaznaczonej opcji: ZSP: generuj weryfikację RR + CSV.")

                if run_report:
                    args += ["--raport"]
                args += ["--weryfikacja-rr"]

                cmd = [
                    sys.executable, str(self.paths.zsp_py),
                    "--xml-dir", str(xml_dir),
                    "--out-dir", str(run_dir),
                    "--children-txt", str((run_dir / "ListaDzieciZSP.txt").resolve()),
                ] + args

                if (run_dir / "RR_manual.csv").exists():
                    cmd += ["--rr-manual", str((run_dir / "RR_manual.csv").resolve())]

                self._run_subprocess(cmd, run_dir)

                verify_csv = run_dir / "Weryfikacja_RR_wplaty_per_nazwisko.csv"
                if not verify_csv.exists():
                    raise FileNotFoundError(f"Nie znaleziono {verify_csv}. Zaznacz w ZSP: weryfikacja RR + CSV.")

                self._log_line("=== PIPELINE: 2) RR_rodzice_html (+PDF) ===")
                out_dir.mkdir(parents=True, exist_ok=True)

                cmd = [
                    sys.executable, str(self.paths.rr_rodzice_py),
                    "--verify-csv", str(verify_csv),
                    "--out-dir", str(out_dir),
                ]
                base_url = self.var_base_url.get().strip()
                salt = self.var_salt.get().strip()
                if base_url:
                    cmd += ["--base-url", base_url]
                if salt:
                    cmd += ["--salt", salt]

                self._run_subprocess(cmd, run_dir)

                # PDF po pipeline
                self._maybe_generate_pdf(out_dir)

                self._log_line("=== PIPELINE: OK ===\n")
                idx = out_dir / "index.html"
                if idx.exists():
                    webbrowser.open(idx.as_uri())
            except Exception as e:
                self._log_line(f"[BŁĄD] {e}")
                messagebox.showerror("Błąd", str(e))

        threading.Thread(target=job, daemon=True).start()


    def open_parent_index(self):
        out_dir = Path(self.var_out_dir.get()).expanduser().resolve()
        idx = out_dir / "index.html"
        if not idx.exists():
            messagebox.showwarning("Brak pliku", f"Nie znaleziono: {idx}\nNajpierw uruchom krok 2.")
            return
        webbrowser.open(idx.as_uri())

    # ---------- HTTP server ----------
    def start_http(self):
        if self.http_proc and self.http_proc.poll() is None:
            messagebox.showinfo("HTTP", "Serwer już działa.")
            return

        run_dir = self.run_dir or (Path(self.var_run_dir.get()).expanduser().resolve() if self.var_run_dir.get().strip() else None)
        if not run_dir or not run_dir.is_dir():
            messagebox.showerror("HTTP", "Brak katalogu wyników (ID...). Najpierw uruchom krok 1 (ZSP).")
            return

        try:
            port = int(self.var_http_port.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except Exception:
            messagebox.showerror("HTTP", "Niepoprawny port (1-65535).")
            return

        cmd = [sys.executable, "-m", "http.server", str(port)]
        try:
            self.http_proc = subprocess.Popen(cmd, cwd=str(run_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.lbl_http.config(text=f"Serwer: http://127.0.0.1:{port}/  (katalog: {run_dir})")
        except Exception as e:
            messagebox.showerror("HTTP", str(e))

    def stop_http(self):
        if not self.http_proc or self.http_proc.poll() is not None:
            self.http_proc = None
            self.lbl_http.config(text="Serwer: (nie uruchomiony)")
            return
        try:
            self.http_proc.terminate()
            self.http_proc.wait(timeout=2)
        except Exception:
            try:
                self.http_proc.kill()
            except Exception:
                pass
        self.http_proc = None
        self.lbl_http.config(text="Serwer: (nie uruchomiony)")

    def open_report_http(self):
        run_dir = self.run_dir or (Path(self.var_run_dir.get()).expanduser().resolve() if self.var_run_dir.get().strip() else None)
        if not run_dir or not run_dir.is_dir():
            messagebox.showerror("HTTP", "Brak katalogu wyników (ID...). Najpierw uruchom krok 1 (ZSP).")
            return

        rpt = run_dir / "raport_operacji.html"
        if not rpt.exists():
            messagebox.showerror("HTTP", f"Nie znaleziono: {rpt}\nNajpierw uruchom krok 1 (ZSP: raport).")
            return

        try:
            port = int(self.var_http_port.get().strip())
        except Exception:
            port = 8000
        webbrowser.open(f"http://127.0.0.1:{port}/{rpt.name}")



def main():
    base_dir = Path(__file__).resolve().parent

    zsp_py = base_dir / "ZSP.py"
    rr_py = base_dir / "RR_rodzice_html.py"

    missing = [p for p in (zsp_py, rr_py) if not p.exists()]
    if missing:
        msg = "Brak wymaganych plików obok RR_GUI.py:\n" + "\n".join(str(p) for p in missing)
        print(msg)
        messagebox.showerror("Brak plików", msg)
        return

    app = App(Paths(base_dir=base_dir, zsp_py=zsp_py, rr_rodzice_py=rr_py))
    app.mainloop()


if __name__ == "__main__":
    main()
