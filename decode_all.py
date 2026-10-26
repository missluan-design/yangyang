import os, base64, glob

ROOT = r"D:\lyx\Downloads\deepseek\.ghdl"
count = 0
for b64path in glob.glob(os.path.join(ROOT, "**", "*.b64"), recursive=True):
    out = b64path[:-4]  # strip .b64
    with open(b64path, "r", encoding="utf-8") as f:
        data = f.read().strip()
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(base64.b64decode(data))
        count += 1
        print("decoded", os.path.relpath(out, ROOT))
    except Exception as e:
        print("ERR", b64path, repr(e)[:80])
print("TOTAL decoded:", count)
