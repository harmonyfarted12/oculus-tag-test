import requests
import random
from flask import Flask, jsonify, request
import json
import os
import base64
from datetime import datetime, timedelta
import uuid
import logging
from typing import Dict, List, Optional

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GameInfo:
    def __init__(self):
        self.TitleId: str = "8A822"  # Playfab Title Id
        self.SecretKey: str = "7IZU6H9CDUQPCS81O4WJ84D4143DRFSBIQ7IA8G5CRM9573W4J"  # Playfab Secret Key
        self.ApiKey: str = ""  # App Api Key (Oculus/Graph API)

    def get_auth_headers(self):
        return {"content-type": "application/json", "X-SecretKey": self.SecretKey}

settings = GameInfo()
app = Flask(__name__)

# Utility function for input validation
def validate_input(data: Dict, required_fields: List[str]) -> Optional[List[str]]:
    return [field for field in required_fields if not data.get(field)]

# Utility function for generating unique session IDs
def generate_session_id() -> str:
    return str(uuid.uuid4())

# Utility function for returning CloudScript results
def return_function_json(funcname: str, funcparam: Dict = {}, playfab_id: Optional[str] = None):
    logger.info(f"Calling function: {funcname} with parameters: {funcparam} for player {playfab_id}")
    req = requests.post(
        url=f"https://{settings.TitleId}.playfabapi.com/Server/ExecuteCloudScript",
        json={
            "PlayFabId": playfab_id,
            "FunctionName": funcname,
            "FunctionParameter": funcparam
        },
        headers=settings.get_auth_headers()
    )
    if req.status_code == 200:
        result = req.json().get("data", {}).get("FunctionResult", {})
        logger.info(f"Function result: {result}")
        return jsonify(result), req.status_code
    else:
        logger.error(f"Function execution failed, status code: {req.status_code}")
        return jsonify({}), req.status_code

# Validate Oculus nonce
def get_is_nonce_valid(nonce: str, oculusId: str) -> bool:
    if not settings.ApiKey:
        return False
    req = requests.post(
        url=f'https://graph.oculus.com/user_nonce_validate?nonce={nonce}&user_id={oculusId}&access_token={settings.ApiKey}',
        headers={"content-type": "application/json"})
    return req.json().get("is_valid", False)

# GitHub codes raw URL for redeem codes
CODES_GITHUB_URL = "https://github.com/redapplegtag/backendsfrr/raw/main/codes.txt"

# Sample item IDs for code redemption
REDEEMABLE_ITEMS = ["cosmetic1", "cosmetic2", "cosmetic3", "bundle1", "skin1", "hat1", "gloves1"]

@app.route("/", methods=["POST", "GET"])
def main():
    return """
        <html>
            <head>
                <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet">
            </head>
            <body style="font-family: 'Inter', sans-serif; background: linear-gradient(to bottom, #004d00, #00cc00); color: white; text-align: center; padding: 50px;">
                <h1 style="color: #eedd82; font-size: 48px; text-shadow: 2px 2px 4px rgba(0,0,0,0.5);">
                    Wsp Broksie. This is a private backend!
                </h1>
                <p style="font-size: 18px;">Christmas Tag Backend Server Running Smoothly!</p>
                <img src="https://aicdn.picsart.com/275c6ae1-73a4-4cee-b3f5-45ccfa4499ae.png" alt="if u see this text it dont work" style="max-width: 500px; border-radius: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); display: block; margin: 30px auto;">
                <p style="font-size: 14px; opacity: 0.8;">Image loads when the server works!</p>
            </body>
        </html>
    """

