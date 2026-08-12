#!/usr/bin/env python3
"""
Research Output Monitor

Automatisiert das Monitoring wissenschaftlicher Publikationen:
- PubMed-Suche pro Forschungsgruppe
- optionaler Abgleich mit Forschungswebseiten via PMID/DOI
- lokale SQLite-Persistenz
- Visualisierung des Publikationsoutputs
- PDF-Bericht mit Hinweisen auf fehlende Webeinträge

Konfiguration erfolgt über eine JSON-Datei, damit keine institutionsspezifischen
Daten im Quellcode fest verdrahtet sind.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import matplotlib
import requests
from bs4 import BeautifulSoup
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DEFAULT_DB = "research_output.sqlite"
DEFAULT_REPORT_DIR = "reports"
DEFAULT_WINDOW_DAYS = 180

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

DEFAULT_USER_AGENT = "ResearchOutputMonitor/1.0"


@dataclass(frozen=True)
class ResearchGroup:
    leader: str
    affiliation: str
    website_url: str | None = None
    keywords: tuple[str, ...] = ()


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    if not config.get("research_groups"):
        raise ValueError("Konfiguration enthält keine 'research_groups'.")

    return config


def parse_groups(config: dict) -> list[ResearchGroup]:
    groups: list[ResearchGroup] = []
    default_affiliation = config.get("default_affiliation", "")

    for raw in config["research_groups"]:
        leader = raw.get("leader", "").strip()
        if not leader:
            raise ValueError("Jede Forschungsgruppe benötigt ein Feld 'leader'.")

        groups.append(
            ResearchGroup(
                leader=leader,
                affiliation=raw.get("affiliation", default_affiliation).strip(),
                website_url=(raw.get("website_url") or "").strip() or None,
                keywords=tuple(raw.get("keywords", [])),
            )
        )
    return groups


def request_headers(config: dict) -> dict[str, str]:
    user_agent = config.get("user_agent") or DEFAULT_USER_AGENT
    email = config.get("contact_email")
    if email:
        user_agent = f"{user_agent} ({email})"
    return {"User-Agent": user_agent}


def request_with_retry(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 20,
    retries: int = 2,
) -> requests.Response:
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 429 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"HTTP-Anfrage fehlgeschlagen: {last_error}") from last_error


def init_db(db_path: str | Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pubmed_entries (
                pmid TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT,
                pub_date TEXT,
                group_leader TEXT NOT NULL,
                journal TEXT,
                doi TEXT,
                abstract TEXT,
                found_date TEXT NOT NULL
            )
            """
        )
        conn.commit()


def publication_exists(db_path: str | Path, pmid: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM pubmed_entries WHERE pmid = ?",
            (pmid,),
        ).fetchone()
    return row is not None


def save_publication(db_path: str | Path, pub: dict) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO pubmed_entries
            (pmid, title, authors, pub_date, group_leader, journal, doi, abstract, found_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pub["pmid"],
                pub["title"],
                pub.get("authors", ""),
                pub.get("date", ""),
                pub["leader"],
                pub.get("journal", ""),
                pub.get("doi"),
                pub.get("abstract", ""),
                datetime.now().strftime("%Y-%m-%d"),
            ),
        )
        conn.commit()


def fetch_abstract(pmid: str, headers: dict[str, str]) -> str:
    try:
        response = request_with_retry(
            PUBMED_EFETCH,
            params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"},
            headers=headers,
        )
        return " ".join(response.text.split())
    except RuntimeError:
        return "Abstract nicht verfügbar."


def extract_doi(item: dict) -> str | None:
    doi = item.get("doi")
    if isinstance(doi, str) and doi.strip():
        return doi.strip()

    for article_id in item.get("articleids", []):
        if isinstance(article_id, dict) and article_id.get("idtype") == "doi":
            value = article_id.get("value")
            return value.strip() if isinstance(value, str) else None

    return None


def matches_keywords(title: str, journal: str, keywords: Iterable[str]) -> bool:
    keywords = tuple(keywords)
    if not keywords:
        return True

    haystack = f"{title} {journal}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def scrape_publication_ids(url: str | None, headers: dict[str, str]) -> set[str]:
    if not url:
        return set()

    try:
        response = request_with_retry(url, headers=headers)
    except RuntimeError:
        return set()

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    found: set[str] = set()

    doi_pattern = r"(?:doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)"
    for doi in re.findall(doi_pattern, text, flags=re.IGNORECASE):
        found.add(f"DOI:{doi.rstrip('.,;)}]')}")

    for pmid in re.findall(r"PMID[:\s]*(\d{6,9})", text, flags=re.IGNORECASE):
        found.add(f"PMID:{pmid}")

    return found


