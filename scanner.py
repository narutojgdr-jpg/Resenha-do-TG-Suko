import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

SESSION = "resenha_session"
DELAY_BETWEEN = 6

client = TelegramClient(SESSION, API_ID, API_HASH)

def auto_pull():
    try:
        print("Checking for updates...")
        result = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            if "Already up to date" not in result.stdout and "Already up to date" not in result.stderr:
                print("Updated.")
                print(result.stdout.strip())
            else:
                print("Already up to date.")
        else:
            print("Git pull failed:", result.stderr.strip())
    except Exception as e:
        print("Could not auto-update:", e)

def extract_price(text: str):
    if not text:
        return None
    patterns = [
        r"([0-9]+\.?[0-9]*)\s*USDT",
        r"\$\s*([0-9]+\.?[0-9]*)",
        r"Price:\s*\$?([0-9]+\.?[0-9]*)",
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
    text_lower = text.lower().strip()
    markup = msg.reply_markup
    if not hasattr(markup, "rows"):
        return False
    for row in markup.rows:
        for btn in row.buttons:
            if hasattr(btn, "text"):
                btn_text = btn.text.lower().strip()
                if text_lower in btn_text or btn_text in text_lower:
                    try:
                        await msg.click(btn)
                        print(f"    Clicked button: {btn.text}")
                        return True
                    except Exception as e:
                        print(f"    Click failed: {e}")
    return False

async def navigate(entity, steps):
    for step in steps:
        try:
            print(f"  Step: {step}")
            if step.startswith("/"):
                await client.send_message(entity, step)
            else:
                msgs = await client.get_messages(entity, limit=5)
                clicked = False
                for m in msgs:
                    if await click_button(m, step):
                        clicked = True
                        break
                if not clicked:
                    print(f"    Button not found, sending as text: {step}")
                    await client.send_message(entity, step)
            await asyncio.sleep(3)
        except FloodWaitError as e:
            print(f"FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            print(f"Nav error: {e}")
            return False
    return True

async def scan_source(source, keyword):
    try:
        entity = await client.get_entity(source["username"])
        print(f"  Navigating {source['username']}...")
        ok = await navigate(entity, source.get("steps", ["/start"]))
        if not ok:
            return {"source": source["id"], "status": "nav_failed", "price": None}

        await asyncio.sleep(2)
        msgs = await client.get_messages(entity, limit=10)
        keyword_lower = keyword.lower()

        for msg in msgs:
            if not msg.text:
                continue
            if keyword_lower in msg.text.lower():
                price = extract_price(msg.text)
                print(f"  Found in message: {msg.text[:80]}...")
                return {
                    "source": source["id"],
                    "username": source["username"],
                    "status": "found",
                    "price": price,
                    "snippet": msg.text[:250],
                    "at": datetime.now(timezone.utc).isoformat()
                }

        # Try clicking any button that contains the keyword
        for msg in msgs:
            if msg.reply_markup and await click_button(msg, keyword):
                await asyncio.sleep(2.5)
                new_msgs = await client.get_messages(entity, limit=5)
                for nm in new_msgs:
                    if nm.text and keyword_lower in nm.text.lower():
                        price = extract_price(nm.text)
                        return {
                            "source": source["id"],
                            "username": source["username"],
                            "status": "found",
                            "price": price,
                            "snippet": nm.text[:250],
                            "at": datetime.now(timezone.utc).isoformat()
                        }

        return {"source": source["id"], "status": "not_found", "price": None}
    except FloodWaitError as e:
        print(f"FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds + 10)
        return {"source": source["id"], "status": "flood", "price": None}
    except Exception as e:
        return {"source": source["id"], "status": f"error: {str(e)[:100]}", "price": None}

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

    print("Connected. Ready.\n")

    with open("sources.json") as f:
        config = json.load(f)
    sources = config.get("sources", [])

    keyword = input("Keyword to search: ").strip()
    if not keyword:
        print("No keyword. Exiting.")
        return

    results = []
    for src in sources:
        print(f"\nChecking {src['id']} ({src['username']})...")
        res = await scan_source(src, keyword)
        results.append(res)
        print(f"  -> {res['status']} {res.get('price') or ''}")
        await asyncio.sleep(DELAY_BETWEEN)

    out = {
        "keyword": keyword,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
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