@app.route("/api/PlayFabAuthentication", methods=["POST", "GET"])
def playfab_authentication():
    rjson = request.get_json()
    if not rjson:
        return jsonify({"error": "No JSON body"}), 400

    required_fields = ["Nonce", "AppId", "Platform", "OculusId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"Message": f"Missing parameter(s): {', '.join(missing_fields)}", "Error": f"BadRequest-No{missing_fields[0]}"},), 401

    if rjson.get("AppId") != settings.TitleId:
        return jsonify({"Message": "Request sent for the wrong App ID", "Error": "BadRequest-AppIdMismatch"},), 400

    if rjson.get("Platform") in ["Oculus", "Quest"] and not get_is_nonce_valid(rjson["Nonce"], rjson["OculusId"]):
        return jsonify({"Message": "Invalid nonce", "Error": "BadRequest-InvalidNonce"}), 401

    url = f"https://{settings.TitleId}.playfabapi.com/Server/LoginWithServerCustomId"
    login_request = requests.post(
        url=url,
        json={
            "ServerCustomId": "OCULUS" + rjson.get("OculusId"),
            "CreateAccount": True,
        },
        headers=settings.get_auth_headers(),
    )

    if login_request.status_code == 200:
        data = login_request.json().get("data")
        session_ticket = data.get("SessionTicket")
        entity_token = data.get("EntityToken").get("EntityToken")
        playfab_id = data.get("PlayFabId")
        entity_type = data.get("EntityToken").get("Entity").get("Type")
        entity_id = data.get("EntityToken").get("Entity").get("Id")
        session_id = generate_session_id()

        custom_id = rjson.get("CustomId")
        if custom_id:
            link_response = requests.post(
                url=f"https://{settings.TitleId}.playfabapi.com/Server/LinkServerCustomId",
                json={
                    "ForceLink": True,
                    "PlayFabId": playfab_id,
                    "ServerCustomId": custom_id,
                },
                headers=settings.get_auth_headers(),
            )
            if link_response.status_code != 200:
                logger.error(f"Link failed: {link_response.text}")

        auth_req = requests.post(
            url=f"https://{settings.TitleId}.playfabapi.com/Server/ExecuteCloudScript",
            json={
                "PlayFabId": playfab_id,
                "FunctionName": "SigmaAuth",
                "FunctionParameter": {"sessionId": session_id}
            },
            headers=settings.get_auth_headers()
        )
        logger.info(f"CloudScript auth response: {auth_req.text}")

        return jsonify(
            {
                "PlayFabId": playfab_id,
                "SessionTicket": session_ticket,
                "EntityToken": entity_token,
                "EntityId": entity_id,
                "EntityType": entity_type,
                "SessionId": session_id,
            }
        ), 200
    else:
        if login_request.status_code == 403:
            ban_info = login_request.json()
            if ban_info.get("errorCode") == 1002:
                ban_details = ban_info.get("errorDetails", {})
                ban_expiration_key = next(iter(ban_details.keys()), None)
                ban_expiration_list = ban_details.get(ban_expiration_key, [])
                ban_expiration = ban_expiration_list[0] if len(ban_expiration_list) > 0 else "No expiration date provided."
                return jsonify({"BanMessage": ban_expiration_key, "BanExpirationTime": ban_expiration},), 403
            else:
                error_message = ban_info.get("errorMessage", "Forbidden without ban information.")
                return jsonify({"Error": "PlayFab Error", "Message": error_message}), 403
        else:
            error_info = login_request.json()
            error_message = error_info.get("errorMessage", "An error occurred.")
            return jsonify({"Error": "PlayFab Error", "Message": error_message}), login_request.status_code

@app.route("/api/CachePlayFabId", methods=["POST"])
def cache_playfab_id():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    # Placeholder for caching logic (e.g., Redis)
    logger.info(f"Caching PlayFabId: {playfab_id}")
    return jsonify({"Message": "Success", "PlayFabId": playfab_id}), 200

@app.route('/api/TitleData', methods=['POST', 'GET'])
def titledata():
    response_data = {
        # Auto-Mute and Naming
        "AutoMuteCheckedHours": {"hours": 169},
        "AutoName_Adverbs": [
            "Cool", "Fine", "Bald", "Bold", "Half", "Only", "Calm", "Fab", "Ice", "Mad",
            "Rad", "Big", "New", "Old", "Shy", "Wild", "Brave", "Swift", "Gentle", "Fierce"
        ],
        "AutoName_Nouns": [
            "Gorilla", "Chicken", "Darling", "Sloth", "King", "Queen", "Royal", "Major", "Actor", "Agent",
            "Elder", "Honey", "Nurse", "Doctor", "Rebel", "Shape", "Ally", "Driver", "Deputy", "Wizard"
        ],
        # Bundle and Sign Configurations
        "BundleBoardSign": "<color=#ff4141>DISCORD.GG/CHRISTMASTAG</color>",
        "BundleKioskButton": "<color=#ff4141>DISCORD.GG/CHRISTMASTAG</color>",
        "BundleKioskSign": "<color=#ff4141>DISCORD.GG/CHRISTMASTAG</color>",
        "BundleLargeSign": "<color=#ff4141>DISCORD.GG/CHRISTMASTAG</color>",
        "SeasonalStoreBoardSign": "<color=red>RATE THE GAME 5 STARS!</color>\n<color=blue>.GG/UNLOADTAG</color>",
        # Text and Messages
        "EmptyFlashbackText": "FLOOR TWO NOW OPEN\n FOR BUSINESS\n\nSTILL SEARCHING FOR\nBOX LABELED 2021",
        "MOTD": "<color=#FFC0CB>WELCOME TO CHRISTMAS TAG!</color>\n\n<color=#0099c2>CURRENT UPDATE: XMAS24</color>\n<color=#cacfd2>BOOST THE DISCORD FOR ALL COSMETICS (EXCLUDING STAFF COS)</color>\n<color=#41ff80>YANDERE MADE THIS MOTD</color>\n<color=#6417ff>OUR DEVELOPERS ARE: FATAL & CASHSMILE</color>\n<color=#ac1a00>CREDITS FOR GAMES OG OS: VIPER</color>\n\n<color=#91A3B0>discord.gg/CHRISTMASTAG</color>",
        "TOBAlreadyOwnCompTxt": "DISCORD.GG/CHRISTMASTAG",
        "TOBAlreadyOwnPurchaseBundle": "CHRISTMAS TAG",
        "TOBDefCompTxt": "DISCORD.GG/CHRISTMASTAG",
        "TOBDefPurchaseBtnDefTxt": "CHRISTMAS TAG",
        # Legal and Versions
        "EnableCustomAuthentication": True,
        "LatestPrivacyPolicyVersion": "2024.09.20",
        "LatestTOSVersion": "2024.09.20",
        "TOS_2024.09.20": "DISCORD.GG/CHRISTMASTAG",
        "EnableTwoFactorAuth": False,
        "MaxLoginAttempts": 5,
        "SessionTimeoutMinutes": 30,
        # Game Mechanics
        "GorillanalyticsChance": 4320,
        "UseLegacyIAP": False,
        "MaxPlayersPerRoom": 8,
        "DefaultGameMode": "Tag",
        "EnableVoiceChat": True,
        "ChatFilterEnabled": True,
        "MaxChatLength": 100,
        "SpawnProtectionTime": 5,
        "GameRoundDuration": 300,
        "RespawnDelay": 3,
        "TagCooldown": 1,
        "PowerupSpawnRate": 0.1,
        "CurrencyMultiplier": 1.0,
        "DailyLoginReward": 100,
        "XPPerKill": 50,
        "LevelCap": 100,
        "EnableAchievements": True,
        "LeaderboardUpdateInterval": 60,
        "AntiCheatEnabled": True,
        "ReportCooldown": 300,
        "FriendLimit": 50,
        "PartySizeLimit": 4,
        "MatchmakingTimeout": 30,
        "PingThreshold": 200,
        "RegionPriority": ["US", "EU", "AS"],
        "EnableSpectatorMode": True,
        "TutorialEnabled": True,
        "NewsFeedUrl": "https://discord.gg/CHRISTMASTAG",
        "UpdateCheckInterval": 3600,
        "BackupInterval": 86400,
        "LogLevel": "INFO",
        "DebugMode": False,
        "MaintenanceMode": False,
        "ServerVersion": "1.2.3",
        "ClientMinVersion": "1.2.0",
        "EnableBetaFeatures": False,
        "CustomEmotesEnabled": True,
        "EmoteLimitPerPlayer": 10,
        "VoiceVolumeDefault": 0.8,
        "MusicVolumeDefault": 0.5,
        "SFXVolumeDefault": 1.0,
        "HUDEnabled": True,
        "MinimapEnabled": True,
        "CrosshairCustomizable": True,
        "ControllerSupport": True,
        "KeyboardBindingsDefault": {"forward": "W", "backward": "S", "jump": "SPACE", "crouch": "C"},
        "TouchControlsEnabled": True,
        # New Configurations
        "EnableSeasonalEvents": True,
        "SeasonalEventName": "WinterFest2025",
        "EventStartDate": "2025-12-01",
        "EventEndDate": "2026-01-15",
        "DailyChallengeLimit": 3,
        "WeeklyChallengeLimit": 10,
        "AchievementRewardCurrency": 50,
        "MaxInventorySlots": 100,
        "TradeEnabled": True,
        "TradeTaxRate": 0.05,
        "VoiceChatMaxRange": 10.0,
        "MinLevelForRanked": 10,
        "RankedMatchmakingEnabled": True,
        "SeasonResetIntervalDays": 90,
        "DailyRewardMultiplier": 1.5,
        "WeeklyRewardMultiplier": 2.0,
        "EnableGuilds": True,
        "MaxGuildMembers": 50,
        "GuildCreationCost": 1000,
        "SpectatorCameraModes": ["Free", "Follow", "Fixed"],
        "EnableCustomSkins": True,
        "CustomSkinUploadLimit": 5,
        "ServerRestartIntervalHours": 24,
        "MaxReportCountPerDay": 5,
        "BanAppealUrl": "https://discord.gg/CHRISTMASTAG",
        "EnableCrossplay": True,
        "DefaultFOV": 90,
        "MaxFOV": 120,
        "MinFOV": 60,
        "EnableDynamicWeather": True,
        "WeatherChangeInterval": 600,
        "SupportedLanguages": ["en", "es", "fr", "de", "zh"],
        "DefaultLanguage": "en",
        "EnablePushNotifications": True,
        "NotificationCooldownSeconds": 300,
        "MaxPartyInvites": 10,
        "EnableClanTags": True,
        "MaxClanTagLength": 4,
        "EnableDailyQuests": True,
        "DailyQuestRefreshHour": 0,
        "MaxConcurrentMatches": 100,
        "ServerRegionLatencyCaps": {"US": 150, "EU": 200, "AS": 250},
        "EnableVoiceModeration": True,
        "VoiceModerationThreshold": 0.9,
        "EnablePlayerFeedback": True,
        "FeedbackSubmissionUrl": "https://discord.gg/CHRISTMASTAG/feedback"
    }
    return jsonify(response_data)

@app.route("/api/ConsumeOculusIAP", methods=["POST"])
def consume_oculus_iap():
    rjson = request.get_json()
    required_fields = ["userToken", "userID", "nonce", "sku"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    access_token = rjson.get("userToken")
    user_id = rjson.get("userID")
    nonce = rjson.get("nonce")
    sku = rjson.get("sku")

    response = requests.post(
        url=f"https://graph.oculus.com/consume_entitlement?nonce={nonce}&user_id={user_id}&sku={sku}&access_token={settings.ApiKey}",
        headers={"content-type": "application/json"},
    )

    if response.json().get("success"):
        return jsonify({"result": True})
    else:
        return jsonify({"error": True, "message": response.json().get("message", "Consume failed")})

@app.route("/api/GetAcceptedAgreements", methods=['POST', 'GET'])
def GetAcceptedAgreements():
    return jsonify({"PrivacyPolicy": "1.1.28", "TOS": "11.05.22.2", "EULA": "2024.09.20"}), 200

@app.route("/api/SubmitAcceptedAgreements", methods=['POST'])
def SubmitAcceptedAgreements():
    data = request.get_json()
    playfab_id = data.get("PlayFabId")
    agreements = data.get("Agreements", {})
    # Placeholder: Update PlayFab user data
    logger.info(f"Updating agreements for PlayFabId: {playfab_id}")
    return jsonify({"success": True}), 200

@app.route("/api/ConsumeCodeItem", methods=["POST"])
def consume_code_item():
    rjson = request.get_json()
    required_fields = ["itemGUID", "playFabID"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing parameters: {', '.join(missing_fields)}"}), 400

    code = rjson.get("itemGUID")
    playfab_id = rjson.get("playFabID")

    response = requests.get(CODES_GITHUB_URL)
    if response.status_code != 200:
        return jsonify({"error": "Code fetch failed"}), 500

    lines = response.text.splitlines()
    codes = {}
    for line in lines:
        if ':' in line:
            split = line.split(":", 1)
            codes[split[0].strip()] = split[1].strip()

    if code not in codes:
        return jsonify({"result": "CodeInvalid"}), 404

    if codes[code] == "AlreadyRedeemed":
        return jsonify({"result": codes[code]}), 200

    grant_response = requests.post(
        f"https://{settings.TitleId}.playfabapi.com/Admin/GrantItemsToUsers",
        json={
            "ItemGrants": [
                {
                    "PlayFabId": playfab_id,
                    "ItemId": item_id,
                    "CatalogVersion": "DLC"
                } for item_id in REDEEMABLE_ITEMS
            ]
        },
        headers=settings.get_auth_headers()
    )

    if grant_response.status_code != 200:
        return jsonify({"result": "PlayFabError", "errorMessage": grant_response.json().get("errorMessage", "Grant failed")}), 500

    new_lines = []
    for line in lines:
        if line.strip().startswith(code + ":"):
            new_lines.append(f"{code}:AlreadyRedeemed")
        else:
            new_lines.append(line)
    updated_content = "\n".join(new_lines).strip()
    # TODO: Push to GitHub or webhook
    return jsonify({"result": "Success", "itemID": code, "playFabItemName": codes[code]}), 200

@app.route('/api/v2/GetName', methods=['POST', 'GET'])
def GetNameIg():
    adverb = random.choice([
        "Cool", "Fine", "Bald", "Bold", "Half", "Only", "Calm", "Fab", "Ice", "Mad",
        "Rad", "Big", "New", "Old", "Shy", "Wild", "Brave", "Swift", "Gentle", "Fierce"
    ])
    noun = random.choice([
        "Gorilla", "Chicken", "Darling", "Sloth", "King", "Queen", "Royal", "Major", "Actor", "Agent",
        "Elder", "Honey", "Nurse", "Doctor", "Rebel", "Shape", "Ally", "Driver", "Deputy", "Wizard"
    ])
    return jsonify({"result": f"{adverb}{noun}{random.randint(1000,9999)}"})

@app.route("/api/photon/authenticate", methods=["POST", "GET"])
def photonauth():
    logger.info(f"Received {request.method} request at /api/photon")
    rjson = request.get_json()
    if not rjson:
        return jsonify({'resultCode': 2, 'message': 'Invalid request'}), 400

    ticket = rjson.get("Ticket")
    nonce = rjson.get("Nonce")
    platform = rjson.get("Platform")
    user_id = rjson.get("UserId")
    nick_name = rjson.get("username")

    if ticket:
        extracted_id = ticket.split('-')[0]
        user_id = user_id or extracted_id

    if not user_id or len(user_id) != 16:
        logger.error("Invalid userId")
        return jsonify({'resultCode': 2, 'message': 'Invalid token', 'userId': None, 'nickname': None})

    if platform != 'Quest':
        return jsonify({'Error': 'Bad request', 'Message': 'Invalid platform!'}), 403

    if nonce and platform == 'Quest' and not get_is_nonce_valid(nonce, user_id):
        return jsonify({'Error': 'Bad request', 'Message': 'Invalid nonce!'}), 304

    req = requests.post(
        url=f"https://{settings.TitleId}.playfabapi.com/Server/GetUserAccountInfo",
        json={"PlayFabId": user_id},
        headers=settings.get_auth_headers()
    )

    logger.info(f"Request to PlayFab returned status code: {req.status_code}")
    if req.status_code == 200:
        user_info = req.json().get("data", {}).get("UserInfo", {})
        nick_name = user_info.get("Username") or nick_name or f"Player{random.randint(1000, 9999)}"
        logger.info(f"Authenticated user {user_id.lower()} with nickname: {nick_name}")
        return_function_json("SigmaAuth", {}, user_id)
        return jsonify({
            'resultCode': 1,
            'message': f'Authenticated user {user_id.lower()} title {settings.TitleId.lower()}',
            'userId': f'{user_id.upper()}',
            'nickname': nick_name
        })
    else:
        logger.error("Failed to get user account info from PlayFab")
        return jsonify({
            'resultCode': 0,
            'message': "Something went wrong",
            'userId': None,
            'nickname': None
        })

# Photon Webhook Endpoints
@app.route("/PathCreate", methods=["POST"])
def path_create():
    rjson = request.get_json()
    user_id = rjson.get("UserId")
    return return_function_json("RoomCreated", rjson, user_id)

@app.route("/PathJoin", methods=["POST"])
def path_join():
    rjson = request.get_json()
    user_id = rjson.get("UserId")
    return return_function_json("RoomJoined", rjson, user_id)

@app.route("/PathLeave", methods=["POST"])
def path_leave():
    rjson = request.get_json()
    user_id = rjson.get("UserId")
    rjson["Type"] = "ClientDisconnect"
    return return_function_json("RoomLeft", rjson, user_id)

@app.route("/PathClose", methods=["POST"])
def path_close():
    rjson = request.get_json()
    user_id = rjson.get("UserId")
    rjson["Type"] = "Close"
    return return_function_json("RoomClosed", rjson, user_id)

@app.route("/PathRaiseEvent", methods=["POST"])
def path_raise_event():
    rjson = request.get_json()
    user_id = rjson.get("UserId")
    return return_function_json("RoomEventRaised", rjson, user_id)

@app.route("/PathSetProperties", methods=["POST"])
def path_set_properties():
    rjson = request.get_json()
    user_id = rjson.get("UserId")
    return return_function_json("RoomPropertyUpdated", rjson, user_id)

# New Endpoints (Total >50 with TitleData keys and routes)
@app.route("/api/GetLeaderboard", methods=["POST"])
def get_leaderboard():
    rjson = request.get_json()
    statistic_name = rjson.get("StatisticName", "GlobalScore")
    return return_function_json("GetLeaderboard", {"StatisticName": statistic_name}, rjson.get("PlayFabId"))

@app.route("/api/UpdateStats", methods=["POST"])
def update_stats():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "Statistics"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("UpdatePlayerStatistics", rjson.get("Statistics"), rjson.get("PlayFabId"))

@app.route("/api/GetInventory", methods=["POST"])
def get_inventory():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    return return_function_json("GetUserInventory", {}, playfab_id)

@app.route("/api/GrantCurrency", methods=["POST"])
def grant_currency():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "Currency", "Amount"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    response = requests.post(
        f"https://{settings.TitleId}.playfabapi.com/Admin/AddUserVirtualCurrency",
        json={
            "PlayFabId": rjson.get("PlayFabId"),
            "VirtualCurrency": rjson.get("Currency"),
            "Amount": rjson.get("Amount")
        },
        headers=settings.get_auth_headers()
    )
    return jsonify(response.json().get("data", {})), response.status_code

@app.route("/api/ReportPlayer", methods=["POST"])
def report_player():
    rjson = request.get_json()
    required_fields = ["ReporterId", "ReportedId", "Reason"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('ReporterId')} reported {rjson.get('ReportedId')} for {rjson.get('Reason')}")
    return jsonify({"success": True, "message": "Report submitted"}), 200

@app.route("/api/AddFriend", methods=["POST"])
def add_friend():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "FriendId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("AddFriend", {"FriendPlayFabId": rjson.get("FriendId")}, rjson.get("PlayFabId"))

@app.route("/api/RemoveFriend", methods=["POST"])
def remove_friend():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "FriendId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("RemoveFriend", {"FriendPlayFabId": rjson.get("FriendId")}, rjson.get("PlayFabId"))

@app.route("/api/GetFriendsList", methods=["POST"])
def get_friends_list():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    return return_function_json("GetFriendsList", {}, playfab_id)

@app.route("/api/CreateParty", methods=["POST"])
def create_party():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    party_id = generate_session_id()
    logger.info(f"Created party {party_id} for {playfab_id}")
    return jsonify({"success": True, "PartyId": party_id}), 200

@app.route("/api/JoinParty", methods=["POST"])
def join_party():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "PartyId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} joined party {rjson.get('PartyId')}")
    return jsonify({"success": True}), 200

@app.route("/api/LeaveParty", methods=["POST"])
def leave_party():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "PartyId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} left party {rjson.get('PartyId')}")
    return jsonify({"success": True}), 200

@app.route("/api/InviteToParty", methods=["POST"])
def invite_to_party():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "FriendId", "PartyId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} invited {rjson.get('FriendId')} to party {rjson.get('PartyId')}")
    return jsonify({"success": True}), 200

