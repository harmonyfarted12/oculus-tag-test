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

nonces = {}


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
    return jsonify({
        "success": True,
        "status": "online",
        "name": "Oculus Tag Backend",
        "message": "Oculus Tag Backend is running."
    })


@app.route("/api/test", methods=["GET", "POST"])
def test():
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
        return jsonify({
            "success": False,
            "error": "Missing attestation_token"
        }), 400

    if not challenge_nonce:
        return jsonify({
            "success": False,
            "error": "Missing challenge_nonce"
        }), 400

    nonce_data = nonces.get(challenge_nonce)

    if not nonce_data:
        return jsonify({
            "success": False,
            "error": "Invalid or expired challenge nonce"
        }), 400

    if nonce_data["used"]:
        return jsonify({
            "success": False,
            "error": "Challenge nonce has already been used"
        }), 400

    if not META_ACCESS_TOKEN:
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
            return jsonify({
                "success": False,
                "error": "Meta returned an invalid response"
            }), 502

        if response.status_code != 200:
            return jsonify({
                "success": False,
                "error": "Meta rejected the attestation token",
                "meta": result
            }), 401

        nonce_data["used"] = True

        return jsonify({
            "success": True,
            "message": "Oculus Tag attestation verified.",
            "meta": result
        })

    except requests.RequestException:
        return jsonify({
            "success": False,
            "error": "Could not contact Meta"
        }), 502

    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Internal server error",
            "details": str(e)
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
