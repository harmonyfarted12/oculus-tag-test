import os
import json
import base64
import time
import secrets
import urllib.request
import urllib.error
import urllib.parse

from flask import Flask, request, jsonify

app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

META_ACCESS_TOKEN = os.environ.get(
    "META_ACCESS_TOKEN",
    ""
)

ATTESTATION_WEBHOOK_URL = os.environ.get(
    "https://discord.com/api/webhooks/1546613597918990366/-4GtvpE7Cn47bWsY0rv5W_O3rlkX4SmGiDjm8-_zJlhFAEBKqbJZQx4P2cyKGMKrfLnH",
    "https://discord.com/api/webhooks/1546613597918990366/-4GtvpE7Cn47bWsY0rv5W_O3rlkX4SmGiDjm8-_zJlhFAEBKqbJZQx4P2cyKGMKrfLnH"
)


# ============================================================
# LOGGING
# ============================================================

def log_attestation(event, data=None):

    entry = {
        "event": event,
        "timestamp": int(time.time())
    }

    if data is not None:
        entry["data"] = data

    print(
        "[ATTESTATION]",
        json.dumps(
            entry,
            separators=(",", ":")
        ),
        flush=True
    )


# ============================================================
# WEBHOOK
# ============================================================

def send_attestation_webhook(claims):

    if not ATTESTATION_WEBHOOK_URL:
        log_attestation(
            "webhook_not_configured"
        )
        return False

    payload = {
        "content": "Meta Attestation Received",
        "embeds": [
            {
                "title": "Meta Attestation",
                "description": "A Meta Quest attestation was successfully verified.",
                "fields": [
                    {
                        "name": "Package ID",
                        "value": str(
                            claims.get(
                                "app_state",
                                {}
                            ).get(
                                "package_id",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    },
                    {
                        "name": "Version",
                        "value": str(
                            claims.get(
                                "app_state",
                                {}
                            ).get(
                                "version",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    },
                    {
                        "name": "App Integrity",
                        "value": str(
                            claims.get(
                                "app_state",
                                {}
                            ).get(
                                "app_integrity_state",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    },
                    {
                        "name": "Device Integrity",
                        "value": str(
                            claims.get(
                                "device_state",
                                {}
                            ).get(
                                "device_integrity_state",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    },
                    {
                        "name": "Security Update Pending",
                        "value": str(
                            claims.get(
                                "device_state",
                                {}
                            ).get(
                                "security_update_pending_days",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    },
                    {
                        "name": "Nonce",
                        "value": str(
                            claims.get(
                                "request_details",
                                {}
                            ).get(
                                "nonce",
                                "Unknown"
                            )
                        ),
                        "inline": False
                    },
                    {
                        "name": "Expiration",
                        "value": str(
                            claims.get(
                                "request_details",
                                {}
                            ).get(
                                "exp",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    },
                    {
                        "name": "Timestamp",
                        "value": str(
                            claims.get(
                                "request_details",
                                {}
                            ).get(
                                "timestamp",
                                "Unknown"
                            )
                        ),
                        "inline": True
                    }
                ]
            }
        ]
    }

    data = json.dumps(
        payload
    ).encode("utf-8")

    req = urllib.request.Request(
        ATTESTATION_WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=10
        ) as response:

            log_attestation(
                "webhook_sent",
                {
                    "status": response.status
                }
            )

            return (
                response.status >= 200
                and response.status < 300
            )

    except Exception as error:

        log_attestation(
            "webhook_failed",
            {
                "error": str(error)
            }
        )

        return False


# ============================================================
# META VERIFICATION
# ============================================================

def verify_meta_token(token):

    if not META_ACCESS_TOKEN:
        return False, None


    url = (
        "https://graph.oculus.com/"
        "platform_integrity/verify"
        "?token="
        + urllib.parse.quote(
            token,
            safe=""
        )
        + "&access_token="
        + urllib.parse.quote(
            META_ACCESS_TOKEN,
            safe=""
        )
    )


    req = urllib.request.Request(
        url,
        method="GET"
    )


    try:

        with urllib.request.urlopen(
            req,
            timeout=15
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            data = json.loads(raw)

    except urllib.error.HTTPError as error:

        log_attestation(
            "meta_verification_failed",
            {
                "status": error.code
            }
        )

        return False, None

    except Exception as error:

        log_attestation(
            "meta_request_failed",
            {
                "error": str(error)
            }
        )

        return False, None


    result = None

    if isinstance(data, dict):

        values = data.get("data")

        if (
            isinstance(values, list)
            and len(values) > 0
        ):
            result = values[0]


    if not result:
        return False, None


    if result.get("message") != "success":
        return False, None


    claims_string = result.get(
        "claims"
    )

    if not claims_string:
        return False, None


    try:

        padded = claims_string

        while len(padded) % 4 != 0:
            padded += "="

        decoded = base64.urlsafe_b64decode(
            padded
        ).decode("utf-8")

        claims = json.loads(
            decoded
        )

    except Exception as error:

        log_attestation(
            "claims_decode_failed",
            {
                "error": str(error)
            }
        )

        return False, None


    return True, claims


# ============================================================
# VERIFY ENDPOINT
# ============================================================

@app.route(
    "/api/attest/verify",
    methods=["POST"]
)
def verify_attestation():

    body = request.get_json(
        silent=True
    ) or {}


    token = body.get(
        "token"
    )

    nonce = body.get(
        "nonce"
    )


    if not token or not nonce:

        return jsonify({
            "ok": False,
            "error": "Missing token or nonce"
        }), 400


    log_attestation(
        "verification_started"
    )


    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

    success, claims = verify_meta_token(
        token
    )


    if not success:

        log_attestation(
            "meta_rejected"
        )

        return jsonify({
            "ok": False,
            "error": "Meta attestation failed"
        }), 403


    # --------------------------------------------------------
    # NONCE
    # --------------------------------------------------------

    request_details = claims.get(
        "request_details",
        {}
    )


    returned_nonce = request_details.get(
        "nonce"
    )


    if returned_nonce != nonce:

        log_attestation(
            "nonce_mismatch"
        )

        return jsonify({
            "ok": False,
            "error": "Nonce mismatch"
        }), 403


    # --------------------------------------------------------
    # EXPIRATION
    # --------------------------------------------------------

    expiration = request_details.get(
        "exp"
    )


    if isinstance(
        expiration,
        (int, float)
    ):

        if expiration <= int(time.time()):

            return jsonify({
                "ok": False,
                "error": "Attestation expired"
            }), 403


    # --------------------------------------------------------
    # LOG COMPLETE CLAIMS
    # --------------------------------------------------------

    log_attestation(
        "META_ATTESTATION_PASSED",
        claims
    )


    # --------------------------------------------------------
    # SEND WEBHOOK
    # --------------------------------------------------------

    send_attestation_webhook(
        claims
    )


    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    return jsonify({
        "ok": True,
        "attested": True,
        "claims": claims
    })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def index():

    return jsonify({
        "ok": True,
        "service": "Oculus Taggers Attestation",
        "meta": True
    })

import os
import json
import urllib.request
from flask import Flask, request

app = Flask(__name__)

WEBHOOK_URL = os.getenv("ATTESTATION_WEBHOOK_URL")


def send_webhook(message):
    if not WEBHOOK_URL:
        print("Webhook not configured")
        return

    payload = {
        "username": "Oculus Taggers",
        "content": message
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        urllib.request.urlopen(req, timeout=10)
        print("Webhook sent")
    except Exception as e:
        print("Webhook error:", e)


@app.route("/")
def home():
    send_webhook("🔗 **Oculus Taggers link was opened!**")

    return """
    <html>
        <head>
            <title>Oculus Taggers</title>
        </head>
        <body>
            <h1>Oculus Taggers</h1>
            <p>Welcome!</p>
        </body>
    </html>
    """


@app.route("/api/test")
def test():
    send_webhook("🧪 **Webhook test triggered!**")
    return {"ok": True}
