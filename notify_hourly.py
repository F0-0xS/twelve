#!/usr/bin/env python3
"""
Twelve – Hourly notification script (called by cron-job.org every hour at :00)
Sends push notifications when a slot opens, using the challenge's local timezone.
"""
import sys
import os
import json
from datetime import datetime, date, timedelta

sys.path.insert(0, '/home/FOOxS/venv39/lib/python3.9/site-packages')
sys.path.insert(1, '/home/FOOxS/twelve')

os.environ.setdefault('TWELVE_DB_PATH', '/home/FOOxS/twelve/twelve.db')

# VAPID private key: prefer PEM file, fall back to env variable
_PEM_PATH = '/home/FOOxS/twelve/vapid_private_key.pem'
VAPID_PRIVATE_KEY = _PEM_PATH if os.path.exists(_PEM_PATH) else os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS = {"sub": "mailto:admin@fooxs.pythonanywhere.com"}

import db


def send_push(subscription_json: str, title: str, body: str, url: str = "/") -> bool:
    if not VAPID_PRIVATE_KEY:
        print("[push] VAPID_PRIVATE_KEY not set, skipping.")
        return False
    try:
        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=json.loads(subscription_json),
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return True
    except Exception as e:
        err = str(e)
        if "410" in err or "404" in err:
            try:
                sub = json.loads(subscription_json)
                db.delete_push_subscription(sub.get("endpoint", ""))
            except Exception:
                pass
        print(f"[push] failed: {e}")
        return False


def notify_challenge(challenge_id: str, title: str, body: str, url: str = None):
    subs = db.get_push_subscriptions(challenge_id)
    if url is None:
        url = f"/challenge/{challenge_id}"
    sent = 0
    for sub in subs:
        if send_push(sub["subscription_json"], title, body, url):
            sent += 1
    return sent


def compute_recap_unlock(challenge: dict, participants: list) -> datetime:
    """Returns the UTC datetime when the recap unlocks for all participants."""
    from datetime import date as _date
    cdate = challenge["challenge_date"]
    next_day_dt = datetime.strptime(cdate, "%Y-%m-%d") + timedelta(days=1)
    offsets = [p.get("utc_offset_minutes") or 0 for p in participants]
    if not offsets:
        offsets = [challenge.get("utc_offset_minutes") or 0]
    min_offset = min(offsets)  # most behind UTC = latest midnight
    return next_day_dt - timedelta(minutes=min_offset)


def notify_participant(challenge_id: str, participant_id: str, title: str, body: str, url: str = None) -> int:
    """Envoie seulement aux téléphones d'un joueur."""
    if url is None:
        url = f"/challenge/{challenge_id}"
    sent = 0
    for sub in db.get_push_subscriptions(challenge_id):
        if sub.get("participant_id") == participant_id:
            if send_push(sub["subscription_json"], title, body, url):
                sent += 1
    return sent


REMIND_BEFORE_CLOSE_MIN = 15   # rappel « plus que 15 min » si pas encore de photo
RECAP_WINDOW_H = 6             # le récap est annoncé dans les 6 h qui suivent minuit


def _color_hint(challenge: dict) -> str:
    try:
        c = db.daily_color(challenge)
    except Exception:
        c = None
    return f" Couleur du jour : {c['name']}." if c else ""


def main(utc_now: datetime = None):
    """Envoie les notifications dues. Sans risque si appelée souvent (toutes les 5-10 min) :
    chaque notification n'est envoyée qu'une fois (table notified)."""
    utc_now = utc_now or datetime.utcnow()
    print(f"[notify] {utc_now.strftime('%Y-%m-%d %H:%M')} UTC")

    # Hier inclus : le récap tombe après minuit
    yesterday = (utc_now.date() - timedelta(days=2)).isoformat()
    challenges = db.get_all_active_challenges(from_date=yesterday)

    for challenge in challenges:
        cdate = challenge["challenge_date"]
        name  = challenge["name"]
        cid   = challenge["id"]
        subs  = db.get_push_subscriptions(cid)
        if not subs:
            continue

        utc_offset_min = challenge.get("utc_offset_minutes") or 0
        local_now  = utc_now + timedelta(minutes=utc_offset_min)
        local_hour = local_now.hour
        local_date = local_now.date().isoformat()

        # ── Créneau ouvert (de HH:00 à HH:59) ────────────────────────────────
        if local_hour in db.SLOT_HOURS and local_date == cdate:
            slot_key = f"slot:{local_date}:{local_hour:02d}"
            if not db.has_notified(cid, slot_key):
                sent = notify_challenge(
                    cid,
                    f"📸 Créneau {local_hour:02d}h00 ouvert !",
                    f"Prends ta photo pour « {name} ».{_color_hint(challenge)}",
                )
                if sent > 0:
                    db.mark_notified(cid, slot_key)
                print(f"[notify] ouverture {local_hour:02d}h → {name}: {sent}")

            # ── Plus que 15 min : seulement à ceux qui n'ont rien posté ───────
            if local_now.minute >= 60 - REMIND_BEFORE_CLOSE_MIN:
                slot_index = db.SLOT_HOURS.index(local_hour)
                done = {c["participant_id"] for c in db.get_contributions(cid) if c["slot_index"] == slot_index}
                for p in db.get_participants(cid):
                    if p["id"] in done:
                        continue
                    key = f"remind:{local_date}:{local_hour:02d}:{p['id']}"
                    if db.has_notified(cid, key):
                        continue
                    sent = notify_participant(
                        cid, p["id"],
                        f"⏳ Plus que {60 - local_now.minute} min",
                        f"Le créneau {local_hour:02d}h00 de « {name} » se ferme bientôt.",
                        url=f"/challenge/{cid}/slot/{slot_index}",
                    )
                    if sent > 0:
                        db.mark_notified(cid, key)
                    print(f"[notify] rappel {local_hour:02d}h {p['name']} → {sent}")

        # ── Récap disponible (dans les 6 h après son ouverture) ──────────────
        try:
            participants = db.get_participants(cid)
            recap_unlock = compute_recap_unlock(challenge, participants)
            if recap_unlock <= utc_now < recap_unlock + timedelta(hours=RECAP_WINDOW_H):
                recap_key = f"recap:{cdate}"
                if not db.has_notified(cid, recap_key):
                    couleur = (challenge.get("mode") or "libre") == "couleur"
                    sent = notify_challenge(
                        cid,
                        "🖼️ Les tableaux sont prêts !" if couleur else "🎞️ Le récap est disponible !",
                        f"La journée « {name} » est finie : viens voir les photos de tout le monde.",
                        url=f"/challenge/{cid}/tableaux" if couleur else f"/challenge/{cid}/summary",
                    )
                    if sent > 0:
                        db.mark_notified(cid, recap_key)
                    print(f"[notify] récap → {name}: {sent}")
        except Exception as e:
            print(f"[notify] recap check failed for {cid}: {e}")

    print("[notify] fini.")


if __name__ == "__main__":
    main()
