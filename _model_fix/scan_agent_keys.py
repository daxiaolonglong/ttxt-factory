import json
import os
import shutil
import sqlite3

src = os.path.join(os.environ["APPDATA"], "Trae CN", "User", "globalStorage", "state.vscdb")
dst = r"D:\ttxt\_model_fix\state_scan.vscdb"
shutil.copy2(src, dst)
con = sqlite3.connect(dst)

rows = con.execute("SELECT key, value FROM ItemTable").fetchall()
print("total keys:", len(rows))
for key, value in rows:
    if not isinstance(key, str):
        continue
    low = key.lower()
    if "agent" not in low and "mode" not in low and "assistant" not in low:
        continue
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    if len(text) > 1200:
        text = text[:1200] + "...[truncated]"
    print("=" * 12, key)
    print(text)
