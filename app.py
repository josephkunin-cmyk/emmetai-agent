"""
Banyan Communications LLC — Emmet AI Phone Agent
Twilio + Anthropic Claude voice agent for Amish homesteads and rural communities.
"""

import os
import json
import logging
import sqlite3
import base64
import hashlib
import hmac
import re
import time
import uuid
from datetime import datetime
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from flask import Flask, request, Response
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Gather
from anthropic import Anthropic
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("emmet")


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def env_text(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return (value or default).strip()


BUSINESS_TIMEZONE = env_text("BUSINESS_TIMEZONE", "America/New_York")
FREE_DAILY_QUERIES = env_int("FREE_DAILY_QUERIES", 5)
DB_PATH = env_text("DB_PATH", "./hotline_usage.db")
UPGRADE_MESSAGE = env_text(
    "UPGRADE_MESSAGE",
    (
        "You've reached your daily limit of five full questions on this line. "
        "To continue today, paid access is required. "
        "Please contact Banyan Communications to upgrade."
    ),
)
SERVICE_SCOPE_MESSAGE = env_text(
    "SERVICE_SCOPE_MESSAGE",
    (
        "This hotline is for agriculture, equestrian, homestead, and practical rural-life questions. "
        "Please ask a question in that scope."
    ),
)
SERVICE_GREETING = env_text(
    "SERVICE_GREETING",
    (
        "Hello, and welcome to Banyan Communications. I'm Emmet, your AI assistant. "
        "This line includes five full questions per day per phone number. "
        "After that, paid access is required."
    ),
)
VOICE_NAME = env_text("VOICE_NAME", "Polly.Joanna")
TWILIO_ACCOUNT_SID = env_text("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = env_text("TWILIO_AUTH_TOKEN", "")
TWILIO_MESSAGING_FROM = env_text("TWILIO_MESSAGING_FROM", "")
SQUARE_ACCESS_TOKEN = env_text("SQUARE_ACCESS_TOKEN", "")
SQUARE_LOCATION_ID = env_text("SQUARE_LOCATION_ID", "")
SQUARE_ENVIRONMENT = env_text("SQUARE_ENVIRONMENT", "production").lower()
SQUARE_API_VERSION = env_text("SQUARE_API_VERSION", "2025-10-16")
SQUARE_CURRENCY = env_text("SQUARE_CURRENCY", "USD").upper()
SQUARE_DAILY_UNLOCK_CENTS = env_int("SQUARE_DAILY_UNLOCK_CENTS", 500)
SQUARE_WEBHOOK_SIGNATURE_KEY = env_text("SQUARE_WEBHOOK_SIGNATURE_KEY", "")
PUBLIC_BASE_URL = env_text("PUBLIC_BASE_URL", "https://emmetai-agent.onrender.com").rstrip("/")

EMERGENCY_KEYWORDS = {
    "heart attack", "stroke", "can't breathe", "not breathing", "seizure",
    "overdose", "suicide", "kill myself", "self harm", "bleeding badly",
    "house fire", "barn fire", "emergency",
}

DISALLOWED_CONTENT_KEYWORDS = {
    "porn", "nude", "sex video", "erotic", "explicit",
    "make a bomb", "build a bomb", "buy cocaine", "meth recipe", "credit card fraud",
    "hack account", "phishing", "steal password", "weapon for attack",
}

OFF_TOPIC_KEYWORDS = {
    "celebrity", "movie review", "sports betting", "crypto day trading",
    "video game", "dating advice", "instagram growth", "tiktok strategy",
    "celebrity gossip", "hollywood",
}


class UsageStore:
    """SQLite-backed daily query counters keyed by caller and local date."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_usage (
                    caller_phone TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    query_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (caller_phone, usage_date)
                );

                CREATE TABLE IF NOT EXISTS caller_profiles (
                    caller_phone TEXT PRIMARY KEY,
                    caller_name TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paid_daily_access (
                    caller_phone TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (caller_phone, usage_date)
                );

                CREATE TABLE IF NOT EXISTS square_checkout_links (
                    order_id TEXT PRIMARY KEY,
                    payment_link_id TEXT,
                    payment_link_url TEXT,
                    caller_phone TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    paid_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS call_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_sid TEXT NOT NULL,
                    caller_phone TEXT NOT NULL,
                    caller_name TEXT,
                    turn_number INTEGER NOT NULL DEFAULT 1,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL,
                    latency_ms INTEGER,
                    usage_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_call_logs_sid ON call_logs(call_sid);
                CREATE INDEX IF NOT EXISTS idx_call_logs_date ON call_logs(usage_date);
                """
            )
            conn.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.utcnow().isoformat(timespec="seconds")

    def get_count(self, caller_phone: str, usage_date: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT query_count
                FROM daily_usage
                WHERE caller_phone = ? AND usage_date = ?
                """,
                (caller_phone, usage_date),
            ).fetchone()
        return int(row["query_count"]) if row else 0

    def increment(self, caller_phone: str, usage_date: str) -> int:
        now_iso = self._now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_usage (caller_phone, usage_date, query_count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(caller_phone, usage_date)
                DO UPDATE SET
                    query_count = query_count + 1,
                    updated_at = excluded.updated_at
                """,
                (caller_phone, usage_date, now_iso),
            )
            row = conn.execute(
                """
                SELECT query_count
                FROM daily_usage
                WHERE caller_phone = ? AND usage_date = ?
                """,
                (caller_phone, usage_date),
            ).fetchone()
            conn.commit()
        return int(row["query_count"]) if row else 1

    def get_caller_name(self, caller_phone: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT caller_name
                FROM caller_profiles
                WHERE caller_phone = ?
                """,
                (caller_phone,),
            ).fetchone()
        if not row:
            return None
        return (row["caller_name"] or "").strip() or None

    def upsert_caller_name(self, caller_phone: str, caller_name: str) -> None:
        now_iso = self._now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO caller_profiles (caller_phone, caller_name, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(caller_phone)
                DO UPDATE SET
                    caller_name = excluded.caller_name,
                    updated_at = excluded.updated_at
                """,
                (caller_phone, caller_name, now_iso),
            )
            conn.commit()

    def has_paid_access(self, caller_phone: str, usage_date: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status
                FROM paid_daily_access
                WHERE caller_phone = ? AND usage_date = ?
                """,
                (caller_phone, usage_date),
            ).fetchone()
        return bool(row and (row["status"] or "").lower() == "paid")

    def set_paid_access(self, caller_phone: str, usage_date: str, source: str) -> None:
        now_iso = self._now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO paid_daily_access (caller_phone, usage_date, status, source, updated_at)
                VALUES (?, ?, 'paid', ?, ?)
                ON CONFLICT(caller_phone, usage_date)
                DO UPDATE SET
                    status = 'paid',
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (caller_phone, usage_date, source, now_iso),
            )
            conn.commit()

    def record_square_checkout(
        self,
        order_id: str,
        payment_link_id: str,
        payment_link_url: str,
        caller_phone: str,
        usage_date: str,
        amount_cents: int,
    ) -> None:
        now_iso = self._now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO square_checkout_links (
                    order_id, payment_link_id, payment_link_url, caller_phone,
                    usage_date, amount_cents, paid_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    order_id,
                    payment_link_id,
                    payment_link_url,
                    caller_phone,
                    usage_date,
                    amount_cents,
                    now_iso,
                ),
            )
            conn.commit()

    def get_checkout_by_order(self, order_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT order_id, caller_phone, usage_date, paid_at
                FROM square_checkout_links
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
        return dict(row) if row else None

    def log_turn(
        self,
        call_sid: str,
        caller_phone: str,
        caller_name: str,
        user_message: str,
        assistant_message: str,
        latency_ms: int,
    ) -> None:
        """Write a single Q&A turn to the call_logs table."""
        now_iso = self._now_iso()
        usage_date = now_iso[:10]
        with self._connect() as conn:
            turn_number = (conn.execute(
                "SELECT COUNT(*) FROM call_logs WHERE call_sid = ?", (call_sid,)
            ).fetchone()[0] or 0) + 1
            conn.execute(
                """
                INSERT INTO call_logs
                    (call_sid, caller_phone, caller_name, turn_number,
                     user_message, assistant_message, latency_ms, usage_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (call_sid, caller_phone, caller_name or "", turn_number,
                 user_message, assistant_message, latency_ms, usage_date, now_iso),
            )
            conn.commit()

    def mark_checkout_paid(self, order_id: str) -> None:
        now_iso = self._now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE square_checkout_links
                SET paid_at = ?
                WHERE order_id = ?
                """,
                (now_iso, order_id),
            )
            conn.commit()

# ── App Setup ────────────────────────────────────────────────────────────
app = Flask(__name__)
anthropic_client = None
usage_store = UsageStore(DB_PATH)

# In-memory conversation store keyed by Twilio CallSid
conversations = {}
call_metadata = {}


def business_today_iso() -> str:
    try:
        now_local = datetime.now(ZoneInfo(BUSINESS_TIMEZONE))
    except Exception:  # fallback for invalid timezone configuration
        now_local = datetime.utcnow()
    return now_local.date().isoformat()


def caller_identity(call_sid: str, caller_raw: str) -> str:
    caller = (caller_raw or "").strip()
    if caller and caller.lower() != "unknown":
        return caller
    if call_sid:
        return f"call:{call_sid}"
    return "unknown"


def guardrail_response(user_text: str) -> Optional[str]:
    text = (user_text or "").strip().lower()
    if not text:
        return None

    if any(keyword in text for keyword in EMERGENCY_KEYWORDS):
        return (
            "If this is an emergency, please hang up and call 911 right now. "
            "For immediate mental health crisis help, call or text 988."
        )

    if any(keyword in text for keyword in DISALLOWED_CONTENT_KEYWORDS):
        return (
            "I can't help with that. This hotline stays PG-13 and does not support harmful, sexual, or illegal requests. "
            "I can help with safe agriculture, horse care, and practical rural questions."
        )

    if any(keyword in text for keyword in OFF_TOPIC_KEYWORDS):
        return SERVICE_SCOPE_MESSAGE

    return None


def extract_first_name(raw_text: str, fallback: str = "friend") -> str:
    text = (raw_text or "").strip()
    if not text:
        return fallback
    lowered = text.lower()
    for prefix in ("my name is", "this is", "i am", "i'm", "its", "it is"):
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    clean = re.sub(r"[^A-Za-z\s\-']", " ", text)
    parts = [p for p in clean.split() if p]
    if not parts:
        return fallback
    first = parts[0][:30]
    return first.capitalize()


def is_e164_phone(value: str) -> bool:
    return bool(re.fullmatch(r"\+\d{8,15}", (value or "").strip()))


def get_twilio_client() -> Optional[TwilioClient]:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return None
    try:
        return TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to initialize Twilio client: {exc}")
        return None


def send_payment_sms(caller_phone: str, payment_url: str) -> bool:
    if not is_e164_phone(caller_phone):
        return False
    if not TWILIO_MESSAGING_FROM:
        return False
    client = get_twilio_client()
    if client is None:
        return False
    text = (
        "Banyan Communications: you've hit today's free limit. "
        f"Use this secure link to unlock paid access now: {payment_url}"
    )
    try:
        client.messages.create(
            to=caller_phone,
            from_=TWILIO_MESSAGING_FROM,
            body=text,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"SMS send failed for {caller_phone}: {exc}")
        return False


def square_api_base_url() -> str:
    if SQUARE_ENVIRONMENT == "sandbox":
        return "https://connect.squareupsandbox.com"
    return "https://connect.squareup.com"


def create_square_payment_link(caller_phone: str, usage_date: str) -> Optional[str]:
    if not SQUARE_ACCESS_TOKEN or not SQUARE_LOCATION_ID:
        return None
    payload = {
        "idempotency_key": str(uuid.uuid4()),
        "quick_pay": {
            "name": "Emmet AI Daily Unlock",
            "price_money": {
                "amount": SQUARE_DAILY_UNLOCK_CENTS,
                "currency": SQUARE_CURRENCY,
            },
            "location_id": SQUARE_LOCATION_ID,
        },
        "description": f"Daily unlock for {caller_phone} on {usage_date}",
    }
    if PUBLIC_BASE_URL:
        payload["checkout_options"] = {"redirect_url": f"{PUBLIC_BASE_URL}/health"}
    body = json.dumps(payload).encode("utf-8")
    endpoint = f"{square_api_base_url()}/v2/online-checkout/payment-links"
    req = Request(endpoint, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {SQUARE_ACCESS_TOKEN}")
    req.add_header("Square-Version", SQUARE_API_VERSION)
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        logger.error(f"Square payment-link HTTP error: {exc.code} {details}")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Square payment-link failure: {exc}")
        return None

    payment_link = data.get("payment_link") or {}
    payment_link_url = (payment_link.get("url") or "").strip()
    payment_link_id = (payment_link.get("id") or "").strip()
    order_id = (payment_link.get("order_id") or "").strip()

    related_resources = data.get("related_resources") or {}
    if not order_id:
        orders = related_resources.get("orders") or []
        if orders:
            order_id = (orders[0].get("id") or "").strip()

    if payment_link_url and order_id:
        usage_store.record_square_checkout(
            order_id=order_id,
            payment_link_id=payment_link_id,
            payment_link_url=payment_link_url,
            caller_phone=caller_phone,
            usage_date=usage_date,
            amount_cents=SQUARE_DAILY_UNLOCK_CENTS,
        )
    return payment_link_url or None


def build_limit_message(caller_phone: str, usage_date: str) -> str:
    payment_url = create_square_payment_link(caller_phone, usage_date)
    if payment_url:
        if send_payment_sms(caller_phone, payment_url):
            return (
                f"{UPGRADE_MESSAGE} I just texted a secure payment link to this phone number. "
                "After payment, call back and I can keep helping today."
            )
        return (
            f"{UPGRADE_MESSAGE} A secure payment link is ready. "
            "Please contact Banyan Communications if you need help completing payment."
        )
    return UPGRADE_MESSAGE


def verify_square_webhook(payload: bytes) -> bool:
    if not SQUARE_WEBHOOK_SIGNATURE_KEY:
        return True
    signature = request.headers.get("x-square-hmacsha256-signature", "").strip()
    if not signature:
        return False
    signed_payload = request.url.encode("utf-8") + payload
    digest = hmac.new(
        SQUARE_WEBHOOK_SIGNATURE_KEY.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def apply_paid_event_from_square(payment: dict) -> None:
    status = (payment.get("status") or "").upper()
    if status != "COMPLETED":
        return
    order_id = (payment.get("order_id") or "").strip()
    if not order_id:
        return
    checkout = usage_store.get_checkout_by_order(order_id)
    if not checkout:
        return
    usage_store.mark_checkout_paid(order_id)
    usage_store.set_paid_access(
        checkout["caller_phone"],
        checkout["usage_date"],
        source="square_webhook",
    )
    logger.info(
        "Square payment completed for %s on %s",
        checkout["caller_phone"],
        checkout["usage_date"],
    )

# ── Knowledge Base ───────────────────────────────────────────────────────
KNOWLEDGE_BASE_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.json")

def load_knowledge_base():
    """Load the local knowledge base from JSON file."""
    try:
        with open(KNOWLEDGE_BASE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": [], "announcements": []}

def format_knowledge_for_prompt():
    """Format knowledge base entries for the system prompt."""
    kb = load_knowledge_base()
    sections = []

    if kb.get("announcements"):
        announcements = "\n".join(f"- {a}" for a in kb["announcements"])
        sections.append(f"CURRENT COMMUNITY ANNOUNCEMENTS:\n{announcements}")

    if kb.get("entries"):
        entries = "\n".join(
            f"Q: {e['question']}\nA: {e['answer']}"
            for e in kb["entries"]
        )
        sections.append(f"COMMUNITY KNOWLEDGE BASE:\n{entries}")

    return "\n\n".join(sections)


# ── System Prompt ────────────────────────────────────────────────────────
def build_system_prompt():
    """Build the full system prompt including knowledge base."""
    base_prompt = """You are Emmet, a friendly and practical AI phone assistant for Banyan Communications.

Voice style:
- Warm, respectful, and plainspoken.
- Speak naturally for phone audio, no bullet points.
- Keep answers short: usually 2 to 4 sentences.
- Use practical advice first.

Scope:
- Agriculture, farming, gardening, livestock, horse and equestrian care.
- Homesteading and rural practical life: weather, tools, repairs, food preservation, measurements.
- PG-13 content only.

Guardrails:
- If a request is outside scope, politely decline and steer back to allowed topics.
- Refuse harmful, illegal, or explicit sexual content.
- For emergencies, tell caller to contact 911 immediately.
- For medical or veterinary high-risk situations, advise urgent professional help.
- Never shame the caller. Keep tone calm and respectful.

Identity:
- If asked your name, say: I'm Emmet, your AI assistant from Banyan Communications."""

    kb_text = format_knowledge_for_prompt()
    if kb_text:
        return f"{base_prompt}\n\n{kb_text}"
    return base_prompt


# ── Claude Integration ───────────────────────────────────────────────────
def get_conversation(call_sid):
    """Get or create conversation history for a call."""
    if call_sid not in conversations:
        conversations[call_sid] = []
    return conversations[call_sid]


def get_anthropic_client():
    """Lazily initialize the Anthropic client so boot never fails."""
    global anthropic_client
    if anthropic_client is not None:
        return anthropic_client

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    anthropic_client = Anthropic(api_key=api_key)
    return anthropic_client


def ask_claude(call_sid: str, user_message: str) -> str:
    """Send message to Claude Haiku with prompt caching for low latency."""
    history = get_conversation(call_sid)
    history.append({"role": "user", "content": user_message})

    logger.info(f"[{call_sid[:8]}] Caller: {user_message}")

    t0 = time.monotonic()

    # Use prompt caching on the system prompt — cuts repeat-call latency ~60%
    response = get_anthropic_client().messages.create(
        model="claude-haiku-4-5-20251001",   # 3-5x faster than Sonnet
        max_tokens=150,                        # phone answers are short
        system=[
            {
                "type": "text",
                "text": build_system_prompt(),
                "cache_control": {"type": "ephemeral"},   # cache the prompt
            }
        ],
        messages=history,
    )

    latency_ms = int((time.monotonic() - t0) * 1000)
    assistant_message = response.content[0].text.strip()
    history.append({"role": "assistant", "content": assistant_message})

    logger.info(f"[{call_sid[:8]}] Emmet ({latency_ms}ms): {assistant_message}")

    # Write turn to call log
    caller_id = call_metadata.get(call_sid, {}).get("from", "unknown")
    caller_name = call_metadata.get(call_sid, {}).get("caller_name", "")
    usage_store.log_turn(call_sid, caller_id, caller_name, user_message, assistant_message, latency_ms)

    # Keep conversation history manageable (last 20 turns)
    if len(history) > 20:
        conversations[call_sid] = history[-20:]

    return assistant_message


# ── TwiML Response Builder ──────────────────────────────────────────────
def twiml_listen(
    text: str,
    action: str = "/gather",
    fallback_text: str = "I'm still here. Go ahead whenever you're ready.",
    redirect_url: str = "/voice?returning=true",
) -> str:
    """Build TwiML that speaks text and listens for a reply."""
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action=action,
        method="POST",
        speech_timeout="2",       # 2s silence = end of turn (was "auto" ~3-4s)
        speech_model="phone_call",
        enhanced=True,
        language="en-US",
        profanity_filter=False,   # faster transcription
        action_on_empty_result=False,
    )
    gather.say(text, voice=VOICE_NAME, language="en-US")
    resp.append(gather)

    # Fallback if no speech detected
    resp.say(fallback_text, voice=VOICE_NAME, language="en-US")
    resp.redirect(redirect_url)
    return str(resp)


def twiml_say(text: str) -> str:
    """Build TwiML that speaks text and hangs up."""
    resp = VoiceResponse()
    resp.say(text, voice=VOICE_NAME, language="en-US")
    resp.hangup()
    return str(resp)


# ── Routes ───────────────────────────────────────────────────────────────
@app.route("/voice", methods=["GET", "POST"])
def voice():
    """Handle incoming calls — greet the caller."""
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")
    caller_id = caller_identity(call_sid, caller)
    returning = request.args.get("returning", "false")
    stage = request.args.get("stage", "").strip().lower()
    usage_date = business_today_iso()

    if returning == "true":
        if stage == "name":
            existing_name = usage_store.get_caller_name(caller_id)
            name_prompt = "Before we start, what's your first name?"
            if existing_name:
                name_prompt = (
                    f"Welcome back {existing_name}. "
                    "What name should I call you today? You can say same name."
                )
            return Response(
                twiml_listen(
                    name_prompt,
                    action="/intro-name",
                    fallback_text="I didn't catch your name. Please say your first name.",
                    redirect_url="/voice?returning=true&stage=name",
                ),
                mimetype="text/xml",
            )
        return Response(
            twiml_listen("Go ahead, I'm listening."),
            mimetype="text/xml"
        )

    # New call — clear old conversation
    conversations.pop(call_sid, None)

    # Track call metadata
    call_metadata[call_sid] = {
        "from": caller_id,
        "start": datetime.utcnow().isoformat(),
        "turns": 0
    }

    used_today = usage_store.get_count(caller_id, usage_date)
    has_paid = usage_store.has_paid_access(caller_id, usage_date)
    if used_today >= FREE_DAILY_QUERIES and not has_paid:
        limit_message = build_limit_message(caller_id, usage_date)
        logger.info(
            f"[{call_sid[:8]}] Caller {caller_id} over limit "
            f"({used_today}/{FREE_DAILY_QUERIES})"
        )
        return Response(
            twiml_say(limit_message),
            mimetype="text/xml"
        )

    if has_paid:
        quota_line = "You're on paid access today."
    else:
        remaining = max(0, FREE_DAILY_QUERIES - used_today)
        question_word = "question" if remaining == 1 else "questions"
        quota_line = f"You have {remaining} free full {question_word} left today."

    existing_name = usage_store.get_caller_name(caller_id)
    if existing_name:
        name_prompt = (
            f"Welcome back {existing_name}. "
            "What name should I call you today? You can say same name."
        )
    else:
        name_prompt = "Before we begin, what's your first name?"

    logger.info(
        f"[{call_sid[:8]}] New call from {caller_id} "
        f"(used_today={used_today}/{FREE_DAILY_QUERIES}, paid={has_paid})"
    )

    return Response(
        twiml_listen(
            f"{SERVICE_GREETING} {quota_line} {name_prompt}",
            action="/intro-name",
            fallback_text="I didn't catch your name. Please say your first name.",
            redirect_url="/voice?returning=true&stage=name",
        ),
        mimetype="text/xml"
    )


@app.route("/intro-name", methods=["GET", "POST"])
def intro_name():
    """Capture caller name before main Q&A."""
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")
    caller_id = caller_identity(call_sid, caller)
    usage_date = business_today_iso()
    spoken = request.form.get("SpeechResult", "").strip()

    if not spoken:
        return Response(
            twiml_listen(
                "I didn't catch your first name. Please say your first name now.",
                action="/intro-name",
                fallback_text="Still didn't catch that. Please say your first name.",
                redirect_url="/voice?returning=true&stage=name",
            ),
            mimetype="text/xml",
        )

    existing_name = usage_store.get_caller_name(caller_id)
    if existing_name and spoken.lower() in {"same", "same name", "use same name"}:
        caller_name = existing_name
    else:
        caller_name = extract_first_name(spoken, fallback=existing_name or "friend")
        usage_store.upsert_caller_name(caller_id, caller_name)

    if call_sid in call_metadata:
        call_metadata[call_sid]["caller_name"] = caller_name

    if usage_store.has_paid_access(caller_id, usage_date):
        quota_line = "You're on paid access today."
    else:
        used_today = usage_store.get_count(caller_id, usage_date)
        remaining = max(0, FREE_DAILY_QUERIES - used_today)
        question_word = "question" if remaining == 1 else "questions"
        quota_line = f"You have {remaining} free full {question_word} left today."

    return Response(
        twiml_listen(
            f"Thanks, {caller_name}. {quota_line} What would you like help with first?",
            action="/gather",
            fallback_text="I'm still here. Go ahead and ask your question.",
            redirect_url="/voice?returning=true",
        ),
        mimetype="text/xml",
    )


@app.route("/gather", methods=["GET", "POST"])
def gather():
    """Handle speech input from the caller and respond via Claude."""
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")
    caller_id = caller_identity(call_sid, caller)
    speech_result = request.form.get("SpeechResult", "").strip()
    usage_date = business_today_iso()

    if not speech_result:
        return Response(
            twiml_listen(
                "I didn't catch that clearly. Could you say it again?"
            ),
            mimetype="text/xml"
        )

    # Track turns
    if call_sid in call_metadata:
        call_metadata[call_sid]["turns"] += 1

    # Check for goodbye / end call
    farewell_words = [
        "goodbye", "bye", "hang up", "that's all", "thank you goodbye",
        "i'm done", "nothing else", "no thanks", "no thank you",
        "that'll do", "that will do", "all done"
    ]
    if any(word in speech_result.lower() for word in farewell_words):
        logger.info(f"[{call_sid[:8]}] Call ended by caller")
        return Response(
            twiml_say(
                "You're very welcome. Have a blessed day. Goodbye!"
            ),
            mimetype="text/xml"
        )

    has_paid = usage_store.has_paid_access(caller_id, usage_date)
    used_before = usage_store.get_count(caller_id, usage_date)
    if used_before >= FREE_DAILY_QUERIES and not has_paid:
        limit_message = build_limit_message(caller_id, usage_date)
        logger.info(
            f"[{call_sid[:8]}] Daily limit hit for {caller_id}: "
            f"{used_before}/{FREE_DAILY_QUERIES}"
        )
        return Response(
            twiml_say(limit_message),
            mimetype="text/xml"
        )

    used_after = used_before
    if not has_paid:
        used_after = usage_store.increment(caller_id, usage_date)
    logger.info(
        f"[{call_sid[:8]}] Counted inquiry for {caller_id}: "
        f"{used_after}/{FREE_DAILY_QUERIES}"
    )

    policy_response = guardrail_response(speech_result)
    if policy_response:
        ai_response = policy_response
    else:
        # Get Claude's response
        try:
            ai_response = ask_claude(call_sid, speech_result)
        except Exception as e:
            logger.error(f"[{call_sid[:8]}] Claude API error: {e}")
            ai_response = (
                "I'm sorry, I'm having a little trouble thinking right now. "
                "Could you try asking me again?"
            )

    remaining = max(0, FREE_DAILY_QUERIES - used_after)
    if not has_paid and remaining == 0:
        limit_message = build_limit_message(caller_id, usage_date)
        final_message = (
            f"{ai_response} That was your fifth free question today. {limit_message}"
        )
        return Response(
            twiml_say(final_message),
            mimetype="text/xml"
        )

    # Add natural continuation prompt (vary it)
    turns = call_metadata.get(call_sid, {}).get("turns", 0)
    continuations = [
        "What else can I help with?",
        "Anything else on your mind?",
        "Is there something else I can help you with?",
        "What else would you like to know?",
        "Got another question?"
    ]
    continuation = continuations[turns % len(continuations)]
    usage_note = ""
    if not has_paid and remaining <= 2:
        question_word = "question" if remaining == 1 else "questions"
        usage_note = f" You have {remaining} free {question_word} left today."
    if has_paid:
        usage_note = " Paid access is active for today."
    full_response = f"{ai_response}{usage_note} {continuation}"

    return Response(
        twiml_listen(full_response),
        mimetype="text/xml"
    )


@app.route("/status", methods=["GET", "POST"])
def status():
    """Call status webhook — clean up conversation when call ends."""
    call_sid = request.form.get("CallSid", "")
    call_status = request.form.get("CallStatus", "")

    if call_status in ("completed", "failed", "busy", "no-answer"):
        meta = call_metadata.pop(call_sid, {})
        conversations.pop(call_sid, None)
        if meta:
            logger.info(
                f"[{call_sid[:8]}] Call ended ({call_status}) — "
                f"{meta.get('turns', 0)} turns"
            )

    return Response("OK", status=200)


@app.route("/square-webhook", methods=["POST"])
def square_webhook():
    """Square webhook: mark caller/day as paid when checkout completes."""
    payload = request.get_data() or b""
    if not verify_square_webhook(payload):
        return Response("invalid square signature", status=403)

    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return Response("invalid payload", status=400)

    event_type = (event.get("type") or "").strip().lower()
    obj = (event.get("data") or {}).get("object") or {}
    payment = obj.get("payment") if isinstance(obj, dict) else None

    if event_type in {"payment.created", "payment.updated"} and isinstance(payment, dict):
        apply_paid_event_from_square(payment)

    return Response("ok", status=200)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "Emmet AI",
        "phone": "+17179225968",
        "active_calls": len(conversations),
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "model": "claude-haiku-4-5-20251001",
        "free_daily_queries": FREE_DAILY_QUERIES,
        "business_timezone": BUSINESS_TIMEZONE,
        "square_configured": bool(SQUARE_ACCESS_TOKEN and SQUARE_LOCATION_ID),
        "voice_name": VOICE_NAME,
    }


@app.route("/logs", methods=["GET"])
def logs():
    """View recent call logs (last 50 turns)."""
    date = request.args.get("date", business_today_iso())
    with usage_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT call_sid, caller_phone, caller_name, turn_number,
                   user_message, assistant_message, latency_ms, created_at
            FROM call_logs
            WHERE usage_date = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (date,),
        ).fetchall()
    return Response(
        json.dumps([dict(r) for r in rows], indent=2),
        mimetype="application/json",
    )


# ── Entry Point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Emmet AI agent on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
