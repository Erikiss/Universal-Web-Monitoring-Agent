# Pythia Google watch

Tägliche Google-Websuche nach **Pythia von EleutherAI**, einschließlich Paper,
Blogs, Modellkarten und anderer Webseiten. Ein neuer relevanter Link erzeugt
eine eigene Mail über die bestehende SMTP-/Pushover-Mail-Anbindung.

## Einmalige Einrichtung

Der Monitor benötigt neben den bereits verwendeten Mail-Secrets **einen
Such-API-Schlüssel**. Er verwendet die Google-Engine von
[SerpApi](https://serpapi.com/) und benötigt das Repository-Secret
`SERPAPI_API_KEY` (alternativ wird ein vorhandenes `SERPAPI_KEY` verwendet).

Den Schlüssel unter
[Settings → Secrets and variables → Actions](https://github.com/Erikiss/Universal-Web-Monitoring-Agent/settings/secrets/actions)
als **New repository secret** speichern. Schlüssel nie in Code, Issues oder
Workflow-Logs eintragen. Es wird kein Konto angelegt, kein Tarif gebucht und
kein Secret-Wert ausgelesen. Tarif und verfügbares Suchkontingent müssen zum
gewählten SerpApi-Konto passen; jede Ergebnisseite ist ein API-Abruf.

Anschließend unter
[Actions → Pythia Google watch](https://github.com/Erikiss/Universal-Web-Monitoring-Agent/actions/workflows/pythia-google-watch.yml)
**Run workflow** starten. `dry_run=true` prüft die Suche ohne Versand oder
Zustandsänderung. Ein normaler manueller Lauf und der tägliche Zeitplan senden
neue Treffer. Code-Pushes führen ausschließlich den Testlauf aus.

Fehlt der Schlüssel, schlägt der Konfigurationscheck sichtbar fehl. Dann ist
die Funktion implementiert, aber die Google-Suche **noch nicht betriebsbereit**.
Die vorhandenen `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`ALERT_EMAIL_TO` und `ALERT_EMAIL_FROM` werden unverändert verwendet.

## Suchumfang und Zeitplan

- Täglich **06:43 UTC**, also **08:43 MESZ / 07:43 MEZ**. GitHub Actions kann
  später starten. Die Uhrzeit ist kein garantierter Zustelltermin.
- Wie in der manuellen Google-Suche: `pythia`, **letzte 24 Stunden** (`tbs=qdr:d`).
- Zusätzlich dieselbe Suche mit mindestens **drei Tagen Überlappung**. Nach
  Ausfällen wird bis zur letzten erfolgreichen Suche plus zwei Puffertagen
  zurückgesucht; das Nachholfenster wird nicht still auf drei Tage gekürzt.
- Eine ergänzende Suche mit EleutherAI-/Sprachmodell-Begriffen verbessert die
  Sichtbarkeit von Modelltreffern zwischen andersartigen Pythia-Ergebnissen.
- Google-Websuche, keine Beschränkung auf Google News, bestimmte Domains,
  Dateiformate oder Quellsprachen. Die Oberfläche ist auf Deutsch eingestellt.
- Alle vom Anbieter angebotenen Folgeseiten werden abgefragt, standardmäßig
  bis zehn Seiten **je Suche**. Bei noch vorhandenen Folgeseiten wird der Lauf
  rot, die Zeitmarke bleibt stehen, bereits gefundene Treffer bleiben erhalten.
  Das Limit kann mit der Repository-Variable `PYTHIA_MAX_PAGES` (1–100) erhöht
  werden; zusätzlich schützt ein zwölfminütiges Abrufbudget vor Job-Timeouts.

## Relevanz

Titel, URL und Suchauszug werden auf EleutherAI/GPT-NeoX, bekannte
Pythia-Modellgrößen und Sprachmodell-/Interpretierbarkeits-Kontext geprüft.
Erkennbare PYTHIA-Teilchenphysik, Orakel, Krypto, Prefetcher und Robotik werden
ausgeschlossen, sofern kein expliziter EleutherAI-Modellbezug vorhanden ist.
Ein bloßes „AI“ oder „Machine Learning“ reicht absichtlich nicht aus: auch
Physik-PYTHIA wird mit ML kombiniert.

Der Filter ist eine Heuristik, keine inhaltliche Volltextprüfung. Unklare
Ergebnisse stehen im Prüfbericht und im unveränderlichen Laufarchiv, werden
nicht als gemeldet markiert und bei erneutem Auftauchen erneut bewertet.
Suchbegriffe selbst gelten niemals als Relevanzbeleg. Ein Treffer ohne
erkennbaren Modellkontext im Suchauszug kann daher ohne Mail bleiben.

## Speicherung und Zustellung

1. Der Lauf sammelt Treffer in `seen_pythia_google.json` (`pending`) und
   speichert den Prüfbericht plus ein eigenes JSON pro Lauf unter
   `reports/pythia-google/`. Mehrere Läufe am selben Tag überschreiben einander
   nicht. `reports/pythia_google_latest.md` zeigt die jüngste Sammlung.
2. Der Workflow **committet und pusht diese Warteschlange vor dem Mailversand**.
   Ohne erfolgreiche Speicherung wird nichts versendet.
3. Pro neuem Link wird `send_alert.py` mit den bestehenden Secrets aufgerufen.
   Erst nach erfolgreicher SMTP-Annahme wandert der Link nach `delivered`.
4. Versandbestätigungen werden auch nach einem späteren Fehler im selben Lauf
   gespeichert. Nicht versendete Treffer bleiben in der Warteschlange, selbst
   wenn sie später aus den Google-Ergebnissen verschwinden.

Bekannte URLs werden nicht täglich erneut gemeldet. Tracking-Parameter und
Fragmente werden entfernt; arXiv-PDF/HTML/Abstract derselben Version werden
zusammengeführt. Inhaltliche Änderungen unter derselben URL lösen keine neue
Mail aus. Explizit unterschiedliche arXiv-Versions-URLs bleiben unterscheidbar.

Ein Absturz nach SMTP-Annahme, aber vor dauerhaftem Speichern der Bestätigung,
kann eine doppelte Mail verursachen. Eine exakt einmalige Zustellung lässt sich
mit SMTP und Git nicht garantieren. SMTP-Annahme bestätigt außerdem nicht die
Zustellung bis zur Pushover-App. Ein technischer Fehler ist kein stiller Tag:
der Workflow schlägt sichtbar fehl und bewahrt die letzte erfolgreiche Zeitmarke.

Google liefert keinen vollständigen, unveränderlichen Feed. Indexierung,
Datumszuordnung, Ranking, API-Kontingente und die Einordnung anhand von
Suchauszügen begrenzen die Abdeckung. Überlappung und Warteschlange verhindern
vermeidbare Verluste im Agenten; sie garantieren nicht, dass Google jeden
relevanten Beitrag zeigt. Die API-Ergebnisse können von einer personalisierten
manuellen Suche abweichen.

## Tests und API-Vertrag

```bash
python -m unittest test_pythia_google_watch -v
```

Offline getestet werden Relevanz, Physik-Abgrenzung, Datumsfenster, Pagination,
unvollständige Antworten, Secret-sichere Fehler, Duplikate, Ausfälle und
teilweise fehlgeschlagener Versand. Ein grüner Offline-Test ersetzt keinen
erfolgreichen Live-Abruf oder SMTP-Test.

- [SerpApi Google Search API: Zeitfilter, Parameter und Pagination](https://serpapi.com/search-api)
- [SerpApi organische Suchergebnisse](https://serpapi.com/organic-results)
