"""
Test: Can agy continue a conversation using --conversation <id> --print?
Step 1: Run agy --print "My name is Krishna" → detect new conversation ID
Step 2: Run agy --conversation <id> --print "What is my name?" → should remember
"""
import os
import time
import winpty

AGY = r"C:\Users\Isha\AppData\Local\agy\bin\agy.exe"
BRAIN = r"C:\Users\Isha\.gemini\antigravity-cli\brain"


def get_conversations():
    return set(os.listdir(BRAIN))


def run_agy_pty(command, timeout=60):
    pty = winpty.PTY(120, 500)
    pty.spawn(command)
    chunks = []
    start = time.time()
    while time.time() - start < timeout:
        if not pty.isalive():
            try:
                r = pty.read()
                if r:
                    chunks.append(r)
            except Exception:
                pass
            break
        try:
            data = pty.read(blocking=False)
            if data:
                chunks.append(data)
        except Exception:
            pass
        time.sleep(0.3)
    return "".join(chunks).strip()


# Step 1: Send first message, detect conversation ID
print("=" * 50)
print("STEP 1: Sending first message...")
before = get_conversations()
result1 = run_agy_pty(f'"{AGY}" --print "My name is Krishna. Remember it. Reply with just OK."')
after = get_conversations()
new_convs = after - before
print(f"Response: {result1}")
print(f"New conversations detected: {new_convs}")

if not new_convs:
    print("ERROR: No new conversation detected!")
    # Try using --continue instead
    print("\nFalling back to --continue test...")
    result2 = run_agy_pty(f'"{AGY}" -c --print "What is my name? Reply in one sentence."')
    print(f"Response with -c: {result2}")
else:
    conv_id = new_convs.pop()
    print(f"Conversation ID: {conv_id}")

    # Step 2: Continue the conversation
    print("\n" + "=" * 50)
    print(f"STEP 2: Continuing conversation {conv_id}...")
    result2 = run_agy_pty(f'"{AGY}" --conversation {conv_id} --dangerously-skip-permissions --print "summarize the top trending GitHub report"')
    print(f"Response: {result2}")

print("\n" + "=" * 50)
print("TEST COMPLETE")
