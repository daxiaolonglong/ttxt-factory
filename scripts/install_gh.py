"""Download a portable GitHub CLI release into D:\\ttxt\\tools\\gh (no PATH, no registry)."""
import json
import os
import sys
import urllib.request
import zipfile

TOKEN_FILE = r"D:\ttxt\.secrets\github_token"
TOOLS = r"D:\ttxt\tools"
DL = os.path.join(TOOLS, "_dl")


def token():
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env:
        return env.strip()
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return ""


def request(url, headers=None, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": "ttxt-install-gh", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    return data if raw else json.loads(data.decode("utf-8"))


def main():
    tok = token()
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"} if tok else {}
    release = request("https://api.github.com/repos/cli/cli/releases/latest", headers)
    tag = release["tag_name"]
    asset = None
    for item in release.get("assets", []):
        name = item["name"]
        if name.startswith("gh_") and name.endswith("windows_amd64.zip"):
            asset = item
            break
    if not asset:
        print(f"no windows_amd64.zip asset for {tag}")
        return 1
    os.makedirs(DL, exist_ok=True)
    zip_path = os.path.join(DL, asset["name"])
    if os.path.exists(zip_path) and os.path.getsize(zip_path) == asset["size"]:
        print(f"reusing {zip_path}")
    else:
        print(f"downloading {asset['name']} ({asset['size']} bytes) from {tag}")
        with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "ttxt-install-gh"}), timeout=180) as resp, open(zip_path, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
    dest = os.path.join(TOOLS, "gh")
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    print("installed to", dest)
    print("exe:", os.path.join(dest, "bin", "gh.exe"), os.path.exists(os.path.join(dest, "bin", "gh.exe")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
