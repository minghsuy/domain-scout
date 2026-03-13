import re

with open("domain_scout/scout.py", "r") as f:
    lines = f.readlines()

for i, l in enumerate(lines[573:]):
    if "def _validate_seed" in l:
        print(f"validate seed starts at {i+573}")
        break
