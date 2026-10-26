import re
p = r"D:\lyx\Downloads\deepseek\.ghdl\src\platform\persistence\models.py"
t = open(p, encoding="utf-8").read()
# classes that are ORM models: have __tablename__
for m in re.finditer(r"class\s+(\w+)\(.*?\):\s*(.*?)(?=\nclass\s|\Z)", t, re.S):
    name = m.group(1)
    body = m.group(2)
    tn = re.search(r"__tablename__\s*=\s*[\"'](\w+)[\"']", body)
    if tn:
        print(f"MODEL {name} -> table {tn.group(1)}")
# also list all top-level class names
print("---ALL CLASSES---")
for c in re.findall(r"^class\s+(\w+)", t, re.M):
    print(c)
