import os
import subprocess
import sys
import webbrowser
import textwrap  # 👈 ADD THIS

def run_backend():
    print("🚀 Starting Flask backend...")
    return subprocess.Popen([sys.executable, "backend/app.py"])

def open_frontend():
    path = os.path.abspath("frontend/index.html")
    url = "file://" + path
    print(f"🌐 Opening frontend at {url}")
    webbrowser.open(url)

if __name__ == "__main__":
    backend = run_backend()
    try:
        open_frontend()
        print(textwrap.dedent("""
        ✅ Finance Tracker running!

        - Backend API: http://127.0.0.1:5000/api
        - Frontend: frontend/index.html (already opened)

        Press CTRL+C to stop.
        """))
        backend.wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        backend.terminate()
