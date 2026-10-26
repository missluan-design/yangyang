import re, base64

raw = open(r'C:\Users\lyx\AppData\Local\Temp\dsh-spill-hTJNs9\session-9cbf5dc985b9\b279480428a8-web_fetch.txt', encoding='utf-8').read()
m = re.search(r'"content":"([^"]+)","encoding":"base64"', raw)
if not m:
    print('NO MATCH')
    raise SystemExit(1)
data = base64.b64decode(m.group(1))
open(r'D:\lyx\Downloads\deepseek\panwatch-readme.md', 'wb').write(data)
print('decoded bytes:', len(data))
