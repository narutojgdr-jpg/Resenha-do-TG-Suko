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
DELAY_BETWEEN = 1.5
FOLDER_NAME = "LOJAS"
TIMEOUT_PER_BOT = 25

NAV_BUTTONS = ["Browse Products", "Browse", "Products", "Shop", "Buy", "Menu", "Store", "Catalog"]

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
    return any(x in low for x in ["out of stock", "sem estoque", "no stock", "sold out", "esgotado", "unavailable"])

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'[^a-zA-Z0-9\s]', '', text).lower().strip()

def is_relevant_button(btn_text, keyword):
    low = btn_text.lower()
    kw = keyword.lower()
    if kw in low:
        return True
    if any(clean_text(nb) in clean_text(btn_text) for nb in NAV_BUTTONS):
        competitors = ["claude", "chatgpt", "gpt", "openai", "capcut", "netflix", "gmail"]
        for c in competitors:
            if c in low and kw not in low:
                return False
        return True
    return False

async def click_relevant_button(msg, keyword):
    if not msg or not msg.reply_markup or not hasattr(msg.reply_markup, "rows"):
        return False

    keyword_btns = []
    nav_btns = []

    for i, row in enumerate(msg.reply_markup.rows):
        for j, btn in enumerate(row.buttons):
            if not hasattr(btn, "text"):
                continue
            if is_relevant_button(btn.text, keyword):
                if keyword.lower() in btn.text.lower():
                    keyword_btns.append((i, j, btn.text))
                else:
                    nav_btns.append((i, j, btn.text))

    target = keyword_btns[0] if keyword_btns else (nav_btns[0] if nav_btns else None)
    if not target:
        return False

    i, j, text = target
    try:
        await msg.click(i, j)
        print(f"    Clicked: {text}")
        return True
    except:
        return False

async def search_in_chat(entity, keyword):
    """Search inside the bot chat for the keyword. Returns last restock and any price found."""
    last_restock = None
    found_price = None
    snippet = None
    out_of_stock = False

    try:
        async for msg in client.iter_messages(entity, search=keyword, limit=30):
            if not msg.text or not msg.date:
                continue
            text_low = msg.text.lower()
            if keyword.lower() not in text_low:
                continue

            # Prefer restock messages
            if any(x in text_low for x in ["restock", "new stock", "added", "reestock", "stock added"]):
                if last_restock is None or msg.date > datetime.fromisoformat(last_restock):
                    last_restock = msg.date.isoformat()

            # Capture price if present
            price = extract_price(msg.text)
            if price and found_price is None:
                found_price = price
                snippet = msg.text[:160]
                out_of_stock = is_out_of_stock(msg.text)

            # Stop early if we have both
            if last_restock and found_price:
                break
    except Exception:
        pass

    return last_restock, found_price, snippet, out_of_stock

async def get_current_price(entity, keyword):
    """Try to get the live price by navigating."""
    try:
        await asyncio.wait_for(client.send_message(entity, "/start"), timeout=6)
        await asyncio.sleep(1.5)
    except:
        return None

    for _ in range(2):
        try:
            msgs = await asyncio.wait_for(client.get_messages(entity, limit=6), timeout=5)
            clicked = False
            for m in msgs:
                if await click_relevant_button(m, keyword):
                    clicked = True
                    break
            if not clicked:
                break
            await asyncio.sleep(1.2)
            # Check latest messages for price
            latest = await client.get_messages(entity, limit=5)
            for msg in latest:
                if msg.text and keyword.lower() in msg.text.lower():
                    price = extract_price(msg.text)
                    if price:
                        return price, is_out_of_stock(msg.text), msg.text[:140]
        except:
            break

    return None

async def clean_chat(entity):
    try:
        me = await client.get_me()
        msgs = await client.get_messages(entity, limit=12)
        ids = [m.id for m in msgs if m.sender_id == me.id or m.reply_markup]
        if ids:
            await client.delete_messages(entity, ids[:10])
    except:
        pass

async def remove_from_folder(username):
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        for f in result.filters:
            if isinstance(f, types.DialogFilter):
                title = getattr(f, "title", "")
                if isinstance(title, types.TextWithEntities):
                    title = title.text
                if str(title).upper() == FOLDER_NAME.upper():
                    new_peers = []
                    for peer in getattr(f, "include_peers", []):
                        try:
                            ent = await client.get_entity(peer)
                            un = "@" + ent.username if getattr(ent, "username", None) else None
                            if un != username:
                                new_peers.append(peer)
                        except:
                            new_peers.append(peer)
                    f.include_peers = new_peers
                    await client(functions.messages.UpdateDialogFilterRequest(id=f.id, filter=f))
                    print(f"    Removed {username} from folder")
                    return True
    except:
        pass
    return False

