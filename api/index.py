import os
import time
import secrets
import requests

from flask import Flask, request, jsonify

app = Flask(__name__)
app.start_time = time.time()

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
ATTESTATION_WEBHOOK_URL = os.environ.get("ATTESTATION_WEBHOOK_URL", "")

nonces = {}


def send_discord_log(title, message):
    if not DISCORD_WEBHOOK_URL:
        return

    payload = {
        "embeds": [{
            "title": title,
            "description": message,
            "footer": {
                "text": "Oculus Tag Backend"
            }
        }]
    }

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=5
        )
    except Exception:
        pass


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
def server_status():
    return jsonify({
        "success": True,
        "status": "online",
        "server": "Oculus Tag Backend",
        "uptime": int(time.time() - app.start_time)
    })


@app.route("/api/attestation/challenge", methods=["GET", "POST"])
def challenge():
    nonce = secrets.token_urlsafe(32)

    nonces[nonce] = {
        "created": time.time(),
        "used": False
    }

    send_discord_log(
        "Attestation Challenge",
        "A new attestation challenge was generated."
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

    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "success": False,
            "error": "Missing JSON body."
        }), 400

    attestation_token = data.get("attestation_token")
    challenge_nonce = data.get("challenge_nonce")

    if not attestation_token:
        return jsonify({
            "success": False,
            "error": "Missing attestation_token."
        }), 400

    if not challenge_nonce:
        return jsonify({
            "success": False,
            "error": "Missing challenge_nonce."
        }), 400

    nonce = nonces.get(challenge_nonce)

    if not nonce:
        return jsonify({
            "success": False,
            "error": "Invalid challenge nonce."
        }), 400

    if nonce["used"]:
        return jsonify({
            "success": False,
            "error": "Challenge nonce already used."
        }), 400

    if not META_ACCESS_TOKEN:
        return jsonify({
            "success": False,
            "error": "META_ACCESS_TOKEN is not configured."
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
                "error": "Meta returned an invalid response."
            }), 502

        if response.status_code != 200:
            send_discord_log(
                "Attestation Rejected",
                "Meta rejected an attestation token."
            )

            return jsonify({
                "success": False,
                "error": "Meta rejected the attestation token."
            }), 401

        nonce["used"] = True

        send_discord_log(
            "Attestation Verified",
            "Oculus Tag attestation was successfully verified."
        )

        return jsonify({
            "success": True,
            "message": "Oculus Tag attestation verified.",
            "meta": result
        })

    except requests.RequestException:
        send_discord_log(
            "Meta Connection Error",
            "The backend could not contact Meta."
        )

        return jsonify({
            "success": False,
            "error": "Could not contact Meta."
        }), 502

    except Exception as e:
        send_discord_log(
            "Backend Error",
            f"Internal error: {str(e)}"
        )

        return jsonify({
            "success": False,
            "error": "Internal server error."
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
