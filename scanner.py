import asyncio
import json
import os
import re
import subprocess
import logging
import signal
from datetime import datetime, timezone
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from dotenv import load_dotenv

logging.getLogger("telethon").setLevel(logging.ERROR)

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

SESSION = "resenha_session"
DELAY_BETWEEN = 3
FOLDER_NAME = "LOJAS"

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
        "esgotado", "unavailable"
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
                        return True
                    except Exception:
                        pass
    return False

async def navigate(entity, steps):
    for step in steps:
        try:
            if step.startswith("/"):
                await client.send_message(entity, step)
                await asyncio.sleep(2.5)
            else:
                found = False
                for attempt in range(2):
                    msgs = await client.get_messages(entity, limit=5)
                    for m in msgs:
                        if m.reply_markup and await click_button(m, step):
                            found = True
                            break
                    if found:
                        break
                    await asyncio.sleep(1)
                if not found:
                    await client.send_message(entity, step)
                await asyncio.sleep(2.5)
        except FloodWaitError as e:
            print(f"FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 2)
            return False
        except Exception:
            return False
    return True

async def get_last_restock(entity, keyword):
    try:
        msgs = await client.get_messages(entity, limit=15)
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

async def scan_bot(username, keyword, steps=None):
    if steps is None:
        steps = ["/start"]
    try:
        entity = await client.get_entity(username)
        ok = await navigate(entity, steps)
        if not ok:
            return {"username": username, "status": "nav_failed", "price": None}

        await asyncio.sleep(1)
        msgs = await client.get_messages(entity, limit=8)
        keyword_lower = keyword.lower()

        for msg in msgs:
            if not msg.text:
                continue
            if keyword_lower in msg.text.lower():
                price = extract_price(msg.text)
                out_of_stock = is_out_of_stock(msg.text)
                last_restock = await get_last_restock(entity, keyword)

                status = "out_of_stock" if out_of_stock else "found"
                print(f"  Found: {msg.text[:80]}...")
                return {
                    "username": username,
                    "status": status,
                    "price": price,
                    "out_of_stock": out_of_stock,
                    "last_restock": last_restock,
                    "snippet": msg.text[:200],
                    "at": datetime.now(timezone.utc).isoformat()
                }

        return {"username": username, "status": "not_found", "price": None}
    except FloodWaitError as e:
        print(f"FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds + 3)
        return {"username": username, "status": "flood", "price": None}
    except Exception as e:
        err = str(e)
        if "Security error" in err or "old message" in err.lower() or "Too many messages" in err:
            return {"username": username, "status": "skipped_flood", "price": None}
        return {"username": username, "status": f"error: {err[:50]}", "price": None}

async def get_lojas_bots():
    bots = []
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        for f in result.filters:
            if isinstance(f, types.DialogFilter):
                title = getattr(f, "title", "")
                if isinstance(title, types.TextWithEntities):
                    title = title.text
                if str(title).upper() == FOLDER_NAME.upper():
                    for peer in getattr(f, "include_peers", []):
                        try:
                            entity = await client.get_entity(peer)
                            if hasattr(entity, "username") and entity.username:
                                uname = "@" + entity.username
                                if uname not in bots:
                                    bots.append(uname)
                        except Exception:
                            pass
                    break
    except Exception as e:
        print(f"Could not read folders: {e}")
    return bots

async def cleanup():
    """Mark recent dialogs as read and disconnect cleanly."""
    try:
        if client.is_connected():
            print("\nCleaning up...")
            try:
                await client.get_dialogs(limit=10)
            except:
                pass
            await client.disconnect()
            print("Disconnected.")
    except Exception:
        pass

def signal_handler(sig, frame):
    print("\nCtrl+C detected. Cleaning up...")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(cleanup())
    except:
        pass
    raise SystemExit

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

    # Catch up and reduce flood
    try:
        print("Syncing updates...")
        await client.catch_up()
        await asyncio.sleep(1)
    except Exception:
        pass

    lojas_bots = await get_lojas_bots()
    if not lojas_bots:
        print(f"No bots found in '{FOLDER_NAME}' folder.")
        await cleanup()
        return

    print(f"Found {len(lojas_bots)} bots in '{FOLDER_NAME}'.\n")

    custom_steps = {}
    try:
        with open("sources.json", encoding="utf-8") as f:
            config = json.load(f)
            for s in config.get("sources", []):
                custom_steps[s["username"]] = s.get("steps", ["/start"])
    except:
        pass

    keyword = input("Keyword to search: ").strip()
    if not keyword:
        print("No keyword. Exiting.")
        await cleanup()
        return

    results = []
    try:
        for i, username in enumerate(lojas_bots, 1):
            steps = custom_steps.get(username, ["/start"])
            print(f"\n[{i}/{len(lojas_bots)}] Checking {username}...")
            res = await scan_bot(username, keyword, steps)
            results.append(res)
            status_extra = ""
            if res.get("out_of_stock"):
                status_extra = " (OUT OF STOCK)"
            if res.get("last_restock"):
                status_extra += f" | last restock: {res['last_restock']}"
            print(f"  -> {res['status']} {res.get('price') or ''}{status_extra}")
            await asyncio.sleep(DELAY_BETWEEN)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        # Save partial results
        if results:
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
        else:
            print("No prices found in stock.")

        await cleanup()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try:
        asyncio.run(main())
    except SystemExit:
        pass
