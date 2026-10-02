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
        self.TitleId: str = "8A822"  # PlayFab Title ID
        self.SecretKey: str = "PEKS8YH8HTOAYATD93F4MS4N4BXNKOBRY4PJMYPWCYOOYE7I78"  # PlayFab Secret Key
        self.ApiKey: str = "OC|1368813259653754|673098554f8a983dc591cf6114427955"  # Oculus/Graph API key


game_info = GameInfo()

BACKEND_URL = "https://oculus-tag-test.vercel.app"

META_PACKAGE_ID = "com.harmonystudios.oculustaggers"

# Put your actual certificate SHA-256 here.
META_CERT_SHA256 = "5dc7570563e442b4fcb1595e61d957f4dd19e1793514592c6e52eef31157f25e"

# Server-side secret used to sign attestation challenges.
ATTESTATION_SECRET = os.environ.get(
    "ATTESTATION_SECRET",
    "v9Kx7Qm2Rz8Np4Lw6Tj3Yh5Vc1Fs0Aa8Ud7Ge2Wx9Bn4Mp6Qr3Zk8Hs5Jv1Pc7Nt2"
)

ATTESTATION_WEBHOOK_URL = os.environ.get(
    "ATTESTATION_WEBHOOK_URL",
    ""
)

ATTESTATION_MAX_AGE = 300
CHALLENGE_MAX_AGE = 600


REDEEMABLE_ITEMS = [
    "Spear",
    "Banana",
    "Admin Badge"
]


def json_body():
    data = request.get_json(silent=True)

    if isinstance(data, dict):
        return data

    return {}


def get_value(data, *names):
    for name in names:
        value = data.get(name)

        if value is not None and str(value) != "":
            return value

    return None


def validate_input(value, max_length=10000):
    if value is None:
        return False

    try:
        return 0 < len(str(value)) <= max_length
    except Exception:
        return False


def mask_secret(value):
    if not value:
        return ""

    value = str(value)

    if len(value) <= 8:
        return "***"

    return value[:4] + "..." + value[-4:]


def discord_log(title, message):
    if not DISCORD_WEBHOOK_URL:
        return

    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "embeds": [
                    {
                        "title": title,
                        "description": message
                    }
                ]
            },
            timeout=5
        )
    except Exception:
        pass


def game_log(message):
    logging.info(message)


def attestation_log(message):
    logging.info("[ATTESTATION] %s", message)


def playfab_headers():
    return {
        "X-SecretKey": game_info.SecretKey,
        "Content-Type": "application/json"
    }


