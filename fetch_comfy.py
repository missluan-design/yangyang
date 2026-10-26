import os, json, base64, urllib.request, time

REPO="comfyanonymous/ComfyUI"; BRANCH="master"
ROOT=r"D:\lyx\Downloads\deepseek\.comfy"
os.makedirs(ROOT, exist_ok=True)

def api_get(path):
    url=f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    req=urllib.request.Request(url, headers={"User-Agent":"x"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

# README
try:
    o=api_get("README.md")
    open(os.path.join(ROOT,"README.md"),"wb").write(base64.b64decode(o["content"]))
    print("README OK", o.get("size"))
except Exception as e:
    print("README ERR", repr(e)[:60])

# directory listings
for d in ["api_server","comfy_execution","blueprints","comfy","comfy_api","comfy_config","app","custom_nodes"]:
    try:
        lst=api_get(d)
        names=[x["name"] for x in lst]
        print(f"--- {d}/ ({len(names)}) ---")
        print("  "+", ".join(names[:40]))
    except Exception as e:
        print(f"--- {d} ERR {repr(e)[:50]}")
    time.sleep(0.8)
