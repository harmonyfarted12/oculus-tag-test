import os
import time
import secrets
import requests

from flask import Flask, request, jsonify

app = Flask(__name__)
app.start_time = time.time()

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
PLAYFAB_TITLE_ID = os.environ.get("PLAYFAB_TITLE_ID", "")
PLAYFAB_SECRET_KEY = os.environ.get("PLAYFAB_SECRET_KEY", "")
ATTESTATION_WEBHOOK_URL = os.environ.get("ATTESTATION_WEBHOOK_URL", "")

nonces = {}


def discord_log(title, message, level="info"):
    if not DISCORD_WEBHOOK_URL:
        return

    colors = {
        "info": 3447003,
        "success": 5763719,
        "warning": 16776960,
        "error": 15548997
    }

    payload = {
        "embeds": [
            {
                "title": title,
                "description": message,
                "color": colors.get(level, colors["info"]),
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime()
                ),
                "footer": {
                    "text": "Oculus Tag Backend"
                }
            }
        ]
    }

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=5
        )
    except Exception:
        pass


def generate_nonce():
    nonce = secrets.token_urlsafe(32)

    while len(nonce) < 22 or len(nonce) > 172:
        nonce = secrets.token_urlsafe(32)

    nonces[nonce] = {
        "created": time.time(),
        "used": False
    }

    return nonce


def cleanup_nonces():
    now = time.time()

    expired = [
        nonce
        for nonce, data in nonces.items()
        if now - data["created"] > 600
    ]

    for nonce in expired:
        nonces.pop(nonce, None)


@app.route("/", methods=["GET", "POST"])
def home():
    discord_log(
        "Backend Request",
        f"GET/POST request received from {request.remote_addr}",
        "info"
    )

    return jsonify({
        "success": True,
        "status": "online",
        "name": "Oculus Tag Backend",
        "message": "Oculus Tag Backend is running."
    })


@app.route("/api/test", methods=["GET", "POST"])
def test():
    discord_log(
        "API Test",
        f"Test endpoint accessed from {request.remote_addr}",
        "info"
    )

    return jsonify({
        "success": True,
        "message": "Oculus Tag Backend is working."
    })


@app.route("/api/GetServerStatus", methods=["GET"])
def get_server_status():
    uptime = int(time.time() - app.start_time)

    return jsonify({
        "success": True,
        "status": "online",
        "server": "Oculus Tag Backend",
        "uptime": uptime
    })


@app.route("/api/attestation/challenge", methods=["GET", "POST"])
def get_challenge():
    cleanup_nonces()

    nonce = generate_nonce()

    discord_log(
        "Attestation Challenge",
        f"New challenge generated.\nIP: `{request.remote_addr}`",
        "info"
    )

    return jsonify({
        "success": True,
        "challenge_nonce": nonce
    })


@app.route("/api/verify", methods=["GET", "POST"])
def verify():
    if request.method == "GET":
        return jsonify({
            "success": True,
            "status": "online",
            "message": "Oculus Tag Verify API is online.",
            "method": "POST"
        })

    data = request.get_json(silent=True) or {}

    attestation_token = data.get("attestation_token")
    challenge_nonce = data.get("challenge_nonce")

    if not attestation_token:
        discord_log(
            "Attestation Failed",
            f"Missing attestation token.\nIP: `{request.remote_addr}`",
            "warning"
        )

        return jsonify({
            "success": False,
            "error": "Missing attestation_token"
        }), 400

    if not challenge_nonce:
        discord_log(
            "Attestation Failed",
            f"Missing challenge nonce.\nIP: `{request.remote_addr}`",
            "warning"
        )

        return jsonify({
            "success": False,
            "error": "Missing challenge_nonce"
        }), 400

    nonce_data = nonces.get(challenge_nonce)

    if not nonce_data:
        discord_log(
            "Attestation Failed",
            f"Invalid or expired challenge nonce.\nIP: `{request.remote_addr}`",
            "warning"
        )

        return jsonify({
            "success": False,
            "error": "Invalid or expired challenge nonce"
        }), 400

    if nonce_data["used"]:
        discord_log(
            "Attestation Failed",
            f"Nonce was already used.\nIP: `{request.remote_addr}`",
            "warning"
        )

        return jsonify({
            "success": False,
            "error": "Challenge nonce has already been used"
        }), 400

    if not META_ACCESS_TOKEN:
        discord_log(
            "Backend Configuration Error",
            "META_ACCESS_TOKEN is not configured.",
            "error"
        )

        return jsonify({
            "success": False,
            "error": "META_ACCESS_TOKEN is not configured"
        }), 500

    try:
        response = requests.get(
            "https://graph.oculus.com/platform_integrity/verify",
            params={
                "token": attestation_token,
                "access_token": META_ACCESS_TOKEN
            },
            timeout=15
        )

        try:
            result = response.json()
        except ValueError:
            discord_log(
                "Meta Verification Error",
                f"Meta returned an invalid response.\nHTTP: `{response.status_code}`",
                "error"
            )

            return jsonify({
                "success": False,
                "error": "Meta returned an invalid response"
            }), 502

        if response.status_code != 200:
            discord_log(
                "Attestation Rejected",
                f"Meta rejected the attestation token.\n"
                f"HTTP: `{response.status_code}`\n"
                f"IP: `{request.remote_addr}`",
                "warning"
            )

            return jsonify({
                "success": False,
                "error": "Meta rejected the attestation token"
            }), 401

        nonce_data["used"] = True

        discord_log(
            "Attestation Verified",
            f"Quest attestation successfully verified.\n"
            f"IP: `{request.remote_addr}`",
            "success"
        )

        return jsonify({
            "success": True,
            "message": "Oculus Tag attestation verified.",
            "meta": result
        })

    except requests.RequestException as e:
        discord_log(
            "Meta Connection Error",
            f"Could not contact Meta.\nError: `{str(e)}`",
            "error"
        )

        return jsonify({
            "success": False,
            "error": "Could not contact Meta"
        }), 502

    except Exception as e:
        discord_log(
            "Backend Error",
            f"Internal server error.\nError: `{str(e)}`",
            "error"
        )

        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500


@app.route("/api/attestation/verify", methods=["GET", "POST"])
def attestation_verify():
    return verify()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=9080,
        debug=False
    )
