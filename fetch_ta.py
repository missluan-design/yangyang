import os, json, base64, urllib.request, time

REPO="TNT-Likely/PanWatch"; BRANCH="main"
ROOT=r"D:\lyx\Downloads\deepseek\.ghdl"
FILES=[
 "src/modules/automation/tradingagents/__init__.py",
 "src/modules/automation/tradingagents/agent.py",
 "src/modules/automation/tradingagents/decision.py",
 "src/modules/automation/tradingagents/runtime_support.py",
]
def api_get(path):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    req=urllib.request.Request(url, headers={"User-Agent":"pw"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
for p in FILES:
    try:
        obj=api_get(p)
        dest=os.path.join(ROOT,p)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest,"wb").write(base64.b64decode(obj["content"]))
        print("OK",p,obj.get("size"))
    except Exception as e:
        print("ERR",p,repr(e)[:80])
    time.sleep(1.2)
