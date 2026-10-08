"""
Twelve – Main Tornado application
"""
from __future__ import annotations
import os
import io
import json
import uuid
import socket
import traceback
from datetime import datetime, timedelta

import threading
import tornado.web
import tornado.ioloop
import tornado.options
from PIL import Image, ImageOps

import db

# ─── Auto-notify: lance notify_hourly au premier accès de chaque heure UTC ────
_notify_lock = threading.Lock()
_last_notify_hour: datetime | None = None

def _maybe_notify():
    """Appelé à chaque requête. Lance notify_hourly.main() en background
    si l'heure UTC a changé depuis le dernier appel. Idempotent grâce à
    notification_log dans la DB."""
    global _last_notify_hour
    now_h = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    with _notify_lock:
        if _last_notify_hour == now_h:
            return
        _last_notify_hour = now_h
    def _run():
        try:
            import notify_hourly
            notify_hourly.main()
        except Exception as _e:
            print(f"[auto-notify] {_e}")
    threading.Thread(target=_run, daemon=True).start()

# ─── Utility ──────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    """Return the machine's LAN IP (e.g. 192.168.x.x) for the share link."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

UPLOAD_DIR = os.environ.get(
    "TWELVE_UPLOAD_DIR",
    os.path.join(os.path.dirname(__file__), "static", "uploads"),
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

COOKIE_SECRET = os.environ.get("TWELVE_COOKIE_SECRET", "twelve-dev-secret-change-in-prod-please")
PORT = int(os.environ.get("PORT", 8888))
MAX_PARTICIPANTS = 6
VAPID_PUBLIC_KEY = "BLlM08OwpGSh3PfiBXwoJWR7QjyqXOp6ZwyNyqlgXCY7k8EmAuHuAh_atp7jvDJbEs3Y_7Xt1qX0frskgXSzfto"

# ─── Helpers ─────────────────────────────────────────────────────────────────

def compute_recap_unlock_utc_ms(challenge: dict, participants: list) -> int:
    """Returns the UTC timestamp (ms) when the recap should unlock for all participants.
    Uses the LATEST local midnight across all participant timezones (most-behind-UTC wins)."""
    cdate = challenge["challenge_date"]
    next_day = datetime.strptime(cdate, "%Y-%m-%d") + timedelta(days=1)
    offsets = [p.get("utc_offset_minutes") or 0 for p in participants]
    if not offsets:
        offsets = [challenge.get("utc_offset_minutes") or 0]
    min_offset = min(offsets)  # smallest = most behind UTC = latest midnight in UTC
    # Local midnight in UTC: next_day 00:00 local = next_day 00:00 UTC - offset_min
    recap_dt = next_day - timedelta(minutes=min_offset)
    return int(recap_dt.timestamp() * 1000)


def all_slots_filled(participants: list, contributions: list) -> bool:
    """True if every participant has a contribution (photo or missed) for every slot."""
    if not participants:
        return False
    n = len(db.SLOT_HOURS)
    keys = {(c["participant_id"], c["slot_index"]) for c in contributions}
    return all(
        all((p["id"], i) in keys for i in range(n))
        for p in participants
    )


def get_waiting_names(participants: list, contributions: list) -> list:
    """Names of participants who haven't filled all their slots yet."""
    n = len(db.SLOT_HOURS)
    keys = {(c["participant_id"], c["slot_index"]) for c in contributions}
    return [
        p["name"] for p in participants
        if sum(1 for i in range(n) if (p["id"], i) in keys) < n
    ]


# ─── Base Handler ─────────────────────────────────────────────────────────────

class BaseHandler(tornado.web.RequestHandler):
    def prepare(self):
        _maybe_notify()

    def get_current_participant_id(self, challenge_id: str) -> str | None:
        raw = self.get_secure_cookie("twelve_ids")
        if not raw:
            return None
        try:
            mapping = json.loads(raw.decode())
            return mapping.get(challenge_id)
        except Exception:
            return None

    def set_participant_id(self, challenge_id: str, participant_id: str):
        raw = self.get_secure_cookie("twelve_ids")
        try:
            mapping = json.loads(raw.decode()) if raw else {}
        except Exception:
            mapping = {}
        mapping[challenge_id] = participant_id
        self.set_secure_cookie("twelve_ids", json.dumps(mapping), expires_days=30)

    def get_share_url(self, challenge_id: str) -> str:
        """Build share URL, handling tunnels (localhost.run, ngrok) and LAN."""
        host = self.request.host  # e.g. "localhost:8888" or "abc.lhr.life"
        hostname = host.split(":")[0]
        port = host.split(":")[1] if ":" in host else None

        if hostname in ("localhost", "127.0.0.1"):
            # Accès local → utiliser l'IP LAN
            hostname = get_local_ip()
            return f"http://{hostname}:{port or PORT}/challenge/{challenge_id}"

        # Tunnel public (localhost.run, ngrok…) → utiliser https, sans port
        protocol = "https"
        forwarded_proto = self.request.headers.get("X-Forwarded-Proto", "")
        if forwarded_proto:
            protocol = forwarded_proto
        if port and port not in ("80", "443"):
            return f"{protocol}://{hostname}:{port}/challenge/{challenge_id}"
        return f"{protocol}://{hostname}/challenge/{challenge_id}"

    def render_error(self, status: int, message: str):
        self.set_status(status)
        self.render("error.html", message=message, status=status)


# ─── Home ─────────────────────────────────────────────────────────────────────

class HomeHandler(BaseHandler):
    def get(self):
        # Récupère tous les défis que l'utilisateur a créés ou rejoints
        raw = self.get_secure_cookie("twelve_ids")
        my_challenges = []
        if raw:
            try:
                mapping = json.loads(raw.decode())
                for cid in mapping:
                    c = db.get_challenge(cid)
                    if c:
                        my_challenges.append(c)
            except Exception:
                pass
        # Trier par date de création décroissante
        my_challenges.sort(key=lambda c: c.get("created_at", ""), reverse=True)
        from datetime import date as _date
        today = _date.today().isoformat()
        active_challenges = [c for c in my_challenges if c.get("challenge_date", "") >= today]
        past_challenges   = [c for c in my_challenges if c.get("challenge_date", "") < today]
        self.render("home.html", error=None, my_challenges=my_challenges,
                    active_challenges=active_challenges, past_challenges=past_challenges)

    def post(self):
        name = self.get_argument("name", "").strip()
        if not name:
            self.render("home.html", error="Le nom du défi est requis.", my_challenges=[])
            return
        if len(name) > 60:
            name = name[:60]
        # Use the browser's local date (sent as a hidden field) so the
        # challenge_date matches the creator's timezone, not the server's.
        client_date = self.get_argument("challenge_date", "").strip()
        try:
            datetime.strptime(client_date, "%Y-%m-%d")
        except (ValueError, AttributeError):
            client_date = None
        try:
            utc_offset = int(self.get_argument("utc_offset_minutes", "0"))
        except ValueError:
            utc_offset = 0
        challenge = db.create_challenge(name, challenge_date=client_date, utc_offset_minutes=utc_offset)
        self.redirect(f"/challenge/{challenge['id']}")


# ─── Challenge ────────────────────────────────────────────────────────────────

class ChallengeHandler(BaseHandler):
    def get(self, challenge_id: str):
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            return self.render_error(404, "Ce défi n'existe pas.")

        participants = db.get_participants(challenge_id)
        participant_id = self.get_current_participant_id(challenge_id)
        participant = db.get_participant(participant_id) if participant_id else None

        # Validate stored participant still belongs to this challenge
        if participant and participant.get("challenge_id") != challenge_id:
            participant = None
            participant_id = None

        contributions = db.get_contributions(challenge_id)
        slots = db.build_timeline(challenge, participants, contributions)

        # Next open / upcoming slot
        next_slot = next(
            (s for s in slots if s["status"] in ("open", "upcoming")), None
        )

        # Find current participant's contributions for quick lookup
        my_contribs = {}
        if participant_id:
            for c in contributions:
                if c["participant_id"] == participant_id:
                    my_contribs[c["slot_index"]] = c

        recap_unlock_utc_ms = compute_recap_unlock_utc_ms(challenge, participants)
        filled = all_slots_filled(participants, contributions)
        waiting_names = get_waiting_names(participants, contributions)

        self.render(
            "challenge.html",
            challenge=challenge,
            participants=participants,
            participant=participant,
            slots=slots,
            next_slot=next_slot,
            my_contribs=my_contribs,
            error=None,
            full=(len(participants) >= MAX_PARTICIPANTS and not participant),
            slot_hours=db.SLOT_HOURS,
            share_url=self.get_share_url(challenge_id),
            vapid_public_key=VAPID_PUBLIC_KEY,
            recap_unlock_utc_ms=recap_unlock_utc_ms,
            all_filled=filled,
            waiting_names=waiting_names,
        )

    def post(self, challenge_id: str):
        """Join the challenge."""
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            return self.render_error(404, "Ce défi n'existe pas.")

        participants = db.get_participants(challenge_id)

        # Already joined?
        participant_id = self.get_current_participant_id(challenge_id)
        if participant_id and any(p["id"] == participant_id for p in participants):
            self.redirect(f"/challenge/{challenge_id}")
            return

        share_url = self.get_share_url(challenge_id)

        # Capture participant's timezone offset
        try:
            join_utc_offset = int(self.get_argument("utc_offset_minutes", "0") or "0")
        except (ValueError, TypeError):
            join_utc_offset = 0

        recap_ms = compute_recap_unlock_utc_ms(challenge, participants)

        if len(participants) >= MAX_PARTICIPANTS:
            contributions = db.get_contributions(challenge_id)
            slots = db.build_timeline(challenge, participants, contributions)
            self.render(
                "challenge.html",
                challenge=challenge, participants=participants,
                participant=None, slots=slots, next_slot=None,
                my_contribs={}, error="Ce défi est complet (6 participants max).",
                full=True, slot_hours=db.SLOT_HOURS, share_url=share_url,
                vapid_public_key=VAPID_PUBLIC_KEY, recap_unlock_utc_ms=recap_ms,
                all_filled=all_slots_filled(participants, contributions),
                waiting_names=get_waiting_names(participants, contributions),
            )
            return

        name = self.get_argument("name", "").strip()
        if not name:
            contributions = db.get_contributions(challenge_id)
            slots = db.build_timeline(challenge, participants, contributions)
            self.render(
                "challenge.html",
                challenge=challenge, participants=participants,
                participant=None, slots=slots, next_slot=None,
                my_contribs={}, error="Ton prénom est requis.",
                full=False, slot_hours=db.SLOT_HOURS, share_url=share_url,
                vapid_public_key=VAPID_PUBLIC_KEY, recap_unlock_utc_ms=recap_ms,
                all_filled=all_slots_filled(participants, contributions),
                waiting_names=get_waiting_names(participants, contributions),
            )
            return

        if len(name) > 30:
            name = name[:30]

        new_participant = db.join_challenge(challenge_id, name, utc_offset_minutes=join_utc_offset)
        if not new_participant:
            return self.render_error(409, "Ce défi est déjà complet.")

        self.set_participant_id(challenge_id, new_participant["id"])

        self.redirect(f"/challenge/{challenge_id}")


# ─── Slot ─────────────────────────────────────────────────────────────────────

class SlotHandler(BaseHandler):
    def get(self, challenge_id: str, slot_index: str):
        challenge, participant, slot_index = self._preflight(challenge_id, slot_index)
        if not challenge:
            return

        contributions = db.get_contributions(challenge_id)
        participants = db.get_participants(challenge_id)
        slots = db.build_timeline(challenge, participants, contributions)
        slot = slots[slot_index]

        participant_id = self.get_current_participant_id(challenge_id)
        my_contribution = db.get_contribution(challenge_id, participant_id, slot_index) if participant_id else None

        self.render(
            "slot.html",
            challenge=challenge,
            participant=participant,
            slot=slot,
            my_contribution=my_contribution,
            missed_options=db.MISSED_OPTIONS,
            error=None,
        )

    def post(self, challenge_id: str, slot_index: str):
        challenge, participant, slot_index = self._preflight(challenge_id, slot_index)
        if not challenge:
            return

        action = self.get_argument("action", "")

        if action == "photo":
            self._handle_photo(challenge, participant, slot_index)
        elif action == "missed":
            self._handle_missed(challenge, participant, slot_index)
        else:
            self.render_error(400, "Action inconnue.")

    def _preflight(self, challenge_id, slot_index_str):
        """Common validation. Returns (challenge, participant, slot_index) or (None,None,None)."""
        try:
            slot_index = int(slot_index_str)
            assert 0 <= slot_index <= 11
        except (ValueError, AssertionError):
            self.render_error(400, "Créneau invalide.")
            return None, None, None

        challenge = db.get_challenge(challenge_id)
        if not challenge:
            self.render_error(404, "Ce défi n'existe pas.")
            return None, None, None

        participant_id = self.get_current_participant_id(challenge_id)
        participant = db.get_participant(participant_id) if participant_id else None
        if not participant or participant.get("challenge_id") != challenge_id:
            self.redirect(f"/challenge/{challenge_id}")
            return None, None, None

        return challenge, participant, slot_index

    def _handle_photo(self, challenge, participant, slot_index):
        files = self.request.files.get("photo")
        if not files:
            return self._render_slot_error(challenge, participant, slot_index, "Aucune photo reçue.")

        file_info = files[0]
        try:
            img_data = file_info["body"]
            img = Image.open(io.BytesIO(img_data))
            img = ImageOps.exif_transpose(img)  # fix EXIF rotation before anything else
            img = img.convert("RGB")

            # Resize to max 1200px on longest side
            img.thumbnail((1200, 1200), Image.LANCZOS)

            # Save with EXIF stripped for privacy + size
            challenge_upload_dir = os.path.join(UPLOAD_DIR, challenge["id"])
            os.makedirs(challenge_upload_dir, exist_ok=True)
            filename = f"{uuid.uuid4().hex}.jpg"
            save_path = os.path.join(challenge_upload_dir, filename)
            img.save(save_path, "JPEG", quality=82, optimize=True)

            photo_url = f"/static/uploads/{challenge['id']}/{filename}"
        except Exception as e:
            return self._render_slot_error(
                challenge, participant, slot_index,
                f"Erreur lors du traitement de l'image : {e}"
            )

        db.add_contribution(
            challenge["id"], participant["id"], slot_index,
            type="photo", photo_url=photo_url,
        )
        self.redirect(f"/challenge/{challenge['id']}")

    def _handle_missed(self, challenge, participant, slot_index):
        reason = self.get_argument("reason", "").strip()
        emoji = self.get_argument("emoji", "").strip()
        if not reason:
            return self._render_slot_error(
                challenge, participant, slot_index, "Choisis une raison."
            )

        db.add_contribution(
            challenge["id"], participant["id"], slot_index,
            type="missed", missed_reason=reason, missed_emoji=emoji,
        )
        self.redirect(f"/challenge/{challenge['id']}")

    def _render_slot_error(self, challenge, participant, slot_index, error_msg):
        contributions = db.get_contributions(challenge["id"])
        participants = db.get_participants(challenge["id"])
        slots = db.build_timeline(challenge, participants, contributions)
        slot = slots[slot_index]
        my_contribution = db.get_contribution(
            challenge["id"], participant["id"], slot_index
        )
        self.render(
            "slot.html",
            challenge=challenge, participant=participant, slot=slot,
            my_contribution=my_contribution, missed_options=db.MISSED_OPTIONS,
            error=error_msg,
        )


# ─── Summary ──────────────────────────────────────────────────────────────────

class SummaryHandler(BaseHandler):
    def get(self, challenge_id: str):
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            return self.render_error(404, "Ce défi n'existe pas.")

        participants = db.get_participants(challenge_id)
        contributions = db.get_contributions(challenge_id)

        # Gate: recap only accessible when midnight passed AND all slots filled
        unlock_ms = compute_recap_unlock_utc_ms(challenge, participants)
        unlock_dt = datetime.utcfromtimestamp(unlock_ms / 1000)
        if datetime.utcnow() < unlock_dt or not all_slots_filled(participants, contributions):
            self.redirect(f"/challenge/{challenge_id}")
            return

        slots = db.build_timeline(challenge, participants, contributions)

        participant_id = self.get_current_participant_id(challenge_id)
        participant = db.get_participant(participant_id) if participant_id else None

        # Precompute individual stories: one entry per (slot × participant)
        import json as _json
        contribs_by_key = {
            (c["participant_id"], c["slot_index"]): c
            for c in contributions
        }
        stories = []
        for slot in slots:
            for p in participants:
                c = contribs_by_key.get((p["id"], slot["index"]))
                t = c["type"] if c else "empty"
                if t == "empty":
                    continue  # skip unsubmitted — keep only photo/missed
                stories.append({
                    "slot_label":    slot["label"],
                    "slot_hour":     slot["hour"],
                    "p_name":        p["name"],
                    "type":          t,
                    "photo_url":     (c.get("photo_url") or "") if c else "",
                    "missed_emoji":  (c.get("missed_emoji") or "❓") if c else "❓",
                    "missed_reason": (c.get("missed_reason") or "Manqué") if c else "Manqué",
                })
        stories_json = _json.dumps(stories, ensure_ascii=False).replace("</", "<\\/")

        # Slot-by-slot data for the stories slideshow (all participants per slot)
        slots_data = []
        for slot in slots:
            cells = []
            for p in participants:
                c = contribs_by_key.get((p["id"], slot["index"]))
                cells.append({
                    "p_name":        p["name"],
                    "type":          (c["type"] if c else "empty"),
                    "photo_url":     (c.get("photo_url") or "") if c else "",
                    "missed_emoji":  (c.get("missed_emoji") or "❓") if c else "❓",
                    "missed_reason": (c.get("missed_reason") or "Manqué") if c else "Manqué",
                })
            slots_data.append({"label": slot["label"], "hour": slot["hour"], "cells": cells})
        slots_json = _json.dumps(slots_data, ensure_ascii=False).replace("</", "<\\/")

        self.render(
            "summary.html",
            challenge=challenge,
            participants=participants,
            contributions=contributions,
            participant=participant,
            slots=slots,
            stories_json=stories_json,
            slots_json=slots_json,
            n_stories=len(stories),
            n_participants=len(participants),
        )


# ─── Rename participant ───────────────────────────────────────────────────────

class RenameHandler(BaseHandler):
    def post(self, challenge_id: str):
        participant_id = self.get_current_participant_id(challenge_id)
        if not participant_id:
            self.set_status(403)
            self.write({"error": "not in this challenge"})
            return
        new_name = self.get_argument("name", "").strip()
        if not new_name:
            self.set_status(400)
            self.write({"error": "name required"})
            return
        ok = db.update_participant_name(participant_id, new_name)
        if ok:
            self.redirect(f"/challenge/{challenge_id}")
        else:
            self.set_status(400)
            self.write({"error": "invalid name"})


# ─── Leave challenge ──────────────────────────────────────────────────────────

class LeaveHandler(BaseHandler):
    def post(self, challenge_id: str):
        participant_id = self.get_current_participant_id(challenge_id)
        if participant_id:
            db.leave_challenge(challenge_id, participant_id)
            # Remove from cookie
            raw = self.get_secure_cookie("twelve_ids")
            try:
                mapping = json.loads(raw.decode()) if raw else {}
            except Exception:
                mapping = {}
            mapping.pop(challenge_id, None)
            self.set_secure_cookie("twelve_ids", json.dumps(mapping), expires_days=30)
        self.redirect("/")


# ─── Forget challenge (remove from home screen, keep participation in DB) ─────

class ForgetHandler(BaseHandler):
    def post(self, challenge_id: str):
        raw = self.get_secure_cookie("twelve_ids")
        try:
            mapping = json.loads(raw.decode()) if raw else {}
        except Exception:
            mapping = {}
        mapping.pop(challenge_id, None)
        self.set_secure_cookie("twelve_ids", json.dumps(mapping), expires_days=30)
        self.redirect("/")


# ─── API: Data refresh (JSON) ─────────────────────────────────────────────────

class ApiChallengeDataHandler(BaseHandler):
    def get(self, challenge_id: str):
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            self.set_status(404)
            self.write({"error": "not found"})
            return
        participants = db.get_participants(challenge_id)
        contributions = db.get_contributions(challenge_id)
        slots = db.build_timeline(challenge, participants, contributions)

        self.set_header("Content-Type", "application/json")
        self.write({
            "participants": participants,
            "contributions": contributions,
            "slots": [
                {
                    "index": s["index"],
                    "label": s["label"],
                    "status": s["status"],
                    "contribution_count": len(s["contributions"]),
                }
                for s in slots
            ],
        })


# ─── JSON API (for iOS app) ───────────────────────────────────────────────────

class ApiBaseHandler(tornado.web.RequestHandler):
    """JSON-only base handler — no cookies, no HTML."""

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, X-Participant-Id")

    def options(self, *args):
        self.set_status(204)
        self.finish()

    def json_body(self):
        try:
            return json.loads(self.request.body)
        except Exception:
            return {}

    def participant_id(self):
        return self.request.headers.get("X-Participant-Id", "").strip() or None

    def err(self, status, msg):
        self.set_status(status)
        self.write({"error": msg})


class ApiCreateChallengeHandler(ApiBaseHandler):
    """POST /api/challenges  →  {name, challenge_date?}  →  challenge dict"""

    def post(self):
        body = self.json_body()
        name = (body.get("name") or "").strip()[:60]
        if not name:
            return self.err(400, "name required")
        date_str = (body.get("challenge_date") or "").strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, AttributeError):
            date_str = None
        challenge = db.create_challenge(name, challenge_date=date_str)
        self.write(challenge)


