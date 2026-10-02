import os
import time
import random
import uuid
import logging
import secrets
import base64
import json
import requests

from flask import Flask, jsonify, request


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OculusTag")

app = Flask(__name__)


class GameInfo:
    def __init__(self):
        self.TitleId: str = "C7E30"
        self.SecretKey: str = "PEKS8YH8HTOAYATD93F4MS4N4BXNKOBRY4PJMYPWCYOOYE7I78"
        self.ApiKey: str = "OC|1368813259653754|673098554f8a983dc591cf6114427955"

    def get_auth_headers(self):
        return {
            "content-type": "application/json",
            "X-SecretKey": self.SecretKey
        }


game = GameInfo()


# Meta application information
# Put your real values here manually.
META_PACKAGE_ID = "com.harmonystudios.oculustaggers"
META_CERT_SHA256 = "5dc7570563e442b4fcb1595e61d957f4dd19e1793514592c6e52eef31157f25e"


# Discord webhook can remain an environment variable.
ATTESTATION_WEBHOOK_URL = os.environ.get(
    "ATTESTATION_WEBHOOK_URL",
    ""
)


START_TIME = time.time()

# Temporary in-memory challenge storage.
NONCES = {}

# Temporary PlayFab ID cache.
CACHED_PLAYFAB_IDS = {}

# Users that have successfully passed Meta attestation.
#
# {
#     "oculus_id": {
#         "verified": True,
#         "verified_at": 1234567890
#     }
# }
#
# NOTE:
# Vercel serverless instances are not guaranteed to share
# this dictionary. A persistent database/KV store is recommended
# for production.
VERIFIED_USERS = {}


ATTESTATION_EXPIRATION = 600


REDEEMABLE_ITEMS = [
    "cosmetic1",
    "cosmetic2",
    "cosmetic3",
    "bundle1",
    "skin1",
    "hat1",
    "gloves1"
]


def validate_input(data, fields):
    if not isinstance(data, dict):
        return fields

    return [
        field
        for field in fields
        if data.get(field) is None or data.get(field) == ""
    ]


def game_log(title, message):
    logger.info("%s: %s", title, message)

    if not ATTESTATION_WEBHOOK_URL:
        return

    payload = {
        "embeds": [
            {
                "title": title,
                "description": str(message)[:3900],
                "footer": {
                    "text": "Oculus Tag Backend"
                },
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime()
                )
            }
        ]
    }

    try:
        requests.post(
            ATTESTATION_WEBHOOK_URL,
            json=payload,
            timeout=5
        )
    except Exception as e:
        logger.warning(
            "Discord logging failed: %s",
            e
        )


def mask_secret(value, visible=12):
    if not value:
        return ""

    value = str(value)

    if len(value) <= visible * 2:
        return "*" * len(value)

    return (
        value[:visible]
        + "..."
        + value[-visible:]
    )


def attestation_log(title, fields=None):
    fields = fields or {}

    lines = [
        f"**{title}**"
    ]

    for key, value in fields.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(
                value,
                indent=2
            )

        lines.append(
            f"**{key}:**"
        )

        lines.append(
            f"```text\n{value}\n```"
        )

    game_log(
        title,
        "\n".join(lines)
    )


def playfab_headers():
    return game.get_auth_headers()


def playfab_request(endpoint, payload):
    if not game.TitleId or not game.SecretKey:
        return None, {
            "error": "PlayFab backend credentials are not configured."
        }

    if game.SecretKey == "YOUR_NEW_PLAYFAB_SECRET_KEY":
        return None, {
            "error": "PlayFab SecretKey has not been configured."
        }

    url = (
        f"https://{game.TitleId}.playfabapi.com"
        f"{endpoint}"
    )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=playfab_headers(),
            timeout=15
        )

        try:
            data = response.json()
        except ValueError:
            data = {
                "errorMessage": response.text
            }

        return response, data

    except requests.RequestException as e:
        logger.error(
            "PlayFab request failed: %s",
            e
        )

        return None, {
            "errorMessage": "Unable to contact PlayFab."
        }


def cloud_script(function_name, parameters=None, playfab_id=None):
    payload = {
        "FunctionName": function_name,
        "FunctionParameter": parameters or {}
    }

    if playfab_id:
        payload["PlayFabId"] = playfab_id

    response, data = playfab_request(
        "/Server/ExecuteCloudScript",
        payload
    )

    if response is None:
        return jsonify(data), 502

    result = data.get(
        "data",
        {}
    ).get(
        "FunctionResult",
        {}
    )

    return jsonify(result), response.status_code


