"""
Twelve – Main Tornado application
"""
import os
import io
import json
import uuid
import socket
import traceback
from datetime import datetime

import tornado.web
import tornado.ioloop
import tornado.options
from PIL import Image

import db


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

# ─── Base Handler ─────────────────────────────────────────────────────────────

class BaseHandler(tornado.web.RequestHandler):
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
        self.render("home.html", error=None, my_challenges=my_challenges)

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
        challenge = db.create_challenge(name, challenge_date=client_date)
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

        if len(participants) >= MAX_PARTICIPANTS:
            contributions = db.get_contributions(challenge_id)
            slots = db.build_timeline(challenge, participants, contributions)
            self.render(
                "challenge.html",
                challenge=challenge, participants=participants,
                participant=None, slots=slots, next_slot=None,
                my_contribs={}, error="Ce défi est complet (6 participants max).",
                full=True, slot_hours=db.SLOT_HOURS, share_url=share_url,
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
            )
            return

        if len(name) > 30:
            name = name[:30]

        new_participant = db.join_challenge(challenge_id, name)
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
        slots = db.build_timeline(challenge, participants, contributions)

        participant_id = self.get_current_participant_id(challenge_id)
        participant = db.get_participant(participant_id) if participant_id else None

        # Precompute slides: list of {slot, cells: [{participant, contribution|None}]}
        contribs_by_key = {
            (c["participant_id"], c["slot_index"]): c
            for c in contributions
        }
        slides = []
        for slot in slots:
            cells = []
            for p in participants:
                c = contribs_by_key.get((p["id"], slot["index"]))
                cells.append({"participant": p, "contribution": c})
            slides.append({"slot": slot, "cells": cells})

        self.render(
            "summary.html",
            challenge=challenge,
            participants=participants,
            participant=participant,
            slots=slots,
            slides=slides,
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
                img = Image.open(io.BytesIO(file_info["body"])).convert("RGB")
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


# ─── Application ──────────────────────────────────────────────────────────────

def make_app():
    return tornado.web.Application(
        [
            (r"/", HomeHandler),
            (r"/challenge/([^/]+)", ChallengeHandler),
            (r"/challenge/([^/]+)/slot/(\d+)", SlotHandler),
            (r"/challenge/([^/]+)/rename", RenameHandler),
            (r"/challenge/([^/]+)/summary", SummaryHandler),
            # Legacy JSON endpoint (kept for web client)
            (r"/api/challenge/([^/]+)/data", ApiChallengeDataHandler),
            # iOS JSON API
            (r"/api/challenges", ApiCreateChallengeHandler),
            (r"/api/challenges/([^/]+)", ApiChallengeHandler),
            (r"/api/challenges/([^/]+)/join", ApiJoinChallengeHandler),
            (r"/api/challenges/([^/]+)/slots/(\d+)/contribute", ApiContributeHandler),
            (r"/api/challenges/([^/]+)/rename", ApiRenameHandler),
            (r"/api/missed-options", ApiMissedOptionsHandler),
            (r"/static/uploads/(.*)", tornado.web.StaticFileHandler, {
                "path": UPLOAD_DIR,
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
    app = make_app()
    app.listen(PORT, xheaders=True)
    print(f"✦ Twelve is running at http://localhost:{PORT}")
    tornado.ioloop.IOLoop.current().start()
