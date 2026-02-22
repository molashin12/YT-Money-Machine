#!/usr/bin/env python3
"""
YouTube Shorts Generator — Cross-Platform Installer

Detects your operating system and runs the appropriate installation script.

Usage:
    python install.py
"""

import os
import sys
import platform
import subprocess


BANNER = """
╔══════════════════════════════════════════════════════╗
║                                                      ║
║   🎬 YouTube Shorts Generator — Installer            ║
║                                                      ║
║   Automated deployment with SSL + reverse proxy      ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
"""


def main():
    print(BANNER)

    os_name = platform.system().lower()
    script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")

    print(f"  Detected OS: {platform.system()} {platform.release()}")
    print(f"  Architecture: {platform.machine()}")
    print(f"  Python: {platform.python_version()}")
    print()

    if os_name == "linux":
        script = os.path.join(script_dir, "install_linux.sh")
        if not os.path.exists(script):
            print(f"  ❌ Script not found: {script}")
            sys.exit(1)

        print("  🐧 Running Linux installer...")
        print("  ℹ️  This script requires sudo/root access.")
        print()

        # Make executable
        os.chmod(script, 0o755)

        # Check if running as root
        if os.geteuid() != 0:
            print("  Relaunching with sudo...")
            os.execvp("sudo", ["sudo", "bash", script])
        else:
            os.execvp("bash", ["bash", script])

    elif os_name == "windows":
        script = os.path.join(script_dir, "install_windows.ps1")
        if not os.path.exists(script):
            print(f"  ❌ Script not found: {script}")
            sys.exit(1)

        print("  🪟 Running Windows installer...")
        print("  ℹ️  This script requires Administrator access.")
        print()

        # Check if admin
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            is_admin = False

        if not is_admin:
            print("  ⚠️  Please run this script as Administrator!")
            print("  Right-click PowerShell → 'Run as Administrator'")
            print(f"  Then run: python install.py")
            sys.exit(1)

        subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", script],
            check=True,
        )

    elif os_name == "darwin":
        print("  🍎 macOS detected.")
        print("  macOS deployment is not yet supported.")
        print("  Use the Linux script on a VPS/cloud server for production.")
        sys.exit(1)

    else:
        print(f"  ❌ Unsupported OS: {platform.system()}")
        print("  Supported: Linux (Ubuntu/Debian), Windows 10/11")
        sys.exit(1)


if __name__ == "__main__":
    main()