async def scan_bot(username, keyword):
    try:
        entity = await asyncio.wait_for(client.get_entity(username), timeout=7)

        # 1. Search inside the chat for restock + historical price
        last_restock, hist_price, hist_snippet, hist_oos = await search_in_chat(entity, keyword)

        # 2. Try to get current live price
        current = await get_current_price(entity, keyword)

        # Clean
        try:
            await asyncio.wait_for(clean_chat(entity), timeout=4)
        except:
            pass

        if current:
            price, oos, snippet = current
            status = "out_of_stock" if oos else "found"
            print(f"  Found (live): {snippet[:55]}...")
            return {
                "username": username,
                "status": status,
                "price": price,
                "out_of_stock": oos,
                "last_restock": last_restock,
                "snippet": snippet,
                "at": datetime.now(timezone.utc).isoformat()
            }

        if hist_price:
            status = "out_of_stock" if hist_oos else "found"
            print(f"  Found (history): {hist_snippet[:55] if hist_snippet else ''}...")
            return {
                "username": username,
                "status": status,
                "price": hist_price,
                "out_of_stock": hist_oos,
                "last_restock": last_restock,
                "snippet": hist_snippet,
                "at": datetime.now(timezone.utc).isoformat()
            }

        # Check if bot is dead (no response at all)
        try:
            msgs = await client.get_messages(entity, limit=3)
            me = await client.get_me()
            if not any(m.sender_id and m.sender_id != me.id for m in msgs):
                await remove_from_folder(username)
                return {"username": username, "status": "removed_dead", "price": None}
        except:
            pass

        return {"username": username, "status": "not_found", "price": None, "last_restock": last_restock}

    except asyncio.TimeoutError:
        return {"username": username, "status": "timeout", "price": None}
    except FloodWaitError as e:
        await asyncio.sleep(min(e.seconds, 20))
        return {"username": username, "status": "flood", "price": None}
    except Exception as e:
        err = str(e)
        if any(x in err for x in ["Security error", "old message", "Too many messages"]):
            return {"username": username, "status": "skipped_flood", "price": None}
        return {"username": username, "status": f"error: {err[:25]}", "price": None}

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
                            if getattr(entity, "username", None):
                                uname = "@" + entity.username
                                if uname not in bots:
                                    bots.append(uname)
                        except:
                            pass
                    break
    except Exception as e:
        print(f"Could not read folders: {e}")
    return bots

async def cleanup():
    try:
        if client.is_connected():
            await client.disconnect()
    except:
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
        await asyncio.wait_for(client.catch_up(), timeout=8)
    except:
        pass

    lojas_bots = await get_lojas_bots()
    if not lojas_bots:
        print(f"No bots found in '{FOLDER_NAME}' folder.")
        await cleanup()
        return

    print(f"Found {len(lojas_bots)} bots in '{FOLDER_NAME}'.\n")

    keyword = input("Keyword to search: ").strip()
    if not keyword:
        print("No keyword.")
        await cleanup()
        return

    results = []
    try:
        for i, username in enumerate(lojas_bots, 1):
            print(f"\n[{i}/{len(lojas_bots)}] Checking {username}...")
            res = await scan_bot(username, keyword)
            results.append(res)
            extra = ""
            if res.get("out_of_stock"):
                extra = " (OUT OF STOCK)"
            if res.get("last_restock"):
                extra += f" | restock: {res['last_restock']}"
            print(f"  -> {res['status']} {res.get('price') or ''}{extra}")
            await asyncio.sleep(DELAY_BETWEEN)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if results:
            with open("last_scan.json", "w", encoding="utf-8") as f:
                json.dump({"keyword": keyword, "scanned_at": datetime.now(timezone.utc).isoformat(), "results": results}, f, indent=2, ensure_ascii=False)

        valid = [r for r in results if r.get("price") and not r.get("out_of_stock")]
        valid.sort(key=lambda x: x["price"])
        print("\n=== Summary (in stock) ===")
        for r in valid:
            rest = f" | restock: {r.get('last_restock')}" if r.get("last_restock") else ""
            print(f"{r['username']}: ${r['price']}{rest}")
        if valid:
            print(f"Average: ${sum(r['price'] for r in valid)/len(valid):.2f}")
        else:
            print("No prices found.")

        await cleanup()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try:
        asyncio.run(main())
    except SystemExit:
        pass