def playfab_request(endpoint, payload):
    if not game_info.SecretKey:
        return None, {
            "error": "PlayFab secret key is not configured."
        }

    url = (
        "https://"
        + game_info.TitleId
        + ".playfabapi.com"
        + endpoint
    )

    try:
        response = requests.post(
            url,
            headers=playfab_headers(),
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

    except Exception as e:
        return None, {
            "error": str(e)
        }


def cloud_script(function_name, function_parameter=None):
    payload = {
        "FunctionName": function_name
    }

    if function_parameter is not None:
        payload["FunctionParameter"] = function_parameter

    return playfab_request(
        "/Server/ExecuteCloudScript",
        payload
    )


def b64url_encode(value):
    return base64.urlsafe_b64encode(
        value
    ).decode("utf-8").rstrip("=")


def b64url_decode(value):
    padding = "=" * (
        4 - len(value) % 4
    )

    return base64.urlsafe_b64decode(
        value + padding
    )


def sign_challenge(timestamp, oculus_id, nonce):
    if not ATTESTATION_SECRET:
        return ""

    data = (
        str(timestamp)
        + "."
        + str(oculus_id)
        + "."
        + str(nonce)
    )

    return hmac.new(
        ATTESTATION_SECRET.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def generate_challenge(oculus_id):
    timestamp = int(time.time())

    nonce = secrets.token_urlsafe(32)

    signature = sign_challenge(
        timestamp,
        oculus_id,
        nonce
    )

    if not signature:
        return None

    payload = {
        "timestamp": timestamp,
        "oculus_id": str(oculus_id),
        "nonce": nonce,
        "signature": signature
    }

    encoded = b64url_encode(
        json.dumps(
            payload,
            separators=(",", ":")
        ).encode("utf-8")
    )

    return encoded


def verify_challenge(challenge):
    if not ATTESTATION_SECRET:
        return False, None, "Attestation secret is not configured."

    try:
        decoded = b64url_decode(challenge)

        payload = json.loads(
            decoded.decode("utf-8")
        )

    except Exception:
        return False, None, "Invalid challenge."

    timestamp = payload.get("timestamp")
    oculus_id = payload.get("oculus_id")
    nonce = payload.get("nonce")
    signature = payload.get("signature")

    if not timestamp or not oculus_id:
        return False, None, "Invalid challenge data."

    if not nonce or not signature:
        return False, None, "Invalid challenge data."

    try:
        timestamp = int(timestamp)
    except Exception:
        return False, None, "Invalid challenge timestamp."

    age = int(time.time()) - timestamp

    if age < 0 or age > CHALLENGE_MAX_AGE:
        return False, None, "Challenge expired."

    expected_signature = sign_challenge(
        timestamp,
        oculus_id,
        nonce
    )

    if not hmac.compare_digest(
        str(signature),
        str(expected_signature)
    ):
        return False, None, "Invalid challenge signature."

    return True, {
        "timestamp": timestamp,
        "oculus_id": str(oculus_id),
        "nonce": str(nonce)
    }, None


def decode_attestation_claims(claims):
    try:
        decoded = b64url_decode(claims)

        return json.loads(
            decoded.decode("utf-8")
        )

    except Exception:
        return None


def verify_meta_attestation(
    token,
    challenge_nonce,
    oculus_id
):
    if not game_info.ApiKey:
        return False, "Meta API key is not configured.", {}

    if not token:
        return False, "Missing attestation token.", {}

    if not challenge_nonce:
        return False, "Missing challenge nonce.", {}

    if not oculus_id:
        return False, "Missing Oculus ID.", {}

    valid_challenge, challenge_data, challenge_error = (
        verify_challenge(challenge_nonce)
    )

    if not valid_challenge:
        return False, challenge_error, {}

    if str(challenge_data["oculus_id"]) != str(oculus_id):
        return False, "Oculus ID does not match challenge.", {}

    try:
        response = requests.get(
            "https://graph.oculus.com/platform_integrity/verify",
            params={
                "token": token,
                "access_token": game_info.ApiKey
            },
            timeout=15
        )

    except Exception as e:
        return False, (
            "Meta verification request failed: "
            + str(e)
        ), {}

    if response.status_code != 200:
        return False, (
            "Meta verification returned HTTP "
            + str(response.status_code)
        ), {}

    try:
        body = response.json()
    except Exception:
        return False, "Invalid Meta verification response.", {}

    data = body.get("data")

    if not isinstance(data, list) or not data:
        return False, "Meta returned no attestation data.", {}

    result = data[0]

    if result.get("message") != "success":
        return False, "Meta attestation was rejected.", result

    claims = result.get("claims")

    if not claims:
        return False, "Meta returned no claims.", result

    claims_data = decode_attestation_claims(
        claims
    )

    if not isinstance(claims_data, dict):
        return False, "Invalid attestation claims.", result

    request_details = claims_data.get(
        "request_details"
    )

    app_state = claims_data.get(
        "app_state"
    )

    device_state = claims_data.get(
        "device_state"
    )

    if not isinstance(request_details, dict):
        return False, "Missing request details.", claims_data

    if not isinstance(app_state, dict):
        return False, "Missing app state.", claims_data

    if not isinstance(device_state, dict):
        return False, "Missing device state.", claims_data

    attestation_nonce = request_details.get(
        "nonce"
    )

    if attestation_nonce != challenge_data["nonce"]:
        return False, "Attestation nonce mismatch.", claims_data

    expiration = request_details.get(
        "expiration"
    )

    if expiration is not None:
        try:
            if float(expiration) <= time.time():
                return False, "Attestation token expired.", claims_data
        except Exception:
            return False, "Invalid attestation expiration.", claims_data

    timestamp = request_details.get(
        "timestamp"
    )

    if timestamp is not None:
        try:
            timestamp = float(timestamp)

            if abs(time.time() - timestamp) > ATTESTATION_MAX_AGE:
                return False, "Attestation timestamp is too old.", claims_data

        except Exception:
            return False, "Invalid attestation timestamp.", claims_data

    package_id = app_state.get(
        "package_id"
    )

    if package_id != META_PACKAGE_ID:
        return False, (
            "Package ID mismatch."
        ), claims_data

    certificate_digests = app_state.get(
        "package_cert_sha256_digest"
    )

    if isinstance(certificate_digests, str):
        certificate_digests = [
            certificate_digests
        ]

    if not isinstance(certificate_digests, list):
        return False, (
            "Missing package certificate."
        ), claims_data

    normalized_expected = (
        META_CERT_SHA256
        .replace(":", "")
        .replace(" ", "")
        .lower()
    )

    certificate_match = False

    for certificate in certificate_digests:
        normalized_certificate = (
            str(certificate)
            .replace(":", "")
            .replace(" ", "")
            .lower()
        )

        if hmac.compare_digest(
            normalized_certificate,
            normalized_expected
        ):
            certificate_match = True
            break

    if not certificate_match:
        return False, (
            "Package certificate mismatch."
        ), claims_data

    app_integrity_state = app_state.get(
        "app_integrity_state"
    )

    if app_integrity_state != "StoreRecognized":
        return False, (
            "App is not StoreRecognized."
        ), claims_data

    device_integrity_state = device_state.get(
        "device_integrity_state"
    )

    if device_integrity_state not in (
        "Advanced",
        "Basic"
    ):
        return False, (
            "Device integrity check failed."
        ), claims_data

    device_ban = device_state.get(
        "device_ban"
    )

    if isinstance(device_ban, dict):
        if device_ban.get("is_banned") is True:
            return False, (
                "Device is banned."
            ), claims_data

    return True, "Attestation verified.", {
        "package_id": package_id,
        "app_integrity_state": app_integrity_state,
        "device_integrity_state": device_integrity_state
    }


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "success": True,
        "name": "Oculus Tag Backend",
        "status": "online"
    })