@app.route("/api/GetDailyQuests", methods=["POST"])
def get_daily_quests():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    return jsonify({"quests": [{"id": "quest1", "name": "Tag 5 Players", "reward": 100}]}), 200

@app.route("/api/CompleteQuest", methods=["POST"])
def complete_quest():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "QuestId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("CompleteQuest", {"QuestId": rjson.get("QuestId")}, rjson.get("PlayFabId"))

@app.route("/api/GetAchievements", methods=["POST"])
def get_achievements():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    return return_function_json("GetPlayerAchievements", {}, playfab_id)

@app.route("/api/UnlockAchievement", methods=["POST"])
def unlock_achievement():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "AchievementId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("UnlockAchievement", {"AchievementId": rjson.get("AchievementId")}, rjson.get("PlayFabId"))

@app.route("/api/GetSeasonalEvent", methods=["POST"])
def get_seasonal_event():
    return jsonify({
        "EventName": "WinterFest2025",
        "StartDate": "2025-12-01",
        "EndDate": "2026-01-15",
        "Rewards": ["snow_hat", "ice_gloves"]
    }), 200

@app.route("/api/SubmitFeedback", methods=["POST"])
def submit_feedback():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "Feedback"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Feedback from {rjson.get('PlayFabId')}: {rjson.get('Feedback')}")
    return jsonify({"success": True}), 200

