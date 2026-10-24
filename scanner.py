import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

SESSION = "resenha_session"
DELAY_BETWEEN = 8  # seconds between sources

client = TelegramClient(SESSION, API_ID, API_HASH)

def auto_pull():
    """Pull latest code from GitHub before starting."""
    try:
        print("Checking for updates...")
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            if "Already up to date" in result.stdout or "Already up to date" in result.stderr:
                print("Already up to date.")
            else:
                print("Updated. Restart recommended if code changed a lot.")
                print(result.stdout.strip())
        else:
            print("Git pull failed (continuing with current code):", result.stderr.strip())
    except Exception as e:
        print("Could not auto-update:", e)

def extract_price(text: str):
    if not text:
        return None
    patterns = [
        r"\$\s*([0-9]+\.?[0-9]*)",
        r"([0-9]+\.?[0-9]*)\s*USDT",
        r"Price:\s*\$?([0-9]+\.?[0-9]*)",
        r"Unit Price:\s*\$?([0-9]+\.?[0-9]*)",
        r"([0-9]+\.?[0-9]*)\s*USD",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            try:
                return float(m.group(1))
            except:
                pass
    return None

async def click_button(msg, text):
    if not msg or not msg.reply_markup:
        return False
    text_lower = text.lower()
    markup = msg.reply_markup
    if not hasattr(markup, "rows"):
        return False
    for row in markup.rows:
        for btn in row.buttons:
            if hasattr(btn, "text") and text_lower in btn.text.lower():
                try:
                    await msg.click(btn)
                    return True
                except:
                    pass
    return False

async def navigate(entity, steps):
    last_msg = None
    for step in steps:
        try:
            if step.startswith("/"):
                await client.send_message(entity, step)
            else:
                msgs = await client.get_messages(entity, limit=1)
                if msgs and await click_button(msgs[0], step):
                    pass
                else:
                    await client.send_message(entity, step)
            await asyncio.sleep(2.5)
            msgs = await client.get_messages(entity, limit=2)
            last_msg = msgs[0] if msgs else None
        except FloodWaitError as e:
            print(f"FloodWait {e.seconds}s on {entity.username or entity.id}")
            await asyncio.sleep(e.seconds + 5)
            return None
        except Exception as e:
            print(f"Nav error: {e}")
            return None
    return last_msg

async def scan_source(source, keyword):
    try:
        entity = await client.get_entity(source["username"])
        msg = await navigate(entity, source.get("steps", ["/start"]))
        if not msg or not msg.text:
            return {"source": source["id"], "status": "no_msg", "price": None}

        text = msg.text.lower()
        if keyword.lower() in text:
            price = extract_price(msg.text)
            return {
                "source": source["id"],
                "username": source["username"],
                "status": "found",
                "price": price,
                "snippet": msg.text[:180],
                "at": datetime.utcnow().isoformat()
            }

        if msg.reply_markup:
            clicked = await click_button(msg, keyword)
            if clicked:
                await asyncio.sleep(2.5)
                msgs = await client.get_messages(entity, limit=1)
                if msgs and msgs[0].text:
                    price = extract_price(msgs[0].text)
                    return {
                        "source": source["id"],
                        "username": source["username"],
                        "status": "found",
                        "price": price,
                        "snippet": msgs[0].text[:180],
                        "at": datetime.utcnow().isoformat()
                    }

        return {"source": source["id"], "status": "not_found", "price": None}
    except FloodWaitError as e:
        print(f"FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds + 10)
        return {"source": source["id"], "status": "flood", "price": None}
    except Exception as e:
        return {"source": source["id"], "status": f"error: {str(e)[:80]}", "price": None}

async def main():
    auto_pull()

    print("Connecting...")
    await client.connect()

    if not await client.is_user_authorized():
        await client.send_code_request(PHONE)
        code = input("Please enter the code you received: ").strip()
        try:
            await client.sign_in(PHONE, code)
        except SessionPasswordNeededError:
            password = input("Please enter your password: ").strip()
            await client.sign_in(password=password)

    print("Connected. Ready.")

    with open("sources.json") as f:
        config = json.load(f)
    sources = config.get("sources", [])

    keyword = input("Keyword to search (or enter for skip): ").strip()
    if not keyword:
        print("No keyword. Exiting.")
        return

    results = []
    for src in sources:
        print(f"Checking {src['id']} ({src['username']})...")
        res = await scan_source(src, keyword)
        results.append(res)
        print(f"  -> {res['status']} {res.get('price') or ''}")
        await asyncio.sleep(DELAY_BETWEEN)

    out = {
        "keyword": keyword,
        "scanned_at": datetime.utcnow().isoformat(),
        "results": results
    }
    with open("last_scan.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    valid = [r for r in results if r.get("price")]
    valid.sort(key=lambda x: x["price"])
    print("\n=== Summary ===")
    for r in valid:
        print(f"{r['username']}: ${r['price']}")
    if valid:
        avg = sum(r["price"] for r in valid) / len(valid)
        print(f"Average: ${avg:.2f}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