@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message": "Oculus Tag Backend"
    })


@app.route("/api/GetServerStatus", methods=["GET"])
def get_server_status():
    return jsonify({
        "success": True,
        "status": "online",
        "title_id": game_info.TitleId
    })


@app.route("/api/GetServerConfig", methods=["GET"])
def get_server_config():
    return jsonify({
        "success": True,
        "TitleId": game_info.TitleId,
        "PlayFabTitleId": game_info.TitleId,
        "Backend": BACKEND_URL
    })


@app.route("/api/attestation/challenge", methods=["GET"])
def attestation_challenge():
    oculus_id = request.args.get("OculusId", "").strip()

    if not oculus_id:
        return jsonify({
            "success": False,
            "error": "OculusId is required."
        }), 400

    try:
        challenge = generate_challenge(oculus_id)

        send_discord_log(
            f"New Attestation Nonce\n"
            f"Oculus ID: `{oculus_id}`\n"
            f"Challenge created successfully."
        )

        return jsonify({
            "success": True,
            "challenge_nonce": challenge
        })

    except Exception:
        logging.exception("Attestation challenge failed")

        return jsonify({
            "success": False,
            "error": "Internal Server Error"
        }), 500

@app.route("/api/attestation/verify", methods=["POST"])
def attestation_verify():
    data = json_body()

    token = get_value(
        data,
        "attestation_token",
        "token"
    )

    challenge_nonce = get_value(
        data,
        "challenge_nonce",
        "nonce"
    )

    oculus_id = get_value(
        data,
        "OculusId",
        "oculusId",
        "oculus_id"
    )

    if not token:
        return jsonify({
            "verified": False,
            "error": "Missing attestation token."
        }), 400

    if not challenge_nonce:
        return jsonify({
            "verified": False,
            "error": "Missing challenge nonce."
        }), 400

    if not oculus_id:
        return jsonify({
            "verified": False,
            "error": "Missing OculusId."
        }), 400

    verified, message, details = verify_meta_attestation(
        token,
        challenge_nonce,
        oculus_id
    )

    if verified:
        attestation_log(
            "Verification successful for Oculus ID "
            + str(oculus_id)
        )

        discord_log(
            "Meta Attestation Verified",
            "Oculus ID: " + str(oculus_id)
        )

        return jsonify({
            "verified": True,
            "success": True,
            "message": message,
            "details": details
        })

    attestation_log(
        "Verification rejected for Oculus ID "
        + str(oculus_id)
        + ": "
        + str(message)
    )

    discord_log(
        "Meta Attestation Rejected",
        "Oculus ID: "
        + str(oculus_id)
        + "\nReason: "
        + str(message)
    )

    return jsonify({
        "verified": False,
        "success": False,
        "error": message
    }), 403


