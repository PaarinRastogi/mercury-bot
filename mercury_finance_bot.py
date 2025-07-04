import os
import json
import requests
from time import sleep
from datetime import datetime

def _raise_env_error(var_name: str):
    raise RuntimeError(f"Environment variable {var_name} is required but not set")

# ——— CONFIG ———
API_KEY = os.getenv("API_KEY") or _raise_env_error("API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL") or _raise_env_error("SLACK_WEBHOOK_URL")

BASE_ACCOUNT_IDS = {
    "IN": os.getenv("ACCOUNT_IN") or _raise_env_error("ACCOUNT_IN"),
    "OUT": os.getenv("ACCOUNT_OUT") or _raise_env_error("ACCOUNT_OUT"),
    "SAVINGS": os.getenv("ACCOUNT_SAVINGS") or _raise_env_error("ACCOUNT_SAVINGS"),
}

# where we persist which tx IDs we’ve already seen
STATE_FILE = "seen_tx_ids.json"

BASE_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def load_state() -> dict[str, set]:
    """
    Load the mapping of account → set of seen transaction IDs.
    If the file doesn’t exist, initialize each account with an empty set.
    """
    if not os.path.exists(STATE_FILE):
        return {acct: set() for acct in BASE_ACCOUNT_IDS}
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
    # convert lists back to sets
    return {acct: set(ids) for acct, ids in data.items()}


def save_state(state: dict[str, set]):
    """
    Save the seen-IDs mapping, converting sets back to lists for JSON.
    """
    serializable = {acct: list(ids) for acct, ids in state.items()}
    with open(STATE_FILE, "w") as f:
        json.dump(serializable, f)


def format_transaction_for_slack(tx: dict) -> str:
    """
    Turn a Mercury transaction dict into a Slack-friendly message string.
    """
    # 1) Parse & reformat timestamp
    date_str = tx["createdAt"]
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        formatted_date = dt.strftime("%-I:%M %p on %B %d, %Y")
    except Exception:
        formatted_date = date_str

    # 2) Amount direction & emoji
    amount = tx["amount"]
    money = f"${abs(amount):,.2f}"
    if amount > 0:
        emoji = "🟢💰"
        direction = f"{money} received from"
    else:
        emoji = "🔴💸"
        direction = f"{money} sent to"

    # 3) Kind & status emojis
    kind_emoji = {
        "internalTransfer": "🔁",
        "outgoingPayment": "📤",
        "incomingDomesticWire": "🏦",
        "creditCardTransaction": "🧾",
        "other": "🧩",
    }.get(tx.get("kind"), "❓")

    status_emoji = {
        "pending": "⏳",
        "sent": "✅",
        "cancelled": "🚫",
        "failed": "❌",
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
    payload = {"text": text}
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
    resp.raise_for_status()


def fetch_transactions(account_id: str) -> list[dict]:
    """
    Return up to 5 recent transactions (JSON list) for the given account ID.
    """
    url = f"https://api.mercury.com/api/v1/account/{account_id}/transactions"
    params = {"limit": 5, "order": "desc"}
    resp = requests.get(url, headers=BASE_HEADERS, params=params)
    resp.raise_for_status()
    return resp.json().get("transactions", [])


def notify_new_transactions():
    """
    For each account, fetch any unseen transactions and send each one as its own Slack message.
    """
    seen_state = load_state()

    for acct_name, acct_id in BASE_ACCOUNT_IDS.items():
        seen_ids = seen_state[acct_name]
        txs = fetch_transactions(acct_id)

        # Identify only those txs whose IDs we haven't seen
        # We send oldest→newest so messages are chronological
        new_txs = [tx for tx in reversed(txs) if tx["id"] not in seen_ids]

        if not new_txs:
            print(f"No new transactions for {acct_name}.")
            continue

        for tx in new_txs:
            text = format_transaction_for_slack(tx)
            send_to_slack(text)
            sleep(0.1)
            # mark it as sent
            seen_ids.add(tx["id"])

        print(f"Notified {len(new_txs)} new transactions for {acct_name}.")

    # persist our updated seen-ID lists
    save_state(seen_state)


def main():
    notify_new_transactions()


if __name__ == "__main__":
    main()
