import os
import time
import json
import uuid
import hmac
import base64
import hashlib
import logging
import secrets

import requests
from flask import Flask, jsonify, request


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


class GameInfo:
    def __init__(self):
        self.TitleId: str = "C7E30"
        self.SecretKey: str = "PEKS8YH8HTOAYATD93F4MS4N4BXNKOBRY4PJMYPWCYOOYE7I78"
        self.ApiKey: str = "OC|1368813259653754|673098554f8a983dc591cf6114427955"


game_info = GameInfo()

BACKEND_URL = "https://oculus-tag-test.vercel.app"

META_PACKAGE_ID = "com.harmonystudios.oculustaggers"
META_CERT_SHA256 = "YOUR_META_CERT_SHA256"

ATTESTATION_SECRET = os.environ.get(
    "ATTESTATION_SECRET",
    ""
)

ATTESTATION_WEBHOOK_URL = os.environ.get(
    "ATTESTATION_WEBHOOK_URL",
    ""
)

ATTESTATION_MAX_AGE = 300
CHALLENGE_MAX_AGE = 600


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_challenge(payload):
    if not ATTESTATION_SECRET:
        raise RuntimeError("ATTESTATION_SECRET is not configured.")

    encoded = b64url_encode(
        json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True
        ).encode()
    )

    signature = hmac.new(
        ATTESTATION_SECRET.encode(),
        encoded.encode(),
        hashlib.sha256
    ).digest()

    return encoded + "." + b64url_encode(signature)


def generate_challenge(oculus_id):
    payload = {
        "oculus_id": str(oculus_id),
        "nonce": secrets.token_urlsafe(32),
        "created": int(time.time())
    }

    return sign_challenge(payload)


def verify_challenge(challenge):
    if not challenge or "." not in challenge:
        return None

    try:
        encoded, signature = challenge.split(".", 1)

        expected = hmac.new(
            ATTESTATION_SECRET.encode(),
            encoded.encode(),
            hashlib.sha256
        ).digest()

        actual = b64url_decode(signature)

        if not hmac.compare_digest(expected, actual):
            return None

        payload = json.loads(
            b64url_decode(encoded).decode()
        )

        created = int(payload.get("created", 0))

        if time.time() - created > CHALLENGE_MAX_AGE:
            return None

        return payload

    except Exception:
        return None


def send_discord_log(message):
    if not ATTESTATION_WEBHOOK_URL:
        return

    try:
        requests.post(
            ATTESTATION_WEBHOOK_URL,
            json={
                "content": message
            },
            timeout=5
        )
    except Exception:
        pass