@app.route("/api/PlayFabAuthentication", methods=["POST"])
def playfab_authentication():
    data = json_body()

    nonce = get_value(
        data,
        "Nonce",
        "nonce"
    )

    app_id = get_value(
        data,
        "AppId",
        "AppID",
        "appId"
    )

    platform = get_value(
        data,
        "Platform",
        "platform"
    )

    oculus_id = get_value(
        data,
        "OculusId",
        "oculusId",
        "oculus_id"
    )

    attestation_token = get_value(
        data,
        "attestation_token",
        "AttestationToken"
    )

    challenge_nonce = get_value(
        data,
        "challenge_nonce",
        "ChallengeNonce"
    )

    if not app_id:
        return jsonify({
            "success": False,
            "error": "Missing AppId."
        }), 400

    if str(app_id) != game_info.TitleId:
        return jsonify({
            "success": False,
            "error": "Invalid AppId."
        }), 403

    if not oculus_id:
        return jsonify({
            "success": False,
            "error": "Missing OculusId."
        }), 400

    if not nonce:
        return jsonify({
            "success": False,
            "error": "Missing Nonce."
        }), 400

    if not platform:
        return jsonify({
            "success": False,
            "error": "Missing Platform."
        }), 400

    verified = False

    if attestation_token and challenge_nonce:
        verified, reason, _ = verify_meta_attestation(
            attestation_token,
            challenge_nonce,
            oculus_id
        )

        if not verified:
            discord_log(
                "PlayFab Authentication Rejected",
                "Oculus ID "
                + str(oculus_id)
                + " failed Meta attestation.\nReason: "
                + str(reason)
            )

            return jsonify({
                "success": False,
                "error": "AttestationRequired",
                "message": reason
            }), 403

    else:
        return jsonify({
            "success": False,
            "error": "AttestationRequired",
            "message": (
                "A valid Meta attestation token and "
                "challenge are required."
            )
        }), 403

    if not verified:
        return jsonify({
            "success": False,
            "error": "AttestationRequired"
        }), 403

    server_custom_id = (
        "OCULUS"
        + str(oculus_id)
    )

    status, result = playfab_request(
        "/Server/LoginWithServerCustomId",
        {
            "ServerCustomId": server_custom_id,
            "CreateAccount": True,
            "InfoRequestParameters": {
                "GetUserAccountInfo": True,
                "GetUserInventory": True,
                "GetUserVirtualCurrency": True
            }
        }
    )

    if status is None:
        return jsonify({
            "success": False,
            "error": "PlayFab request failed.",
            "details": result
        }), 500

    if status < 200 or status >= 300:
        discord_log(
            "PlayFab Authentication Failed",
            "Oculus ID: "
            + str(oculus_id)
            + "\nResponse: "
            + json.dumps(result)
        )

        return jsonify({
            "success": False,
            "error": "PlayFabAuthenticationFailed",
            "details": result
        }), 403

    info = result.get(
        "data",
        {}
    )

    session_ticket = info.get(
        "SessionTicket"
    )

    playfab_id = info.get(
        "PlayFabId"
    )

    entity_token = info.get(
        "EntityToken"
    )

    entity = {}

    if isinstance(entity_token, dict):
        entity = entity_token.get(
            "Entity",
            {}
        )

    discord_log(
        "PlayFab Authentication Success",
        "Oculus ID: "
        + str(oculus_id)
        + "\nPlayFab ID: "
        + str(playfab_id)
    )

    return jsonify({
        "success": True,
        "PlayFabId": playfab_id,
        "SessionTicket": session_ticket,
        "EntityToken": entity_token,
        "EntityId": entity.get("Id"),
        "EntityType": entity.get("Type"),
        "SessionId": str(uuid.uuid4())
    })


