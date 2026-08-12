# Research Output Monitor

Ein Python-Tool zur automatisierten Erfassung, Auswertung und Dokumentation wissenschaftlicher Publikationen.

Das Projekt entstand aus einem praktischen Research-Operations-/Controlling-Use-Case: Neue Publikationen mehrerer Forschungsgruppen sollen regelmäßig über PubMed erkannt, mit den jeweiligen Forschungswebseiten abgeglichen und in einem kompakten Bericht zusammengeführt werden.

## Was das Tool macht

- durchsucht **PubMed** gruppenspezifisch nach aktuellen Publikationen
- ruft bibliografische Daten, DOI und Abstracts ab
- speichert bereits gefundene Publikationen lokal in **SQLite**
- erkennt neue Publikationen bei späteren Läufen
- gleicht **PMID/DOI** optional mit Forschungswebseiten ab
- markiert Publikationen, die auf einer Website noch nicht aufgeführt sind
- erstellt eine Visualisierung des Publikationsoutputs mit **Matplotlib**
- erzeugt einen strukturierten **PDF-Report** mit ReportLab
- hält institutionsspezifische Angaben vollständig außerhalb des Quellcodes in einer JSON-Konfiguration

## Beispiel-Workflow

```text
Konfiguration
     ↓
PubMed Search API
     ↓
Publikationsdaten + Abstracts
     ↓
SQLite / Deduplizierung
     ↓
optionaler Website-Abgleich
     ↓
Kennzahlen + Visualisierung
     ↓
PDF-Bericht
```

## Tech Stack

- Python 3.11+
- PubMed / NCBI E-Utilities
- Requests
- BeautifulSoup
- SQLite
- Matplotlib
- ReportLab

## Projektstruktur

```text
research-output-monitor/
├── research_output_monitor.py
├── config.example.json
├── requirements.txt
├── .gitignore
└── README.md
```

## Installation

```bash
git clone https://github.com/MelisaMus/research-output-monitor.git
cd research-output-monitor

python -m venv .venv
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt
```

## Konfiguration

Die Beispielkonfiguration kopieren:

```bash
cp config.example.json config.json
```

Anschließend `config.json` anpassen:

```json
{
  "report_title": "Research Output Monitor",
  "default_affiliation": "Example University",
  "window_days": 180,
  "research_groups": [
    {
      "leader": "Example A",
      "website_url": "https://example.org/research-group-a",
      "keywords": []
    }
  ]
}
```

`leader` sollte so angegeben werden, wie der Name in PubMed typischerweise als Autor erscheint.

Optional können pro Forschungsgruppe eigene `affiliation`-Werte und Keyword-Filter definiert werden.

## Start

```bash
python research_output_monitor.py --config config.json
```

Der Report wird standardmäßig im Ordner `reports/` erzeugt.

## Datenschutz

Das Tool verarbeitet ausschließlich öffentlich zugängliche bibliografische Publikationsdaten. Institutions- oder gruppenspezifische Konfigurationsdaten werden nicht im Python-Code hinterlegt.

`config.json`, lokale Datenbanken und erzeugte Reports sind in `.gitignore` ausgeschlossen und sollten nicht versehentlich veröffentlicht werden.

## Entwicklungsansatz

Das Projekt wurde **KI-gestützt entwickelt**. Problemdefinition, fachlicher Workflow, Anforderungen, Prüfung der Ergebnisse und iterative Weiterentwicklung erfolgten an einem konkreten Anwendungsfall des Forschungsmonitorings.

KI wurde dabei als Entwicklungswerkzeug für Codegenerierung und Refactoring eingesetzt.

## Grenzen

- Die Autorensuche basiert auf PubMed-Autorenangaben und kann bei Namensgleichheit zusätzliche Filter benötigen.
- Der Website-Abgleich erkennt nur PMID/DOI, die im HTML/Text der Zielseite vorhanden sind.
- Webseiten mit dynamisch nachgeladenen Inhalten können einen Browser-basierten Scraper erfordern.
- Das Tool ersetzt keine bibliometrische Datenbank oder wissenschaftliche Qualitätsbewertung.

## Mögliche Weiterentwicklung

- ORCID-basierte Zuordnung
- institutionelle Dashboards
- CSV-/Excel-Export
- automatisierte periodische Ausführung via GitHub Actions
- E-Mail-Benachrichtigungen bei neuen Publikationen
- API- oder Web-Frontend für Research-Management-Teams