def generate_nonce(userid=None):
    nonce = secrets.token_urlsafe(32)

    NONCES[nonce] = {
        "created": time.time(),
        "used": False,
        "userid": str(userid or "Unknown")
    }

    return nonce


def cleanup_nonces():
    now = time.time()

    expired = []

    for nonce, data in list(NONCES.items()):
        if now - data["created"] > ATTESTATION_EXPIRATION:
            expired.append(nonce)

    for nonce in expired:
        NONCES.pop(nonce, None)


def cleanup_verified_users():
    now = time.time()

    expired = []

    for userid, data in list(VERIFIED_USERS.items()):
        verified_at = data.get("verified_at", 0)

        if now - verified_at > ATTESTATION_EXPIRATION:
            expired.append(userid)

    for userid in expired:
        VERIFIED_USERS.pop(userid, None)


def mark_user_verified(userid):
    if not userid:
        return

    userid = str(userid)

    VERIFIED_USERS[userid] = {
        "verified": True,
        "verified_at": time.time()
    }


def is_user_verified(userid):
    if not userid:
        return False

    cleanup_verified_users()

    userid = str(userid)

    verification = VERIFIED_USERS.get(userid)

    if not verification:
        return False

    if verification.get("verified") is not True:
        return False

    verified_at = verification.get(
        "verified_at",
        0
    )

    if time.time() - verified_at > ATTESTATION_EXPIRATION:
        VERIFIED_USERS.pop(
            userid,
            None
        )

        return False

    return True


def decode_base64url_json(value):
    try:
        padding = "=" * (
            (-len(value)) % 4
        )

        decoded = base64.urlsafe_b64decode(
            value + padding
        )

        return json.loads(
            decoded.decode("utf-8")
        )

    except Exception:
        return None


@app.before_request
def api_request_start():
    if request.path.startswith("/api/"):
        request.api_start_time = time.perf_counter()


@app.after_request
def api_request_log(response):
    if not request.path.startswith("/api/"):
        return response

    start_time = getattr(
        request,
        "api_start_time",
        time.perf_counter()
    )

    elapsed_ms = (
        time.perf_counter() - start_time
    ) * 1000

    ip = request.headers.get(
        "X-Forwarded-For",
        request.remote_addr or "Unknown"
    ).split(",")[0].strip()

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):
        data = {}

    playfab_id = (
        data.get("PlayFabId")
        or data.get("playfabId")
        or data.get("UserId")
        or data.get("UserID")
        or "Unknown"
    )

    username = (
        data.get("Username")
        or data.get("username")
        or data.get("DisplayName")
        or data.get("Name")
        or "Unknown"
    )

    if playfab_id == "Unknown":
        cached = CACHED_PLAYFAB_IDS.get(
            str(data.get("OculusId", ""))
        )

        if cached:
            playfab_id = cached.get(
                "PlayFabId",
                "Unknown"
            )

    game_log(
        "API Request",
        f"**Endpoint:** `{request.path}`\n"
        f"**Method:** `{request.method}`\n"
        f"**IP:** `{ip}`\n"
        f"**Status:** `{response.status_code}`\n"
        f"**PlayFab ID:** `{playfab_id}`\n"
        f"**Username:** `{username}`\n"
        f"**Request Time:** `{elapsed_ms:.2f} ms`"
    )

    return response


@app.route("/", methods=["GET", "POST"])
def home():
    return jsonify({
        "success": True,
        "status": "online",
        "name": "Oculus Tag Backend",
        "version": "1.0.0"
    })


@app.route("/api/test", methods=["GET", "POST"])
def test():
    return jsonify({
        "success": True,
        "message": "Oculus Tag Backend is working."
    })


@app.route("/api/GetServerStatus", methods=["GET"])
def get_server_status():
    return jsonify({
        "status": "online",
        "server": "Oculus Tag Backend",
        "uptime": int(time.time() - START_TIME),
        "activePlayers": 0
    })


@app.route("/api/GetServerConfig", methods=["GET"])
def get_server_config():
    return jsonify({
        "version": "1.0.0",
        "maintenance": False,
        "regions": [
            "US",
            "EU",
            "AS"
        ],
        "maxPlayers": 8,
        "gameMode": "Tag"
    })


