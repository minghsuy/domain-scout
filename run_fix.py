import re

with open("domain_scout/scout.py", "r") as f:
    lines = f.readlines()

end_idx = 0
for i, l in enumerate(lines[278:]):
    if l.startswith("    # --- Step 1: Seed validation ---"):
        end_idx = i + 278
        break

print(f"End idx: {end_idx}")
