import json
import os
import shutil
import sqlite3

src = os.path.join(os.environ["APPDATA"], "Trae CN", "User", "globalStorage", "state.vscdb")
dst = r"D:\ttxt\_model_fix\state_check2.vscdb"
shutil.copy2(src, dst)
con = sqlite3.connect(dst)

rows = con.execute("SELECT key, value FROM ItemTable WHERE value LIKE '%QWEN3.8%'").fetchall()
print("keys containing QWEN3.8:", len(rows))
for key, value in rows:
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    print("=" * 10, key, "len", len(text))
    print(text[:2500])

print("\n\n### all model-related keys ###")
keys = con.execute("SELECT key FROM ItemTable WHERE key LIKE '%model%'").fetchall()
for (k,) in keys:
    print(k)
