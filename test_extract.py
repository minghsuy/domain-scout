with open("domain_scout/scout.py", "r") as f:
    lines = f.readlines()
# let's look at lines 278 to 572
code = "".join(lines[278:573])
print(code[:200])
print("...")
print(code[-200:])
