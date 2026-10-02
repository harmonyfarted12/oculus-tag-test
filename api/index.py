import os
import time
import uuid
import logging
import secrets
import base64
import json
import requests

from flask import Flask, jsonify, request

from pymongo import MongoClient
from pymongo.server_api import ServerApi


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OculusTag")


class GameInfo:
    def __init__(self):
        self.TitleId: str = "8A822"

        self.SecretKey: str = os.environ.get(
            "PLAYFAB_SECRET_KEY",
            ""
        )

        self.ApiKey: str = os.environ.get(
            "META_API_KEY",
            ""
        )

    def get_auth_headers(self):
        return {
            "content-type": "application/json",
            "X-SecretKey": self.SecretKey
        }


settings = GameInfo()
app = Flask(__name__)


META_PACKAGE_ID = (
    "com.harmonystudios.oculustaggers"
)

META_CERT_SHA256 = (
    "5dc7570563e442b4fcb1595e61d957f4dd19e179"
    "3514592c6e52eef31157f25e"
)

META_VERIFY_URL = (
    "https://graph.oculus.com/"
    "platform_integrity/verify"
)

META_DEVICE_BAN_URL = (
    "https://graph.oculus.com/"
    "platform_integrity/device_ban"
)

META_DEVICE_BAN_IDS_URL = (
    "https://graph.oculus.com/"
    "platform_integrity/device_ban_ids"
)

META_DEVICE_BAN_STATUS_URL = (
    "https://graph.oculus.com/"
    "platform_integrity/device_ban_status"
)

ATTESTATION_WEBHOOK_URL = os.environ.get(
    "ATTESTATION_WEBHOOK_URL",
    ""
)

MONGODB_URI = os.environ.get(
    "MONGODB_URI",
    ""
)

START_TIME = time.time()

ATTESTATION_TIMESTAMP_MAX_AGE = 300

ATTESTATION_NONCE_TTL = 600

MAX_BAN_MINUTES = 52560000

REDEEMABLE_ITEMS = [
    "ITEM_COSMETIC_1",
    "ITEM_COSMETIC_2",
    "ITEM_COSMETIC_3"
]


mongo_client = None
mongo_database = None
attestation_nonces = None


def game_log(title, message):
    logger.info(
        "%s: %s",
        title,
        message
    )

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


def get_attestation_collection():
    global mongo_client
    global mongo_database
    global attestation_nonces

    if not MONGODB_URI:
        raise RuntimeError(
            "MONGODB_URI is not configured"
        )

    if attestation_nonces is None:
        mongo_client = MongoClient(
            MONGODB_URI,
            server_api=ServerApi(
                "1",
                strict=True,
                deprecation_errors=True
            ),
            serverSelectionTimeoutMS=5000
        )

        mongo_database = mongo_client[
            "oculus_tag"
        ]

        attestation_nonces = mongo_database[
            "attestation_nonces"
        ]

        attestation_nonces.create_index(
            "nonce",
            unique=True
        )

        attestation_nonces.create_index(
            "expires_at",
            expireAfterSeconds=0
        )

    return attestation_nonces


def generate_challenge_nonce():
    return base64.urlsafe_b64encode(
        secrets.token_bytes(16)
    ).decode("ascii").rstrip("=")


def create_attestation_nonce():
    collection = get_attestation_collection()

    nonce = generate_challenge_nonce()

    now = int(time.time())

    collection.insert_one({
        "nonce": nonce,
        "created_at": now,
        "expires_at": (
            now + ATTESTATION_NONCE_TTL
        ),
        "used_at": None
    })

    return nonce


def consume_attestation_nonce(nonce):
    if not nonce:
        return False

    collection = get_attestation_collection()

    now = int(time.time())

    result = collection.find_one_and_update(
        {
            "nonce": nonce,
            "used_at": None,
            "expires_at": {
                "$gt": now
            }
        },
        {
            "$set": {
                "used_at": now
            }
        }
    )

    return result is not None


