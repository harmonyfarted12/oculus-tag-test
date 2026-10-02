import os
import time
import secrets
import base64

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
app.start_time = time.time()

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
PLAYFAB_TITLE_ID = os.environ.get("PLAYFAB_TITLE_ID", "")
PLAYFAB_SECRET_KEY = os.environ.get("PLAYFAB_SECRET_KEY", "")

used_nonces = {}


def create_nonce():
    nonce = secrets.token_urlsafe(32)

    while len(nonce) < 22 or len(nonce) > 172:
        nonce = secrets.token_urlsafe(32)

    used_nonces[nonce] = {
        "created": time.time(),
        "used": False
    }

    return nonce


def cleanup_nonces():
    now = time.time()

    expired = [
        nonce
        for nonce, data in used_nonces.items()
        if now - data["created"] > 600
    ]

    for nonce in expired:
        used_nonces.pop(nonce, None)


@app.route("/", methods=["GET", "POST"])
def main():
    return jsonify({
        "status": "online",
        "name": "Oculus Tag Backend",
        "message": "Oculus Tag Backend is running.",
        "attestation": "/api/attestation/challenge",
        "verify": "/api/attestation/verify"
    })


@app.route("/api/test", methods=["GET"])
def test():
    return jsonify({
        "status": "ok",
        "message": "Oculus Tag Backend is working."
    })


@app.route("/api/GetServerStatus", methods=["GET"])
def get_server_status():
    uptime = int(time.time() - app.start_time)

    return jsonify({
        "status": "online",
        "uptime": uptime,
        "server": "Oculus Tag Backend"
    })


@app.route("/api/attestation/challenge", methods=["GET", "POST"])
def attestation_challenge():
    cleanup_nonces()

    nonce = create_nonce()

    return jsonify({
        "success": True,
        "challenge_nonce": nonce
    })


@app.route("/api/attestation/verify", methods=["POST"])
def attestation_verify():
    if not META_ACCESS_TOKEN:
        return jsonify({
            "success": False,
            "error": "META_ACCESS_TOKEN is not configured."
        }), 500

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

    nonce_data = used_nonces.get(challenge_nonce)

    if not nonce_data:
        return jsonify({
            "success": False,
            "error": "Invalid or expired challenge nonce."
        }), 400

    if nonce_data["used"]:
        return jsonify({
            "success": False,
            "error": "Challenge nonce has already been used."
        }), 400

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
            meta_result = response.json()
        except ValueError:
            return jsonify({
                "success": False,
                "error": "Meta returned an invalid response."
            }), 502

        if response.status_code != 200:
            return jsonify({
                "success": False,
                "error": "Meta rejected the attestation token.",
                "meta": meta_result
            }), 401

        if not meta_result.get("data"):
            return jsonify({
                "success": False,
                "error": "Meta did not return attestation data.",
                "meta": meta_result
            }), 401

        result = meta_result["data"][0]

        if result.get("message") != "success":
            return jsonify({
                "success": False,
                "error": "Meta attestation verification failed.",
                "meta": meta_result
            }), 401

        claims_encoded = result.get("claims")

        if not claims_encoded:
            return jsonify({
                "success": False,
                "error": "Meta response did not contain claims."
            }), 401

        try:
            padding = "=" * (-len(claims_encoded) % 4)
            claims_json = base64.urlsafe_b64decode(
                claims_encoded + padding
            ).decode("utf-8")

            import json
            claims = json.loads(claims_json)

        except Exception:
            return jsonify({
                "success": False,
                "error": "Could not decode Meta attestation claims."
            }), 401

        request_details = claims.get("request_details", {})
        app_state = claims.get("app_state", {})
        device_state = claims.get("device_state", {})

        token_nonce = request_details.get("nonce")

        if token_nonce != challenge_nonce:
            return jsonify({
                "success": False,
                "error": "Attestation nonce mismatch."
            }), 401

        now = int(time.time())

        expiration = request_details.get("exp")
        timestamp = request_details.get("timestamp")

        if expiration is not None and now >= int(expiration):
            return jsonify({
                "success": False,
                "error": "Attestation token has expired."
            }), 401

        if timestamp is not None:
            token_age = now - int(timestamp)

            if token_age < -300 or token_age > 300:
                return jsonify({
                    "success": False,
                    "error": "Attestation timestamp is too old."
                }), 401

        app_integrity = app_state.get("app_integrity_state")
        device_integrity = device_state.get("device_integrity_state")

        if app_integrity != "StoreRecognized":
            return jsonify({
                "success": False,
                "error": "Application integrity check failed.",
                "app_integrity_state": app_integrity
            }), 403

        if device_integrity not in ("Advanced", "Basic"):
            return jsonify({
                "success": False,
                "error": "Device integrity check failed.",
                "device_integrity_state": device_integrity
            }), 403

        nonce_data["used"] = True

        return jsonify({
            "success": True,
            "message": "Oculus Tag attestation verified.",
            "app_integrity_state": app_integrity,
            "device_integrity_state": device_integrity,
            "package_id": app_state.get("package_id"),
            "version": app_state.get("version")
        })

    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": "Could not contact Meta.",
            "details": str(e)
        }), 502

    except Exception as e:
        return jsonify({
            "success": False,
            "error": "Internal server error.",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=9080,
        debug=False
    )