def build_pubmed_query(group: ResearchGroup, window_days: int) -> str:
    date_cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y/%m/%d")

    parts = [
        f'"{group.leader}"[Author]',
        f'("{date_cutoff}"[Date - Publication] : "3000"[Date - Publication])',
    ]
    if group.affiliation:
        parts.insert(1, f'"{group.affiliation}"[Affiliation]')

    return " AND ".join(parts)


def search_pubmed_for_group(
    group: ResearchGroup,
    *,
    db_path: str | Path,
    window_days: int,
    headers: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    query = build_pubmed_query(group, window_days)
    print(f"→ PubMed-Suche: {group.leader}")

    try:
        search_response = request_with_retry(
            PUBMED_ESEARCH,
            params={"db": "pubmed", "term": query, "retmax": 100, "retmode": "json"},
            headers=headers,
        )
        pmids = search_response.json().get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return [], []

        summary_response = request_with_retry(
            PUBMED_ESUMMARY,
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
            headers=headers,
        )
        details = summary_response.json().get("result", {})
    except (RuntimeError, ValueError) as exc:
        print(f"  Fehler bei PubMed-Abfrage: {exc}")
        return [], []

    web_ids = scrape_publication_ids(group.website_url, headers)

    new_publications: list[dict] = []
    missing_on_web: list[dict] = []

    for uid, item in details.items():
        if uid == "uids" or not isinstance(item, dict):
            continue

        pmid = item.get("uid")
        if not pmid or publication_exists(db_path, pmid):
            continue

        title = item.get("title", "Kein Titel")
        journal = item.get("fulljournalname", item.get("source", "Unbekannt"))

        if not matches_keywords(title, journal, group.keywords):
            continue

        doi = extract_doi(item)
        authors = ", ".join(
            author.get("name", "")
            for author in item.get("authors", [])
            if isinstance(author, dict)
        )
        pub_date = item.get("pubdate", "Unbekannt")

        on_website = True
        if group.website_url:
            on_website = (
                f"PMID:{pmid}" in web_ids
                or (doi is not None and f"DOI:{doi}" in web_ids)
            )

        if not on_website:
            missing_on_web.append(
                {
                    "leader": group.leader,
                    "pmid": pmid,
                    "title": title,
                    "doi": doi,
                    "date": pub_date,
                }
            )

        abstract = fetch_abstract(pmid, headers)
        time.sleep(0.35)

        publication = {
            "leader": group.leader,
            "title": title,
            "pmid": pmid,
            "date": pub_date,
            "journal": journal,
            "doi": doi,
            "authors": authors,
            "abstract": abstract,
            "missing_on_web": not on_website,
        }

        save_publication(db_path, publication)
        new_publications.append(publication)

    return new_publications, missing_on_web


def register_font(font_file: str | None) -> str:
    if not font_file:
        return "Helvetica"

    path = Path(font_file)
    if not path.exists():
        print(f"Hinweis: Schriftdatei '{font_file}' nicht gefunden. Verwende Helvetica.")
        return "Helvetica"

    try:
        pdfmetrics.registerFont(TTFont("CustomUnicodeFont", str(path)))
        return "CustomUnicodeFont"
    except Exception as exc:
        print(f"Hinweis: Schrift konnte nicht registriert werden: {exc}")
        return "Helvetica"


def create_publication_chart(publications: list[dict], groups: list[ResearchGroup], output: Path) -> None:
    counts = Counter(pub["leader"] for pub in publications)
    pairs = sorted(
        ((group.leader, counts.get(group.leader, 0)) for group in groups),
        key=lambda item: item[1],
        reverse=True,
    )

    if not pairs:
        return

    labels, values = zip(*pairs)

    plt.figure(figsize=(10, 6))
    plt.barh(labels, values)
    plt.xlabel("Anzahl neuer Publikationen")
    plt.title("Publikationsoutput im Monitoring-Zeitraum")
    plt.gca().invert_yaxis()

    for idx, value in enumerate(values):
        if value:
            plt.text(value + 0.05, idx, str(value), va="center")

    plt.tight_layout()
    plt.savefig(output, dpi=150)
    plt.close()


def create_pdf_report(
    publications: list[dict],
    missing_on_web: list[dict],
    *,
    chart_path: Path,
    report_path: Path,
    font_name: str,
    title: str,
    window_days: int,
) -> None:
    if not publications:
        return

    doc = SimpleDocTemplate(
        str(report_path),
        pagesize=A4,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.HexColor("#24536A"),
        fontName=font_name,
        spaceAfter=12,
    )
    header_style = ParagraphStyle(
        "HeaderCustom",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.grey,
        fontName=font_name,
        alignment=TA_RIGHT,
    )
    group_style = ParagraphStyle(
        "GroupCustom",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#24536A"),
        fontName=font_name,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "BodyCustom",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        fontName=font_name,
        alignment=TA_LEFT,
    )
    warning_style = ParagraphStyle(
        "WarningCustom",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#8A1C1C"),
        backColor=colors.HexColor("#FCECEC"),
        fontName=font_name,
        spaceAfter=5,
    )

    story.append(Paragraph(title, title_style))
    story.append(
        Paragraph(
            f"Berichtsdatum: {datetime.now():%d.%m.%Y} | Monitoring-Zeitraum: letzte {window_days} Tage",
            header_style,
        )
    )
    story.append(Spacer(1, 0.2 * inch))

    if chart_path.exists():
        story.append(Image(str(chart_path), width=6.5 * inch, height=4 * inch))
        story.append(Spacer(1, 0.2 * inch))

    if missing_on_web:
        story.append(Paragraph("<b>Aktualisierungsbedarf auf Forschungswebseiten</b>", warning_style))
        table_data = [["Gruppe", "PMID", "Titel"]]
        for item in missing_on_web:
            short_title = item["title"][:60] + ("…" if len(item["title"]) > 60 else "")
            table_data.append([item["leader"], item["pmid"], short_title])

        table = Table(table_data, colWidths=[1.3 * inch, 0.9 * inch, 4.0 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 0.3 * inch))

    for idx, pub in enumerate(publications, start=1):
        if pub.get("missing_on_web"):
            story.append(
                Paragraph(
                    f"<b>FEHLT AUF WEBSEITE:</b> {pub['leader']} | PMID {pub['pmid']}",
                    warning_style,
                )
            )
        else:
            story.append(
                Paragraph(
                    f"<b>{idx}. {pub['leader']}</b> | PMID {pub['pmid']} | {pub['date']}",
                    group_style,
                )
            )

        story.append(Paragraph(f"<b>{pub['title']}</b>", styles["Normal"]))
        if pub.get("authors"):
            story.append(Paragraph(pub["authors"], body_style))
        if pub.get("journal"):
            story.append(Paragraph(f"<i>{pub['journal']}</i>", body_style))
        if pub.get("doi"):
            story.append(Paragraph(f"DOI: {pub['doi']}", body_style))
        story.append(Paragraph("<b>Abstract:</b>", body_style))
        story.append(Paragraph(pub.get("abstract") or "Nicht verfügbar.", body_style))
        story.append(Spacer(1, 0.15 * inch))

    doc.build(story)


def run(config_path: str | Path) -> None:
    config = load_config(config_path)
    groups = parse_groups(config)
    headers = request_headers(config)

    db_path = Path(config.get("database", DEFAULT_DB))
    report_dir = Path(config.get("report_directory", DEFAULT_REPORT_DIR))
    report_dir.mkdir(parents=True, exist_ok=True)

    window_days = int(config.get("window_days", DEFAULT_WINDOW_DAYS))
    font_name = register_font(config.get("font_file"))

    init_db(db_path)

    all_new: list[dict] = []
    all_missing: list[dict] = []

    for index, group in enumerate(groups):
        print(f"\nPrüfe: {group.leader}")
        publications, missing = search_pubmed_for_group(
            group,
            db_path=db_path,
            window_days=window_days,
            headers=headers,
        )
        all_new.extend(publications)
        all_missing.extend(missing)

        if index < len(groups) - 1:
            time.sleep(float(config.get("group_delay_seconds", 1.0)))

    if not all_new:
        print("Keine neuen Publikationen gefunden.")
        return

    date_stamp = datetime.now().strftime("%Y-%m-%d")
    chart_path = report_dir / f"publication_output_{date_stamp}.png"
    report_path = report_dir / f"research_output_report_{date_stamp}.pdf"

    create_publication_chart(all_new, groups, chart_path)
    create_pdf_report(
        all_new,
        all_missing,
        chart_path=chart_path,
        report_path=report_path,
        font_name=font_name,
        title=config.get("report_title", "Research Output Monitor"),
        window_days=window_days,
    )

    print(f"Bericht erstellt: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatisiertes Monitoring wissenschaftlicher Publikationen.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Pfad zur JSON-Konfiguration (Standard: config.json)",
    )
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
