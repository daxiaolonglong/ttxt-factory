import json
import os
import shutil
import sqlite3

src = os.path.join(os.environ["APPDATA"], "Trae CN", "User", "globalStorage", "state.vscdb")
dst = r"D:\ttxt\_model_fix\state_check3.vscdb"
shutil.copy2(src, dst)
con = sqlite3.connect(dst)

for key in ("582112113858064:AI.agent.model.model_list_map",
            "582112113858064_AI.agent.model.model_list_map"):
    row = con.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()
    if not row:
        continue
    data = json.loads(row[0])
    print("=" * 20, key, "groups:", list(data.keys()))
    for group, models in data.items():
        for m in models:
            name = m.get("name", "")
            if "QWEN" in name.upper() or m.get("base_url"):
                print(f"  [{group}] name={name!r} display={m.get('display_name')!r} "
                      f"preset={m.get('is_preset')} base_url={m.get('base_url')!r} "
                      f"selectable={m.get('selectable')} status={m.get('status')}")
