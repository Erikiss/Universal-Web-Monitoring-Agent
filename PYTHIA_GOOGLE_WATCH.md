# Pythia Google watch

Tägliche Google-Websuche nach **Pythia von EleutherAI**, einschließlich Paper,
Blogs, Modellkarten und anderer Webseiten. Alle neuen relevanten Links eines
Laufs werden in **einer Sammelmail und damit einem Pushover-Alarm** gebündelt.
Ohne vorgemerkte Treffer wird keine Mail versendet.

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
3. `send_alert.py` wird **höchstens einmal pro Lauf** mit den bestehenden
   Secrets aufgerufen. Die Sammelmail umfasst alle vorgemerkten Links,
   einschließlich noch nicht versendeter Treffer aus früheren Läufen.
   Der Alarm enthält die Gesamtzahl, eine kompakte Liste und den Link zum
   Prüfbericht. Der Text bleibt innerhalb von 1024 Zeichen; bei vielen oder
   langen Treffern stehen die vollständigen Titel, Links und Suchauszüge im
   Abschnitt „Sammelmeldung“ des vorab gespeicherten Prüfberichts und Laufarchivs.
   Lange Listen werden **nicht auf mehrere Alarme aufgeteilt**.
4. Erst nach erfolgreicher SMTP-Annahme wandern alle enthaltenen Links gemeinsam
   nach `delivered`. Bei einem Versandfehler bleibt die gesamte Warteschlange
   erhalten, selbst wenn Treffer später aus den Google-Ergebnissen verschwinden.

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
Sammelversand (fünf Treffer → eine Mail), lange Listen und fehlgeschlagener
Versand mit anschließendem erneutem Versuch. Ein grüner Offline-Test ersetzt keinen
erfolgreichen Live-Abruf oder SMTP-Test.

- [SerpApi Google Search API: Zeitfilter, Parameter und Pagination](https://serpapi.com/search-api)
- [SerpApi organische Suchergebnisse](https://serpapi.com/organic-results)
- [Pushover-Nachrichtenlimit](https://pushover.net/api#limits)
