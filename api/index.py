import os
import json
import urllib.request
from flask import Flask

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("https://discord.com/api/webhooks/1553160386528809042/R57uwwFZE2ai1GVzC8yUZBB4AkOdwfzCvhBSZPrOfyqLioWIY8e7a-We6qKNG9ykiT1w")


def send_webhook():
    if not WEBHOOK_URL:
        print("ERROR: ATTESTATION_WEBHOOK_URL is not set")
        return

    payload = {
        "username": "Oculus Taggers",
        "content": "bird.."
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

    except Exception as e:
        print("Discord webhook error:", str(e))


@app.route("/")
def home():
    send_webhook()

    return """
    <h1>Oculus Taggers</h1>
    <p>bird.</p>
    """


@app.route("/api/test")
def test():
    send_webhook()

    return {
        "ok": True,
        "message": "Webhook request sent"
    }
