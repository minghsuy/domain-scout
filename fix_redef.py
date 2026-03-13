import re

with open("domain_scout/scout.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, l in enumerate(lines):
    if i == 205:
        skip = True

    if skip and l.startswith("class Scout:"):
        skip = False

    if not skip:
        new_lines.append(l)

with open("domain_scout/scout.py", "w") as f:
    f.write("".join(new_lines))
