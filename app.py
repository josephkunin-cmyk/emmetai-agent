"""
Banyan Communications LLC — Emmet AI Phone Agent
Twilio + Anthropic Claude voice agent for Amish homesteads and rural communities.
"""

import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional
from flask import Flask, request, Response
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
        "After that, paid access is required. "
        "Please ask your first question."
    ),
)

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_usage (
                    caller_phone TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    query_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (caller_phone, usage_date)
                )
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


def ask_claude(call_sid, user_message):
    """Send message to Claude and get a response."""
    history = get_conversation(call_sid)
    history.append({"role": "user", "content": user_message})

    logger.info(f"[{call_sid[:8]}] Caller: {user_message}")

    response = get_anthropic_client().messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=300,
        system=build_system_prompt(),
        messages=history
    )

    assistant_message = response.content[0].text
    history.append({"role": "assistant", "content": assistant_message})

    logger.info(f"[{call_sid[:8]}] Emmet: {assistant_message}")

    # Keep conversation history manageable (last 20 turns)
    if len(history) > 20:
        conversations[call_sid] = history[-20:]

    return assistant_message


# ── TwiML Response Builder ──────────────────────────────────────────────
def twiml_listen(text, call_sid=None):
    """Build TwiML that speaks text and listens for a reply."""
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/gather",
        method="POST",
        speech_timeout="auto",
        speech_model="phone_call",
        enhanced=True,
        language="en-US"
    )
    gather.say(text, voice="Polly.Joanna", language="en-US")
    resp.append(gather)

    # Fallback if no speech detected
    resp.say(
        "I'm still here if you have a question. Just go ahead and ask.",
        voice="Polly.Joanna"
    )
    resp.redirect("/voice?returning=true")
    return str(resp)


def twiml_say(text):
    """Build TwiML that speaks text and hangs up."""
    resp = VoiceResponse()
    resp.say(text, voice="Polly.Joanna", language="en-US")
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
    usage_date = business_today_iso()

    if returning == "true":
        # Caller came back after silence
        return Response(
            twiml_listen("Go ahead, I'm listening.", call_sid),
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
    if used_today >= FREE_DAILY_QUERIES:
        logger.info(
            f"[{call_sid[:8]}] Caller {caller_id} over limit "
            f"({used_today}/{FREE_DAILY_QUERIES})"
        )
        return Response(
            twiml_say(UPGRADE_MESSAGE),
            mimetype="text/xml"
        )

    remaining = max(0, FREE_DAILY_QUERIES - used_today)
    if used_today == 0:
        greeting = SERVICE_GREETING
    else:
        greeting = (
            f"Welcome back. You have {remaining} free full questions left today. "
            "Please ask your next question."
        )

    logger.info(
        f"[{call_sid[:8]}] New call from {caller_id} "
        f"(used_today={used_today}/{FREE_DAILY_QUERIES})"
    )

    return Response(
        twiml_listen(greeting, call_sid),
        mimetype="text/xml"
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
                "I'm sorry, I didn't quite catch that. Could you say that again?",
                call_sid
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

    used_before = usage_store.get_count(caller_id, usage_date)
    if used_before >= FREE_DAILY_QUERIES:
        logger.info(
            f"[{call_sid[:8]}] Daily limit hit for {caller_id}: "
            f"{used_before}/{FREE_DAILY_QUERIES}"
        )
        return Response(
            twiml_say(UPGRADE_MESSAGE),
            mimetype="text/xml"
        )

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
    if remaining == 0:
        final_message = (
            f"{ai_response} That was your fifth free question today. {UPGRADE_MESSAGE}"
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
    if remaining <= 2:
        question_word = "question" if remaining == 1 else "questions"
        usage_note = f" You have {remaining} free {question_word} left today."
    full_response = f"{ai_response}{usage_note} {continuation}"

    return Response(
        twiml_listen(full_response, call_sid),
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


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "Emmet AI — Banyan Communications",
        "active_calls": len(conversations),
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "free_daily_queries": FREE_DAILY_QUERIES,
        "business_timezone": BUSINESS_TIMEZONE,
    }


# ── Entry Point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Emmet AI agent on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
