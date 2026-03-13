import re
with open("domain_scout/scout.py", "r") as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    if "def _discover" in l:
        print(f"found at {i}")
        break
