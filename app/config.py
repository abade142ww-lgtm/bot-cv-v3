import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

print("DEBUG: BOT_TOKEN loaded =", "YES" if BOT_TOKEN else "NO")
print("DEBUG: BASE_URL loaded =", BASE_URL if BASE_URL else "EMPTY")
print("DEBUG: WEBHOOK_SECRET loaded =", "YES" if WEBHOOK_SECRET else "NO")
print("DEBUG: ADMIN_CHAT_ID loaded =", ADMIN_CHAT_ID if ADMIN_CHAT_ID else "EMPTY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in .env")

if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET is missing in .env")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY")
