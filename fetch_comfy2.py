import os, json, base64, urllib.request, time

REPO="comfyanonymous/ComfyUI"; BRANCH="master"
ROOT=r"D:\lyx\Downloads\deepseek\.comfy"
os.makedirs(ROOT, exist_ok=True)

def get(path):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    req=urllib.request.Request(url, headers={"User-Agent":"x"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())

def save(rel):
    try:
        o=get(rel)
        dest=os.path.join(ROOT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest,"wb").write(base64.b64decode(o["content"]))
        print("OK",rel,o.get("size"))
    except Exception as e:
        print("ERR",rel,repr(e)[:70])
    time.sleep(0.8)

for f in ["main.py","server.py","api_server/__init__.py","api_server/routes/__init__.py",
          "comfy_execution/graph.py","app/__init__.py","comfy/model_management.py"]:
    save(f)

# is main.py at root?
try:
    get("main.py"); print("main.py EXISTS at root")
except Exception as e:
    print("main.py check:", repr(e)[:50])
