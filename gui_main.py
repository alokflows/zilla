import os
import sys
import subprocess
import threading
import json
import customtkinter as ctk

# Ensure PyInstaller bundles bot.py and its dependencies
try:
    import bot
except ImportError:
    pass

# Run as background bot if arguments provided
if len(sys.argv) > 1 and sys.argv[1] == "--run-bot":
    import bot
    bot.main()
    sys.exit(0)

# Configure CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class AGYDesktopApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Zilla Engine — Legacy Mode")
        self.geometry("900x700")
        self.minsize(800, 500)
        
        self.bot_process = None
        self.env_path = os.path.join(os.getcwd(), ".env")
        self.model_path = os.path.join(os.getcwd(), "selected_model.txt")
        self.settings_json_path = os.path.join(os.getcwd(), "settings.json")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Sidebar ---
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color="#1a1a1a")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="ZILLA", font=ctk.CTkFont(size=26, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 10))
        
        self.version_label = ctk.CTkLabel(self.sidebar_frame, text="Legacy Mode", font=ctk.CTkFont(size=12), text_color="gray")
        self.version_label.grid(row=1, column=0, padx=20, pady=(0, 30))

        self.btn_chat = ctk.CTkButton(self.sidebar_frame, text="Live Dashboard", corner_radius=8, height=40, font=ctk.CTkFont(weight="bold"), command=self.show_chat)
        self.btn_chat.grid(row=2, column=0, padx=20, pady=10)

        self.btn_settings = ctk.CTkButton(self.sidebar_frame, text="Zilla Settings", corner_radius=8, height=40, font=ctk.CTkFont(weight="bold"), command=self.show_settings)
        self.btn_settings.grid(row=3, column=0, padx=20, pady=10)

        self.status_label = ctk.CTkLabel(self.sidebar_frame, text="Status: OFFLINE", text_color="#ff5555", font=ctk.CTkFont(weight="bold", size=14))
        self.status_label.grid(row=5, column=0, padx=20, pady=20)

        # --- Main Frame ---
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#0d0d0d")
        self.main_frame.grid(row=0, column=1, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)
        
        # --- Chat & Control View ---
        self.chat_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.chat_frame.grid_columnconfigure(0, weight=1)
        self.chat_frame.grid_rowconfigure(1, weight=1)
        
        self.controls_frame = ctk.CTkFrame(self.chat_frame, fg_color="#1a1a1a", corner_radius=15)
        self.controls_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
        
        self.btn_start = ctk.CTkButton(self.controls_frame, text="🚀 START SYSTEM", fg_color="#28a745", hover_color="#218838", corner_radius=8, height=45, font=ctk.CTkFont(weight="bold", size=14), command=self.start_bot)
        self.btn_start.pack(side="left", padx=20, pady=15)
        
        self.btn_stop = ctk.CTkButton(self.controls_frame, text="🛑 STOP SYSTEM", fg_color="#dc3545", hover_color="#c82333", corner_radius=8, height=45, font=ctk.CTkFont(weight="bold", size=14), state="disabled", command=self.stop_bot)
        self.btn_stop.pack(side="left", padx=20, pady=15)
        
        self.chat_display = ctk.CTkTextbox(self.chat_frame, wrap="word", font=ctk.CTkFont(family="Consolas", size=13), fg_color="#141414", border_color="#333333", border_width=1)
        self.chat_display.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.chat_display.insert("0.0", "Welcome to Zilla Engine.\nSystem ready. Click START SYSTEM to initialize the agent loop...\n\n")
        
        # --- Settings View ---
        self.settings_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.settings_frame.grid_columnconfigure(1, weight=1)
        
        self.settings_header = ctk.CTkLabel(self.settings_frame, text="Configuration Panel", font=ctk.CTkFont(size=28, weight="bold"))
        self.settings_header.grid(row=0, column=0, columnspan=2, sticky="w", pady=(30, 20), padx=30)
        
        # Telegram Settings
        self.lbl_tele = ctk.CTkLabel(self.settings_frame, text="Telegram Keys", font=ctk.CTkFont(size=18, weight="bold", text_color="#aaaaaa"))
        self.lbl_tele.grid(row=1, column=0, sticky="w", padx=30, pady=(10, 5))
        
        self.token_entry = ctk.CTkEntry(self.settings_frame, placeholder_text="TELEGRAM_BOT_TOKEN", height=40, corner_radius=8, border_width=1)
        self.token_entry.grid(row=2, column=0, columnspan=2, padx=30, pady=10, sticky="ew")
        
        self.owner_entry = ctk.CTkEntry(self.settings_frame, placeholder_text="TELEGRAM_OWNER_ID", height=40, corner_radius=8, border_width=1)
        self.owner_entry.grid(row=3, column=0, columnspan=2, padx=30, pady=10, sticky="ew")
        
        # AGY Backend Settings
        self.lbl_agy = ctk.CTkLabel(self.settings_frame, text="AI Model Selection", font=ctk.CTkFont(size=18, weight="bold", text_color="#aaaaaa"))
        self.lbl_agy.grid(row=4, column=0, sticky="w", padx=30, pady=(30, 5))
        
        self.model_var = ctk.StringVar(value="gemini-1.5-pro")
        self.model_dropdown = ctk.CTkOptionMenu(
            self.settings_frame, 
            variable=self.model_var,
            values=["gemini-1.5-pro", "gemini-2.0-flash", "gemini-1.5-flash", "gpt-4o", "claude-3-5-sonnet"],
            height=40, corner_radius=8, font=ctk.CTkFont(weight="bold")
        )
        self.model_dropdown.grid(row=5, column=0, columnspan=2, sticky="ew", padx=30, pady=10)

        self.btn_save = ctk.CTkButton(self.settings_frame, text="Save Configuration", corner_radius=8, height=45, font=ctk.CTkFont(weight="bold", size=14), command=self.save_settings)
        self.btn_save.grid(row=6, column=0, columnspan=2, padx=30, pady=40, sticky="ew")

        # Initialization
        self.load_settings()
        self.show_chat()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def show_chat(self):
        self.settings_frame.grid_forget()
        self.chat_frame.grid(row=0, column=0, sticky="nsew")

    def show_settings(self):
        self.chat_frame.grid_forget()
        self.settings_frame.grid(row=0, column=0, sticky="nsew")

    def load_settings(self):
        # Load .env
        if os.path.exists(self.env_path):
            with open(self.env_path, "r") as f:
                for line in f.readlines():
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        self.token_entry.insert(0, line.split("=", 1)[1].strip().strip('"').strip("'"))
                    elif line.startswith("TELEGRAM_OWNER_ID="):
                        self.owner_entry.insert(0, line.split("=", 1)[1].strip().strip('"').strip("'"))
        
        # Load Model
        if os.path.exists(self.model_path):
            with open(self.model_path, "r") as f:
                model = f.read().strip()
                if model:
                    self.model_var.set(model)

    def save_settings(self):
        token = self.token_entry.get().strip()
        owner = self.owner_entry.get().strip()
        with open(self.env_path, "w") as f:
            f.write(f'TELEGRAM_BOT_TOKEN="{token}"\nTELEGRAM_OWNER_ID="{owner}"\n')
            
        model = self.model_var.get()
        with open(self.model_path, "w") as f:
            f.write(model)
            
        self.log_message("[SYSTEM] Settings and Model Configuration saved successfully.")
        self.show_chat()

    def log_message(self, message):
        self.chat_display.insert("end", message + "\n")
        self.chat_display.see("end")

    def start_bot(self):
        if self.bot_process is None:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.status_label.configure(text="Status: ONLINE", text_color="#50fa7b")
            
            # The executable calls ITSELF with --run-bot to avoid opening the GUI again
            executable = sys.executable
            args = [executable, "--run-bot"]
            
            # Use environment variables to force non-buffered output
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            self.bot_process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            
            self.read_thread = threading.Thread(target=self.read_bot_output, daemon=True)
            self.read_thread.start()
            self.log_message("[SYSTEM] Zilla Engine & Telegram Bot initialized...\n")

    def read_bot_output(self):
        while self.bot_process:
            try:
                line = self.bot_process.stdout.readline()
                if not line:
                    break
                self.after(0, self.log_message, f"[AGY] {line.strip()}")
            except Exception:
                break
            
        self.after(0, self.on_bot_stopped)

    def on_bot_stopped(self):
        self.bot_process = None
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_label.configure(text="Status: OFFLINE", text_color="#ff5555")
        self.log_message("\n[SYSTEM] Engine offline.")

    def stop_bot(self):
        if self.bot_process:
            self.log_message("[SYSTEM] Sending termination signal to Zilla...")
            self.bot_process.terminate()
            try:
                self.bot_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.bot_process.kill()
            self.bot_process = None
            self.on_bot_stopped()

    def on_closing(self):
        self.stop_bot()
        self.destroy()

if __name__ == "__main__":
    app = AGYDesktopApp()
    app.mainloop()
