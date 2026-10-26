import asyncio
import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk
from telethon import TelegramClient, functions, types
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
PRESETS_FILE = "presets.json"

SITE_URL = os.getenv("SITE_URL", "https://tempmailsuko.shop")
SCANNER_SECRET = os.getenv("SCANNER_SECRET", "suko-scanner-2026")

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
                if 0.2 <= val <= 50:
                    return val
            except:
                pass
    return None

def extract_title(text: str, keyword: str):
    if not text:
        return None
    lines = text.strip().split("\n")
    for line in lines[:5]:
        line = line.strip()
        if not line or len(line) < 3:
            continue
        if keyword.lower() in line.lower():
            clean = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", line)
            clean = re.sub(r"^[\s\-\*\#\>]+|[\s\-\*\#\>]+$", "", clean)
            if 3 < len(clean) < 100:
                return clean
    return None

def is_out_of_stock(text: str):
    if not text:
        return False
    low = text.lower()
    return any(x in low for x in ["out of stock", "sem estoque", "no stock", "sold out", "esgotado", "unavailable", "temporarily unavailable"])

def passes_filters(title: str, blacklist: list, whitelist: list):
    if not title:
        return False
    low = title.lower()
    for bl in blacklist:
        if bl.strip() and bl.strip().lower() in low:
            return False
    if whitelist:
        for wl in whitelist:
            if wl.strip() and wl.strip().lower() in low:
                return True
        return False
    return True

class ScannerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SuKo Scanner")
        self.root.geometry("580x720")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        
        self.client = None
        self.scanning = False
        self.results = []
        self.presets = self.load_presets()
        
        self.setup_ui()
        
    def load_presets(self):
        try:
            if os.path.exists(PRESETS_FILE):
                with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except:
            pass
        return {}
    
    def save_presets(self):
        try:
            with open(PRESETS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.presets, f, ensure_ascii=False, indent=2)
        except:
            pass
        
    def setup_ui(self):
        header = tk.Frame(self.root, bg=PRIMARY, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        tk.Label(header, text="SuKo Scanner", font=("Arial", 16, "bold"), bg=PRIMARY, fg="#fff").pack(side=tk.LEFT, padx=16, pady=10)
        
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        
        # Keyword + Preset row
        top_row = tk.Frame(main, bg=BG)
        top_row.pack(fill=tk.X, pady=(0, 8))
        
        tk.Label(top_row, text="Keyword", bg=BG, fg=TEXT_MUTED, font=("Arial", 9)).pack(side=tk.LEFT)
        
        self.keyword_entry = tk.Entry(top_row, font=("Arial", 11), bg=MUTED, fg=TEXT, insertbackground=TEXT, relief=tk.FLAT, width=14)
        self.keyword_entry.pack(side=tk.LEFT, ipady=4, padx=(8, 8))
        self.keyword_entry.insert(0, "claude")
        self.keyword_entry.bind("<KeyRelease>", self.on_keyword_change)
        
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(top_row, textvariable=self.preset_var, width=12, state="readonly")
        self.preset_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.preset_combo.bind("<<ComboboxSelected>>", self.load_preset)
        self.update_preset_list()
        
        self.start_btn = tk.Button(top_row, text="Iniciar", command=self.start_scan, bg=PRIMARY, fg="#fff", font=("Arial", 10, "bold"), relief=tk.FLAT, padx=16, pady=4, activebackground=PRIMARY_HOVER, activeforeground="#fff", cursor="hand2")
        self.start_btn.pack(side=tk.LEFT)
        
        # Filters card
        filters_card = tk.Frame(main, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        filters_card.pack(fill=tk.X, pady=(0, 8))
        
        filters_inner = tk.Frame(filters_card, bg=CARD)
        filters_inner.pack(padx=12, pady=10, fill=tk.X)
        
        # Blacklist
        bl_frame = tk.Frame(filters_inner, bg=CARD)
        bl_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        
        tk.Label(bl_frame, text="Blacklist (excluir)", bg=CARD, fg=DANGER, font=("Arial", 9)).pack(anchor=tk.W)
        self.blacklist_text = tk.Text(bl_frame, height=3, bg=MUTED, fg=TEXT, font=("Arial", 9), relief=tk.FLAT, wrap=tk.WORD)
        self.blacklist_text.pack(fill=tk.X, pady=(2, 0))
        self.blacklist_text.insert("1.0", "API")
        
        # Whitelist
        wl_frame = tk.Frame(filters_inner, bg=CARD)
        wl_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        tk.Label(wl_frame, text="Whitelist (obrigatorio)", bg=CARD, fg=ACCENT, font=("Arial", 9)).pack(anchor=tk.W)
        self.whitelist_text = tk.Text(wl_frame, height=3, bg=MUTED, fg=TEXT, font=("Arial", 9), relief=tk.FLAT, wrap=tk.WORD)
        self.whitelist_text.pack(fill=tk.X, pady=(2, 0))
        
        # Save preset button
        btn_row = tk.Frame(filters_inner, bg=CARD)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        
        tk.Button(btn_row, text="Salvar Preset", command=self.save_current_preset, bg=MUTED, fg=TEXT, font=("Arial", 9), relief=tk.FLAT, padx=12, pady=2, cursor="hand2").pack(side=tk.LEFT)
        
        tk.Label(btn_row, text="Separe por virgula", bg=CARD, fg=TEXT_MUTED, font=("Arial", 8)).pack(side=tk.RIGHT)
        
        # Status
        self.status_label = tk.Label(main, text="Pronto", bg=BG, fg=ACCENT, font=("Arial", 10))
        self.status_label.pack(anchor=tk.W, pady=(0, 4))
        
        # Progress
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Custom.Horizontal.TProgressbar", troughcolor=MUTED, background=PRIMARY, thickness=6, borderwidth=0)
        
        self.progress = ttk.Progressbar(main, length=548, mode="determinate", style="Custom.Horizontal.TProgressbar")
        self.progress.pack(pady=(0, 8))
        
        # Results
        results_card = tk.Frame(main, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        results_card.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(results_card, text="Resultados", bg=CARD, fg=TEXT_MUTED, font=("Arial", 9)).pack(anchor=tk.W, padx=12, pady=(8, 2))
        
        self.log = tk.Text(results_card, height=16, bg=CARD, fg=TEXT, font=("Arial", 9), relief=tk.FLAT, state=tk.DISABLED, wrap=tk.WORD, padx=8, pady=4)
        self.log.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 6))
        
        # Footer
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill=tk.X, padx=16, pady=(0, 10))
        
        self.count_label = tk.Label(footer, text="", bg=BG, fg=TEXT_MUTED, font=("Arial", 9))
        self.count_label.pack(side=tk.LEFT)
        
    def update_preset_list(self):
        presets = list(self.presets.keys())
        self.preset_combo["values"] = presets
        
    def on_keyword_change(self, event=None):
        keyword = self.keyword_entry.get().strip().lower()
        if keyword in self.presets:
            self.preset_var.set(keyword)
            self.apply_preset(keyword)
            
    def load_preset(self, event=None):
        name = self.preset_var.get()
        if name in self.presets:
            self.apply_preset(name)
            self.keyword_entry.delete(0, tk.END)
            self.keyword_entry.insert(0, name)
            
    def apply_preset(self, name):
        preset = self.presets.get(name, {})
        self.blacklist_text.delete("1.0", tk.END)
        self.blacklist_text.insert("1.0", ", ".join(preset.get("blacklist", [])))
        self.whitelist_text.delete("1.0", tk.END)
        self.whitelist_text.insert("1.0", ", ".join(preset.get("whitelist", [])))
        
    def save_current_preset(self):
        keyword = self.keyword_entry.get().strip().lower()
        if not keyword:
            return
        bl = [x.strip() for x in self.blacklist_text.get("1.0", tk.END).split(",") if x.strip()]
        wl = [x.strip() for x in self.whitelist_text.get("1.0", tk.END).split(",") if x.strip()]
        self.presets[keyword] = {"blacklist": bl, "whitelist": wl}
        self.save_presets()
        self.update_preset_list()
        self.preset_var.set(keyword)
        self.log_msg(f"Preset '{keyword}' salvo", ACCENT)
        
    def get_filters(self):
        bl = [x.strip() for x in self.blacklist_text.get("1.0", tk.END).split(",") if x.strip()]
        wl = [x.strip() for x in self.whitelist_text.get("1.0", tk.END).split(",") if x.strip()]
        return bl, wl
        
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
        blacklist, whitelist = self.get_filters()
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
        if blacklist:
            self.root.after(0, lambda: self.log_msg(f"Blacklist: {', '.join(blacklist)}", DANGER))
        if whitelist:
            self.root.after(0, lambda: self.log_msg(f"Whitelist: {', '.join(whitelist)}", ACCENT))
        self.root.after(0, lambda: self.log_msg(""))
        
        for i, username in enumerate(bots):
            self.root.after(0, lambda i=i, u=username, t=total: self.set_status(f"[{i+1}/{t}] {u}", PRIMARY))
            self.root.after(0, lambda i=i: self.progress.config(value=i+1))
            
            products = await self.scan_bot(username, keyword, blacklist, whitelist)
            for p in products:
                self.results.append(p)
                price = p.get("price")
                if price:
                    oos = p.get("out_of_stock")
                    color = DANGER if oos else ACCENT
                    status = " (sem estoque)" if oos else ""
                    label = p.get("label") or username
                    self.root.after(0, lambda l=label, pr=price, s=status, c=color: self.log_msg(f"{l}   ${pr:.2f}{s}", c))
            
            await asyncio.sleep(DELAY_BETWEEN)
            
        self.root.after(0, lambda: self.set_status("Enviando pro site...", PRIMARY))
        self.send_to_site(keyword, self.results)
        
        valid = [r for r in self.results if r.get("price") and not r.get("out_of_stock")]
        valid.sort(key=lambda x: x["price"])
        
        self.root.after(0, lambda: self.log_msg(""))
        self.root.after(0, lambda: self.log_msg(f"{len(valid)} em estoque", TEXT_MUTED))
        
        if valid:
            avg = sum(r["price"] for r in valid) / len(valid)
            self.root.after(0, lambda a=avg, n=len(valid): self.count_label.config(text=f"{n} encontrados  -  media ${a:.2f}"))
            
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
        
    async def scan_bot(self, username, keyword, blacklist, whitelist):
        products = []
        try:
            entity = await asyncio.wait_for(self.client.get_entity(username), timeout=7)
            
            last_restock = None
            try:
                async for msg in self.client.iter_messages(entity, search=keyword, limit=10):
                    if msg.text and any(x in msg.text.lower() for x in ["restock", "added", "stock"]):
                        if msg.date:
                            last_restock = msg.date.isoformat()
                            break
            except:
                pass
            
            try:
                await asyncio.wait_for(self.client.send_message(entity, "/start"), timeout=5)
                await asyncio.sleep(1)
                msgs = await self.client.get_messages(entity, limit=8)
                
                matching_buttons = []
                for m in msgs:
                    if m.reply_markup and hasattr(m.reply_markup, "rows"):
                        for i, row in enumerate(m.reply_markup.rows):
                            for j, btn in enumerate(row.buttons):
                                if hasattr(btn, "text") and keyword.lower() in btn.text.lower():
                                    matching_buttons.append((m, i, j, btn.text))
                
                for m, i, j, btn_text in matching_buttons:
                    try:
                        await m.click(i, j)
                        await asyncio.sleep(0.8)
                        latest = await self.client.get_messages(entity, limit=2)
                        for lm in latest:
                            if lm.text and keyword.lower() in lm.text.lower():
                                p = extract_price(lm.text)
                                if p:
                                    title = extract_title(lm.text, keyword) or btn_text.strip()
                                    if passes_filters(title, blacklist, whitelist):
                                        products.append({
                                            "username": username,
                                            "label": title,
                                            "price": p,
                                            "out_of_stock": is_out_of_stock(lm.text),
                                            "last_restock": last_restock,
                                        })
                                    break
                    except:
                        pass
            except:
                pass
            
            if not products:
                try:
                    async for msg in self.client.iter_messages(entity, search=keyword, limit=20):
                        if not msg.text or keyword.lower() not in msg.text.lower():
                            continue
                        p = extract_price(msg.text)
                        if p:
                            title = extract_title(msg.text, keyword) or username
                            if passes_filters(title, blacklist, whitelist):
                                products.append({
                                    "username": username,
                                    "label": title,
                                    "price": p,
                                    "out_of_stock": is_out_of_stock(msg.text),
                                    "last_restock": last_restock,
                                })
                            break
                except:
                    pass
                    
        except:
            pass
        return products
        
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
