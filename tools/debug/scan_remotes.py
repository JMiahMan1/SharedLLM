
# import requests  # not needed - prints directly

HA_URL = "http://ai.local:11435/api/ha/states"
# We'll valid auth via the server's known method or just try generic if we can't get token easily.
# Actually, the test script uses /api/ha/state/{entity_id} proxy which doesn't list all.
# I will use the internal app code or just curl if I can.
# Wait, I can use the existing 'live_test_media_fixes.py' helper or just assume I can't easily list without a token.
# BUT, I can grep the logs for 'remote.' again or look at 'devices.py' logic.
# Better: Use the python shell with the app's credentials if possible?
# No, let's just use curl with the HA_URL and the token from a known config if available? 
# I don't have the token handy in the prompt context.
# I'll search the codebase for where `remote.` is used or defined to see naming conventions.
print("Scanning for remote entities...")