def decode_base64url_json(value):
    if not isinstance(value, str):
        return None

    try:
        padding = "=" * (-len(value) % 4)

        decoded = base64.urlsafe_b64decode(
            value + padding
        )

        return json.loads(
            decoded.decode("utf-8")
        )

    except Exception:
        return None


def get_meta_access_token():
    return settings.ApiKey.strip()


def verify_meta_attestation(
    attestation_token,
    expected_nonce
):
    if not attestation_token:
        return (
            False,
            "Missing attestation token",
            None
        )

    if not expected_nonce:
        return (
            False,
            "Missing challenge nonce",
            None
        )

    access_token = get_meta_access_token()

    if not access_token:
        return (
            False,
            "Meta access token is not configured",
            None
        )

    try:
        response = requests.get(
            META_VERIFY_URL,
            params={
                "token": attestation_token,
                "access_token": access_token
            },
            timeout=15
        )
    except requests.RequestException:
        logger.exception(
            "Meta attestation request failed"
        )

        return (
            False,
            "Meta attestation request failed",
            None
        )

    try:
        result = response.json()
    except Exception:
        return (
            False,
            "Invalid Meta verification response",
            None
        )

    if response.status_code != 200:
        return (
            False,
            (
                f"Meta verification HTTP "
                f"{response.status_code}"
            ),
            result
        )

    data = result.get("data")

    if not isinstance(data, list) or not data:
        return (
            False,
            "Missing Meta verification data",
            result
        )

    verification = data[0]

    if not isinstance(verification, dict):
        return (
            False,
            "Invalid Meta verification data",
            result
        )

    message = verification.get(
        "message"
    )

    if message != "success":
        if message == "invalid signature":
            return (
                False,
                "Invalid attestation signature",
                result
            )

        if message == "token expired":
            return (
                False,
                "Attestation token expired",
                result
            )

        return (
            False,
            f"Meta verification failed: {message}",
            result
        )

    encoded_claims = verification.get(
        "claims"
    )

    if not encoded_claims:
        return (
            False,
            "Missing attestation claims",
            result
        )

    claims = decode_base64url_json(
        encoded_claims
    )

    if not isinstance(claims, dict):
        return (
            False,
            "Invalid Base64URL claims",
            result
        )

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
        return (
            False,
            "Missing request_details",
            claims
        )

    if not isinstance(
        app_state,
        dict
    ):
        return (
            False,
            "Missing app_state",
            claims
        )

    if not isinstance(
        device_state,
        dict
    ):
        return (
            False,
            "Missing device_state",
            claims
        )

    claim_nonce = request_details.get(
        "nonce"
    )

    if not claim_nonce:
        return (
            False,
            "Missing attestation nonce",
            claims
        )

    if claim_nonce != expected_nonce:
        return (
            False,
            "Attestation nonce mismatch",
            claims
        )

    timestamp = request_details.get(
        "timestamp"
    )

    if timestamp is None:
        return (
            False,
            "Missing attestation timestamp",
            claims
        )

    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError):
        return (
            False,
            "Invalid attestation timestamp",
            claims
        )

    current_time = int(time.time())

    if (
        abs(
            current_time - timestamp
        )
        > ATTESTATION_TIMESTAMP_MAX_AGE
    ):
        return (
            False,
            "Attestation timestamp is too old",
            claims
        )

    expiration = request_details.get(
        "exp"
    )

    if expiration is None:
        return (
            False,
            "Missing attestation expiration",
            claims
        )

    try:
        expiration = int(expiration)
    except (TypeError, ValueError):
        return (
            False,
            "Invalid attestation expiration",
            claims
        )

    if current_time >= expiration:
        return (
            False,
            "Attestation token expired",
            claims
        )

    app_integrity_state = app_state.get(
        "app_integrity_state"
    )

    if app_integrity_state != "StoreRecognized":
        return (
            False,
            (
                "App integrity failed: "
                f"{app_integrity_state}"
            ),
            claims
        )

    package_id = app_state.get(
        "package_id"
    )

    if package_id != META_PACKAGE_ID:
        return (
            False,
            "Package ID mismatch",
            claims
        )

    certificate_digests = app_state.get(
        "package_cert_sha256_digest"
    )

    if not isinstance(
        certificate_digests,
        list
    ):
        return (
            False,
            "Missing package certificate digest",
            claims
        )

    normalized_digests = [
        str(value)
        .replace(":", "")
        .lower()
        for value in certificate_digests
    ]

    if (
        META_CERT_SHA256.lower()
        not in normalized_digests
    ):
        return (
            False,
            "Package certificate mismatch",
            claims
        )

    app_version = app_state.get(
        "version"
    )

    logger.info(
        "Attestation app version: %s",
        app_version
    )

    device_integrity_state = (
        device_state.get(
            "device_integrity_state"
        )
    )

    if device_integrity_state not in (
        "Advanced",
        "Basic"
    ):
        return (
            False,
            (
                "Device integrity failed: "
                f"{device_integrity_state}"
            ),
            claims
        )

    unique_id = device_state.get(
        "unique_id"
    )

    if not unique_id:
        return (
            False,
            "Missing device unique_id",
            claims
        )

    security_update_pending_days = (
        device_state.get(
            "security_update_pending_days"
        )
    )

    logger.info(
        "Security update pending days: %s",
        security_update_pending_days
    )

    device_ban = claims.get(
        "device_ban"
    )

    if device_ban is not None:
        if not isinstance(
            device_ban,
            dict
        ):
            return (
                False,
                "Invalid device_ban",
                claims
            )

        is_banned = device_ban.get(
            "is_banned",
            False
        )

        remaining_ban_time = (
            device_ban.get(
                "remaining_ban_time",
                0
            )
        )

        if is_banned is True:
            game_log(
                "Banned Device",
                (
                    f"**Unique ID:** `{unique_id}`\n"
                    f"**Remaining Ban Time:** "
                    f"`{remaining_ban_time}`"
                )
            )

            return (
                False,
                "Device is banned",
                claims
            )

    return (
        True,
        "Attestation verified",
        claims
    )


