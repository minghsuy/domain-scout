import re

with open("domain_scout/scout.py", "r") as f:
    text = f.read()

# I see "self._dns.reset()" is throwing a coroutine was never awaited warning.
# Looking at domain_scout/scout.py:299 it says self._dns.reset().
# But previously in _discover it was also self._dns.reset(), why didn't it throw this before?
# Ah, wait. `_discover` was an `async def`. It's still `async def _discover`. But `self._dns.reset()` wasn't awaited before either. Wait!