def verify_meta_attestation(
    attestation_token,
    challenge_nonce,
    oculus_id
):
    if not attestation_token:
        return False, "Attestation token is required."

    if not challenge_nonce:
        return False, "Challenge nonce is required."

    if not game_info.ApiKey:
        return False, "Meta API key is not configured."

    try:
        response = requests.get(
            "https://graph.oculus.com/platform_integrity/verify",
            params={
                "token": attestation_token,
                "access_token": game_info.ApiKey
            },
            timeout=15
        )

        if response.status_code != 200:
            return False, "Meta attestation request failed."

        body = response.json()

        data = body.get("data")

        if not isinstance(data, list) or not data:
            return False, "Meta returned no attestation data."

        attestation = data[0]

        if attestation.get("message") != "success":
            return False, "Meta attestation was rejected."

        claims_value = attestation.get("claims")

        if not claims_value:
            return False, "Attestation claims are missing."

        try:
            claims = json.loads(
                b64url_decode(claims_value).decode()
            )
        except Exception:
            return False, "Invalid attestation claims."

        request_details = claims.get(
            "request_details",
            {}
        )

        app_state = claims.get(
            "app_state",
            {}
        )

        device_state = claims.get(
            "device_state",
            {}
        )

        if not isinstance(request_details, dict):
            return False, "Invalid request details."

        if not isinstance(app_state, dict):
            return False, "Invalid app state."

        if not isinstance(device_state, dict):
            return False, "Invalid device state."

        token_nonce = request_details.get("nonce")

        if token_nonce != challenge_nonce:
            return False, "Attestation nonce does not match."

        expiration = request_details.get(
            "expiration_time"
        )

        if expiration is not None:
            try:
                if float(expiration) <= time.time():
                    return False, "Attestation token expired."
            except Exception:
                return False, "Invalid attestation expiration."

        timestamp = request_details.get(
            "timestamp"
        )

        if timestamp is not None:
            try:
                timestamp = float(timestamp)

                if abs(time.time() - timestamp) > ATTESTATION_MAX_AGE:
                    return False, "Attestation timestamp is too old."

            except Exception:
                return False, "Invalid attestation timestamp."

        package_id = app_state.get(
            "package_id"
        )

        if package_id != META_PACKAGE_ID:
            return False, "Invalid Meta package."

        certificate = app_state.get(
            "package_cert_sha256_digest"
        )

        if isinstance(certificate, list):
            certificate_values = [
                str(x).lower()
                for x in certificate
            ]
        else:
            certificate_values = [
                str(certificate).lower()
            ]

        expected_certificate = META_CERT_SHA256.lower()

        if expected_certificate not in certificate_values:
            return False, "Invalid APK certificate."

        app_integrity = app_state.get(
            "app_integrity_state"
        )

        if app_integrity != "StoreRecognized":
            return False, "App is not recognized by Meta."

        device_integrity = device_state.get(
            "device_integrity_state"
        )

        if device_integrity not in (
            "Advanced",
            "Basic"
        ):
            return False, "Device integrity check failed."

        device_ban = device_state.get(
            "device_ban",
            {}
        )

        if isinstance(device_ban, dict):
            if device_ban.get("is_banned") is True:
                return False, "Device is banned."

        request_oculus_id = request_details.get(
            "user_id"
        )

        if request_oculus_id is not None:
            if str(request_oculus_id) != str(oculus_id):
                return False, "Oculus ID does not match."

        return True, "Attestation verified."

    except requests.RequestException:
        return False, "Unable to contact Meta."

    except Exception:
        logging.exception(
            "Meta attestation verification failed"
        )
        return False, "Attestation verification failed."


def playfab_request(endpoint, payload):
    url = (
        "https://"
        + game_info.TitleId
        + ".playfabapi.com"
        + endpoint
    )

    try:
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-SecretKey": game_info.SecretKey
            },
            json=payload,
            timeout=15
        )

        try:
            body = response.json()
        except Exception:
            body = {
                "error": response.text
            }

        return response.status_code, body

    except requests.RequestException as e:
        return 500, {
            "error": str(e)
        }


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "success": True,
        "message": "Oculus Tag Backend"
    })


@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message": "Oculus Tag Backend"
    })


@app.route("/api/GetServerStatus", methods=["GET", "POST"])
def get_server_status():
    return jsonify({
        "success": True,
        "online": True,
        "status": "online"
    })


@app.route("/api/GetServerConfig", methods=["GET", "POST"])
def get_server_config():
    return jsonify({
        "success": True,
        "TitleId": game_info.TitleId
    })


@app.route("/api/attestation/challenge", methods=["GET"])
def attestation_challenge():
    oculus_id = request.args.get(
        "OculusId",
        ""
    ).strip()

    if not oculus_id:
        return jsonify({
            "success": False,
            "error": "OculusId is required."
        }), 400

    try:
        challenge = generate_challenge(
            oculus_id
        )

        challenge_data = verify_challenge(
            challenge
        )

        if not challenge_data:
            return jsonify({
                "success": False,
                "error": "Failed to create challenge."
            }), 500

        return jsonify({
            "success": True,
            "challenge_nonce": challenge
        })

    except Exception:
        logging.exception(
            "Failed to create attestation challenge"
        )

        return jsonify({
            "success": False,
            "error": "Internal Server Error"
        }), 500


