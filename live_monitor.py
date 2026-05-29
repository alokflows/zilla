import os
import time
import json

SESSIONS_FILE = "sessions.json"
BRAIN_DIR = r"C:\Users\Isha\.gemini\antigravity-cli\brain"

def get_active_conversation():
    try:
        if not os.path.exists(SESSIONS_FILE):
            return None
        with open(SESSIONS_FILE, "r") as f:
            data = json.load(f)
            active_name = data.get("active")
            if not active_name:
                return None
            return data.get("sessions", {}).get(active_name, {}).get("conversation_id")
    except Exception:
        return None

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def main():
    clear_screen()
    print("=======================================================")
    print("  AGY Live Monitor — Watching the Brain in Real-Time")
    print("=======================================================")
    print("Waiting for bot activity...\n")

    current_conv = None
    transcript_file = None
    f_log = None

    try:
        while True:
            active_conv = get_active_conversation()
            
            # Did the active conversation change?
            if active_conv != current_conv:
                if f_log:
                    f_log.close()
                    f_log = None
                
                current_conv = active_conv
                if current_conv:
                    print(f"\n[MONITOR] Switched to conversation: {current_conv[:8]}...")
                    transcript_file = os.path.join(BRAIN_DIR, current_conv, ".system_generated", "logs", "transcript.jsonl")
            
            # Try to open the file if we don't have it open
            if current_conv and not f_log and os.path.exists(transcript_file):
                f_log = open(transcript_file, "r", encoding="utf-8")
                lines = f_log.readlines()
                start_idx = max(0, len(lines) - 50)
                for line in lines[start_idx:]:
                    process_log_line(line.strip())
            
            # Tail the file
            if f_log:
                line = f_log.readline()
                if line:
                    process_log_line(line.strip())
                else:
                    time.sleep(0.5)
            else:
                time.sleep(1.0)
                
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
        if f_log:
            f_log.close()

def process_log_line(line):
    if not line:
        return
    try:
        data = json.loads(line)
        step_type = data.get("type", "")
        content = data.get("content", "")
        tools = data.get("tool_calls", [])

        if step_type == "USER_INPUT":
            print("\n[ YOU SENT TO BOT ]:")
            print(content)
            print("-" * 50)
            
        elif step_type == "PLANNER_RESPONSE":
            if content and content.strip():
                print("\n[ GEMINI THINKING ]:")
                print(content.strip())
            
            for tool in tools:
                name = tool.get("name", "")
                args = tool.get("arguments", {})
                print(f"  --> [Tool Call] {name}({args})")
                
    except Exception:
        pass

if __name__ == "__main__":
    main()