def playfab_request(
    endpoint,
    payload
):
    url = (
        f"https://{settings.TitleId}"
        f".playfabapi.com/{endpoint}"
    )

    try:
        response = requests.post(
            url,
            headers=settings.get_auth_headers(),
            json=payload,
            timeout=15
        )
    except requests.RequestException as e:
        logger.exception(
            "PlayFab request failed"
        )

        return 500, {
            "code": 500,
            "error": str(e)
        }

    try:
        result = response.json()
    except Exception:
        result = {
            "code": response.status_code,
            "status": response.text
        }

    return response.status_code, result


def meta_device_ban_request(
    is_banned,
    remaining_time_in_minute,
    unique_id=None,
    ban_id=None
):
    access_token = get_meta_access_token()

    if not access_token:
        return {
            "error": (
                "Meta access token "
                "is not configured"
            )
        }, 500

    if (
        unique_id is None
        and ban_id is None
    ):
        return {
            "error": (
                "unique_id or ban_id "
                "is required"
            )
        }, 400

    try:
        remaining_time_in_minute = int(
            remaining_time_in_minute
        )
    except (TypeError, ValueError):
        return {
            "error": (
                "remaining_time_in_minute "
                "must be an integer"
            )
        }, 400

    if (
        remaining_time_in_minute < 0
        or remaining_time_in_minute > MAX_BAN_MINUTES
    ):
        return {
            "error": (
                "remaining_time_in_minute "
                "must be between 0 and 52560000"
            )
        }, 400

    params = {
        "method": "POST",
        "is_banned": (
            "true"
            if bool(is_banned)
            else "false"
        ),
        "remaining_time_in_minute":
            remaining_time_in_minute,
        "access_token": access_token
    }

    if ban_id:
        params["ban_id"] = ban_id
    else:
        params["unique_id"] = unique_id

    try:
        response = requests.post(
            META_DEVICE_BAN_URL,
            params=params,
            timeout=15
        )
    except requests.RequestException:
        logger.exception(
            "Meta device ban request failed"
        )

        return {
            "error": (
                "Meta device ban request failed"
            )
        }, 500

    try:
        result = response.json()
    except Exception:
        result = {
            "status": response.text
        }

    return result, response.status_code