@app.route("/api/GetServerStatus", methods=["GET"])
def get_server_status():
    return jsonify({
        "status": "online",
        "uptime": str(timedelta(seconds=int(time.time() - app.start_time))),
        "activePlayers": random.randint(100, 1000)
    }), 200

@app.route("/api/UpdateProfile", methods=["POST"])
def update_profile():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "DisplayName"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("UpdateUserTitleDisplayName", {"DisplayName": rjson.get("DisplayName")}, rjson.get("PlayFabId"))

@app.route("/api/GetCosmetics", methods=["POST"])
def get_cosmetics():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    return jsonify({"cosmetics": REDEEMABLE_ITEMS}), 200

@app.route("/api/EquipCosmetic", methods=["POST"])
def equip_cosmetic():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "CosmeticId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} equipped cosmetic {rjson.get('CosmeticId')}")
    return jsonify({"success": True}), 200

@app.route("/api/TradeItems", methods=["POST"])
def trade_items():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "RecipientId", "Items"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    return return_function_json("TradeItems", {"RecipientId": rjson.get("RecipientId"), "Items": rjson.get("Items")}, rjson.get("PlayFabId"))

@app.route("/api/CreateGuild", methods=["POST"])
def create_guild():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "GuildName"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    guild_id = generate_session_id()
    logger.info(f"Guild {guild_id} created by {rjson.get('PlayFabId')}: {rjson.get('GuildName')}")
    return jsonify({"success": True, "GuildId": guild_id}), 200