@app.route("/api/CachePlayFabId", methods=["POST"])
def cache_playfab_id():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = data.get("PlayFabId")
    oculus_id = data.get("OculusId")

    if not playfab_id:
        return jsonify({
            "error": "Missing PlayFabId"
        }), 400

    cache_key = str(
        oculus_id
        if oculus_id
        else playfab_id
    )

    CACHED_PLAYFAB_IDS[cache_key] = {
        "PlayFabId": playfab_id,
        "Username": data.get(
            "Username",
            data.get("DisplayName")
        ),
        "cached": time.time()
    }

    game_log(
        "PlayFab ID Cached",
        f"PlayFab ID: `{playfab_id}`\n"
        f"Oculus ID: `{oculus_id or 'Unknown'}`"
    )

    return jsonify({
        "Message": "Success",
        "PlayFabId": playfab_id
    })


@app.route(
    "/api/attestation/challenge",
    methods=["GET", "POST"]
)
def attestation_challenge():
    cleanup_nonces()

    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(data, dict):
        data = {}

    userid = (
        data.get("userid")
        or data.get("UserId")
        or data.get("UserID")
        or data.get("OculusId")
        or data.get("oculus_id")
        or request.args.get("userid")
        or request.args.get("UserId")
        or request.args.get("OculusId")
        or "Unknown"
    )

    nonce = generate_nonce(userid)

    # Deliberately no Discord logging here.
    # Simply requesting a challenge does not mean
    # that the user has successfully authenticated.

    return jsonify({
        "success": True,
        "challenge_nonce": nonce
    })


