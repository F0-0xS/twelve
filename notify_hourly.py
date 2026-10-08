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


def main():
    utc_now = datetime.utcnow()
    print(f"[notify_hourly] Running at {utc_now.strftime('%Y-%m-%d %H:%M')} UTC")

    # Include yesterday to catch recap notifications that fire after UTC midnight
    yesterday = (utc_now.date() - timedelta(days=1)).isoformat()
    challenges = db.get_all_active_challenges(from_date=yesterday)
    print(f"[notify_hourly] {len(challenges)} active challenge(s)")

    for challenge in challenges:
        cdate = challenge["challenge_date"]
        name  = challenge["name"]
        cid   = challenge["id"]
        subs  = db.get_push_subscriptions(cid)
        print(f"[notify] {cid} ({cdate}): {len(subs)} sub(s)")
        if not subs:
            continue

        # ── Slot notifications (independant des participants) ─────────────────
        utc_offset_min = challenge.get("utc_offset_minutes") or 0
        local_now  = utc_now + timedelta(minutes=utc_offset_min)
        local_hour = local_now.hour
        local_date = local_now.date().isoformat()

        if local_hour in db.SLOT_HOURS and local_date == cdate:
            slot_key = f"slot:{local_date}:{local_hour:02d}"
            if db.has_notified(cid, slot_key):
                print(f"[notify] slot {local_hour:02d}h → {name}: déjà envoyé, skip")
            else:
                sent = notify_challenge(
                    cid,
                    f"📸 Créneau {local_hour:02d}h00 ouvert !",
                    f"C'est le moment de prendre ta photo pour « {name} ».",
                )
                if sent > 0:
                    db.mark_notified(cid, slot_key)
                print(f"[notify] slot {local_hour:02d}h (UTC+{utc_offset_min//60}) → {name}: {sent} notifs")

        # ── Recap notification ────────────────────────────────────────────────
        try:
            participants = db.get_participants(cid)
            recap_unlock = compute_recap_unlock(challenge, participants)
            if (utc_now.date() == recap_unlock.date() and
                    utc_now.hour == recap_unlock.hour):
                recap_key = f"recap:{recap_unlock.strftime('%Y-%m-%d:%H')}"
                if db.has_notified(cid, recap_key):
                    print(f"[notify] récap → {name}: déjà envoyé, skip")
                else:
                    sent = notify_challenge(
                        cid,
                        "🎞️ Le récap est disponible !",
                        f"La journée est terminée — découvrez toutes les photos de « {name} » !",
                        url=f"/challenge/{cid}/summary",
                    )
                    if sent > 0:
                        db.mark_notified(cid, recap_key)
                    print(f"[notify] récap unlock {recap_unlock.strftime('%H:%M')} UTC → {name}: {sent} notifs")
        except Exception as e:
            print(f"[notify] recap check failed for {cid}: {e}")

    print("[notify_hourly] Done.")


if __name__ == "__main__":
    main()
