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
DELAY_BETWEEN = 2
FOLDER_NAME = "LOJAS"

PRODUCT_BUTTONS = [
    "Browse Products", "Browse", "Products", "Shop", "Buy", "Menu",
    "Store", "Catalog", "Services", "Premium", "AI", "Gemini",
    "ChatGPT", "Claude", "GPT"
]

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
        r"([0-9]+\.?[0-9]*)\s*\$",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            try:
                val = float(m.group(1))
                if 0.1 <= val <= 100:
                    return val
            except:
                pass
    return None

def is_out_of_stock(text: str):
    if not text:
        return False
    low = text.lower()
    return any(x in low for x in [
        "out of stock", "sem estoque", "no stock", "sold out",
        "esgotado", "unavailable", "0 pcs", "0 left"
    ])

def clean_text(text):
    if not text:
        return ""
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', text)
    return cleaned.lower().strip()

async def click_any_button(msg, keywords):
    if not msg or not msg.reply_markup or not hasattr(msg.reply_markup, "rows"):
        return False
    markup = msg.reply_markup
    for i, row in enumerate(markup.rows):
        for j, btn in enumerate(row.buttons):
            if not hasattr(btn, "text"):
                continue
            btn_clean = clean_text(btn.text)
            for kw in keywords:
                if clean_text(kw) in btn_clean or btn_clean in clean_text(kw):
                    try:
                        await msg.click(i, j)
                        print(f"    Clicked button: {btn.text}")
                        return True
                    except Exception:
                        pass
    return False

async def try_find_with_price(entity, keyword):
    await asyncio.sleep(1.2)
    msgs = await client.get_messages(entity, limit=12)
    keyword_lower = keyword.lower()

    for msg in msgs:
        if not msg.text:
            continue
        if keyword_lower not in msg.text.lower():
            continue
        price = extract_price(msg.text)
        if price is None:
            continue

        out_of_stock = is_out_of_stock(msg.text)
        last_restock = None
        try:
            older = await client.get_messages(entity, limit=20)
            for om in older:
                if om.text and om.date:
                    low = om.text.lower()
                    if keyword_lower in low and any(x in low for x in ["new stock", "restock", "added", "stock added", "reestock"]):
                        last_restock = om.date.isoformat()
                        break
        except:
            pass

        status = "out_of_stock" if out_of_stock else "found"
        return {
            "status": status,
            "price": price,
            "out_of_stock": out_of_stock,
            "last_restock": last_restock,
            "snippet": msg.text[:180]
        }
    return None

async def bot_responded(entity):
    """Check if the bot sent any message recently."""
    try:
        msgs = await client.get_messages(entity, limit=8)
        me = await client.get_me()
        for m in msgs:
            if m.sender_id and m.sender_id != me.id:
                return True
    except:
        pass
    return False

async def clean_chat(entity):
    """Delete messages sent by us and recent bot menu messages."""
    try:
        me = await client.get_me()
        msgs = await client.get_messages(entity, limit=30)
        to_delete = []
        for m in msgs:
            if m.sender_id == me.id or (m.reply_markup or (m.text and len(m.text) < 500)):
                to_delete.append(m.id)
        if to_delete:
            await client.delete_messages(entity, to_delete)
            print(f"    Cleaned {len(to_delete)} messages")
    except Exception as e:
        print(f"    Clean error: {e}")

async def remove_from_folder(username):
    """Remove a bot from the LOJAS folder."""
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        for f in result.filters:
            if isinstance(f, types.DialogFilter):
                title = getattr(f, "title", "")
                if isinstance(title, types.TextWithEntities):
                    title = title.text
                if str(title).upper() == FOLDER_NAME.upper():
                    # Build new include_peers without this bot
                    new_peers = []
                    for peer in getattr(f, "include_peers", []):
                        try:
                            entity = await client.get_entity(peer)
                            uname = "@" + entity.username if hasattr(entity, "username") and entity.username else None
                            if uname != username:
                                new_peers.append(peer)
                        except:
                            new_peers.append(peer)
                    f.include_peers = new_peers
                    await client(functions.messages.UpdateDialogFilterRequest(
                        id=f.id,
                        filter=f
                    ))
                    print(f"    Removed {username} from {FOLDER_NAME}")
                    return True
    except Exception as e:
        print(f"    Could not remove from folder: {e}")
    return False

async def explore_bot(entity, keyword):
    try:
        await client.send_message(entity, "/start")
        await asyncio.sleep(2.5)
        if not await bot_responded(entity):
            return "dead"
        res = await try_find_with_price(entity, keyword)
        if res:
            return res
    except:
        return "dead"

    for attempt in range(3):
        msgs = await client.get_messages(entity, limit=6)
        clicked = False
        for m in msgs:
            if await click_any_button(m, PRODUCT_BUTTONS + [keyword]):
                clicked = True
                break
        if clicked:
            res = await try_find_with_price(entity, keyword)
            if res:
                return res
            await asyncio.sleep(1.5)
        else:
            break

    for cmd in ["/shop", "/products", "/menu", "/buy"]:
        try:
            await client.send_message(entity, cmd)
            await asyncio.sleep(2)
            res = await try_find_with_price(entity, keyword)
            if res:
                return res
        except:
            pass

    msgs = await client.get_messages(entity, limit=8)
    for m in msgs:
        if await click_any_button(m, [keyword, "Gemini", "AI", "Premium", "ChatGPT", "Claude"]):
            res = await try_find_with_price(entity, keyword)
            if res:
                return res
            break

    return None

async def scan_bot(username, keyword):
    try:
        entity = await client.get_entity(username)
        result = await explore_bot(entity, keyword)

        # Clean messages after analysis
        await clean_chat(entity)

        if result == "dead":
            await remove_from_folder(username)
            return {"username": username, "status": "removed_dead", "price": None}

        if result:
            result["username"] = username
            result["at"] = datetime.now(timezone.utc).isoformat()
            print(f"  Found: {result['snippet'][:70]}...")
            return result

        return {"username": username, "status": "not_found", "price": None}
    except FloodWaitError as e:
        print(f"FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds + 3)
        return {"username": username, "status": "flood", "price": None}
    except Exception as e:
        err = str(e)
        if "Security error" in err or "old message" in err.lower() or "Too many messages" in err:
            return {"username": username, "status": "skipped_flood", "price": None}
        return {"username": username, "status": f"error: {err[:40]}", "price": None}

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
    try:
        if client.is_connected():
            print("\nCleaning up...")
            try:
                await client.get_dialogs(limit=5)
            except:
                pass
            await client.disconnect()
            print("Disconnected.")
    except Exception:
        pass

def signal_handler(sig, frame):
    print("\nCtrl+C detected.")
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

    keyword = input("Keyword to search: ").strip()
    if not keyword:
        print("No keyword. Exiting.")
        await cleanup()
        return

    results = []
    try:
        for i, username in enumerate(lojas_bots, 1):
            print(f"\n[{i}/{len(lojas_bots)}] Checking {username}...")
            res = await scan_bot(username, keyword)
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
