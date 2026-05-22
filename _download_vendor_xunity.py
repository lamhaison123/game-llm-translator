"""One-off script to download BepInEx + XUnity into vendor/xunity/ for offline bundling."""
import requests
from pathlib import Path

vendor_dir = Path(__file__).parent / "vendor" / "xunity"
vendor_dir.mkdir(parents=True, exist_ok=True)

def download(url, dest):
    if dest.exists():
        print(f"  Already exists: {dest.name}")
        return
    print(f"  Downloading {dest.name}...")
    tmp = dest.with_suffix(".tmp")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as fp:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    fp.write(chunk)
    tmp.replace(dest)
    print(f"  Done: {dest.name} ({dest.stat().st_size // 1024} KB)")

# BepInEx win x64
r = requests.get("https://api.github.com/repos/BepInEx/BepInEx/releases/latest", timeout=15)
rel = r.json()
for a in rel["assets"]:
    name = a["name"]
    if "BepInEx_win_x64_" in name and name.endswith(".zip"):
        download(a["browser_download_url"], vendor_dir / name)
        break

# BepInEx linux x64
for a in rel["assets"]:
    name = a["name"]
    if "BepInEx_linux_x64_" in name and name.endswith(".zip"):
        download(a["browser_download_url"], vendor_dir / name)
        break

# XUnity BepInEx Mono
r2 = requests.get("https://api.github.com/repos/bbepis/XUnity.AutoTranslator/releases/latest", timeout=15)
rel2 = r2.json()
for a in rel2["assets"]:
    name = a["name"]
    if "XUnity.AutoTranslator-BepInEx-" in name and "IL2CPP" not in name and name.endswith(".zip"):
        download(a["browser_download_url"], vendor_dir / name)
        break

print("All done.")
