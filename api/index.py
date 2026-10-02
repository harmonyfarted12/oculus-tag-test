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
from upstash_redis import Redis


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OculusTag")

app = Flask(__name__)


class GameInfo:
    def __init__(self):
        self.TitleId = "C7E30"

        # Put these in Vercel Environment Variables.
        self.SecretKey = os.environ.get(
            "PEKS8YH8HTOAYATD93F4MS4N4BXNKOBRY4PJMYPWCYOOYE7I78",
            ""
        )

        self.ApiKey = os.environ.get(
            "OC|1368813259653754|673098554f8a983dc591cf6114427955",
            ""
        )

    def get_auth_headers(self):
        return {
            "content-type": "application/json",
            "X-SecretKey": self.SecretKey
        }


game = GameInfo()


# Meta application information.
# These are intentionally kept as manual values.
META_PACKAGE_ID = "com.harmonystudios.oculustaggers"

META_CERT_SHA256 = (
    "REPLACE_WITH_YOUR_CERT_SHA256"
)


ATTESTATION_WEBHOOK_URL = os.environ.get(
    "ATTESTATION_WEBHOOK_URL",
    ""
)


START_TIME = time.time()

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


# ---------------------------------------------------------
# Redis
# ---------------------------------------------------------

redis = None

redis_error = None

try:
    redis_url = (
        os.environ.get("UPSTASH_REDIS_REST_URL")
        or os.environ.get("KV_REST_API_URL")
    )

    redis_token = (
        os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        or os.environ.get("KV_REST_API_TOKEN")
    )

    if redis_url and redis_token:
        redis = Redis(
            url=redis_url,
            token=redis_token
        )
    else:
        redis_error = (
            "Upstash Redis environment variables are missing."
        )

except Exception as e:
    redis_error = str(e)
    logger.exception(
        "Redis initialization failed"
    )


def redis_available():
    return redis is not None


def redis_key(prefix, value):
    return f"oculus_tag:{prefix}:{value}"


def redis_set_json(key, value, expiration=None):
    if not redis:
        return False

    try:
        redis.set(
            key,
            json.dumps(value)
        )

        if expiration:
            redis.expire(
                key,
                expiration
            )

        return True

    except Exception as e:
        logger.exception(
            "Redis SET failed: %s",
            e
        )

        return False


def redis_get_json(key):
    if not redis:
        return None

    try:
        value = redis.get(key)

        if value is None:
            return None

        if isinstance(value, dict):
            return value

        if isinstance(value, str):
            return json.loads(value)

        return value

    except Exception as e:
        logger.exception(
            "Redis GET failed: %s",
            e
        )

        return None


def redis_delete(key):
    if not redis:
        return False

    try:
        redis.delete(key)
        return True

    except Exception as e:
        logger.exception(
            "Redis DELETE failed: %s",
            e
        )

        return False


# ---------------------------------------------------------
# General helpers
# ---------------------------------------------------------

def validate_input(data, fields):
    if not isinstance(data, dict):
        return fields

    return [
        field
        for field in fields
        if data.get(field) is None
        or data.get(field) == ""
    ]


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

        if isinstance(
            value,
            (dict, list)
        ):
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


# ---------------------------------------------------------
# PlayFab
# ---------------------------------------------------------

def playfab_headers():
    return game.get_auth_headers()