@app.before_request
def api_request_start():
    if request.path.startswith("/api/"):
        request.api_start_time = (
            time.perf_counter()
        )


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

    game_log(
        "API Request",
        (
            f"**Endpoint:** `{request.path}`\n"
            f"**Method:** `{request.method}`\n"
            f"**IP:** `{ip}`\n"
            f"**Status:** `{response.status_code}`\n"
            f"**PlayFab ID:** `{playfab_id}`\n"
            f"**Username:** `{username}`\n"
            f"**Request Time:** `{elapsed_ms:.2f} ms`"
        )
    )

    return response


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "online",
        "name": "Oculus Tag Backend"
    })


@app.route("/api/test", methods=["GET"])
def api_test():
    return jsonify({
        "success": True,
        "message": "Oculus Tag Backend"
    })


@app.route(
    "/api/GetServerStatus",
    methods=["GET", "POST"]
)
def get_server_status():
    return jsonify({
        "success": True,
        "online": True,
        "server": "Oculus Tag Backend"
    })


@app.route(
    "/api/GetServerConfig",
    methods=["GET", "POST"]
)
def get_server_config():
    return jsonify({
        "success": True,
        "TitleId": settings.TitleId,
        "Server": "Oculus Tag Backend"
    })


@app.route(
    "/api/CachePlayFabId",
    methods=["POST"]
)
def cache_playfab_id():
    return jsonify({
        "success": True
    })


@app.route(
    "/api/attestation/challenge",
    methods=["GET", "POST"]
)
def attestation_challenge():
    try:
        nonce = create_attestation_nonce()

        return jsonify({
            "success": True,
            "challenge_nonce": nonce
        })

    except Exception:
        logger.exception(
            "Failed to create attestation nonce"
        )

        return jsonify({
            "success": False,
            "error": (
                "Failed to create "
                "attestation challenge"
            )
        }), 500


@app.route(
    "/api/attestation/verify",
    methods=["POST"]
)
def attestation_verify():
    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "verified": False,
            "error": "Invalid request"
        }), 400

    attestation_token = (
        data.get("attestation_token")
        or data.get("AttestationToken")
        or data.get("token")
        or data.get("integrity_token")
    )

    challenge_nonce = (
        data.get("challenge_nonce")
        or data.get("nonce")
    )

    if not attestation_token:
        return jsonify({
            "success": False,
            "verified": False,
            "error": (
                "Missing attestation token"
            )
        }), 400

    if not challenge_nonce:
        return jsonify({
            "success": False,
            "verified": False,
            "error": (
                "Missing challenge nonce"
            )
        }), 400

    verified, message, claims = (
        verify_meta_attestation(
            attestation_token,
            challenge_nonce
        )
    )

    if not verified:
        game_log(
            "Attestation Failed",
            f"**Reason:** `{message}`"
        )

        return jsonify({
            "success": False,
            "verified": False,
            "error": message
        }), 401

    if not consume_attestation_nonce(
        challenge_nonce
    ):
        game_log(
            "Attestation Replay Blocked",
            (
                "**Reason:** `Nonce already "
                "used or expired`"
            )
        )

        return jsonify({
            "success": False,
            "verified": False,
            "error": (
                "Challenge nonce has already "
                "been used or expired"
            )
        }), 403

    device_state = claims.get(
        "device_state",
        {}
    )

    app_state = claims.get(
        "app_state",
        {}
    )

    return jsonify({
        "success": True,
        "verified": True,
        "app_integrity_state":
            app_state.get(
                "app_integrity_state"
            ),
        "package_id":
            app_state.get(
                "package_id"
            ),
        "version":
            app_state.get(
                "version"
            ),
        "device_integrity_state":
            device_state.get(
                "device_integrity_state"
            ),
        "unique_id":
            device_state.get(
                "unique_id"
            ),
        "security_update_pending_days":
            device_state.get(
                "security_update_pending_days"
            ),
        "device_ban":
            claims.get("device_ban")
    })


