"""
Test agy with a web search type question on a FRESH conversation (no --conversation).
"""
import winpty
import time
import re
import sys

AGY_PATH = r"C:\Users\Isha\AppData\Local\agy\bin\agy.exe"
prompt = "summarize the top 5 trending GitHub repositories right now in bullet points"

print(f"Testing agy with: '{prompt}'")
print("=" * 60)

pty = winpty.PTY(
    200, 1000,
    backend=winpty.Backend.ConPTY,
    agent_config=winpty.AgentConfig.WINPTY_FLAG_COLOR_ESCAPES,
)

# NO --conversation flag — fresh conversation
cmd = f'"{AGY_PATH}" --print-timeout 5m --print "{prompt}"'
print(f"Command: {cmd}")
print("=" * 60)

pty.spawn(cmd, cwd=r"C:\Users\Isha")

chunks = []
start = time.time()

while True:
    elapsed = time.time() - start
    
    if elapsed > 300:
        print(f"\n[TIMEOUT at {elapsed:.0f}s]")
        break
    
    if not pty.isalive():
        try:
            remaining = pty.read()
            if remaining:
                chunks.append(remaining)
        except:
            pass
        print(f"\n[EXITED at {elapsed:.1f}s]")
        break
    
    try:
        data = pty.read(blocking=False)
        if data:
            chunks.append(data)
            cleaned = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\[.*?[@-~]|\x1b[()][AB012]|\x1b[>=<]|\r", "", data)
            if cleaned.strip():
                # Show progress
                preview = cleaned.strip()[:150].replace('\n', ' | ')
                sys.stdout.write(f"[t={elapsed:.0f}s] {preview}\n")
                sys.stdout.flush()
    except:
        pass
    
    time.sleep(0.15)

# Full output
raw = "".join(chunks)
ansi_re = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\[.*?[@-~]|\x1b[()][AB012]|\x1b[>=<]|\r")
clean = ansi_re.sub("", raw)
clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

print("\n" + "=" * 60)
print("FINAL OUTPUT:")
print("=" * 60)
print(clean[:2000])
if len(clean) > 2000:
    print(f"\n... [{len(clean) - 2000} more chars]")
print("=" * 60)
print(f"Total length: {len(clean)} chars")
print(f"Time: {time.time() - start:.1f}s")