def playfab_request(endpoint, payload):

    if not game.TitleId:
        return None, {
            "errorMessage": (
                "PlayFab TitleId is not configured."
            )
        }

    if not game.SecretKey:
        return None, {
            "errorMessage": (
                "PlayFab SecretKey is not configured."
            )
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
            result = response.json()

        except ValueError:
            result = {
                "errorMessage": response.text
            }

        return response, result

    except requests.RequestException as e:

        logger.error(
            "PlayFab request failed: %s",
            e
        )

        return None, {
            "errorMessage": (
                "Unable to contact PlayFab."
            )
        }


def cloud_script(
    function_name,
    parameters=None,
    playfab_id=None
):
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

    result = (
        data
        .get("data", {})
        .get("FunctionResult", {})
    )

    return jsonify(
        result
    ), response.status_code


# ---------------------------------------------------------
# Attestation state
# ---------------------------------------------------------

def generate_nonce(userid=None):

    if not redis_available():
        return None

    nonce = secrets.token_urlsafe(32)

    nonce_data = {
        "created": int(time.time()),
        "used": False,
        "userid": str(
            userid or "Unknown"
        )
    }

    if not redis_set_json(
        redis_key("nonce", nonce),
        nonce_data,
        ATTESTATION_EXPIRATION
    ):
        return None

    return nonce


def get_nonce(nonce):

    if not nonce:
        return None

    return redis_get_json(
        redis_key(
            "nonce",
            str(nonce)
        )
    )


def mark_nonce_used(nonce):

    nonce_data = get_nonce(
        nonce
    )

    if not nonce_data:
        return False

    nonce_data["used"] = True

    return redis_set_json(
        redis_key(
            "nonce",
            str(nonce)
        ),
        nonce_data,
        ATTESTATION_EXPIRATION
    )


def mark_user_verified(userid):

    if not userid:
        return False

    userid = str(userid)

    data = {
        "verified": True,
        "verified_at": int(time.time())
    }

    return redis_set_json(
        redis_key(
            "verified",
            userid
        ),
        data,
        ATTESTATION_EXPIRATION
    )


def is_user_verified(userid):

    if not userid:
        return False

    data = redis_get_json(
        redis_key(
            "verified",
            str(userid)
        )
    )

    if not data:
        return False

    if data.get("verified") is not True:
        return False

    verified_at = int(
        data.get(
            "verified_at",
            0
        )
    )

    if (
        time.time()
        - verified_at
        > ATTESTATION_EXPIRATION
    ):
        redis_delete(
            redis_key(
                "verified",
                str(userid)
            )
        )

        return False

    return True


# ---------------------------------------------------------
# Attestation helpers
# ---------------------------------------------------------

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


def verify_meta_attestation(
    token,
    challenge_nonce,
    oculus_id
):
    """
    Performs the complete Meta attestation check.

    Returns:

        (True, result)

    or:

        (False, error)
    """

    if not token:
        return False, "Missing attestation token"

    if not challenge_nonce:
        return False, "Missing challenge nonce"

    if not oculus_id:
        return False, "Missing Oculus ID"

    if not redis_available():
        return False, (
            "Attestation storage is unavailable. "
            "Configure Upstash Redis."
        )

    nonce_data = get_nonce(
        challenge_nonce
    )

    if not nonce_data:
        return False, (
            "Invalid or expired challenge nonce"
        )

    if nonce_data.get("used") is True:
        return False, (
            "Challenge nonce already used"
        )

    created = int(
        nonce_data.get(
            "created",
            0
        )
    )

    if (
        time.time()
        - created
        > ATTESTATION_EXPIRATION
    ):
        redis_delete(
            redis_key(
                "nonce",
                challenge_nonce
            )
        )

        return False, (
            "Challenge nonce expired"
        )

    challenge_userid = str(
        nonce_data.get(
            "userid",
            "Unknown"
        )
    )

    oculus_id = str(
        oculus_id
    )

    if (
        challenge_userid != "Unknown"
        and challenge_userid != oculus_id
    ):
        return False, (
            "Oculus ID does not match challenge"
        )

    if not game.ApiKey:
        return False, (
            "Meta API key is not configured"
        )

    try:

        response = requests.get(
            "https://graph.oculus.com/platform_integrity/verify",
            params={
                "token": token,
                "access_token": game.ApiKey
            },
            timeout=15
        )

    except requests.RequestException as e:

        logger.error(
            "Meta request failed: %s",
            e
        )

        return False, (
            "Could not contact Meta"
        )

    try:
        result = response.json()

    except ValueError:

        return False, (
            "Meta returned invalid JSON"
        )

    if response.status_code != 200:

        logger.warning(
            "Meta rejected attestation: %s",
            response.status_code
        )

        return False, (
            "Meta rejected the attestation token"
        )

    meta_data = result.get(
        "data"
    )

    if not isinstance(
        meta_data,
        list
    ) or not meta_data:

        return False, (
            "Invalid Meta verification response"
        )

    verification = meta_data[0]

    if verification.get(
        "message"
    ) != "success":

        return False, (
            verification.get(
                "message"
            )
            or "Meta rejected the attestation token"
        )

    claims_encoded = verification.get(
        "claims"
    )

    if not claims_encoded:
        return False, (
            "Meta returned no claims"
        )

    claims = decode_base64url_json(
        claims_encoded
    )

    if not claims:
        return False, (
            "Could not decode attestation claims"
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
        return False, (
            "Missing request details"
        )

    if not isinstance(
        app_state,
        dict
    ):
        return False, (
            "Missing app state"
        )

    if not isinstance(
        device_state,
        dict
    ):
        return False, (
            "Missing device state"
        )

    token_nonce = request_details.get(
        "nonce"
    )

    if token_nonce != challenge_nonce:
        return False, (
            "Attestation nonce mismatch"
        )

    try:
        expiration = int(
            request_details.get(
                "exp"
            )
        )

    except (
        TypeError,
        ValueError
    ):
        return False, (
            "Invalid attestation expiration"
        )

    try:
        timestamp = int(
            request_details.get(
                "timestamp"
            )
        )

    except (
        TypeError,
        ValueError
    ):
        return False, (
            "Invalid attestation timestamp"
        )

    now = int(
        time.time()
    )

    if expiration <= now:
        return False, (
            "Attestation token expired"
        )

    if abs(
        now - timestamp
    ) > 300:
        return False, (
            "Attestation timestamp is too old"
        )

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

    # -----------------------------------------------------
    # Package check
    # -----------------------------------------------------

    if (
        META_PACKAGE_ID
        and package_id != META_PACKAGE_ID
    ):

        return False, (
            "Invalid package ID"
        )

    # -----------------------------------------------------
    # Certificate check
    # -----------------------------------------------------

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

        if (
            expected_cert
            not in normalized_certificates
        ):

            return False, (
                "Invalid package certificate"
            )

    # -----------------------------------------------------
    # Store recognition check
    # -----------------------------------------------------

    if (
        app_integrity_state
        != "StoreRecognized"
    ):

        return False, (
            "App integrity check failed"
        )

    # -----------------------------------------------------
    # Device integrity check
    # -----------------------------------------------------

    if device_integrity_state not in (
        "Advanced",
        "Basic"
    ):

        return False, (
            "Device integrity check failed"
        )

    # -----------------------------------------------------
    # Device ban check
    # -----------------------------------------------------

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

            return False, (
                "Device is banned"
            )

    # -----------------------------------------------------
    # Everything passed
    # -----------------------------------------------------

    if not mark_user_verified(
        oculus_id
    ):
        return False, (
            "Could not save verification state"
        )

    if not mark_nonce_used(
        challenge_nonce
    ):
        return False, (
            "Could not save nonce state"
        )

    return True, {
        "OculusId": oculus_id,
        "package_id": package_id,
        "app_integrity_state":
            app_integrity_state,
        "device_integrity_state":
            device_integrity_state
    }


# ---------------------------------------------------------
# Request logging
# ---------------------------------------------------------

@app.before_request
def api_request_start():

    if request.path.startswith(
        "/api/"
    ):
        request.api_start_time = (
            time.perf_counter()
        )


@app.after_request
def api_request_log(response):

    if not request.path.startswith(
        "/api/"
    ):
        return response

    # Don't spam Discord with the
    # attestation challenge/verify calls.
    ignored = (
        "/api/attestation/challenge",
        "/api/attestation/verify"
    )

    if request.path in ignored:
        return response

    start_time = getattr(
        request,
        "api_start_time",
        time.perf_counter()
    )

    elapsed_ms = (
        time.perf_counter()
        - start_time
    ) * 1000

    ip = request.headers.get(
        "X-Forwarded-For",
        request.remote_addr or "Unknown"
    ).split(",")[0].strip()

    data = request.get_json(
        silent=True
    )

    if not isinstance(
        data,
        dict
    ):
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
        f"**Endpoint:** `{request.path}`\n"
        f"**Method:** `{request.method}`\n"
        f"**IP:** `{ip}`\n"
        f"**Status:** `{response.status_code}`\n"
        f"**PlayFab ID:** `{playfab_id}`\n"
        f"**Username:** `{username}`\n"
        f"**Request Time:** `{elapsed_ms:.2f} ms`"
    )

    return response


# ---------------------------------------------------------
# Basic endpoints
# ---------------------------------------------------------

@app.route(
    "/",
    methods=["GET", "POST"]
)
def home():

    return jsonify({
        "success": True,
        "status": "online",
        "name": "Oculus Tag Backend",
        "version": "1.0.0"
    })


@app.route(
    "/api/test",
    methods=["GET", "POST"]
)
def test():

    return jsonify({
        "success": True,
        "message": (
            "Oculus Tag Backend is working."
        )
    })


@app.route(
    "/api/GetServerStatus",
    methods=["GET"]
)
def get_server_status():

    return jsonify({
        "status": "online",
        "server": "Oculus Tag Backend",
        "uptime": int(
            time.time()
            - START_TIME
        ),
        "activePlayers": 0
    })


@app.route(
    "/api/GetServerConfig",
    methods=["GET"]
)
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


# ---------------------------------------------------------
# Redis status
# ---------------------------------------------------------

@app.route(
    "/api/RedisStatus",
    methods=["GET"]
)
def redis_status():

    if not redis:
        return jsonify({
            "success": False,
            "redis": False,
            "error": redis_error
        }), 503

    try:

        redis.set(
            "oculus_tag:health",
            "ok",
            ex=30
        )

        value = redis.get(
            "oculus_tag:health"
        )

        return jsonify({
            "success": value == "ok",
            "redis": True
        })

    except Exception as e:

        return jsonify({
            "success": False,
            "redis": False,
            "error": str(e)
        }), 503


# ---------------------------------------------------------
# PlayFab cache
# ---------------------------------------------------------

@app.route(
    "/api/CachePlayFabId",
    methods=["POST"]
)
def cache_playfab_id():

    data = request.get_json(
        silent=True
    ) or {}

    playfab_id = data.get(
        "PlayFabId"
    )

    oculus_id = data.get(
        "OculusId"
    )

    if not playfab_id:

        return jsonify({
            "error": "Missing PlayFabId"
        }), 400

    cache_key = str(
        oculus_id
        if oculus_id
        else playfab_id
    )

    cache_data = {
        "PlayFabId": playfab_id,
        "Username": data.get(
            "Username",
            data.get("DisplayName")
        ),
        "cached": int(
            time.time()
        )
    }

    if redis:

        redis_set_json(
            redis_key(
                "playfab",
                cache_key
            ),
            cache_data,
            86400
        )

    game_log(
        "PlayFab ID Cached",
        f"PlayFab ID: `{playfab_id}`\n"
        f"Oculus ID: `{oculus_id or 'Unknown'}`"
    )

    return jsonify({
        "Message": "Success",
        "PlayFabId": playfab_id
    })


# ---------------------------------------------------------
# Attestation challenge
# ---------------------------------------------------------

@app.route(
    "/api/attestation/challenge",
    methods=["GET", "POST"]
)
def attestation_challenge():

    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(
        data,
        dict
    ):
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

    if not redis_available():

        return jsonify({
            "success": False,
            "error": (
                "Attestation storage is unavailable."
            )
        }), 503

    nonce = generate_nonce(
        userid
    )

    if not nonce:

        return jsonify({
            "success": False,
            "error": (
                "Could not create attestation challenge."
            )
        }), 503

    return jsonify({
        "success": True,
        "challenge_nonce": nonce
    })


# ---------------------------------------------------------
# Attestation verification
# ---------------------------------------------------------

@app.route(
    "/api/attestation/verify",
    methods=["POST"]
)
def attestation_verify():

    data = request.get_json(
        silent=True
    ) or {}

    if not isinstance(
        data,
        dict
    ):
        data = {}

    token = data.get(
        "attestation_token"
    )

    challenge_nonce = data.get(
        "challenge_nonce"
    )

    oculus_id = (
        data.get("OculusId")
        or data.get("oculus_id")
        or data.get("userid")
        or data.get("UserId")
        or data.get("UserID")
    )

    if not token:

        return jsonify({
            "success": False,
            "verified": False,
            "error": (
                "Missing attestation_token"
            )
        }), 400

    if not challenge_nonce:

        return jsonify({
            "success": False,
            "verified": False,
            "error": (
                "Missing challenge_nonce"
            )
        }), 400

    if not oculus_id:

        return jsonify({
            "success": False,
            "verified": False,
            "error": (
                "Missing OculusId"
            )
        }), 400

    attestation_log(
        "ATTESTATION VERIFY",
        {
            "oculus id": str(
                oculus_id
            ),
            "nonce": challenge_nonce,
            "attestation token":
                mask_secret(token)
        }
    )

    success, result = verify_meta_attestation(
        token,
        challenge_nonce,
        str(oculus_id)
    )

    if not success:

        game_log(
            "Attestation Rejected",
            f"Oculus ID: `{oculus_id}`\n"
            f"Reason: `{result}`"
        )

        status = 403

        if (
            "nonce" in result.lower()
            or "challenge" in result.lower()
        ):
            status = 401

        return jsonify({
            "success": False,
            "verified": False,
            "error": result
        }), status

    attestation_log(
        "ATTESTATION VERIFIED",
        result
    )

    return jsonify({
        "success": True,
        "verified": True,
        **result
    })


# ---------------------------------------------------------
# PlayFab Authentication
# ---------------------------------------------------------

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
            "Error":
                "BadRequest-MissingParameter"
        }), 400

    if data["AppId"] != game.TitleId:

        return jsonify({
            "Message":
                "Request sent for wrong App ID",
            "Error":
                "BadRequest-AppIdMismatch"
        }), 400

    oculus_id = str(
        data["OculusId"]
    )

    # -----------------------------------------------------
    # Verify Redis state
    # -----------------------------------------------------

    verified = is_user_verified(
        oculus_id
    )

    # -----------------------------------------------------
    # Optional inline verification.
    #
    # This allows clients that send the Meta token
    # directly with PlayFabAuthentication to work too.
    # -----------------------------------------------------

    if not verified:

        attestation_token = (
            data.get("attestation_token")
            or data.get("AttestationToken")
            or data.get("IntegrityToken")
        )

        challenge_nonce = (
            data.get("challenge_nonce")
            or data.get("ChallengeNonce")
            or data.get("AttestationNonce")
        )

        if (
            attestation_token
            and challenge_nonce
        ):

            success, result = (
                verify_meta_attestation(
                    attestation_token,
                    challenge_nonce,
                    oculus_id
                )
            )

            if success:
                verified = True

            else:

                logger.warning(
                    "Inline attestation failed: %s",
                    result
                )

    if not verified:

        game_log(
            "PlayFab Authentication Rejected",
            f"Oculus ID `{oculus_id}` "
            "has not passed Meta attestation."
        )

        return jsonify({
            "Error":
                "AttestationRequired",
            "Message": (
                "Successful Meta attestation "
                "is required before PlayFab "
                "authentication."
            )
        }), 403

    # -----------------------------------------------------
    # PlayFab login
    # -----------------------------------------------------

    login_payload = {
        "ServerCustomId":
            "OCULUS" + oculus_id,
        "CreateAccount": True
    }

    response, result = playfab_request(
        "/Server/LoginWithServerCustomId",
        login_payload
    )

    if response is None:

        return jsonify(
            result
        ), 502

    if response.status_code != 200:

        return jsonify({
            "Error":
                "PlayFab Error",
            "Message":
                result.get(
                    "errorMessage",
                    "PlayFab authentication failed."
                )
        }), response.status_code

    result_data = result.get(
        "data",
        {}
    )

    entity_token_data = (
        result_data.get(
            "EntityToken",
            {}
        )
    )

    entity = (
        entity_token_data.get(
            "Entity",
            {}
        )
    )

    playfab_id = result_data.get(
        "PlayFabId"
    )

    if playfab_id and redis:

        redis_set_json(
            redis_key(
                "playfab",
                oculus_id
            ),
            {
                "PlayFabId":
                    playfab_id,
                "cached":
                    int(time.time())
            },
            86400
        )

    game_log(
        "PlayFab Authentication Success",
        f"Oculus ID: `{oculus_id}`\n"
        f"PlayFab ID: `{playfab_id}`"
    )

    return jsonify({
        "PlayFabId":
            playfab_id,

        "SessionTicket":
            result_data.get(
                "SessionTicket"
            ),

        "EntityToken":
            entity_token_data.get(
                "EntityToken"
            ),

        "EntityId":
            entity.get(
                "Id"
            ),

        "EntityType":
            entity.get(
                "Type"
            ),

        "SessionId":
            str(uuid.uuid4())
    })


