import os
import json
import urllib.request
from flask import Flask

app = Flask(__name__)

# Get the Discord webhook from Vercel Environment Variables
WEBHOOK_URL = os.environ.get("ATTESTATION_WEBHOOK_URL")


def send_webhook():
    if not WEBHOOK_URL:
        print("ERROR: ATTESTATION_WEBHOOK_URL is not set")
        return False

    payload = {
        "username": "Oculus Taggers",
        "content": "Nigga Sybau"
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
    send_webhook()

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Oculus Taggers</title>
    </head>
    <body>
        <h1>Oculus Taggers</h1>
        <p>Nigga Sybau</p>
    </body>
    </html>
    """


@app.route("/api/test")
def test():
    success = send_webhook()

    return {
        "ok": success,
        "message": "Webhook sent" if success else "Webhook failed"
    }