@app.route(
    "/api/PlayFabAuthentication",
    methods=["POST"]
)
def playfab_authentication():
    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "error": "Invalid request"
        }), 400

    oculus_id = (
        data.get("OculusId")
        or data.get("userid")
        or data.get("UserId")
        or data.get("UserID")
    )

    attestation_token = (
        data.get("attestation_token")
        or data.get("AttestationToken")
        or data.get("token")
        or data.get("integrity_token")
    )

    challenge_nonce = (
        data.get("challenge_nonce")
        or data.get("nonce")
    )

    if not oculus_id:
        return jsonify({
            "success": False,
            "error": "Missing OculusId"
        }), 400

    if not attestation_token:
        return jsonify({
            "success": False,
            "error": (
                "Missing attestation token"
            )
        }), 400

    if not challenge_nonce:
        return jsonify({
            "success": False,
            "error": (
                "Missing challenge nonce"
            )
        }), 400

    verified, message, claims = (
        verify_meta_attestation(
            attestation_token,
            challenge_nonce
        )
    )

    if not verified:
        game_log(
            "PlayFab Authentication Failed",
            (
                f"**Oculus ID:** `{oculus_id}`\n"
                f"**Reason:** `{message}`"
            )
        )

        return jsonify({
            "success": False,
            "error": message
        }), 401

    if not consume_attestation_nonce(
        challenge_nonce
    ):
        game_log(
            "Attestation Replay Blocked",
            (
                f"**Oculus ID:** `{oculus_id}`\n"
                f"**Reason:** `Nonce already "
                f"used or expired`"
            )
        )

        return jsonify({
            "success": False,
            "error": (
                "Challenge nonce has already "
                "been used or expired"
            )
        }), 403

    device_state = claims.get(
        "device_state",
        {}
    )

    custom_id = (
        f"OCULUS_{oculus_id}"
    )

    payload = {
        "CustomId": custom_id,
        "CreateAccount": True
    }

    status_code, result = playfab_request(
        "Client/LoginWithCustomID",
        payload
    )

    if status_code >= 400:
        game_log(
            "PlayFab Login Failed",
            (
                f"**Oculus ID:** `{oculus_id}`\n"
                f"**Status:** `{status_code}`"
            )
        )

        return jsonify(result), status_code

    game_log(
        "PlayFab Login",
        (
            f"**Oculus ID:** `{oculus_id}`\n"
            f"**Device Integrity:** "
            f"`{device_state.get('device_integrity_state')}`"
        )
    )

    return jsonify(result), status_code


@app.route(
    "/api/attestation/device-ban",
    methods=["POST"]
)
def device_ban():
    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "error": "Invalid request"
        }), 400

    unique_id = data.get(
        "unique_id"
    )

    ban_id = data.get(
        "ban_id"
    )

    is_banned = data.get(
        "is_banned"
    )

    remaining_time = data.get(
        "remaining_time_in_minute",
        0
    )

    if is_banned is None:
        return jsonify({
            "success": False,
            "error": "Missing is_banned"
        }), 400

    if (
        unique_id is None
        and ban_id is None
    ):
        return jsonify({
            "success": False,
            "error": (
                "Missing unique_id or ban_id"
            )
        }), 400

    result, status_code = (
        meta_device_ban_request(
            is_banned=is_banned,
            remaining_time_in_minute=remaining_time,
            unique_id=unique_id,
            ban_id=ban_id
        )
    )

    if status_code < 400:
        game_log(
            "Meta Device Ban Updated",
            (
                f"**Ban ID:** `{ban_id or 'new'}`\n"
                f"**Unique ID:** `{unique_id or 'N/A'}`\n"
                f"**Banned:** `{bool(is_banned)}`\n"
                f"**Minutes:** `{remaining_time}`"
            )
        )

    return jsonify(result), status_code