@app.route(
    "/api/attestation/verify",
    methods=["POST"]
)
def attestation_verify():
    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(data, dict):
        data = {}

    token = data.get(
        "attestation_token"
    )

    challenge_nonce = data.get(
        "challenge_nonce"
    )

    if not token:
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Missing attestation_token"
        }), 400

    if not challenge_nonce:
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Missing challenge_nonce"
        }), 400

    nonce_data = NONCES.get(
        challenge_nonce
    )

    if not nonce_data:
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Invalid or expired challenge nonce"
        }), 401

    if nonce_data.get("used"):
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Challenge nonce already used"
        }), 401

    if (
        time.time()
        - nonce_data["created"]
        > ATTESTATION_EXPIRATION
    ):
        NONCES.pop(
            challenge_nonce,
            None
        )

        return jsonify({
            "success": False,
            "verified": False,
            "error": "Challenge nonce expired"
        }), 401

    userid = str(
        nonce_data.get(
            "userid",
            data.get(
                "userid",
                data.get(
                    "UserId",
                    data.get(
                        "OculusId",
                        "Unknown"
                    )
                )
            )
        )
    )

    if userid == "Unknown":
        supplied_userid = (
            data.get("OculusId")
            or data.get("userid")
            or data.get("UserId")
        )

        if supplied_userid:
            userid = str(supplied_userid)

    attestation_log(
        "ATTESTATION VERIFY",
        {
            "userid": userid,
            "nonce": challenge_nonce,
            "attestation token": mask_secret(token)
        }
    )

    if not game.ApiKey:
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Meta ApiKey is not configured"
        }), 500

    if game.ApiKey == "YOUR_NEW_META_API_KEY":
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Meta ApiKey has not been configured"
        }), 500

    try:
        response = requests.get(
            "https://graph.oculus.com/platform_integrity/verify",
            params={
                "token": token,
                "access_token": game.ApiKey
            },
            timeout=15
        )

        try:
            result = response.json()
        except ValueError:
            game_log(
                "Attestation Error",
                "Meta returned an invalid JSON response."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Invalid response from Meta"
            }), 502

        if response.status_code != 200:
            game_log(
                "Attestation Rejected",
                "Meta rejected the Quest attestation token."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Meta rejected the attestation token"
            }), 401

        meta_data = result.get(
            "data"
        )

        if not isinstance(meta_data, list):
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Invalid Meta verification response"
            }), 401

        if not meta_data:
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Meta returned no verification data"
            }), 401

        verification = meta_data[0]

        verification_message = verification.get(
            "message"
        )

        if verification_message != "success":
            game_log(
                "Attestation Rejected",
                "Meta verification failed."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": (
                    verification_message
                    or "Meta rejected the attestation token"
                )
            }), 401

        claims_encoded = verification.get(
            "claims"
        )

        if not claims_encoded:
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Meta returned no claims"
            }), 401

        claims = decode_base64url_json(
            claims_encoded
        )

        if not claims:
            game_log(
                "Attestation Rejected",
                "Unable to decode Meta attestation claims."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Could not decode attestation claims"
            }), 401

        request_details = claims.get(
            "request_details"
        )

        app_state = claims.get(
            "app_state"
        )

        device_state = claims.get(
            "device_state"
        )

        if not isinstance(
            request_details,
            dict
        ):
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Missing request details"
            }), 401

        if not isinstance(
            app_state,
            dict
        ):
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Missing app state"
            }), 401

        if not isinstance(
            device_state,
            dict
        ):
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Missing device state"
            }), 401

        token_nonce = request_details.get(
            "nonce"
        )

        expiration = request_details.get(
            "exp"
        )

        timestamp = request_details.get(
            "timestamp"
        )

        if token_nonce != challenge_nonce:
            game_log(
                "Attestation Rejected",
                "The attestation nonce did not match."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Attestation nonce mismatch"
            }), 401

        now = int(time.time())

        try:
            expiration = int(expiration)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Invalid attestation expiration"
            }), 401

        try:
            timestamp = int(timestamp)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "verified": False,
                "error": "Invalid attestation timestamp"
            }), 401

        if expiration <= now:
            game_log(
                "Attestation Rejected",
                "The attestation token has expired."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Attestation token expired"
            }), 401

        if abs(now - timestamp) > 300:
            game_log(
                "Attestation Rejected",
                "The attestation timestamp is too old."
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Attestation timestamp is too old"
            }), 401

        package_id = app_state.get(
            "package_id"
        )

        app_integrity_state = app_state.get(
            "app_integrity_state"
        )

        certificate_digests = app_state.get(
            "package_cert_sha256_digest",
            []
        )

        device_integrity_state = device_state.get(
            "device_integrity_state"
        )

        if META_PACKAGE_ID:
            if package_id != META_PACKAGE_ID:
                game_log(
                    "Attestation Rejected",
                    f"Invalid package ID: `{package_id}`"
                )

                return jsonify({
                    "success": False,
                    "verified": False,
                    "error": "Invalid package ID"
                }), 401

        if META_CERT_SHA256:
            expected_cert = (
                META_CERT_SHA256
                .strip()
                .lower()
            )

            if isinstance(
                certificate_digests,
                str
            ):
                certificate_digests = [
                    certificate_digests
                ]

            normalized_certificates = [
                str(cert)
                .strip()
                .lower()
                for cert in certificate_digests
            ]

            if expected_cert not in normalized_certificates:
                game_log(
                    "Attestation Rejected",
                    "The attestation certificate did not match."
                )

                return jsonify({
                    "success": False,
                    "verified": False,
                    "error": "Invalid package certificate"
                }), 401

        if app_integrity_state != "StoreRecognized":
            game_log(
                "Attestation Rejected",
                f"App integrity: `{app_integrity_state}`"
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "App integrity check failed"
            }), 401

        if device_integrity_state not in (
            "Advanced",
            "Basic"
        ):
            game_log(
                "Attestation Rejected",
                f"Device integrity: `{device_integrity_state}`"
            )

            return jsonify({
                "success": False,
                "verified": False,
                "error": "Device integrity check failed"
            }), 401

        device_ban = claims.get(
            "device_ban"
        )

        if isinstance(
            device_ban,
            dict
        ):
            if device_ban.get(
                "is_banned"
            ) is True:
                game_log(
                    "Attestation Rejected",
                    "The attested device is banned."
                )

                return jsonify({
                    "success": False,
                    "verified": False,
                    "error": "Device is banned"
                }), 403

        # Mark this Oculus ID as successfully verified.
        #
        # This is the important part that connects
        # Meta attestation to PlayFab authentication.
        mark_user_verified(userid)

        nonce_data["used"] = True

        attestation_log(
            "ATTESTATION VERIFIED",
            {
                "userid": userid,
                "package": package_id,
                "app integrity": app_integrity_state,
                "device integrity": device_integrity_state
            }
        )

        return jsonify({
            "success": True,
            "verified": True,
            "package_id": package_id,
            "app_integrity_state": app_integrity_state,
            "device_integrity_state": device_integrity_state
        })

    except requests.RequestException as e:
        logger.error(
            "Meta attestation request failed: %s",
            e
        )

        return jsonify({
            "success": False,
            "verified": False,
            "error": "Could not contact Meta"
        }), 502

    except Exception as e:
        logger.exception(
            "Attestation verification failed: %s",
            e
        )

        return jsonify({
            "success": False,
            "verified": False,
            "error": "Internal attestation verification error"
        }), 500


