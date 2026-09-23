# Transition-Atlas: tägliche Veröffentlichungshinweise

Überwacht das Originalpaper **Where Decoder Cosine Similarity Fails for SAE
Feature Flow Discovery**, arXiv:2609.12591, sowie verifizierte Autorenquellen.

## Ablauf und Zustellung

- GitHub Actions: **Transition-Atlas release watch**.
- Täglich **07:47 UTC** (09:47 deutsche Sommerzeit, 08:47 Winterzeit).
  GitHub kann geplante Läufe verzögert starten. Zusätzlich manuell per
  `workflow_dispatch`; Änderungen am Monitor starten einen Kontrolllauf.
- **Eine gesammelte Mail pro Prüflauf, nur bei neuen Hinweisen.** Keine tägliche
  Keine-Neuigkeiten-Mail, keine historische Erstbefüllung und kein Test-Push.
- Unveränderter vorhandener Mailweg über `send_alert.py` und die bestehenden
  Secrets `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`. Keine neue Adresse/Secrets erforderlich;
  keine Empfängeradressen oder Zugangsdaten im öffentlichen Repository.

## Auslöser

| Kategorie im Betreff | Auslöser |
| --- | --- |
| `NEUE PAPER-VERSION` | Eine höhere explizite Versionsnummer auf der unversionierten arXiv-Abstractseite; Atom-API als Ausweichquelle. Bekannter Ausgangsstand v1. |
| `AUTOREN-GITHUB` | Neue/verschwundene öffentliche eigene Repositories, veränderte Push-Zeitpunkte (Code/README/Branches), relevante Repo-Metadaten sowie neue/veränderte Releases und Assets. Zusätzlich neue öffentliche Autorenevents, auch in anderen Repositories. Kein Atlas-Schlüsselwortfilter für Autorenaktivität. |
| `HPI-LINKHINWEIS` | Neue Forschungs-/Code-/Datenlinks auf Christian Adrianos HPI-Seite oder neue/veränderte Hinweise mit Paper-ID, Transition-Atlas, Feature-Flow oder Decoder-Cosine. Kein Alarm für bloßes Layout, Trackingparameter oder beliebige andere News. |

**Ein GitHub-Commit oder ein neuer HPI-Link ist nicht automatisch ein
veröffentlichter Atlas.** Die Benachrichtigung kennzeichnet ihn ausdrücklich
als Hinweis und nennt die konkrete Quelle. Auch eine neue Paper-Version allein
beweist keine Veröffentlichung der Daten.

## Verifizierte Autorenaccounts und bekannte Lücke

Konfiguration: [`transition_atlas_sources.json`](transition_atlas_sources.json).

- **Christian Medeiros Adriano → `christianadriano`**, direkt auf seiner
  [HPI-Seite](https://hpi.de/giese/people/christian-medeiros-adriano.html) verlinkt.
- **Kathrin Korte → `kat-ko`**, direkt im
  [ACSOS-Autorenprofil](https://2025.acsos.org/profile/kathrinkorte) verlinkt.
- **Hendrik Droste und Holger Giese:** am 23.09.2026 noch kein eindeutig
  zugeordnetes öffentliches GitHub-Profil verifiziert. Keine geratenen Accounts.
  Diese Lücke bleibt in jedem Bericht sichtbar. Ihre privaten/unbekannten
  Accounts werden nicht überwacht. Verifizierte Accounts können später in der
  Konfiguration ergänzt werden; erster Abruf legt dann still eine Basis an.

Die HPI-Seite wird auf neue Links geprüft, nicht nur auf einen vollständigen
HTML-Hash. Keine globale Drittanbieter-Suche oder wöchentliche Fremdrepo-Sammlung.
Bestehende Music-JEPA-, Goodfire- und andere Monitore bleiben unverändert.

## Vergleichszustand, Fehler und Grenzen

Zustand: `seen_transition_atlas.json` (wird beim ersten Lauf angelegt).
Bericht: [`reports/transition_atlas_latest.md`](reports/transition_atlas_latest.md).

Der erste erfolgreiche Abruf jeder GitHub-/HPI-Quelle setzt still die Basis.
Eine bereits höhere Paper-Version als die bekannte v1 wird auch beim ersten
Lauf gemeldet. Danach werden nur neue Änderungen gemeldet. Sterne, Follower,
Download-Zähler und allgemeine GitHub-`updated_at`-Werte lösen keinen Alarm aus.

Nur öffentliche Quellen; keine vollständige Sicht auf private oder anonyme
Repos. Die GitHub Events API stellt maximal 300 jüngste öffentliche Events zur
Verfügung. Sehr hohe Aktivität oder sehr kurze Änderungen zwischen zwei
Tagesabfragen können deshalb unvollständig sein. Eigene Repo- und
Release-Zustände werden zusätzlich unabhängig von diesem Eventfeed verglichen.
Ein Push ist zunächst durch `pushed_at` sichtbar, nicht durch eine semantische
Analyse jedes veränderten Files. Reine GitHub-Profilkosmetik wird nicht geprüft.

Ein fehlgeschlagener Quellenabruf überschreibt keinen früheren erfolgreichen
Stand und zählt nicht als Keine-Neuigkeiten-Ergebnis. Der Bericht nennt die
unvollständigen Checks, und der letzte Workflow-Schritt markiert den Lauf als
fehlgeschlagen. Dafür wird keine gesonderte Pushover-Statusmail verschickt.

Die Mail wird **vor** dem Git-Commit des neuen Vergleichszustands gesendet.
Bei SMTP-Fehlern wird der Zustand nicht auf `main` gespeichert; die Meldung kann
im nächsten Lauf erneut versucht werden. Scheitert ausnahmsweise der Commit
nach erfolgreichem SMTP, ist eine doppelte Zustellung möglich (kein
Exactly-once-Versprechen).

## Testen

```bash
pip install -r requirements.txt
python -m unittest test_transition_atlas_watch -v
python transition_atlas_watch.py --dry-run --alert-file /tmp/transition-atlas.txt
```

`--dry-run` verändert weder gespeicherten Zustand noch Bericht und versendet
keine Mail. Im Actions-Menü ist derselbe Trockenlauf als Eingabe verfügbar.
Die Offline-Tests prüfen u.a. Baselines, Deduplizierung, höhere und veraltete
Paper-Versionen, Repo-/Release-Änderungen, HPI-Layoutfilter, Fehlererhaltung und
Beschränkung des GitHub-Tokens auf API-Anfragen an `api.github.com`.
