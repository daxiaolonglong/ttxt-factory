import json
import os
import shutil
import sqlite3
import time

src = os.path.join(os.environ["APPDATA"], "Trae CN", "User", "globalStorage", "state.vscdb")
outdir = r"D:\ttxt\_model_fix"
os.makedirs(outdir, exist_ok=True)
backup = os.path.join(outdir, f"state_backup_{time.strftime('%H%M%S')}.vscdb")
shutil.copy2(src, backup)
print("backup:", backup, os.path.getsize(backup), "bytes")

STALE = "custom_openai_compatible//QWEN3.8_3295152258"
GOOD = "custom_openai_compatible//qwen3.8-flash-next_3306088066"

keys = (
    "582112113858064:AI.agent.model.session_selected_model",
    "582112113858064:AI.agent.model.recent_user_selection_by_agent_label",
)

con = sqlite3.connect(src, timeout=10)
changed = 0
for key in keys:
    row = con.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()
    if not row:
        print("missing", key)
        continue
    raw = row[0]
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if STALE not in text:
        print("no stale id in", key)
        continue
    new = text.replace(STALE, GOOD)
    json.loads(new)
    con.execute("UPDATE ItemTable SET value=? WHERE key=?", (new, key))
    changed += 1
    print("patched", key)
con.commit()

for key in keys:
    row = con.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()
    text = row[0]
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    print("=" * 4, key)
    print(text[:700])
print("changed keys:", changed)
