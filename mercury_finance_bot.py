import os
import json
import logging
import requests
from time import sleep
from datetime import datetime

def _raise_env_error(var_name: str):
    raise RuntimeError(f"Environment variable {var_name} is required but not set")

# ——— CONFIG ———
API_KEY           = os.getenv("API_KEY")            or _raise_env_error("API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")  or _raise_env_error("SLACK_WEBHOOK_URL")

BASE_ACCOUNT_IDS = {
    "IN":      os.getenv("ACCOUNT_IN")      or _raise_env_error("ACCOUNT_IN"),
    "OUT":     os.getenv("ACCOUNT_OUT")     or _raise_env_error("ACCOUNT_OUT"),
    "SAVINGS": os.getenv("ACCOUNT_SAVINGS") or _raise_env_error("ACCOUNT_SAVINGS"),
}

STATE_FILE   = "seen_tx_ids.json"
BASE_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type":  "application/json",
}

# ——— LOGGING SETUP ———
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def load_state() -> dict[str, set]:
    """
    Load mapping of account → set of seen transaction IDs.
    """
    if not os.path.exists(STATE_FILE):
        logger.info("No state file found; starting fresh.")
        return {acct: set() for acct in BASE_ACCOUNT_IDS}

    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    state = {acct: set(ids) for acct, ids in data.items()}
    logger.info("Loaded state: %s", {acct: len(ids) for acct, ids in state.items()})
    return state


def save_state(state: dict[str, set]):
    """
    Save the seen-IDs mapping back to JSON.
    """
    serializable = {acct: list(ids) for acct, ids in state.items()}
    with open(STATE_FILE, "w") as f:
        json.dump(serializable, f)
    logger.info("Saved state: %s", {acct: len(ids) for acct, ids in state.items()})


def format_transaction_for_slack(tx: dict) -> str:
    """
    Turn a Mercury transaction dict into a Slack-friendly message string.
    """
    # Parse & reformat timestamp
    date_str = tx["createdAt"]
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        formatted_date = dt.strftime("%-I:%M %p on %B %d, %Y")
    except Exception:
        formatted_date = date_str

    amount = tx["amount"]
    money = f"${abs(amount):,.2f}"
    if amount > 0:
        emoji, direction = "🟢💰", f"{money} received from"
    else:
        emoji, direction = "🔴💸", f"{money} sent to"

    kind_emoji = {
        "internalTransfer":      "🔁",
        "outgoingPayment":       "📤",
        "incomingDomesticWire":  "🏦",
        "creditCardTransaction": "🧾",
        "other":                 "🧩",
    }.get(tx.get("kind"), "❓")

    status_emoji = {
        "pending":   "⏳",
        "sent":      "✅",
        "cancelled": "🚫",
        "failed":    "❌",
    }.get(tx.get("status", "unknown"), "❓")

    parts = [
        f"{emoji} {direction} *{tx.get('counterpartyName','Unknown')}*",
        f"{kind_emoji} `{tx.get('kind','unknown')}` • {status_emoji} `Status: {tx.get('status','')}`",
        f"⏰ {formatted_date}",
    ]
    if tx.get("note"):
        parts.append(f"📝 {tx['note']}")
    if link := tx.get("dashboardLink"):
        parts.append(f"🔗 <{link}|View on Mercury>")

    return "\n".join(parts)


def send_to_slack(text: str):
    """
    POST a simple message payload to your Incoming Webhook.
    """
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text})
        resp.raise_for_status()
        logger.info("Sent to Slack: %.50s...", text.replace("\n", " "))
    except Exception as e:
        logger.error("Failed to send to Slack: %s", e)
        raise


def fetch_transactions(account_id: str) -> list[dict]:
    """
    Return up to 5 recent transactions for the given account ID.
    """
    url = f"https://api.mercury.com/api/v1/account/{account_id}/transactions"
    params = {"limit": 5, "order": "desc"}
    try:
        resp = requests.get(url, headers=BASE_HEADERS, params=params)
        resp.raise_for_status()
        txs = resp.json().get("transactions", [])
        logger.info("Fetched %d transactions for account %s", len(txs), account_id)
        return txs
    except Exception as e:
        logger.error("Error fetching transactions for %s: %s", account_id, e)
        return []


def notify_new_transactions():
    """
    For each account, fetch unseen transactions and send each one as its own Slack message.
    """
    logger.info("Starting notification run for accounts: %s", list(BASE_ACCOUNT_IDS.keys()))
    seen_state = load_state()

    for acct_name, acct_id in BASE_ACCOUNT_IDS.items():
        logger.info("Checking account %s (%s)", acct_name, acct_id)
        seen_ids = seen_state[acct_name]
        txs = fetch_transactions(acct_id)

        # Identify only those txs whose IDs we haven't seen; send oldest→newest
        new_txs = [tx for tx in reversed(txs) if tx["id"] not in seen_ids]

        if not new_txs:
            logger.info("No new transactions for %s.", acct_name)
            continue

        for tx in new_txs:
            text = format_transaction_for_slack(tx)
            send_to_slack(text)
            sleep(0.1)
            seen_ids.add(tx["id"])

        logger.info("Notified %d new transactions for %s.", len(new_txs), acct_name)

    save_state(seen_state)
    logger.info("Notification run complete.")


def main():
    notify_new_transactions()

if __name__ == "__main__":
    main()