@app.route(
    "/api/PlayFabAuthentication",
    methods=["POST"]
)
def playfab_authentication():
    data = request.get_json(
        silent=True
    ) or {}

    required = [
        "Nonce",
        "AppId",
        "Platform",
        "OculusId"
    ]

    missing = validate_input(
        data,
        required
    )

    if missing:
        return jsonify({
            "Message": (
                "Missing parameter(s): "
                + ", ".join(missing)
            ),
            "Error": "BadRequest-MissingParameter"
        }), 400

    if data["AppId"] != game.TitleId:
        return jsonify({
            "Message": "Request sent for the wrong App ID",
            "Error": "BadRequest-AppIdMismatch"
        }), 400

    oculus_id = str(
        data["OculusId"]
    )

    # ---------------------------------------------------------
    # REQUIRED META ATTESTATION
    # ---------------------------------------------------------
    #
    # PlayFab authentication is blocked unless this Oculus ID
    # has successfully passed Meta attestation first.
    #
    if not is_user_verified(oculus_id):
        game_log(
            "PlayFab Authentication Rejected",
            f"Oculus ID `{oculus_id}` has not passed Meta attestation."
        )

        return jsonify({
            "Error": "AttestationRequired",
            "Message": (
                "Successful Meta attestation is required "
                "before PlayFab authentication."
            )
        }), 403

    login_payload = {
        "ServerCustomId": "OCULUS" + oculus_id,
        "CreateAccount": True
    }

    response, result = playfab_request(
        "/Server/LoginWithServerCustomId",
        login_payload
    )

    if response is None:
        return jsonify(result), 502

    if response.status_code != 200:
        error_message = result.get(
            "errorMessage",
            "PlayFab authentication failed."
        )

        game_log(
            "PlayFab Login Failed",
            f"Oculus ID: `{oculus_id}`\n"
            f"Error: `{error_message}`"
        )

        return jsonify({
            "Error": "PlayFab Error",
            "Message": error_message
        }), response.status_code

    data_result = result.get(
        "data",
        {}
    )

    playfab_id = data_result.get(
        "PlayFabId"
    )

    session_ticket = data_result.get(
        "SessionTicket"
    )

    entity_token_data = data_result.get(
        "EntityToken",
        {}
    )

    entity_token = entity_token_data.get(
        "EntityToken"
    )

    entity = entity_token_data.get(
        "Entity",
        {}
    )

    session_id = str(
        uuid.uuid4()
    )

    CACHED_PLAYFAB_IDS[oculus_id] = {
        "PlayFabId": playfab_id,
        "Username": data.get(
            "Username"
        ),
        "cached": time.time()
    }

    orgscoped_id = (
        data.get("orgscoped_id")
        or data.get("OrgScopedId")
        or data.get("OrgscopedId")
        or data.get("CustomId")
        or oculus_id
    )

    attestation_log(
        "PLAYFAB AUTH SUCCESS",
        {
            "orgscoped id": str(
                orgscoped_id
            ),
            "oculus id": str(
                oculus_id
            ),
            "playfab id": str(
                playfab_id or "Unknown"
            ),
            "entity token": mask_secret(
                entity_token
            ),
            "session ticket": mask_secret(
                session_ticket
            ),
            "platform": data.get(
                "Platform",
                "Quest"
            )
        }
    )

    return jsonify({
        "PlayFabId": playfab_id,
        "SessionTicket": session_ticket,
        "EntityToken": entity_token,
        "EntityId": entity.get("Id"),
        "EntityType": entity.get("Type"),
        "SessionId": session_id
    })


@app.route(
    "/api/TitleData",
    methods=["GET", "POST"]
)
def title_data():
    return jsonify({
        "ServerVersion": "1.0.0",
        "ClientMinVersion": "1.0.0",
        "MOTD": "discord.gg/oculustagg"
    })


