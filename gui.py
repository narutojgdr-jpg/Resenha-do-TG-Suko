import asyncio
import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from dotenv import load_dotenv
import urllib.request
import logging

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

# Colors matching the site
BG = "#13131a"
CARD = "#1c1c24"
PRIMARY = "#7c5cbf"
PRIMARY_HOVER = "#6b46c1"
ACCENT = "#2ecc71"
MUTED = "#2a2a32"
BORDER = "#303038"
TEXT = "#f2f2f2"
TEXT_MUTED = "#888"
DANGER = "#ef4444"

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

class ScannerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SuKo Scanner")
        self.root.geometry("520x640")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        
        # Remove window border decorations feel
        self.root.option_add("*Font", "Segoe UI 10")
        
        self.client = None
        self.scanning = False
        self.results = []
        
        self.setup_ui()
        
    def setup_ui(self):
        # Header bar
        header = tk.Frame(self.root, bg=PRIMARY, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        tk.Label(header, text="SuKo Scanner", font=("Segoe UI", 16, "bold"), bg=PRIMARY, fg="#fff").pack(side=tk.LEFT, padx=16, pady=10)
        
        # Main content
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        
        # Input card
        input_card = tk.Frame(main, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        input_card.pack(fill=tk.X, pady=(0, 12))
        
        inner = tk.Frame(input_card, bg=CARD)
        inner.pack(padx=14, pady=14)
        
        tk.Label(inner, text="Keyword", bg=CARD, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor=tk.W)
        
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill=tk.X, pady=(4, 0))
        
        self.keyword_entry = tk.Entry(row, font=("Segoe UI", 12), bg=MUTED, fg=TEXT, insertbackground=TEXT, relief=tk.FLAT, width=22)
        self.keyword_entry.pack(side=tk.LEFT, ipady=6, padx=(0, 8))
        self.keyword_entry.insert(0, "gemini")
        
        self.start_btn = tk.Button(row, text="Iniciar", command=self.start_scan, bg=PRIMARY, fg="#fff", font=("Segoe UI", 10, "bold"), relief=tk.FLAT, padx=20, pady=6, activebackground=PRIMARY_HOVER, activeforeground="#fff", cursor="hand2")
        self.start_btn.pack(side=tk.LEFT)
        
        # Status
        self.status_label = tk.Label(main, text="Pronto", bg=BG, fg=ACCENT, font=("Segoe UI", 10))
        self.status_label.pack(anchor=tk.W, pady=(0, 6))
        
        # Progress
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Horizontal.TProgressbar", troughcolor=MUTED, background=PRIMARY, thickness=8, borderwidth=0)
        
        self.progress = ttk.Progressbar(main, length=488, mode="determinate", style="Custom.Horizontal.TProgressbar")
        self.progress.pack(pady=(0, 12))
        
        # Results card
        results_card = tk.Frame(main, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        results_card.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(results_card, text="Resultados", bg=CARD, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor=tk.W, padx=14, pady=(10, 4))
        
        self.log = tk.Text(results_card, height=18, bg=CARD, fg=TEXT, font=("Segoe UI", 10), relief=tk.FLAT, state=tk.DISABLED, wrap=tk.WORD, padx=10, pady=4)
        self.log.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 8))
        
        # Footer
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill=tk.X, padx=16, pady=(0, 12))
        
        self.count_label = tk.Label(footer, text="", bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 9))
        self.count_label.pack(side=tk.LEFT)
        
    def log_msg(self, msg, color=None):
        self.log.config(state=tk.NORMAL)
        if color:
            tag = f"color_{color}"
            self.log.tag_configure(tag, foreground=color)
            self.log.insert(tk.END, msg + "\n", tag)
        else:
            self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)
        
    def set_status(self, text, color=ACCENT):
        self.status_label.config(text=text, fg=color)
        
    def start_scan(self):
        if self.scanning:
            return
        keyword = self.keyword_entry.get().strip()
        if not keyword:
            self.log_msg("Digite uma keyword.", DANGER)
            return
        self.scanning = True
        self.start_btn.config(state=tk.DISABLED, bg=MUTED)
        self.results = []
        self.log.config(state=tk.NORMAL)
        self.log.delete(1.0, tk.END)
        self.log.config(state=tk.DISABLED)
        self.count_label.config(text="")
        
        thread = threading.Thread(target=self.run_scan, args=(keyword,), daemon=True)
        thread.start()
        
    def run_scan(self, keyword):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.do_scan(keyword))
        except Exception as e:
            self.root.after(0, lambda: self.log_msg(f"Erro: {e}", DANGER))
        finally:
            loop.close()
            self.root.after(0, self.finish_scan)
            
    async def do_scan(self, keyword):
        self.root.after(0, lambda: self.set_status("Conectando...", PRIMARY))
        
        self.client = TelegramClient(SESSION, API_ID, API_HASH)
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            self.root.after(0, lambda: self.log_msg("Nao autorizado. Rode o scanner.py primeiro pra logar.", DANGER))
            return
            
        self.root.after(0, lambda: self.set_status("Buscando bots...", PRIMARY))
        
        bots = await self.get_lojas_bots()
        if not bots:
            self.root.after(0, lambda: self.log_msg("Nenhum bot na pasta LOJAS.", DANGER))
            return
            
        total = len(bots)
        self.root.after(0, lambda: self.progress.config(maximum=total))
        self.root.after(0, lambda: self.log_msg(f"Encontrados {total} bots", TEXT_MUTED))
        self.root.after(0, lambda: self.log_msg(""))
        
        found = 0
        for i, username in enumerate(bots):
            self.root.after(0, lambda i=i, u=username, t=total: self.set_status(f"[{i+1}/{t}] {u}", PRIMARY))
            self.root.after(0, lambda i=i: self.progress.config(value=i+1))
            
            res = await self.scan_bot(username, keyword)
            self.results.append(res)
            
            price = res.get("price")
            if price:
                found += 1
                oos = res.get("out_of_stock")
                color = DANGER if oos else ACCENT
                status = " (sem estoque)" if oos else ""
                self.root.after(0, lambda u=username, p=price, s=status, c=color: self.log_msg(f"{u}   ${p:.2f}{s}", c))
            
            await asyncio.sleep(DELAY_BETWEEN)
            
        self.root.after(0, lambda: self.set_status("Enviando pro site...", PRIMARY))
        self.send_to_site(keyword, self.results)
        
        valid = [r for r in self.results if r.get("price") and not r.get("out_of_stock")]
        valid.sort(key=lambda x: x["price"])
        
        self.root.after(0, lambda: self.log_msg(""))
        self.root.after(0, lambda: self.log_msg(f"{len(valid)} em estoque", TEXT_MUTED))
        
        if valid:
            avg = sum(r["price"] for r in valid) / len(valid)
            self.root.after(0, lambda a=avg, n=len(valid): self.count_label.config(text=f"{n} encontrados  ·  media ${a:.2f}"))
            
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
                self.root.after(0, lambda: self.log_msg("Enviado pro site", ACCENT))
        except Exception as e:
            self.root.after(0, lambda: self.log_msg(f"Erro ao enviar: {e}", DANGER))
            
    def finish_scan(self):
        self.scanning = False
        self.start_btn.config(state=tk.NORMAL, bg=PRIMARY)
        self.set_status("Pronto", ACCENT)
        
    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = ScannerApp()
    app.run()
