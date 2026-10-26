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
DELAY_BETWEEN = 5
FOLDER_NAME = "LOJAS"  # Only scan bots inside this folder

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

def is_out_of_stock(text: str):
    if not text:
        return False
    low = text.lower()
    return any(x in low for x in [
        "out of stock", "sem estoque", "no stock", "sold out",
        "esgotado", "unavailable", "out of stock"
    ])

def clean_text(text):
    if not text:
        return ""
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    return cleaned.lower().strip()

async def click_button(msg, text):
    if not msg or not msg.reply_markup:
        return False
    target = clean_text(text)
    markup = msg.reply_markup
    if not hasattr(markup, "rows"):
        return False

    try:
        await msg.click(text=text)
        print(f"    Clicked by text: {text}")
        return True
    except Exception:
        pass

    for i, row in enumerate(markup.rows):
        for j, btn in enumerate(row.buttons):
            if hasattr(btn, "text"):
                btn_clean = clean_text(btn.text)
                if target in btn_clean or btn_clean in target:
                    try:
                        await msg.click(i, j)
                        print(f"    Clicked by index: {btn.text}")
                        return True
                    except Exception as e:
                        print(f"    Click error: {e}")
    return False

async def navigate(entity, steps):
    for step in steps:
        try:
            print(f"  Step: {step}")
            if step.startswith("/"):
                sent = await client.send_message(entity, step)
                print(f"    Sent: {step} (id={sent.id})")
                await asyncio.sleep(4)
            else:
                found = False
                for attempt in range(4):
                    msgs = await client.get_messages(entity, limit=8)
                    for m in msgs:
                        if m.reply_markup and await click_button(m, step):
                            found = True
                            break
                    if found:
                        break
                    await asyncio.sleep(2)
                if not found:
                    print(f"    Button '{step}' not found, sending as text")
                    await client.send_message(entity, step)
                await asyncio.sleep(4)
        except FloodWaitError as e:
            print(f"FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception as e:
            print(f"Nav error: {e}")
            return False
    return True

async def get_last_restock(entity, keyword):
    """Try to find the most recent restock message related to the keyword."""
    try:
        msgs = await client.get_messages(entity, limit=30)
        keyword_lower = keyword.lower()
        for msg in msgs:
            if not msg.text or not msg.date:
                continue
            low = msg.text.lower()
            if keyword_lower in low and any(x in low for x in ["new stock", "restock", "added", "stock added", "reestock"]):
                return msg.date.isoformat()
    except:
        pass
    return None

async def scan_source(source, keyword):
    try:
        entity = await client.get_entity(source["username"])
        print(f"  Navigating {source['username']}...")
        ok = await navigate(entity, source.get("steps", ["/start"]))
        if not ok:
            return {"source": source["id"], "status": "nav_failed", "price": None}

        await asyncio.sleep(2)
        msgs = await client.get_messages(entity, limit=15)
        keyword_lower = keyword.lower()

        for msg in msgs:
            if not msg.text:
                continue
            if keyword_lower in msg.text.lower():
                price = extract_price(msg.text)
                out_of_stock = is_out_of_stock(msg.text)
                last_restock = await get_last_restock(entity, keyword)

                status = "out_of_stock" if out_of_stock else "found"
                print(f"  Found: {msg.text[:120]}...")
                return {
                    "source": source["id"],
                    "username": source["username"],
                    "status": status,
                    "price": price,
                    "out_of_stock": out_of_stock,
                    "last_restock": last_restock,
                    "snippet": msg.text[:300],
                    "at": datetime.now(timezone.utc).isoformat()
                }

        return {"source": source["id"], "status": "not_found", "price": None}
    except FloodWaitError as e:
        print(f"FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds + 10)
        return {"source": source["id"], "status": "flood", "price": None}
    except Exception as e:
        return {"source": source["id"], "status": f"error: {str(e)[:100]}", "price": None}

async def get_lojas_bots():
    """Return only bots that are inside the LOJAS folder."""
    bots = []
    try:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            if d.folder and d.folder.title and d.folder.title.upper() == FOLDER_NAME.upper():
                if d.entity and hasattr(d.entity, "username") and d.entity.username:
                    bots.append("@" + d.entity.username)
    except Exception as e:
        print(f"Could not read folders: {e}")
    return bots

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

    # Optional: show which bots are in LOJAS
    lojas = await get_lojas_bots()
    if lojas:
        print(f"Bots found in '{FOLDER_NAME}' folder: {', '.join(lojas)}\n")

    with open("sources.json", encoding="utf-8") as f:
        config = json.load(f)
    sources = config.get("sources", [])

    # Filter sources to only those that exist in LOJAS folder (if we could read it)
    if lojas:
        sources = [s for s in sources if s["username"] in lojas]
        if not sources:
            print("None of the configured sources are inside the LOJAS folder.")
            print("Add the correct bots to sources.json or move them to the LOJAS folder.")
            await client.disconnect()
            return

    keyword = input("Keyword to search: ").strip()
    if not keyword:
        print("No keyword. Exiting.")
        return

    results = []
    for src in sources:
        print(f"\nChecking {src['id']} ({src['username']})...")
        res = await scan_source(src, keyword)
        results.append(res)
        status_extra = ""
        if res.get("out_of_stock"):
            status_extra = " (OUT OF STOCK)"
        if res.get("last_restock"):
            status_extra += f" | last restock: {res['last_restock']}"
        print(f"  -> {res['status']} {res.get('price') or ''}{status_extra}")
        await asyncio.sleep(DELAY_BETWEEN)

    out = {
        "keyword": keyword,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "results": results
    }
    with open("last_scan.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    valid = [r for r in results if r.get("price") and not r.get("out_of_stock")]
    valid.sort(key=lambda x: x["price"])
    print("\n=== Summary (in stock) ===")
    for r in valid:
        extra = f" | restock: {r.get('last_restock')}" if r.get("last_restock") else ""
        print(f"{r['username']}: ${r['price']}{extra}")
    if valid:
        avg = sum(r["price"] for r in valid) / len(valid)
        print(f"Average: ${avg:.2f}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
