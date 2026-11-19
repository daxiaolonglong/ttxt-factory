import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

DB = os.path.join(os.environ["APPDATA"], "Trae CN", "User", "globalStorage", "state.vscdb")
OUT = r"D:\ttxt\_model_fix"
TRAE = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Trae CN", "Trae CN.exe")
LOG = os.path.join(OUT, "last_run.log")

STALE = "custom_openai_compatible//QWEN3.8_3295152258"
GOOD = "custom_openai_compatible//qwen3.8-flash-next_3306088066"

K_SESSIONS = "582112113858064:AI.agent.model.session_selected_model"
K_LABELS = "582112113858064:AI.agent.model.recent_user_selection_by_agent_label"
K_GLOBAL = "582112113858064_ai-chat:sessionRelation:globalModelMap"
ALL_KEYS = (K_SESSIONS, K_LABELS, K_GLOBAL)

WAIT_SECONDS = 120
KILL = "--kill" in sys.argv
RESTART = "--restart" in sys.argv


def say(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def trae_pids():
    try:
        out = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True, timeout=30).stdout or ""
    except Exception:
        return []
    return [ln for ln in out.splitlines() if ln.lower().startswith("trae")]


def can_write(path):
    try:
        fh = open(path, "r+b")
    except Exception as exc:
        return False, str(exc)
    fh.close()
    return True, ""


def load(con, key):
    row = con.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return row[0].decode("utf-8") if isinstance(row[0], bytes) else row[0]


def patch(path):
    con = sqlite3.connect(path, timeout=20)
    try:
        con.execute("BEGIN IMMEDIATE")
        n1 = 0
        raw = load(con, K_SESSIONS)
        if raw:
            data = json.loads(raw)
            for sel in data.values():
                if not isinstance(sel, dict):
                    continue
                for cfg in sel.values():
                    if isinstance(cfg, dict) and STALE in json.dumps(cfg):
                        cfg["modelId"] = cfg["modelId"].replace(STALE, GOOD)
                        n1 += 1
            con.execute("UPDATE ItemTable SET value=? WHERE key=?",
                        (json.dumps(data, ensure_ascii=False), K_SESSIONS))
        n2 = 0
        for key in (K_LABELS, K_GLOBAL):
            raw = load(con, key)
            if raw and STALE in raw:
                con.execute("UPDATE ItemTable SET value=? WHERE key=?",
                            (raw.replace(STALE, GOOD), key))
                n2 += 1
        con.commit()
        return n1, n2
    finally:
        con.close()


def stale_left(path):
    con = sqlite3.connect(path, timeout=20)
    try:
        return [k for k in ALL_KEYS if (load(con, k) or "").find(STALE) >= 0]
    finally:
        con.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    say("start; kill=%s restart=%s" % (KILL, RESTART))
    if not os.path.exists(DB):
        say("db not found")
        return 3

    if KILL:
        subprocess.run(["taskkill", "/F", "/IM", "Trae CN.exe", "/T"],
                       capture_output=True, timeout=60)
        say("taskkill sent")
        time.sleep(4)

    ok = False
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        ok, reason = can_write(DB)
        if ok:
            say("file writable")
            break
        pids = trae_pids()
        say("waiting; trae lines=%d; %s" % (len(pids), reason))
        if KILL and pids:
            subprocess.run(["taskkill", "/F", "/IM", "Trae CN.exe", "/T"],
                           capture_output=True, timeout=60)
        time.sleep(5)
    if not ok:
        say("STOP: cannot write db")
        if RESTART:
            start_trae()
        return 2

    backup = os.path.join(OUT, "state_backup_" + time.strftime("%Y%m%d_%H%M%S") + ".vscdb")
    shutil.copy2(DB, backup)
    say("backup -> " + backup)

    try:
        n1, n2 = patch(DB)
        say("patched sessions=%d defaults=%d" % (n1, n2))
    except Exception as exc:
        say("direct patch failed: %s" % exc)
        tmp = os.path.join(OUT, "state_patched.vscdb")
        try:
            shutil.copy2(DB, tmp)
            n1, n2 = patch(tmp)
            shutil.copy2(tmp, DB)
            say("copy-and-replace ok sessions=%d defaults=%d" % (n1, n2))
        except Exception as exc2:
            say("FATAL: %s" % exc2)
            if RESTART:
                start_trae()
            return 4

    remain = stale_left(DB)
    if remain:
        say("WARN still stale: %s" % remain)
    else:
        say("DONE all references point to local model")

    if RESTART:
        start_trae()
    return 0


def start_trae():
    if os.path.exists(TRAE):
        subprocess.Popen([TRAE], close_fds=True)
        say("trae started")
    else:
        say("trae exe not found")


if __name__ == "__main__":
    sys.exit(main())
