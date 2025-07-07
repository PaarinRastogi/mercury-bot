import os
import requests
import logging
from time import sleep
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo



# ——— CONFIG ———
API_KEY           = os.getenv("API_KEY")           or _raise_env_error("API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL") or _raise_env_error("SLACK_WEBHOOK_URL")
BASE_ACCOUNT_IDS = {
    "IN":      os.getenv("ACCOUNT_IN")      or _raise_env_error("ACCOUNT_IN"),
    "OUT":     os.getenv("ACCOUNT_OUT")     or _raise_env_error("ACCOUNT_OUT"),
    "SAVINGS": os.getenv("ACCOUNT_SAVINGS") or _raise_env_error("ACCOUNT_SAVINGS"),
}

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

def _raise_env_error(var_name: str):
    raise RuntimeError(f"Environment variable {var_name} is required but not set")

def fetch_transactions(account_id: str) -> list[dict]:
    """Get up to 15 most recent transactions."""
    url = f"https://api.mercury.com/api/v1/account/{account_id}/transactions"
    params = {"limit": 15, "order": "desc"}
    resp = requests.get(url, headers=BASE_HEADERS, params=params)
    resp.raise_for_status()
    txs = resp.json().get("transactions", [])
    logger.info("Fetched %d txs for account %s", len(txs), account_id)
    return txs

def format_transaction_for_slack(tx: dict, acct_name: str) -> str:
    """Build message text for one transaction."""
    # timestamp
    # parse UTC timestamp
    dt_utc = datetime.fromisoformat(tx["createdAt"].replace("Z", "+00:00"))
    # convert to America/Los_Angeles (handles PST/PDT automatically)
    dt_pacific = dt_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    # format in local time
    formatted_date = dt_pacific.strftime("%-I:%M %p on %B %d, %Y")

    amount = tx["amount"]
    money = f"${abs(amount):,.2f}"
    if amount > 0:
        emoji, direction = "🟢💰", f"{money} received in {acct_name} from"
    else:
        emoji, direction = "🔴💸", f"{money} sent from {acct_name} to"

    kind = tx.get("kind", "unknown")
    status = tx.get("status", "unknown")

    parts = [
        f"{emoji} {direction} *{tx.get('counterpartyName','Unknown')}*",
        f"{kind} • Status: {status}",
        f"⏰ {formatted_date}"
    ]
    if tx.get("note"):
        parts.append(f"📝 {tx['note']}")
    if link := tx.get("dashboardLink"):
        parts.append(f"🔗 <{link}|View on Mercury>")
    return "\n".join(parts)

def send_transaction_to_slack(text: str, incoming: bool):
    """Post a single Slack attachment with green or red bar."""
    color = "good" if incoming else "danger"
    payload = {"attachments":[{"color": color, "text": text}]}
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

        # filter to last-30min
        recent = []
        for tx in txs:
            dt = datetime.fromisoformat(tx["createdAt"].replace("Z", "+00:00"))
            if dt >= cutoff:
                recent.append(tx)

        if not recent:
            logger.info("No txs in last 30 min for %s", acct_name)
            continue

        for tx in reversed(recent):  # oldest→newest
            text = format_transaction_for_slack(tx, acct_name)
            send_transaction_to_slack(text, tx["amount"] > 0)
            sleep(0.1)

def main():
    notify_new_transactions()

if __name__ == "__main__":
    main()