class ApiJoinChallengeHandler(ApiBaseHandler):
    """POST /api/challenges/<id>/join  →  {name}  →  {challenge, participant}"""

    def post(self, challenge_id):
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            return self.err(404, "challenge not found")

        body = self.json_body()
        name = (body.get("name") or "").strip()[:30]
        if not name:
            return self.err(400, "name required")

        participants = db.get_participants(challenge_id)
        if len(participants) >= MAX_PARTICIPANTS:
            return self.err(409, "challenge full")

        participant = db.join_challenge(challenge_id, name)
        if not participant:
            return self.err(409, "challenge full")

        self.write({"challenge": challenge, "participant": participant})


class ApiChallengeHandler(ApiBaseHandler):
    """GET /api/challenges/<id>  →  full challenge state"""

    def get(self, challenge_id):
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            return self.err(404, "challenge not found")

        participants = db.get_participants(challenge_id)
        contributions = db.get_contributions(challenge_id)
        slots = db.build_timeline(challenge, participants, contributions)

        self.write({
            "challenge": challenge,
            "participants": participants,
            "contributions": contributions,
            "slots": [
                {
                    "index": s["index"],
                    "hour": s["hour"],
                    "label": s["label"],
                    "status": s["status"],
                    "contributions": s["contributions"],
                    "missing_participant_ids": [p["id"] for p in s["missing_participants"]],
                }
                for s in slots
            ],
        })