@app.route(
    "/api/attestation/device-ban-ids",
    methods=["GET"]
)
def device_ban_ids():
    access_token = get_meta_access_token()

    if not access_token:
        return jsonify({
            "success": False,
            "error": (
                "Meta access token "
                "is not configured"
            )
        }), 500

    try:
        response = requests.get(
            META_DEVICE_BAN_IDS_URL,
            params={
                "access_token": access_token
            },
            timeout=15
        )
    except requests.RequestException:
        logger.exception(
            "Failed to retrieve device ban IDs"
        )

        return jsonify({
            "success": False,
            "error": "Meta request failed"
        }), 500

    try:
        result = response.json()
    except Exception:
        result = {
            "status": response.text
        }

    return jsonify(result), response.status_code


@app.route(
    "/api/attestation/device-ban-status",
    methods=["GET", "POST"]
)
def device_ban_status():
    data = request.get_json(
        silent=True
    ) or {}

    ban_id = (
        data.get("ban_id")
        or request.args.get("ban_id")
    )

    if not ban_id:
        return jsonify({
            "success": False,
            "error": "Missing ban_id"
        }), 400

    access_token = get_meta_access_token()

    if not access_token:
        return jsonify({
            "success": False,
            "error": (
                "Meta access token "
                "is not configured"
            )
        }), 500

    try:
        response = requests.get(
            META_DEVICE_BAN_STATUS_URL,
            params={
                "ban_id": ban_id,
                "access_token": access_token
            },
            timeout=15
        )
    except requests.RequestException:
        logger.exception(
            "Failed to retrieve device ban status"
        )

        return jsonify({
            "success": False,
            "error": "Meta request failed"
        }), 500

    try:
        result = response.json()
    except Exception:
        result = {
            "status": response.text
        }

    return jsonify(result), response.status_code


@app.route(
    "/api/TitleData",
    methods=["GET", "POST"]
)
def title_data():
    data = request.get_json(
        silent=True
    ) or {}

    keys = (
        data.get("Keys")
        or data.get("keys")
        or []
    )

    status_code, result = playfab_request(
        "Client/GetTitleData",
        {
            "Keys": keys
        }
    )

    return jsonify(result), status_code


@app.route(
    "/api/GetAcceptedAgreements",
    methods=["GET", "POST"]
)
def get_accepted_agreements():
    return jsonify({
        "success": True,
        "agreements": []
    })


@app.route(
    "/api/SubmitAcceptedAgreements",
    methods=["POST"]
)
def submit_accepted_agreements():
    return jsonify({
        "success": True
    })


@app.route(
    "/api/v2/GetName",
    methods=["POST"]
)
def get_name():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = (
        data.get("PlayFabId")
        or data.get("playfabId")
        or data.get("UserId")
        or data.get("UserID")
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId"
        }), 400

    status_code, result = playfab_request(
        "Server/GetUserAccountInfo",
        {
            "PlayFabId": playfab_id
        }
    )

    return jsonify(result), status_code


@app.route(
    "/api/GetInventory",
    methods=["POST"]
)
def get_inventory():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = (
        data.get("PlayFabId")
        or data.get("playfabId")
        or data.get("UserId")
        or data.get("UserID")
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId"
        }), 400

    status_code, result = playfab_request(
        "Server/GetUserInventory",
        {
            "PlayFabId": playfab_id
        }
    )

    return jsonify(result), status_code


@app.route(
    "/api/GetLeaderboard",
    methods=["POST"]
)
def get_leaderboard():
    data = request.get_json(
        silent=True
    ) or {}

    payload = {
        "StatisticName": data.get(
            "StatisticName",
            "Score"
        ),
        "StartPosition": int(
            data.get(
                "StartPosition",
                0
            )
        ),
        "MaxResultsCount": int(
            data.get(
                "MaxResultsCount",
                100
            )
        )
    }

    status_code, result = playfab_request(
        "Server/GetLeaderboard",
        payload
    )

    return jsonify(result), status_code


