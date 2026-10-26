"""Use the current macOS network route for a background CLI process."""
import os
import platform
import re
import subprocess


def background_environment():
    environment = dict(os.environ)
    if platform.system() != "Darwin":
        return environment
    result = subprocess.run(["/usr/sbin/scutil", "--proxy"], capture_output=True, text=True, timeout=5)
    if result.returncode:
        return environment
    settings = dict(re.findall(r"^\s*(\w+)\s*:\s*(\S+)\s*$", result.stdout, re.M))
    for kind in ("HTTP", "HTTPS"):
        key = kind + "_PROXY"
        if environment.get(key) or environment.get(key.lower()):
            continue
        host, port = settings.get(kind + "Proxy", ""), settings.get(kind + "Port", "")
        if settings.get(kind + "Enable") == "1" and re.fullmatch(r"[A-Za-z0-9_.-]+", host) and port.isdigit() and 0 < int(port) < 65536:
            environment[key] = f"http://{host}:{port}"
    if "NO_PROXY" not in environment and "no_proxy" not in environment:
        environment["NO_PROXY"] = "localhost,127.0.0.1,::1"
    return environment
