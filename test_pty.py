"""
Test script: Can we capture agy output via a pseudo-terminal (PTY)?
agy writes directly to the terminal, not stdout, so we need a PTY.
"""
import time
import winpty

print("Creating PTY and spawning agy...")

# Create a pseudo-terminal
pty = winpty.PTY(80, 24)  # 80 columns, 24 rows

# Spawn agy in the PTY
# The PTY makes agy think it's running in a real terminal
pty.spawn(r'C:\Users\Isha\AppData\Local\agy\bin\agy.exe --print "What is 2+2? Reply in one short sentence."')

print("agy spawned, collecting output...")

output_chunks = []
start_time = time.time()
timeout = 60  # 60 second timeout

while True:
    elapsed = time.time() - start_time
    if elapsed > timeout:
        print(f"TIMEOUT after {timeout}s")
        break
    
    # Check if process is still alive
    if not pty.isalive():
        # Process finished — grab any remaining output
        try:
            remaining = pty.read()
            if remaining:
                output_chunks.append(remaining)
        except Exception:
            pass
        print(f"Process finished after {elapsed:.1f}s")
        break
    
    # Try to read output
    try:
        data = pty.read(blocking=False)
        if data:
            output_chunks.append(data)
            print(f"  [{elapsed:.1f}s] Got {len(data)} chars")
    except Exception:
        pass
    
    time.sleep(0.5)

full_output = "".join(output_chunks)
print(f"\n{'='*50}")
print(f"Total output length: {len(full_output)} chars")
print(f"{'='*50}")
print("OUTPUT:")
print(full_output)
print(f"{'='*50}")
