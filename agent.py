import json

def _count_actions_in_obj(obj, stats):
    """Durchsucht beliebige Python-Objekte rekursiv nach Action-Einträgen."""
    if obj is None:
        return

    # Liste: jedes Element prüfen
    if isinstance(obj, list):
        for x in obj:
            _count_actions_in_obj(x, stats)
        return

    # Dict: typische Felder auswerten
    if isinstance(obj, dict):
        # browser-use nutzt oft "items" für Actions
        if "items" in obj and isinstance(obj["items"], list):
            for it in obj["items"]:
                _count_actions_in_obj(it, stats)

        # Action-Typ kann in "type" oder "action" stehen
        t = obj.get("type") or obj.get("action")
        if isinstance(t, str):
            tl = t.lower()
            if "click" in tl:
                stats["clicks"] += 1
            elif "type" in tl or "input" in tl or "fill" in tl:
                stats["types"] += 1
            elif "scroll" in tl:
                stats["scrolls"] += 1
            elif "wait" in tl:
                stats["waits"] += 1
            elif "navigate" in tl or "goto" in tl:
                stats["navigates"] += 1

        # Rekursiv weiter
        for v in obj.values():
            _count_actions_in_obj(v, stats)
        return

    # Sonst: nichts tun
    return


def analyze_history(history):
    stats = {
        "clicks": 0,
        "types": 0,
        "scrolls": 0,
        "waits": 0,
        "navigates": 0,
        "errors": 0,
    }

    for step in getattr(history, "history", []):
        # Fehler zählen
        if getattr(step, "error", None):
            stats["errors"] += 1

        # Versuche: model_output strukturiert
        mo = getattr(step, "model_output", None)
        if mo is not None:
            # 1) Wenn es schon dict/list ist
            if isinstance(mo, (dict, list)):
                _count_actions_in_obj(mo, stats)
            else:
                # 2) Wenn String: versuche JSON zu laden
                s = str(mo).strip()
                if s.startswith("{") or s.startswith("["):
                    try:
                        _count_actions_in_obj(json.loads(s), stats)
                    except Exception:
                        pass

        # Fallback: result kann Actions enthalten
        res = getattr(step, "result", None)
        if isinstance(res, (dict, list)):
            _count_actions_in_obj(res, stats)

    report = (
        f"📊 TELEMETRIE\n"
        f"- Navigates: {stats['navigates']}\n"
        f"- Waits: {stats['waits']}\n"
        f"- Scrolls: {stats['scrolls']}\n"
        f"- Klicks: {stats['clicks']}\n"
        f"- Inputs: {stats['types']}\n"
        f"- Fehler: {stats['errors']}\n"
    )
    return stats, report
