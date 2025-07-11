import os
import requests
import logging
from time import sleep
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ——— CONFIG ———
def _raise_env_error(var_name: str):
    raise RuntimeError(f"Environment variable {var_name} is required but not set")

API_KEY           = os.getenv("API_KEY")           or _raise_env_error("API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL") or _raise_env_error("SLACK_WEBHOOK_URL")

BASE_ACCOUNT_IDS = {
    "IN":      os.getenv("ACCOUNT_IN")      or _raise_env_error("ACCOUNT_IN"),
    "OUT":     os.getenv("ACCOUNT_OUT")     or _raise_env_error("ACCOUNT_OUT"),
    "SAVINGS": os.getenv("ACCOUNT_SAVINGS") or _raise_env_error("ACCOUNT_SAVINGS"),
}

# Create reverse lookup for account IDs
ACCOUNT_ID_TO_NAME = {v: k for k, v in BASE_ACCOUNT_IDS.items()}

BASE_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type":  "application/json",
}

# ——— LOGGING ———
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ——— FUNCTIONS ———

def fetch_transactions(account_id: str) -> list[dict]:
    url = f"https://api.mercury.com/api/v1/account/{account_id}/transactions"
    params = {"limit": 15, "order": "desc"}
    resp = requests.get(url, headers=BASE_HEADERS, params=params)
    resp.raise_for_status()
    txs = resp.json().get("transactions", [])
    logger.info("Fetched %d txs for account %s", len(txs), account_id)
    return txs

def format_transaction_for_slack(tx: dict, acct_name: str) -> str:
    dt_utc = datetime.fromisoformat(tx["createdAt"].replace("Z", "+00:00"))
    dt_pacific = dt_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    formatted_date = dt_pacific.strftime("%-I:%M %p on %B %d, %Y")

    amount = tx["amount"]
    money = f"${abs(amount):,.2f}"

    counterparty_id = tx.get("counterpartyId")
    counterparty_nickname = ACCOUNT_ID_TO_NAME.get(counterparty_id)
    counterparty_label = counterparty_nickname if counterparty_nickname else tx.get("counterpartyName", "Unknown")

    if amount > 0:
        emoji = "🟢💰"
        direction = f"{money} received in {acct_name} from {counterparty_label}"
    else:
        emoji = "🔴💸"
        direction = f"{money} sent from {acct_name} to {counterparty_label}"

    kind = tx.get("kind", "unknown")
    status = tx.get("status", "unknown")

    parts = [
        f"{emoji} {direction}",
        f"{kind} • Status: {status}",
        f"⏰ {formatted_date}"
    ]
    if tx.get("note"):
        parts.append(f"📝 {tx['note']}")
    if link := tx.get("dashboardLink"):
        parts.append(f"🔗 <{link}|View on Mercury>")
    return "\n".join(parts)

def send_transaction_to_slack(text: str, incoming: bool):
    color = "good" if incoming else "danger"
    payload = {"attachments": [{"color": color, "text": text}]}
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
    resp.raise_for_status()
    logger.info("Sent to Slack: %s (color=%s)", text.splitlines()[0], color)

def notify_new_transactions():
    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(minutes=30)
    logger.info("Alerting on txs since %s", cutoff.isoformat())

    for acct_name, acct_id in BASE_ACCOUNT_IDS.items():
        logger.info("Checking account %s", acct_name)
        txs = fetch_transactions(acct_id)

        recent = [
            tx for tx in txs
            if datetime.fromisoformat(tx["createdAt"].replace("Z", "+00:00")) >= cutoff
        ]

        if not recent:
            logger.info("No txs in last 30 min for %s", acct_name)
            continue

        for tx in reversed(recent):
            tx_id = tx.get("id", "<no-id>")
            logger.info("Dispatching tx id=%s, account=%s, amount=%s to Slack", tx_id, acct_name, tx["amount"])
            text = format_transaction_for_slack(tx, acct_name)
            send_transaction_to_slack(text, tx["amount"] > 0)
            sleep(0.1)

def main():
    notify_new_transactions()

if __name__ == "__main__":
    main()