@app.route("/api/attestation/verify", methods=["POST"])
def attestation_verify():
    data = request.get_json(
        silent=True
    ) or {}

    attestation_token = data.get(
        "attestation_token",
        ""
    )

    challenge_nonce = data.get(
        "challenge_nonce",
        ""
    )

    oculus_id = str(
        data.get(
            "OculusId",
            ""
        )
    ).strip()

    if not attestation_token:
        return jsonify({
            "verified": False,
            "error": "Attestation token is required."
        }), 400

    if not challenge_nonce:
        return jsonify({
            "verified": False,
            "error": "Challenge nonce is required."
        }), 400

    if not oculus_id:
        return jsonify({
            "verified": False,
            "error": "OculusId is required."
        }), 400

    challenge = verify_challenge(
        challenge_nonce
    )

    if not challenge:
        return jsonify({
            "verified": False,
            "error": "Invalid or expired challenge."
        }), 401

    if str(challenge.get("oculus_id")) != oculus_id:
        return jsonify({
            "verified": False,
            "error": "Challenge Oculus ID does not match."
        }), 401

    actual_nonce = challenge.get(
        "nonce"
    )

    verified, message = verify_meta_attestation(
        attestation_token,
        actual_nonce,
        oculus_id
    )

    if not verified:
        return jsonify({
            "verified": False,
            "error": message
        }), 401

    return jsonify({
        "verified": True,
        "success": True,
        "message": message
    })


@app.route("/api/PlayFabAuthentication", methods=["POST"])
def playfab_authentication():
    data = request.get_json(
        silent=True
    ) or {}

    oculus_id = str(
        data.get(
            "OculusId",
            ""
        )
    ).strip()

    attestation_token = data.get(
        "attestation_token",
        ""
    )

    challenge_nonce = data.get(
        "challenge_nonce",
        ""
    )

    if not oculus_id:
        return jsonify({
            "success": False,
            "error": "OculusId is required."
        }), 400

    if not attestation_token:
        return jsonify({
            "success": False,
            "error": "Attestation token is required."
        }), 401

    if not challenge_nonce:
        return jsonify({
            "success": False,
            "error": "Challenge nonce is required."
        }), 401

    challenge = verify_challenge(
        challenge_nonce
    )

    if not challenge:
        send_discord_log(
            "❌ PlayFab Authentication Rejected\n"
            f"Oculus ID: `{oculus_id}`\n"
            "Reason: Invalid or expired challenge."
        )

        return jsonify({
            "success": False,
            "error": "Invalid or expired attestation challenge."
        }), 401

    if str(
        challenge.get("oculus_id")
    ) != oculus_id:

        send_discord_log(
            "❌ PlayFab Authentication Rejected\n"
            f"Oculus ID: `{oculus_id}`\n"
            "Reason: Challenge Oculus ID mismatch."
        )

        return jsonify({
            "success": False,
            "error": "Challenge Oculus ID does not match."
        }), 401

    actual_nonce = challenge.get(
        "nonce"
    )

    verified, message = verify_meta_attestation(
        attestation_token,
        actual_nonce,
        oculus_id
    )

    if not verified:
        send_discord_log(
            "❌ PlayFab Authentication Rejected\n"
            f"Oculus ID: `{oculus_id}`\n"
            f"Reason: {message}"
        )

        return jsonify({
            "success": False,
            "error": message
        }), 401

    status, playfab_response = playfab_request(
        "/Server/LoginWithServerCustomId",
        {
            "ServerCustomId": (
                "OCULUS"
                + oculus_id
            ),
            "CreateAccount": True,
            "InfoRequestParameters": {
                "GetUserAccountInfo": True,
                "GetUserInventory": True,
                "GetUserVirtualCurrency": True,
                "GetPlayerStatistics": True,
                "GetCharacterList": True
            }
        }
    )

    if status < 200 or status >= 300:
        send_discord_log(
            "❌ PlayFab Authentication Failed\n"
            f"Oculus ID: `{oculus_id}`\n"
            "Reason: PlayFab rejected the login."
        )

        return jsonify({
            "success": False,
            "error": "PlayFab authentication failed.",
            "playfab": playfab_response
        }), status

    send_discord_log(
        "✅ PlayFab Authentication Successful\n"
        f"Oculus ID: `{oculus_id}`"
    )

    return jsonify({
        "success": True,
        "message": "PlayFab authentication successful.",
        "data": playfab_response
    })