# ---------------------------------------------------------
# Game endpoints
# ---------------------------------------------------------

@app.route(
    "/api/TitleData",
    methods=["GET", "POST"]
)
def title_data():

    return jsonify({
        "ServerVersion":
            "1.0.0",

        "ClientMinVersion":
            "1.0.0",

        "MOTD":
            "discord.gg/oculustagg"
    })


@app.route(
    "/api/GetAcceptedAgreements",
    methods=["GET", "POST"]
)
def get_accepted_agreements():

    return jsonify({
        "PrivacyPolicy":
            "1.1.28",

        "TOS":
            "11.05.22.2",

        "EULA":
            "2024.09.20"
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
            "error":
                "Missing PlayFabId"
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
        + str(
            random.randint(
                1000,
                9999
            )
        )
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
            "error":
                "Missing PlayFabId"
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
            "StatisticName":
                statistic
        },
        data.get(
            "PlayFabId"
        )
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
            "error":
                "Missing fields: "
                + ", ".join(missing)
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
            "error":
                "Missing fields: "
                + ", ".join(missing)
        }), 400

    return cloud_script(
        "UpdateUserTitleDisplayName",
        {
            "DisplayName":
                data["DisplayName"]
        },
        data["PlayFabId"]
    )


@app.route(
    "/api/GetCosmetics",
    methods=["GET", "POST"]
)
def get_cosmetics():

    return jsonify({
        "cosmetics":
            REDEEMABLE_ITEMS
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
            "error":
                "Missing fields: "
                + ", ".join(missing)
        }), 400

    cosmetic_id = data[
        "CosmeticId"
    ]

    if cosmetic_id not in REDEEMABLE_ITEMS:

        return jsonify({
            "success": False,
            "error":
                "Unknown cosmetic"
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
            "error":
                "Missing fields: "
                + ", ".join(missing)
        }), 400

    game_log(
        "Player Report",
        f"Reporter: `{data['ReporterId']}`\n"
        f"Reported: `{data['ReportedId']}`\n"
        f"Reason: `{data['Reason']}`"
    )

    return jsonify({
        "success": True,
        "message":
            "Report submitted"
    })