@app.route("/api/CachePlayFabId", methods=["POST"])
def cache_playfab_id():
    data = json_body()

    oculus_id = get_value(
        data,
        "OculusId",
        "oculusId",
        "oculus_id"
    )

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    if not oculus_id or not playfab_id:
        return jsonify({
            "success": False,
            "error": "OculusId and PlayFabId are required."
        }), 400

    return jsonify({
        "success": True,
        "OculusId": oculus_id,
        "PlayFabId": playfab_id
    })


@app.route("/api/TitleData", methods=["POST", "GET"])
def title_data():
    status, result = playfab_request(
        "/Server/GetTitleData",
        {}
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/GetAcceptedAgreements", methods=["POST"])
def get_accepted_agreements():
    data = json_body()

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId."
        }), 400

    status, result = playfab_request(
        "/Server/GetUserReadOnlyData",
        {
            "PlayFabId": playfab_id,
            "Keys": [
                "AcceptedAgreements"
            ]
        }
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/SubmitAcceptedAgreements", methods=["POST"])
def submit_accepted_agreements():
    data = json_body()

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    agreements = get_value(
        data,
        "AcceptedAgreements",
        "acceptedAgreements"
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId."
        }), 400

    status, result = playfab_request(
        "/Server/UpdateUserData",
        {
            "PlayFabId": playfab_id,
            "Data": {
                "AcceptedAgreements": (
                    json.dumps(agreements)
                    if not isinstance(agreements, str)
                    else agreements
                )
            }
        }
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/v2/GetName", methods=["POST"])
def get_name():
    data = json_body()

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId."
        }), 400

    status, result = playfab_request(
        "/Server/GetUserAccountInfo",
        {
            "PlayFabId": playfab_id
        }
    )

    if status is None:
        return jsonify(result), 500

    account = (
        result.get("data", {})
        .get("UserInfo", {})
    )

    return jsonify({
        "success": True,
        "PlayFabId": playfab_id,
        "DisplayName": account.get(
            "TitleInfo",
            {}
        ).get(
            "DisplayName"
        )
    })


