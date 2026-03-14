import re

with open("domain_scout/cli.py", "r") as f:
    content = f.read()

print("File has noqa: PLR0913:", "noqa: PLR0913" in content)