@app.route(
    "/api/GetAcceptedAgreements",
    methods=["GET", "POST"]
)
def get_accepted_agreements():
    return jsonify({
        "PrivacyPolicy": "1.1.28",
        "TOS": "11.05.22.2",
        "EULA": "2024.09.20"
    })


@app.route(
    "/api/SubmitAcceptedAgreements",
    methods=["POST"]
)
def submit_accepted_agreements():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = data.get(
        "PlayFabId"
    )

    agreements = data.get(
        "Agreements",
        {}
    )

    if not playfab_id:
        return jsonify({
            "error": "Missing PlayFabId"
        }), 400

    game_log(
        "Agreements Submitted",
        f"PlayFab ID: `{playfab_id}`"
    )

    return jsonify({
        "success": True,
        "Agreements": agreements
    })


@app.route(
    "/api/v2/GetName",
    methods=["GET", "POST"]
)
def get_name():
    adverbs = [
        "Cool",
        "Fine",
        "Bald",
        "Bold",
        "Wild",
        "Brave",
        "Swift",
        "Fierce"
    ]

    nouns = [
        "Gorilla",
        "Chicken",
        "Sloth",
        "King",
        "Queen",
        "Wizard",
        "Rebel",
        "Agent"
    ]

    name = (
        random.choice(adverbs)
        + random.choice(nouns)
        + str(random.randint(1000, 9999))
    )

    return jsonify({
        "result": name
    })


@app.route(
    "/api/GetInventory",
    methods=["POST"]
)
def get_inventory():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = data.get(
        "PlayFabId"
    )

    if not playfab_id:
        return jsonify({
            "error": "Missing PlayFabId"
        }), 400

    return cloud_script(
        "GetUserInventory",
        {},
        playfab_id
    )


@app.route(
    "/api/GetLeaderboard",
    methods=["POST"]
)
def get_leaderboard():
    data = request.get_json(
        silent=True
    ) or {}

    statistic = data.get(
        "StatisticName",
        "GlobalScore"
    )

    return cloud_script(
        "GetLeaderboard",
        {
            "StatisticName": statistic
        },
        data.get("PlayFabId")
    )


@app.route(
    "/api/UpdateStats",
    methods=["POST"]
)
def update_stats():
    data = request.get_json(
        silent=True
    ) or {}

    missing = validate_input(
        data,
        [
            "PlayFabId",
            "Statistics"
        ]
    )

    if missing:
        return jsonify({
            "error": (
                "Missing fields: "
                + ", ".join(missing)
            )
        }), 400

    return cloud_script(
        "UpdatePlayerStatistics",
        data["Statistics"],
        data["PlayFabId"]
    )


@app.route(
    "/api/UpdateProfile",
    methods=["POST"]
)
def update_profile():
    data = request.get_json(
        silent=True
    ) or {}

    missing = validate_input(
        data,
        [
            "PlayFabId",
            "DisplayName"
        ]
    )

    if missing:
        return jsonify({
            "error": (
                "Missing fields: "
                + ", ".join(missing)
            )
        }), 400

    return cloud_script(
        "UpdateUserTitleDisplayName",
        {
            "DisplayName": data["DisplayName"]
        },
        data["PlayFabId"]
    )


@app.route(
    "/api/GetCosmetics",
    methods=["GET", "POST"]
)
def get_cosmetics():
    return jsonify({
        "cosmetics": REDEEMABLE_ITEMS
    })


@app.route(
    "/api/EquipCosmetic",
    methods=["POST"]
)
def equip_cosmetic():
    data = request.get_json(
        silent=True
    ) or {}

    missing = validate_input(
        data,
        [
            "PlayFabId",
            "CosmeticId"
        ]
    )

    if missing:
        return jsonify({
            "error": (
                "Missing fields: "
                + ", ".join(missing)
            )
        }), 400

    cosmetic_id = data["CosmeticId"]

    if cosmetic_id not in REDEEMABLE_ITEMS:
        return jsonify({
            "success": False,
            "error": "Unknown cosmetic"
        }), 400

    game_log(
        "Cosmetic Equipped",
        f"PlayFab ID: `{data['PlayFabId']}`\n"
        f"Cosmetic: `{cosmetic_id}`"
    )

    return jsonify({
        "success": True
    })


