"""Check tracked and new project files for private paths, credentials and generated state."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
patterns = ["/" + r"Users/[^/\s]+/", r"(?i)-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----", r"(?i)\bsk-[a-zA-Z0-9_-]{24,}", r"(?i)gh[pousr]_[A-Za-z0-9]{25,}"]
problems = []
for name in files:
    if not name:
        continue
    file = root / name
    if not file.is_file():
        continue
    if file.suffix in (".sqlite", ".sqlite3", ".db") or name.endswith("-wal"):
        problems.append(name + ": runtime database")
    if file.suffix == ".pdf":
        continue
    text = file.read_text(errors="replace")
    for pattern in patterns:
        if re.search(pattern, text):
            problems.append(name + ": private content pattern")
if problems:
    print("\n".join(sorted(set(problems))))
    sys.exit(1)
print("Public tree check passed")