class ApiContributeHandler(ApiBaseHandler):
    """POST /api/challenges/<id>/slots/<n>/contribute
       Header: X-Participant-Id: <pid>
       Multipart (photo): action=photo, photo=<file>
       JSON (missed):     {action: "missed", reason: "...", emoji: "..."}
    """

    def post(self, challenge_id, slot_index_str):
        try:
            slot_index = int(slot_index_str)
            assert 0 <= slot_index <= 11
        except (ValueError, AssertionError):
            return self.err(400, "invalid slot")

        challenge = db.get_challenge(challenge_id)
        if not challenge:
            return self.err(404, "challenge not found")

        pid = self.participant_id()
        if not pid:
            return self.err(401, "X-Participant-Id header required")

        participant = db.get_participant(pid)
        if not participant or participant.get("challenge_id") != challenge_id:
            return self.err(403, "not a member of this challenge")

        # Detect multipart (photo) vs JSON (missed)
        content_type = self.request.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            files = self.request.files.get("photo")
            if not files:
                return self.err(400, "photo file required")
            file_info = files[0]
            try:
                img = Image.open(io.BytesIO(file_info["body"]))
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGB")
                img.thumbnail((1200, 1200), Image.LANCZOS)
                upload_dir = os.path.join(UPLOAD_DIR, challenge_id)
                os.makedirs(upload_dir, exist_ok=True)
                fname = f"{uuid.uuid4().hex}.jpg"
                img.save(os.path.join(upload_dir, fname), "JPEG", quality=82, optimize=True)
                photo_url = f"/static/uploads/{challenge_id}/{fname}"
            except Exception as e:
                return self.err(500, f"image error: {e}")
            contrib = db.add_contribution(challenge_id, pid, slot_index, type="photo", photo_url=photo_url)
        else:
            body = self.json_body()
            action = body.get("action", "")
            if action != "missed":
                return self.err(400, "action must be 'missed' or send multipart photo")
            reason = (body.get("reason") or "").strip()
            emoji  = (body.get("emoji")  or "").strip()
            if not reason:
                return self.err(400, "reason required")
            contrib = db.add_contribution(challenge_id, pid, slot_index, type="missed",
                                          missed_reason=reason, missed_emoji=emoji)

        self.write(contrib)


