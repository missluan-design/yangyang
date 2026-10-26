import json

raw = open(r'C:\Users\lyx\AppData\Local\Temp\dsh-spill-hTJNs9\session-9cbf5dc985b9\b279480428a8-web_fetch.txt', encoding='utf-8').read()
s = raw.find('{')
e = raw.rfind('}')
obj = json.loads(raw[s:e+1])
paths = [t['path'] for t in obj['tree']]
print('TOTAL', len(paths))

backend = [p for p in paths if not p.startswith('frontend/') and not p.startswith('.github/') and not p.startswith('.agents/') and not p.startswith('docs/')]
# Show top-level dirs and key backend files
import os
top = {}
for p in backend:
    first = p.split('/')[0]
    top.setdefault(first, 0)
    top[first] += 1
print('\n--- TOP LEVEL (non-frontend/github/agents/docs) ---')
for k, v in sorted(top.items()):
    print(f'{v:4d}  {k}')

print('\n--- .py files at root and 1-2 levels (backend) ---')
for p in backend:
    if p.endswith('.py'):
        parts = p.split('/')
        if len(parts) <= 2:
            print(p)
