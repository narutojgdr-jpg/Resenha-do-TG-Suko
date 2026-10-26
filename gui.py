import asyncio
import json
import os
import re
import subprocess
import logging
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime, timezone
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from dotenv import load_dotenv
import urllib.request

logging.getLogger("telethon").setLevel(logging.ERROR)

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE")

SESSION = "resenha_session"
DELAY_BETWEEN = 1.5
FOLDER_NAME = "LOJAS"

SITE_URL = os.getenv("SITE_URL", "https://tempmailsuko.shop")
SCANNER_SECRET = os.getenv("SCANNER_SECRET", "suko-scanner-2026")

NAV_BUTTONS = ["Browse Products", "Browse", "Products", "Shop", "Buy", "Menu", "Store", "Catalog"]

def extract_price(text: str):
    if not text:
        return None
    patterns = [
        r"Price:?\s*\$?([0-9]+\.?[0-9]*)",
        r"\$\s*([0-9]+\.?[0-9]*)",
        r"([0-9]+\.?[0-9]*)\s*USDT",
        r"([0-9]+\.?[0-9]*)\s*USD",
        r"\u2248\s*([0-9]+\.?[0-9]*)",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            try:
                val = float(m.group(1))
                if 0.2 <= val <= 20:
                    return val
            except:
                pass
    return None

def is_out_of_stock(text: str):
    if not text:
        return False
    low = text.lower()
    return any(x in low for x in ["out of stock", "sem estoque", "no stock", "sold out", "esgotado", "unavailable", "temporarily unavailable"])

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'[^a-zA-Z0-9\s]', '', text).lower().strip()

def is_nav_button(btn_text):
    low = clean_text(btn_text)
    return any(clean_text(nb) in low for nb in NAV_BUTTONS)

def is_wrong_product(btn_text, keyword):
    low = btn_text.lower()
    kw = keyword.lower()
    competitors = ["claude", "chatgpt", "gpt", "openai", "capcut", "netflix", "gmail", "spotify", "canva", "youtube"]
    for c in competitors:
        if c in low and kw not in low:
            return True
    return False

class ScannerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SuKo Scanner")
        self.root.geometry("500x600")
        self.root.configure(bg="#1a1a2e")
        
        self.client = None
        self.scanning = False
        self.results = []
        
        self.setup_ui()
        
    def setup_ui(self):
        # Header
        header = tk.Label(self.root, text="SuKo Scanner", font=("Segoe UI", 18, "bold"), bg="#1a1a2e", fg="#fff")
        header.pack(pady=10)
        
        # Keyword input
        frame = tk.Frame(self.root, bg="#1a1a2e")
        frame.pack(pady=5)
        
        tk.Label(frame, text="Keyword:", bg="#1a1a2e", fg="#aaa", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=5)
        self.keyword_entry = tk.Entry(frame, width=20, font=("Segoe UI", 11), bg="#16213e", fg="#fff", insertbackground="#fff")
        self.keyword_entry.pack(side=tk.LEFT, padx=5)
        self.keyword_entry.insert(0, "gemini")
        
        self.start_btn = tk.Button(frame, text="Iniciar", command=self.start_scan, bg="#0f3460", fg="#fff", font=("Segoe UI", 10, "bold"), padx=15)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        # Status
        self.status_label = tk.Label(self.root, text="Pronto", bg="#1a1a2e", fg="#4ecca3", font=("Segoe UI", 10))
        self.status_label.pack(pady=5)
        
        # Progress
        self.progress = ttk.Progressbar(self.root, length=400, mode="determinate")
        self.progress.pack(pady=5)
        
        # Log
        self.log = scrolledtext.ScrolledText(self.root, height=20, width=55, bg="#0f0f23", fg="#0f0", font=("Consolas", 9), state=tk.DISABLED)
        self.log.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        
        # Results count
        self.count_label = tk.Label(self.root, text="", bg="#1a1a2e", fg="#aaa", font=("Segoe UI", 9))
        self.count_label.pack(pady=5)
        
    def log_msg(self, msg):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)
        
    def set_status(self, text, color="#4ecca3"):
        self.status_label.config(text=text, fg=color)
        
    def start_scan(self):
        if self.scanning:
            return
        keyword = self.keyword_entry.get().strip()
        if not keyword:
            self.log_msg("Digite uma keyword.")
            return
        self.scanning = True
        self.start_btn.config(state=tk.DISABLED)
        self.results = []
        self.log.config(state=tk.NORMAL)
        self.log.delete(1.0, tk.END)
        self.log.config(state=tk.DISABLED)
        
        thread = threading.Thread(target=self.run_scan, args=(keyword,), daemon=True)
        thread.start()
        
    def run_scan(self, keyword):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.do_scan(keyword))
        except Exception as e:
            self.root.after(0, lambda: self.log_msg(f"Erro: {e}"))
        finally:
            loop.close()
            self.root.after(0, self.finish_scan)
            
    async def do_scan(self, keyword):
        self.root.after(0, lambda: self.set_status("Conectando..."))
        
        self.client = TelegramClient(SESSION, API_ID, API_HASH)
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            self.root.after(0, lambda: self.log_msg("Nao autorizado. Rode o scanner.py primeiro pra logar."))
            return
            
        self.root.after(0, lambda: self.set_status("Buscando bots..."))
        
        bots = await self.get_lojas_bots()
        if not bots:
            self.root.after(0, lambda: self.log_msg("Nenhum bot na pasta LOJAS."))
            return
            
        total = len(bots)
        self.root.after(0, lambda: self.progress.config(maximum=total))
        self.root.after(0, lambda: self.log_msg(f"Encontrados {total} bots."))
        
        for i, username in enumerate(bots):
            self.root.after(0, lambda i=i, u=username: self.set_status(f"[{i+1}/{total}] {u}"))
            self.root.after(0, lambda i=i: self.progress.config(value=i+1))
            
            res = await self.scan_bot(username, keyword)
            self.results.append(res)
            
            price = res.get("price")
            if price:
                oos = " (sem estoque)" if res.get("out_of_stock") else ""
                self.root.after(0, lambda u=username, p=price, o=oos: self.log_msg(f"  {u}: ${p}{o}"))
            
            await asyncio.sleep(DELAY_BETWEEN)
            
        # Send to site
        self.root.after(0, lambda: self.set_status("Enviando pro site..."))
        self.send_to_site(keyword, self.results)
        
        # Summary
        valid = [r for r in self.results if r.get("price") and not r.get("out_of_stock")]
        valid.sort(key=lambda x: x["price"])
        self.root.after(0, lambda: self.log_msg(f"\n=== {len(valid)} em estoque ==="))
        for r in valid[:10]:
            self.root.after(0, lambda r=r: self.log_msg(f"{r['username']}: ${r['price']}"))
        if valid:
            avg = sum(r["price"] for r in valid) / len(valid)
            self.root.after(0, lambda a=avg: self.count_label.config(text=f"Media: ${a:.2f}"))
            
        await self.client.disconnect()
        
    async def get_lojas_bots(self):
        bots = []
        try:
            result = await self.client(functions.messages.GetDialogFiltersRequest())
            for f in result.filters:
                if isinstance(f, types.DialogFilter):
                    title = getattr(f, "title", "")
                    if isinstance(title, types.TextWithEntities):
                        title = title.text
                    if str(title).upper() == FOLDER_NAME.upper():
                        for peer in getattr(f, "include_peers", []):
                            try:
                                entity = await self.client.get_entity(peer)
                                if getattr(entity, "username", None):
                                    uname = "@" + entity.username
                                    if uname not in bots:
                                        bots.append(uname)
                            except:
                                pass
                        break
        except:
            pass
        return bots
        
    async def scan_bot(self, username, keyword):
        try:
            entity = await asyncio.wait_for(self.client.get_entity(username), timeout=7)
            
            # Search history
            last_restock, hist_price, hist_oos = None, None, False
            try:
                async for msg in self.client.iter_messages(entity, search=keyword, limit=15):
                    if not msg.text:
                        continue
                    if keyword.lower() not in msg.text.lower():
                        continue
                    price = extract_price(msg.text)
                    if price and hist_price is None:
                        hist_price = price
                        hist_oos = is_out_of_stock(msg.text)
                    if any(x in msg.text.lower() for x in ["restock", "added", "stock"]):
                        if msg.date:
                            last_restock = msg.date.isoformat()
                    if hist_price and last_restock:
                        break
            except:
                pass
                
            # Try live
            try:
                await asyncio.wait_for(self.client.send_message(entity, "/start"), timeout=5)
                await asyncio.sleep(1)
                msgs = await self.client.get_messages(entity, limit=6)
                for m in msgs:
                    if m.reply_markup and hasattr(m.reply_markup, "rows"):
                        for i, row in enumerate(m.reply_markup.rows):
                            for j, btn in enumerate(row.buttons):
                                if hasattr(btn, "text") and keyword.lower() in btn.text.lower():
                                    try:
                                        await m.click(i, j)
                                        await asyncio.sleep(1)
                                        latest = await self.client.get_messages(entity, limit=3)
                                        for lm in latest:
                                            if lm.text and keyword.lower() in lm.text.lower():
                                                p = extract_price(lm.text)
                                                if p:
                                                    return {"username": username, "price": p, "out_of_stock": is_out_of_stock(lm.text), "last_restock": last_restock}
                                    except:
                                        pass
            except:
                pass
                
            if hist_price:
                return {"username": username, "price": hist_price, "out_of_stock": hist_oos, "last_restock": last_restock}
            return {"username": username, "price": None}
            
        except:
            return {"username": username, "price": None}
            
    def send_to_site(self, keyword, results):
        try:
            payload = json.dumps({"keyword": keyword, "results": results}).encode("utf-8")
            req = urllib.request.Request(
                f"{SITE_URL}/api/prices",
                data=payload,
                headers={"Content-Type": "application/json", "x-scanner-secret": SCANNER_SECRET},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.root.after(0, lambda: self.log_msg(f"Enviado pro site."))
        except Exception as e:
            self.root.after(0, lambda: self.log_msg(f"Erro ao enviar: {e}"))
            
    def finish_scan(self):
        self.scanning = False
        self.start_btn.config(state=tk.NORMAL)
        self.set_status("Pronto")
        
    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = ScannerApp()
    app.run()