class ApiRenameHandler(ApiBaseHandler):
    """POST /api/challenges/<id>/rename
       Header: X-Participant-Id: <pid>
       Body:   {name: "..."}
    """

    def post(self, challenge_id):
        pid = self.participant_id()
        if not pid:
            return self.err(401, "X-Participant-Id header required")
        body = self.json_body()
        name = (body.get("name") or "").strip()
        if not name:
            return self.err(400, "name required")
        ok = db.update_participant_name(pid, name)
        if ok:
            self.write({"ok": True})
        else:
            self.err(400, "invalid name")


class ApiMissedOptionsHandler(ApiBaseHandler):
    """GET /api/missed-options  →  [{label, emoji}]"""

    def get(self):
        self.write({"options": [{"label": l, "emoji": e} for l, e in db.MISSED_OPTIONS]})


class DiagHandler(tornado.web.RequestHandler):
    """GET /api/diag?key=... — server-side diagnostics (no CORS limits)."""
    def get(self):
        diag_key = os.environ.get("TWELVE_DIAG_KEY", "")
        if not diag_key or self.get_argument("key", "") != diag_key:
            self.set_status(403); self.finish("forbidden"); return
        import urllib.request as _ur
        import json as _j
        TOKEN = os.environ.get("PA_API_TOKEN", "")
        USER  = "FOOxS"
        out   = {}
        # PA scheduled tasks
        try:
            req = _ur.Request(f"https://www.pythonanywhere.com/api/v0/user/{USER}/schedule/",
                              headers={"Authorization": f"Token {TOKEN}"})
            out["schedule"] = _j.loads(_ur.urlopen(req, timeout=10).read())
        except Exception as e:
            out["schedule_error"] = str(e)
        # Try to create hourly PA scheduled task if none exist
        if out.get("schedule") == []:
            try:
                import urllib.parse as _up
                data = _up.urlencode({
                    "command": "python /home/FOOxS/twelve/notify_hourly.py",
                    "hour": "*", "minute": "0",
                }).encode()
                req2 = _ur.Request(
                    f"https://www.pythonanywhere.com/api/v0/user/{USER}/schedule/",
                    data=data,
                    headers={"Authorization": f"Token {TOKEN}",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    method="POST"
                )
                resp2 = _ur.urlopen(req2, timeout=10)
                out["schedule_created"] = _j.loads(resp2.read())
            except _ur.HTTPError as e:
                out["schedule_create_error"] = e.read().decode()
            except Exception as e:
                out["schedule_create_error"] = str(e)
        # Push subscriptions count per active challenge
        subs_summary = []
        for c in db.get_all_active_challenges():
            subs = db.get_push_subscriptions(c["id"])
            subs_summary.append({"id": c["id"], "name": c["name"],
                                  "date": c["challenge_date"], "subs": len(subs)})
        out["challenges"] = subs_summary
        self.set_header("Content-Type", "application/json")
        self.write(_j.dumps(out, indent=2, ensure_ascii=False))


class NotifyHandler(tornado.web.RequestHandler):
    """GET /api/notify?key=$TWELVE_NOTIFY_KEY — called hourly by cron-job.org."""
    def get(self):
        key = self.get_argument("key", "")
        if not os.environ.get("TWELVE_NOTIFY_KEY") or key != os.environ.get("TWELVE_NOTIFY_KEY"):
            self.set_status(403)
            self.finish("forbidden")
            return
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            import notify_hourly
            notify_hourly.main()
            output = captured.getvalue()
            self.set_header("Content-Type", "text/plain")
            self.write(output if output else "ok (no output)")
        except Exception:
            output = captured.getvalue()
            self.set_status(500)
            self.set_header("Content-Type", "text/plain")
            self.write(output + "\n" + traceback.format_exc())
        finally:
            sys.stdout = old_stdout


class NotifyTestHandler(tornado.web.RequestHandler):
    """GET /api/notify_test?key=... — force-sends a test push regardless of slot hours."""
    def get(self):
        key = self.get_argument("key", "")
        if not os.environ.get("TWELVE_NOTIFY_KEY") or key != os.environ.get("TWELVE_NOTIFY_KEY"):
            self.set_status(403)
            self.finish("forbidden")
            return
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()
        try:
            import notify_hourly
            yesterday = (__import__('datetime').date.today() - __import__('datetime').timedelta(days=1)).isoformat()
            challenges = db.get_all_active_challenges(from_date=yesterday)
            total = 0
            for challenge in challenges:
                cid  = challenge["id"]
                name = challenge["name"]
                subs = db.get_push_subscriptions(cid)
                if not subs:
                    print(f"[test] {cid} ({name}): 0 sub(s), skip")
                    continue
                sent = notify_hourly.notify_challenge(
                    cid,
                    "🔔 Notification test",
                    f"Test Twelve — les notifications fonctionnent pour « {name} » !",
                )
                print(f"[test] {cid} ({name}): {sent}/{len(subs)} sent")
                total += sent
            output = captured.getvalue()
            self.set_header("Content-Type", "text/plain")
            self.write(output + f"\nTotal: {total} notification(s) envoyée(s)")
        except Exception:
            output = captured.getvalue()
            self.set_status(500)
            self.set_header("Content-Type", "text/plain")
            self.write(output + "\n" + traceback.format_exc())
        finally:
            sys.stdout = old_stdout


class PushSubscribeHandler(BaseHandler):
    """POST /challenge/<id>/push-subscribe  — saves push subscription for a challenge."""

    def post(self, challenge_id: str):
        challenge = db.get_challenge(challenge_id)
        if not challenge:
            self.set_status(404)
            self.write({"error": "not found"})
            return
        participant_id = self.get_current_participant_id(challenge_id)
        try:
            sub_json = self.request.body.decode("utf-8")
            db.save_push_subscription(challenge_id, participant_id, sub_json)
            self.set_header("Content-Type", "application/json")
            self.write({"ok": True})
        except Exception as exc:
            self.set_status(400)
            self.write({"error": str(exc)})


# ─── Startup (runs on import, so PythonAnywhere WSGI picks it up too) ────────
db.init_db()
db.migrate_db()

# ─── Application ──────────────────────────────────────────────────────────────

def make_app():
    return tornado.web.Application(
        [
            (r"/", HomeHandler),
            (r"/challenge/([^/]+)", ChallengeHandler),
            (r"/challenge/([^/]+)/slot/(\d+)", SlotHandler),
            (r"/challenge/([^/]+)/rename", RenameHandler),
            (r"/challenge/([^/]+)/leave", LeaveHandler),
            (r"/challenge/([^/]+)/forget", ForgetHandler),
            (r"/challenge/([^/]+)/summary", SummaryHandler),
            (r"/challenge/([^/]+)/push-subscribe", PushSubscribeHandler),
            # Legacy JSON endpoint (kept for web client)
            (r"/api/challenge/([^/]+)/data", ApiChallengeDataHandler),
            # iOS JSON API
            (r"/api/challenges", ApiCreateChallengeHandler),
            (r"/api/challenges/([^/]+)", ApiChallengeHandler),
            (r"/api/challenges/([^/]+)/join", ApiJoinChallengeHandler),
            (r"/api/challenges/([^/]+)/slots/(\d+)/contribute", ApiContributeHandler),
            (r"/api/challenges/([^/]+)/rename", ApiRenameHandler),
            (r"/api/missed-options", ApiMissedOptionsHandler),
            (r"/api/notify", NotifyHandler),
            (r"/api/notify_test", NotifyTestHandler),
            (r"/api/diag", DiagHandler),
            (r"/static/uploads/(.*)", tornado.web.StaticFileHandler, {
                "path": UPLOAD_DIR,
            }),
            (r"/(sw\.js)", tornado.web.StaticFileHandler, {
                "path": os.path.join(os.path.dirname(__file__), "static"),
            }),
            (r"/static/(.*)", tornado.web.StaticFileHandler, {
                "path": os.path.join(os.path.dirname(__file__), "static"),
            }),
        ],
        template_path=os.path.join(os.path.dirname(__file__), "templates"),
        static_path=os.path.join(os.path.dirname(__file__), "static"),
        cookie_secret=COOKIE_SECRET,
        xsrf_cookies=False,
        debug=True,
    )


if __name__ == "__main__":
    db.init_db()
    db.migrate_db()
    app = make_app()
    app.listen(PORT, xheaders=True)
    print(f"✦ Twelve is running at http://localhost:{PORT}")
    tornado.ioloop.IOLoop.current().start()
