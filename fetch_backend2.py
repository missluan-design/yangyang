import os, json, base64, urllib.request, time

REPO = "TNT-Likely/PanWatch"
BRANCH = "main"
ROOT = r"D:\lyx\Downloads\deepseek\.ghdl"
os.makedirs(ROOT, exist_ok=True)

FILES = [
    "src/platform/notifications/notifier.py",
    "src/platform/notifications/notify_policy.py",
    "src/platform/scheduling/trading_calendar.py",
    "src/platform/scheduling/schedule_parser.py",
    "src/platform/persistence/models.py",
    "src/platform/observability/otel.py",
    "src/platform/marketdata/models.py",
    "src/platform/ai/ai_client.py",
    "src/modules/administration/api/auth.py",
    "src/modules/automation/tradingagents.py",
    "Makefile",
    "Dockerfile",
    "src/bootstrap/application.py",
]

def api_get(path):
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    req = urllib.request.Request(url, headers={"User-Agent": "panwatch-gh"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

ok, fail = [], []
for p in FILES:
    try:
        obj = api_get(p)
        if isinstance(obj, dict) and "content" in obj:
            dest = os.path.join(ROOT, p)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(base64.b64decode(obj["content"]))
            ok.append(p)
            print("OK", p, obj.get("size"))
        else:
            fail.append((p, "not-file"))
            print("SKIP", p)
    except Exception as e:
        msg = repr(e)
        if "403" in msg or "rate limit" in msg.lower():
            print("RATELIMIT at", p, "- stopping, will retry remaining later")
            fail.append((p, "RATELIMIT"))
            break
        fail.append((p, msg[:60]))
        print("ERR", p, msg[:80])
    time.sleep(1.2)

print("DONE ok=%d fail=%d" % (len(ok), len(fail)))
print("OK:", ok)
print("FAIL:", fail)
