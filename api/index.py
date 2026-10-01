import os
import json
import urllib.request
from flask import Flask

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("https://discord.com/api/webhooks/1546613597918990366/-4GtvpE7Cn47bWsY0rv5W_O3rlkX4SmGiDjm8-_zJlhFAEBKqbJZQx4P2cyKGMKrfLnH")


def send_webhook():
    if not WEBHOOK_URL:
        print("ERROR: ATTESTATION_WEBHOOK_URL is not set")
        return

    payload = {
        "username": "Oculus Taggers",
        "content": "🔗 Someone opened the Oculus Taggers link!"
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        WEBHOOK_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "OculusTaggers/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print("Discord response:", response.status)

    except Exception as e:
        print("Discord webhook error:", str(e))


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
        <p>Link opened successfully.</p>
    </body>
    </html>
    """


@app.route("/api/test")
def test():
    send_webhook()

    return {
        "ok": True,
        "message": "Webhook request sent"
    }