@app.route(
    "/api/UpdateStats",
    methods=["POST"]
)
def update_stats():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = (
        data.get("PlayFabId")
        or data.get("playfabId")
        or data.get("UserId")
        or data.get("UserID")
    )

    statistics = (
        data.get("Statistics")
        or data.get("statistics")
        or []
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId"
        }), 400

    status_code, result = playfab_request(
        "Server/UpdatePlayerStatistics",
        {
            "PlayFabId": playfab_id,
            "Statistics": statistics
        }
    )

    return jsonify(result), status_code


@app.route(
    "/api/UpdateProfile",
    methods=["POST"]
)
def update_profile():
    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = (
        data.get("PlayFabId")
        or data.get("playfabId")
        or data.get("UserId")
        or data.get("UserID")
    )

    if not playfab_id:
        return jsonify({
            "success": False,
            "error": "Missing PlayFabId"
        }), 400

    payload = {
        "PlayFabId": playfab_id
    }

    if "DisplayName" in data:
        payload["DisplayName"] = data[
            "DisplayName"
        ]

    status_code, result = playfab_request(
        "Server/UpdateUserTitleDisplayName",
        payload
    )

    return jsonify(result), status_code


@app.route(
    "/api/GetCosmetics",
    methods=["GET", "POST"]
)
def get_cosmetics():
    return jsonify({
        "success": True,
        "items": REDEEMABLE_ITEMS
    })


@app.route(
    "/api/EquipCosmetic",
    methods=["POST"]
)
def equip_cosmetic():
    data = request.get_json(
        silent=True
    ) or {}

    cosmetic_id = (
        data.get("CosmeticId")
        or data.get("cosmeticId")
        or data.get("ItemId")
        or data.get("itemId")
    )

    if not cosmetic_id:
        return jsonify({
            "success": False,
            "error": "Missing CosmeticId"
        }), 400

    return jsonify({
        "success": True,
        "CosmeticId": cosmetic_id
    })


@app.route(
    "/api/ReportPlayer",
    methods=["POST"]
)
def report_player():
    data = request.get_json(
        silent=True
    ) or {}

    game_log(
        "Player Report",
        (
            "```json\n"
            f"{json.dumps(data, indent=2)[:3500]}"
            "\n```"
        )
    )

    return jsonify({
        "success": True
    })


@app.route(
    "/api/CreateParty",
    methods=["POST"]
)
def create_party():
    return jsonify({
        "success": True,
        "PartyId": str(uuid.uuid4())
    })


@app.route(
    "/api/JoinParty",
    methods=["POST"]
)
def join_party():
    data = request.get_json(
        silent=True
    ) or {}

    return jsonify({
        "success": True,
        "PartyId": (
            data.get("PartyId")
            or data.get("partyId")
        )
    })


@app.route(
    "/api/LeaveParty",
    methods=["POST"]
)
def leave_party():
    return jsonify({
        "success": True
    })


@app.route(
    "/PathCreate",
    methods=["POST"]
)
def path_create():
    return jsonify({
        "success": True
    })


@app.route(
    "/PathJoin",
    methods=["POST"]
)
def path_join():
    return jsonify({
        "success": True
    })


@app.route(
    "/PathLeave",
    methods=["POST"]
)
def path_leave():
    return jsonify({
        "success": True
    })


@app.route(
    "/PathClose",
    methods=["POST"]
)
def path_close():
    return jsonify({
        "success": True
    })


@app.route(
    "/PathRaiseEvent",
    methods=["POST"]
)
def path_raise_event():
    return jsonify({
        "success": True
    })


@app.route(
    "/PathSetProperties",
    methods=["POST"]
)
def path_set_properties():
    return jsonify({
        "success": True
    })


@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Cannot GET Oculus Tag Backend"
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "error": "Method Not Allowed"
    }), 405


@app.errorhandler(500)
def internal_server_error(error):
    logger.exception(
        "Internal Server Error"
    )

    return jsonify({
        "error": "Internal Server Error"
    }), 500


@app.errorhandler(Exception)
def handle_exception(error):
    logger.exception(
        "Unhandled exception"
    )

    return jsonify({
        "error": "Internal Server Error"
    }), 500


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
