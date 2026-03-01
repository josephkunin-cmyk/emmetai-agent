"""
Banyan Communications LLC — Emmet AI Phone Agent
Twilio + Anthropic Claude voice agent for Amish homesteads and rural communities.
"""

import os
import json
import logging
from datetime import datetime
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("emmet")

# ── App Setup ────────────────────────────────────────────────────────────
app = Flask(__name__)
anthropic_client = None

# In-memory conversation store keyed by Twilio CallSid
conversations = {}
call_metadata = {}

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
    base_prompt = """You are Emmet, a friendly and helpful AI phone assistant for Banyan Communications LLC.
You assist callers from Amish homesteads, Plain communities, and rural areas across America.

Your name is Emmet. If someone asks your name, say "I'm Emmet, your AI assistant from Banyan Communications."

Your personality:
- Warm, patient, and genuinely kind
- Use plain, simple language — avoid tech jargon entirely
- Speak conversationally, as this is a phone call (no bullet points, no formatting)
- Keep responses concise — 2 to 4 sentences unless the caller clearly needs more detail
- Be practical and direct — callers are busy, hardworking people
- Show respect for Plain and traditional ways of life

Topics you help with:
- General knowledge and factual questions
- Weather information and forecasts
- Health and first aid basics (always recommend seeing a doctor for serious concerns)
- Farming, gardening, livestock, and agriculture
- Recipes, food preservation, canning, and cooking
- Measurements, conversions, and calculations
- Business inquiries, store hours, and general directions
- Spelling, reading, and writing help
- Home repair, woodworking, and practical skills
- Legal and financial basics (always recommend a professional for serious matters)
- Veterinary basics for farm animals
- Community announcements if available

Important rules:
- Never encourage or require internet, smartphones, apps, or social media
- If you don't know something, say so honestly and suggest who could help (a doctor, a vet, an extension office, etc.)
- Never make callers feel embarrassed about any question — every question is valid
- Respect traditional values and ways of life without judgment
- This is a PHONE CALL — speak naturally, use contractions, be human
- If a caller sounds distressed or mentions an emergency, encourage them to call 911 or their local emergency services immediately
- When giving measurements or amounts, use traditional units (cups, bushels, acres) not metric
- For medical or veterinary emergencies, always say to get professional help right away"""

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
    returning = request.args.get("returning", "false")

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
        "from": caller,
        "start": datetime.utcnow().isoformat(),
        "turns": 0
    }

    logger.info(f"[{call_sid[:8]}] New call from {caller}")

    greeting = (
        "Hello, and welcome to Banyan Communications. "
        "I'm Emmet, your AI assistant. "
        "Go ahead and ask me anything — I'm here to help."
    )

    return Response(
        twiml_listen(greeting, call_sid),
        mimetype="text/xml"
    )


@app.route("/gather", methods=["GET", "POST"])
def gather():
    """Handle speech input from the caller and respond via Claude."""
    call_sid = request.form.get("CallSid", "unknown")
    speech_result = request.form.get("SpeechResult", "").strip()
    confidence = request.form.get("Confidence", "0")

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

    # Get Claude's response
    try:
        ai_response = ask_claude(call_sid, speech_result)
    except Exception as e:
        logger.error(f"[{call_sid[:8]}] Claude API error: {e}")
        ai_response = (
            "I'm sorry, I'm having a little trouble thinking right now. "
            "Could you try asking me again?"
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
    full_response = f"{ai_response} {continuation}"

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
    }


# ── Entry Point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Emmet AI agent on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