@app.route(
    "/api/ReportPlayer",
    methods=["POST"]
)
def report_player():
    data = request.get_json(
        silent=True
    ) or {}

    missing = validate_input(
        data,
        [
            "ReporterId",
            "ReportedId",
            "Reason"
        ]
    )

    if missing:
        return jsonify({
            "error": (
                "Missing fields: "
                + ", ".join(missing)
            )
        }), 400

    game_log(
        "Player Report",
        f"Reporter: `{data['ReporterId']}`\n"
        f"Reported: `{data['ReportedId']}`\n"
        f"Reason: `{data['Reason']}`"
    )

    return jsonify({
        "success": True,
        "message": "Report submitted"
    })


@app.route(
    "/api/CreateParty",
    methods=["POST"]
)
def create_party():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = data.get(
        "PlayFabId"
    )

    if not playfab_id:
        return jsonify({
            "error": "Missing PlayFabId"
        }), 400

    party_id = str(
        uuid.uuid4()
    )

    game_log(
        "Party Created",
        f"PlayFab ID: `{playfab_id}`\n"
        f"Party ID: `{party_id}`"
    )

    return jsonify({
        "success": True,
        "PartyId": party_id
    })


@app.route(
    "/api/JoinParty",
    methods=["POST"]
)
def join_party():
    data = request.get_json(
        silent=True
    ) or {}

    missing = validate_input(
        data,
        [
            "PlayFabId",
            "PartyId"
        ]
    )

    if missing:
        return jsonify({
            "error": (
                "Missing fields: "
                + ", ".join(missing)
            )
        }), 400

    return jsonify({
        "success": True,
        "PartyId": data["PartyId"]
    })


@app.route(
    "/api/LeaveParty",
    methods=["POST"]
)
def leave_party():
    data = request.get_json(
        silent=True
    ) or {}

    missing = validate_input(
        data,
        [
            "PlayFabId",
            "PartyId"
        ]
    )

    if missing:
        return jsonify({
            "error": (
                "Missing fields: "
                + ", ".join(missing)
            )
        }), 400

    return jsonify({
        "success": True,
        "PartyId": data["PartyId"]
    })


@app.route(
    "/PathCreate",
    methods=["POST"]
)
def path_create():
    data = request.get_json(
        silent=True
    ) or {}

    return cloud_script(
        "RoomCreated",
        data,
        data.get("UserId")
    )


@app.route(
    "/PathJoin",
    methods=["POST"]
)
def path_join():
    data = request.get_json(
        silent=True
    ) or {}

    return cloud_script(
        "RoomJoined",
        data,
        data.get("UserId")
    )


@app.route(
    "/PathLeave",
    methods=["POST"]
)
def path_leave():
    data = request.get_json(
        silent=True
    ) or {}

    data["Type"] = "ClientDisconnect"

    return cloud_script(
        "RoomLeft",
        data,
        data.get("UserId")
    )


@app.route(
    "/PathClose",
    methods=["POST"]
)
def path_close():
    data = request.get_json(
        silent=True
    ) or {}

    data["Type"] = "Close"

    return cloud_script(
        "RoomClosed",
        data,
        data.get("UserId")
    )


@app.route(
    "/PathRaiseEvent",
    methods=["POST"]
)
def path_raise_event():
    data = request.get_json(
        silent=True
    ) or {}

    return cloud_script(
        "RoomEventRaised",
        data,
        data.get("UserId")
    )


@app.route(
    "/PathSetProperties",
    methods=["POST"]
)
def path_set_properties():
    data = request.get_json(
        silent=True
    ) or {}

    return cloud_script(
        "RoomPropertyUpdated",
        data,
        data.get("UserId")
    )


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "server": "Oculus Tag Backend"
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "success": False,
        "error": "Method not allowed",
        "server": "Oculus Tag Backend"
    }), 405


@app.errorhandler(500)
def internal_error(error):
    game_log(
        "Backend Error",
        "An internal server error occurred."
    )

    return jsonify({
        "success": False,
        "error": "Internal server error",
        "server": "Oculus Tag Backend"
    }), 500


@app.errorhandler(Exception)
def handle_exception(error):
    logger.exception(
        "Unhandled backend exception: %s",
        error
    )

    return jsonify({
        "success": False,
        "error": "Internal server error",
        "server": "Oculus Tag Backend"
    }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=9080,
        debug=False
    )