@app.route("/api/CachePlayFabId", methods=["GET", "POST"])
def cache_playfab_id():
    return jsonify({
        "success": True
    })


@app.route("/api/TitleData", methods=["GET", "POST"])
def title_data():
    return jsonify({
        "success": True,
        "data": {}
    })


@app.route("/api/GetAcceptedAgreements", methods=["GET", "POST"])
def get_accepted_agreements():
    return jsonify({
        "success": True,
        "agreements": []
    })


@app.route("/api/SubmitAcceptedAgreements", methods=["GET", "POST"])
def submit_accepted_agreements():
    return jsonify({
        "success": True
    })


@app.route("/api/v2/GetName", methods=["GET", "POST"])
def get_name():
    return jsonify({
        "success": True,
        "Name": "Player"
    })


@app.route("/api/GetInventory", methods=["GET", "POST"])
def get_inventory():
    return jsonify({
        "success": True,
        "Inventory": []
    })


@app.route("/api/GetLeaderboard", methods=["GET", "POST"])
def get_leaderboard():
    return jsonify({
        "success": True,
        "Leaderboard": []
    })


@app.route("/api/UpdateStats", methods=["GET", "POST"])
def update_stats():
    return jsonify({
        "success": True
    })


@app.route("/api/UpdateProfile", methods=["GET", "POST"])
def update_profile():
    return jsonify({
        "success": True
    })


@app.route("/api/GetCosmetics", methods=["GET", "POST"])
def get_cosmetics():
    return jsonify({
        "success": True,
        "Cosmetics": []
    })


@app.route("/api/EquipCosmetic", methods=["GET", "POST"])
def equip_cosmetic():
    return jsonify({
        "success": True
    })


@app.route("/api/ReportPlayer", methods=["GET", "POST"])
def report_player():
    return jsonify({
        "success": True
    })


@app.route("/api/CreateParty", methods=["GET", "POST"])
def create_party():
    return jsonify({
        "success": True,
        "PartyId": str(uuid.uuid4())
    })


@app.route("/api/JoinParty", methods=["GET", "POST"])
def join_party():
    return jsonify({
        "success": True
    })


@app.route("/api/LeaveParty", methods=["GET", "POST"])
def leave_party():
    return jsonify({
        "success": True
    })


@app.route("/PathCreate", methods=["GET", "POST"])
def path_create():
    return jsonify({
        "success": True
    })


@app.route("/PathJoin", methods=["GET", "POST"])
def path_join():
    return jsonify({
        "success": True
    })


@app.route("/PathLeave", methods=["GET", "POST"])
def path_leave():
    return jsonify({
        "success": True
    })


@app.route("/PathClose", methods=["GET", "POST"])
def path_close():
    return jsonify({
        "success": True
    })


@app.route("/PathRaiseEvent", methods=["GET", "POST"])
def path_raise_event():
    return jsonify({
        "success": True
    })


@app.route("/PathSetProperties", methods=["GET", "POST"])
def path_set_properties():
    return jsonify({
        "success": True
    })


@app.after_request
def after_request(response):
    if request.path.startswith("/api/"):
        if request.path not in (
            "/api/attestation/challenge",
            "/api/attestation/verify"
        ):
            logging.info(
                "%s %s -> %s",
                request.method,
                request.path,
                response.status_code
            )

    return response


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Not Found"
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "success": False,
        "error": "Method Not Allowed"
    }), 405


@app.errorhandler(500)
def internal_error(error):
    logging.exception(
        "Internal Server Error"
    )

    return jsonify({
        "success": False,
        "error": "Internal Server Error"
    }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=9080,
        debug=False
    )