# ---------------------------------------------------------
# Party endpoints
# ---------------------------------------------------------

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
            "error":
                "Missing PlayFabId"
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
        "PartyId":
            party_id
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
            "error":
                "Missing fields: "
                + ", ".join(missing)
        }), 400

    return jsonify({
        "success": True,
        "PartyId":
            data["PartyId"]
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
            "error":
                "Missing fields: "
                + ", ".join(missing)
        }), 400

    return jsonify({
        "success": True,
        "PartyId":
            data["PartyId"]
    })


# ---------------------------------------------------------
# Path endpoints
# ---------------------------------------------------------

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

    data["Type"] = (
        "ClientDisconnect"
    )

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


# ---------------------------------------------------------
# Error handlers
# ---------------------------------------------------------

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "success": False,
        "error":
            "Endpoint not found",
        "server":
            "Oculus Tag Backend"
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):

    return jsonify({
        "success": False,
        "error":
            "Method not allowed",
        "server":
            "Oculus Tag Backend"
    }), 405


@app.errorhandler(500)
def internal_error(error):

    logger.exception(
        "Internal server error: %s",
        error
    )

    return jsonify({
        "success": False,
        "error":
            "Internal server error",
        "server":
            "Oculus Tag Backend"
    }), 500


@app.errorhandler(Exception)
def handle_exception(error):

    logger.exception(
        "Unhandled backend exception: %s",
        error
    )

    return jsonify({
        "success": False,
        "error":
            "Internal server error",
        "server":
            "Oculus Tag Backend"
    }), 500


# ---------------------------------------------------------
# Local development
# ---------------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=9080,
        debug=False
    )
