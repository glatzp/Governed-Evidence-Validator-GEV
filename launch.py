import os
import sys
import time
import subprocess
import webbrowser
import threading
import urllib.request
from urllib.error import URLError, HTTPError

# Enable Windows CMD ANSI color processing if on Windows
if sys.platform == "win32":
    os.system("color")

# Premium Styled Banner with ANSI colors
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"

BANNER = f"""{BLUE}{BOLD}
+-----------------------------------------------------------+
|                                                           |
|    {CYAN}GOVERNED VALIDATION SYSTEM - LOCAL LAUNCHER v1.0{BLUE}       |
|                                                           |
+-----------------------------------------------------------+{RESET}"""

def print_status(step, status="[OK]", color=GREEN):
    status_str = status.replace("✓", "OK").replace("✗", "FAIL")
    print(f"{color}{status_str}{RESET} {step}")

def verify_environment():
    print(BANNER)
    print(f"\n{BOLD}Step 1: Verifying Local Environment & Structure...{RESET}")
    
    # 1. Verify project directory layout
    required_dirs = ["data", "web/static", "web/templates", "governed", "validator"]
    for d in required_dirs:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            print_status(f"Created missing directory: {d}", "[✓]", GREEN)
        else:
            print_status(f"Verified directory: {d}", "[✓]", GREEN)

    # 2. Check if web/main.py exists
    if not os.path.exists("web/main.py"):
        print_status("Critical Error: web/main.py not found!", "[✗]", "\033[91m")
        sys.exit(1)
    
    # 3. Check for dependencies and auto-install if missing
    print(f"\n{BOLD}Step 2: Checking Software Dependencies...{RESET}")
    required_packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "jinja2": "jinja2",
        "pydantic": "pydantic"
    }
    
    missing_packages = []
    for pkg_name, import_name in required_packages.items():
        try:
            __import__(import_name)
            print_status(f"Package '{pkg_name}' is installed.", "[✓]", GREEN)
        except ImportError:
            print_status(f"Package '{pkg_name}' is missing.", "[!]", YELLOW)
            missing_packages.append(pkg_name)
            
    if missing_packages:
        print_status("Missing dependencies detected! Attempting automatic installation...", "[~]", YELLOW)
        try:
            # Install missing packages using pip
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
            print_status("Dependencies installed successfully!", "[✓]", GREEN)
        except subprocess.CalledProcessError as e:
            print_status(f"Failed to install dependencies: {e}", "[✗]", "\033[91m")
            print("Please run manually: pip install " + " ".join(missing_packages))
            sys.exit(1)

def poll_and_launch_browser(url, timeout=15):
    """Polls the server URL until responsive, then launches the browser."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Check if port 8000 is listening by making a HEAD request
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=1.0) as response:
                break
        except (URLError, HTTPError):
            # HTTPError indicates port is listening but returned an HTTP status error (e.g. 404)
            # Either way, server is up
            break
        except Exception:
            time.sleep(0.5)
            continue
            
    print_status(f"Server is active! Opening interface at {url} ...", "[✓]", GREEN)
    webbrowser.open(url)

def main():
    verify_environment()
    
    url = "http://127.0.0.1:8000"
    
    print(f"\n{BOLD}Step 3: Initializing FastAPI Server...{RESET}")
    print_status("Launching Uvicorn server in background thread...", "[✓]", GREEN)
    print_status(f"Auto-opening default browser at {url} when online...", "[✓]", GREEN)
    print("----------------------------------------------------------------------")
    
    # Start browser polling thread
    browser_thread = threading.Thread(target=poll_and_launch_browser, args=(url,), daemon=True)
    browser_thread.start()
    
    # Import uvicorn locally
    import uvicorn
    
    # Run uvicorn with reload=True to preserve developer workflow
    try:
        uvicorn.run("web.main:app", host="127.0.0.1", port=8000, reload=True)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[~] Server shut down gracefully by operator request.{RESET}")
    except Exception as e:
        print(f"\n\033[91m[FAIL] Server crashed: {e}\033[0m")
        sys.exit(1)

if __name__ == "__main__":
    main()
