#!/usr/bin/env python3
"""
End-to-end preflight for Emmet AI hotline.

Checks:
1) Service health endpoint is live.
2) Voice webhook returns valid TwiML gather.
3) Gather endpoint returns an AI answer (not fallback error).
4) Optional: verify or set Twilio phone number webhook configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


FALLBACK_ERROR_MARKERS = (
    "having a little trouble thinking right now",
    "could you try asking me again",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def http_get_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def http_post_form(url: str, form: dict[str, str], timeout: int = 30) -> str:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def check_health(base_url: str) -> CheckResult:
    url = f"{base_url}/health"
    try:
        payload = http_get_json(url)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("health", False, f"GET {url} failed: {exc}")

    status = payload.get("status")
    configured = bool(payload.get("anthropic_configured"))
    if status != "ok":
        return CheckResult("health", False, f"status={status!r} payload={payload}")
    if not configured:
        return CheckResult(
            "health",
            False,
            "anthropic_configured=false (set ANTHROPIC_API_KEY in Render environment)",
        )
    return CheckResult("health", True, f"ok (anthropic_configured={configured})")


def parse_twiml(text: str) -> ET.Element:
    return ET.fromstring(text)


def check_voice_twiml(base_url: str) -> CheckResult:
    url = f"{base_url}/voice"
    call_sid = "CA_PREFLIGHT_VOICE_001"
    form = {"CallSid": call_sid, "From": "+15555550123"}
    try:
        xml_text = http_post_form(url, form=form)
        root = parse_twiml(xml_text)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("voice_twiML", False, f"POST {url} failed: {exc}")

    gather_nodes = root.findall(".//Gather")
    if not gather_nodes:
        return CheckResult("voice_twiML", False, "No <Gather> in /voice response")

    action = gather_nodes[0].attrib.get("action", "")
    if "/gather" not in action:
        return CheckResult(
            "voice_twiML", False, f"Gather action missing /gather (action={action!r})"
        )
    return CheckResult("voice_twiML", True, f"Gather action={action}")


def check_ai_response(base_url: str, sample_question: str) -> CheckResult:
    url = f"{base_url}/gather"
    form = {
        "CallSid": "CA_PREFLIGHT_GATHER_001",
        "From": "+15555550123",
        "SpeechResult": sample_question,
        "Confidence": "0.94",
    }
    try:
        xml_text = http_post_form(url, form=form)
        root = parse_twiml(xml_text)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("ai_answer", False, f"POST {url} failed: {exc}")

    say_nodes = root.findall(".//Say")
    full_text = " ".join(" ".join(n.itertext()) for n in say_nodes).strip().lower()
    if not full_text:
        return CheckResult("ai_answer", False, "No <Say> text returned from /gather")

    for marker in FALLBACK_ERROR_MARKERS:
        if marker in full_text:
            return CheckResult("ai_answer", False, f"Fallback error detected: '{marker}'")

    return CheckResult("ai_answer", True, "AI response generated")


def twilio_client_from_env():
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set")
    from twilio.rest import Client  # lazy import

    return Client(account_sid, auth_token)


def check_twilio_number(base_url: str, phone_number: str | None, apply: bool) -> CheckResult:
    expected_voice_url = f"{base_url}/voice"
    expected_status_url = f"{base_url}/status"

    try:
        client = twilio_client_from_env()
        numbers = client.incoming_phone_numbers.list(limit=50)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("twilio_number", False, f"Twilio auth/list failed: {exc}")

    target = None
    for number in numbers:
        if phone_number and number.phone_number == phone_number:
            target = number
            break
    if not target and not phone_number and numbers:
        target = numbers[0]

    if not target:
        if phone_number:
            return CheckResult("twilio_number", False, f"Number not found: {phone_number}")
        return CheckResult("twilio_number", False, "No incoming numbers found in Twilio account")

    current_voice = (target.voice_url or "").strip()
    current_status = (target.status_callback or "").strip()

    needs_update = current_voice != expected_voice_url or current_status != expected_status_url
    if needs_update and apply:
        try:
            updated = client.incoming_phone_numbers(target.sid).update(
                voice_url=expected_voice_url,
                voice_method="POST",
                status_callback=expected_status_url,
                status_callback_method="POST",
            )
            current_voice = (updated.voice_url or "").strip()
            current_status = (updated.status_callback or "").strip()
            needs_update = current_voice != expected_voice_url or current_status != expected_status_url
        except Exception as exc:  # noqa: BLE001
            return CheckResult("twilio_number", False, f"Twilio update failed: {exc}")

    if needs_update:
        return CheckResult(
            "twilio_number",
            False,
            (
                f"voice_url={current_voice or 'unset'} status_callback={current_status or 'unset'}; "
                f"expected voice_url={expected_voice_url} status_callback={expected_status_url}"
            ),
        )

    return CheckResult(
        "twilio_number",
        True,
        f"number={target.phone_number} voice_url/status_callback are correct",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for Emmet AI hotline")
    parser.add_argument(
        "--base-url",
        default=os.getenv("HOTLINE_BASE_URL", "https://emmetai-agent.onrender.com"),
        help="Public base URL of service",
    )
    parser.add_argument(
        "--sample-question",
        default="How do I improve horse pasture for spring grazing?",
        help="Sample user question for /gather AI test",
    )
    parser.add_argument(
        "--check-twilio",
        action="store_true",
        help="Also verify Twilio incoming number webhook settings",
    )
    parser.add_argument(
        "--phone-number",
        default=os.getenv("TWILIO_PHONE_NUMBER", "").strip() or None,
        help="Specific E.164 number to inspect (optional)",
    )
    parser.add_argument(
        "--apply-twilio-webhook",
        action="store_true",
        help="If Twilio webhook is wrong, update it to {base_url}/voice and /status",
    )
    args = parser.parse_args()

    base_url = args.base_url.strip().rstrip("/")
    results: list[CheckResult] = [
        check_health(base_url),
        check_voice_twiml(base_url),
        check_ai_response(base_url, args.sample_question),
    ]

    if args.check_twilio:
        results.append(
            check_twilio_number(base_url, args.phone_number, args.apply_twilio_webhook)
        )

    failed = [r for r in results if not r.ok]
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        print(f"[{tag}] {r.name}: {r.detail}")

    if failed:
        print("\nPreflight failed. Fix the failed checks, then rerun.")
        return 1

    print("\nPreflight passed. Hotline should answer live calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