@app.route("/api/GetInventory", methods=["POST"])
def get_inventory():
    data = json_body()

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId."
        }), 400

    status, result = playfab_request(
        "/Server/GetUserInventory",
        {
            "PlayFabId": playfab_id
        }
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/GetLeaderboard", methods=["POST"])
def get_leaderboard():
    data = json_body()

    statistic_name = get_value(
        data,
        "StatisticName",
        "statisticName"
    )

    if not statistic_name:
        statistic_name = "Score"

    status, result = playfab_request(
        "/Server/GetLeaderboard",
        {
            "StatisticName": statistic_name,
            "StartPosition": 0,
            "MaxResultsCount": 100
        }
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/UpdateStats", methods=["POST"])
def update_stats():
    data = json_body()

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    statistics = data.get(
        "Statistics"
    )

    if not playfab_id or not isinstance(statistics, list):
        return jsonify({
            "success": False,
            "error": "PlayFabId and Statistics are required."
        }), 400

    status, result = playfab_request(
        "/Server/UpdatePlayerStatistics",
        {
            "PlayFabId": playfab_id,
            "Statistics": statistics
        }
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/UpdateProfile", methods=["POST"])
def update_profile():
    data = json_body()

    playfab_id = get_value(
        data,
        "PlayFabId",
        "playFabId",
        "playfab_id"
    )

    display_name = get_value(
        data,
        "DisplayName",
        "displayName"
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId."
        }), 400

    payload = {
        "PlayFabId": playfab_id
    }

    if display_name:
        payload["DisplayName"] = display_name

    status, result = playfab_request(
        "/Server/UpdateUserTitleDisplayName",
        payload
    )

    if status is None:
        return jsonify(result), 500

    return jsonify(result), status


@app.route("/api/GetCosmetics", methods=["POST", "GET"])
def get_cosmetics():
    return jsonify({
        "success": True,
        "items": REDEEMABLE_ITEMS
    })


@app.route("/api/EquipCosmetic", methods=["POST"])
def equip_cosmetic():
    data = json_body()

    cosmetic = get_value(
        data,
        "Cosmetic",
        "cosmetic",
        "ItemId",
        "itemId"
    )

    if not cosmetic:
        return jsonify({
            "success": False,
            "error": "Missing cosmetic."
        }), 400

    return jsonify({
        "success": True,
        "Cosmetic": cosmetic
    })


@app.route("/api/ReportPlayer", methods=["POST"])
def report_player():
    data = json_body()

    reporter = get_value(
        data,
        "Reporter",
        "reporter",
        "OculusId",
        "oculusId"
    )

    reported = get_value(
        data,
        "ReportedPlayer",
        "reportedPlayer",
        "PlayerId",
        "playerId"
    )

    reason = get_value(
        data,
        "Reason",
        "reason"
    )

    discord_log(
        "Player Report",
        "Reporter: "
        + str(reporter)
        + "\nReported: "
        + str(reported)
        + "\nReason: "
        + str(reason)
    )

    return jsonify({
        "success": True
    })


@app.route("/api/CreateParty", methods=["POST"])
def create_party():
    party_id = str(uuid.uuid4())

    return jsonify({
        "success": True,
        "PartyId": party_id
    })


@app.route("/api/JoinParty", methods=["POST"])
def join_party():
    data = json_body()

    party_id = get_value(
        data,
        "PartyId",
        "partyId"
    )

    if not party_id:
        return jsonify({
            "success": False,
            "error": "Missing PartyId."
        }), 400

    return jsonify({
        "success": True,
        "PartyId": party_id
    })


@app.route("/api/LeaveParty", methods=["POST"])
def leave_party():
    return jsonify({
        "success": True
    })


@app.route("/PathCreate", methods=["POST"])
def path_create():
    return jsonify({
        "success": True,
        "PathId": str(uuid.uuid4())
    })


@app.route("/PathJoin", methods=["POST"])
def path_join():
    return jsonify({
        "success": True
    })


@app.route("/PathLeave", methods=["POST"])
def path_leave():
    return jsonify({
        "success": True
    })


@app.route("/PathClose", methods=["POST"])
def path_close():
    return jsonify({
        "success": True
    })


@app.route("/PathRaiseEvent", methods=["POST"])
def path_raise_event():
    return jsonify({
        "success": True
    })


@app.route("/PathSetProperties", methods=["POST"])
def path_set_properties():
    return jsonify({
        "success": True
    })


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
    return jsonify({
        "success": False,
        "error": "Internal Server Error"
    }), 500


@app.after_request
def after_request(response):
    path = request.path

    if (
        path.startswith("/api/")
        and path not in (
            "/api/attestation/challenge",
            "/api/attestation/verify"
        )
    ):
        game_log(
            request.method
            + " "
            + path
            + " -> "
            + str(response.status_code)
        )

    return response


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=9080,
        debug=False
    )