@app.route("/api/JoinGuild", methods=["POST"])
def join_guild():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "GuildId"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} joined guild {rjson.get('GuildId')}")
    return jsonify({"success": True}), 200

@app.route("/api/GetGuildInfo", methods=["POST"])
def get_guild_info():
    rjson = request.get_json()
    guild_id = rjson.get("GuildId")
    if not guild_id:
        return jsonify({"error": "Missing GuildId"}), 400
    return jsonify({"guild": {"id": guild_id, "name": "SampleGuild", "members": 10}}), 200

@app.route("/api/GetMatchHistory", methods=["POST"])
def get_match_history():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    return jsonify({"matches": [{"id": "match1", "result": "win", "date": "2025-09-11"}]}), 200

@app.route("/api/StartMatchmaking", methods=["POST"])
def start_matchmaking():
    rjson = request.get_json()
    required_fields = ["PlayFabId", "GameMode"]
    missing_fields = validate_input(rjson, required_fields)
    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} started matchmaking for {rjson.get('GameMode')}")
    return jsonify({"success": True, "MatchId": generate_session_id()}), 200

@app.route("/api/CancelMatchmaking", methods=["POST"])
def cancel_matchmaking():
    rjson = request.get_json()
    playfab_id = rjson.get("PlayFabId")
    if not playfab_id:
        return jsonify({"error": "Missing PlayFabId"}), 400
    logger.info(f"Player {rjson.get('PlayFabId')} cancelled matchmaking")
    return jsonify({"success": True}), 200

@app.route("/api/GetServerConfig", methods=["GET"])
def get_server_config():
    return jsonify({
        "version": "1.2.3",
        "maintenance": False,
        "regions": ["US", "EU", "AS"],
        "maxPlayers": 1000
    }), 200

if __name__ == "__main__":
    app.start_time = time.time()
    logger.info(f"Server starting on port 9080 with TitleId: {settings.TitleId}")
    app.run(host="0.0.0.0", port=9080, debug=False)
