#!/usr/bin/env python3
"""toro-main (Paper) のプラグインの固定を確かめる・上げる・取りそろえる (標準ライブラリだけ)。

使い方:
  python3 auto_update_plugins.py check            固定した版を配布元の公開ハッシュと比べ、取ったものとも比べる
  python3 auto_update_plugins.py update           配布元の最新のリリースに lock を書き換える (上げるのは PR で)
  python3 auto_update_plugins.py fetch <dir>      固定した版を <dir> へ取りそろえる (本番への配置は運用の段階適用で行う)
  python3 auto_update_plugins.py verify <dir>     <dir> の jar を lock の sha256 と比べる (sha256 を持つエントリのみ)

設計:
  plugins.lock.json が正本。エントリの source は modrinth:<project>、github:<owner/repo>、
  compat、manual の4種。compat は toro-plugin-compat のパッチ済みビルドで、上流の自動更新で
  上書きしてはいけない。manual は検証できる公開ハッシュを持つ配布元が無いものと内製で、
  check / update / fetch の自動処理から外す。ただし compat と manual でも、実測した sha256 を
  lock に持つエントリは verify で実ファイルと照合でき、差し替えや改変を検出できる。
  Velocity のプラグインは TORO-Proxy の
  ops/both-service/plugins.lock.json が担うため、ここには入れない。

公開ハッシュ: Modrinth はファイルの sha512、GitHub はアセットの digest (sha256)。
GitHub の古いアセットには digest が無いものがあり、その場合は update のときに取ったものの
sha256 を lock とし、check では取ったものと lock の照合だけを行う (NG にはしない)。
環境変数 GH_TOKEN があれば GitHub API の回数制限に使う。AUP_GAME_VERSION を指定すると
Modrinth の版をそのゲーム版に絞る。
"""
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.request

LOCK = pathlib.Path(__file__).resolve().parent / "plugins.lock.json"
MODRINTH_API = "https://api.modrinth.com/v2"
GITHUB_API = "https://api.github.com/repos"
USER_AGENT = "TORO-AutoUpdatePlugins/1.0 (+https://github.com/TORO-Server/AutoUpdatePlugins)"
TIMEOUT_SECONDS = 120
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA512 = re.compile(r"^[0-9a-f]{128}$")
PAPER_LOADERS = {"paper", "bukkit", "spigot", "purpur"}


def api_json(url):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = os.environ.get("GH_TOKEN", "")
    if token and url.startswith(GITHUB_API):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
        return json.load(res)


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)


def download_hashes(url):
    """(sha256, sha512) を返す。CDN の一時的な切断に備えて最大3回まで取り直す。"""
    last = None
    for _ in range(3):
        sha256 = hashlib.sha256()
        sha512 = hashlib.sha512()
        try:
            with _open(url) as res:
                for chunk in iter(lambda: res.read(1024 * 1024), b""):
                    sha256.update(chunk)
                    sha512.update(chunk)
            return sha256.hexdigest(), sha512.hexdigest()
        except Exception as e:  # 切断時は途中までのハッシュを捨てて最初から取り直す
            last = e
    raise last


def download_to(url, dest):
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for _ in range(3):
        try:
            with _open(url) as res, open(tmp, "wb") as f:
                for chunk in iter(lambda: res.read(1024 * 1024), b""):
                    f.write(chunk)
            os.replace(tmp, dest)
            return
        except Exception as e:
            last = e
            tmp.unlink(missing_ok=True)
    raise last


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_auto(entry):
    return entry["source"].startswith(("modrinth:", "github:"))


# --- Modrinth ---

def modrinth_pick_file(version_data, glob):
    files = version_data.get("files", [])
    matched = [f for f in files if fnmatch.fnmatch(f["filename"], glob)]
    if not matched:
        return None
    for f in matched:
        if f.get("primary"):
            return f
    return matched[0]


def modrinth_select(versions, entry, latest):
    """entry の条件に合う版を1つ選ぶ。latest なら release で loader が Paper 系のうち最新。"""
    game_version = os.environ.get("AUP_GAME_VERSION", "")
    candidates = []
    for v in versions:
        if latest:
            if v.get("version_type") != "release":
                continue
            if not PAPER_LOADERS.intersection(v.get("loaders", [])):
                continue
            if game_version and game_version not in v.get("game_versions", []):
                continue
        else:
            if v.get("version_number") != entry["version"]:
                continue
        f = modrinth_pick_file(v, entry["glob"])
        if f is None:
            continue
        candidates.append((v, f))
    if not candidates:
        return None
    if latest:
        candidates.sort(key=lambda vf: vf[0].get("date_published", ""), reverse=True)
    v, f = candidates[0]
    return {
        "version": v["version_number"],
        "file": f["filename"],
        "url": f["url"],
        "sha512": f["hashes"]["sha512"],
    }


def modrinth_resolve(entry, latest):
    slug = entry["source"].partition(":")[2]
    versions = api_json(f"{MODRINTH_API}/project/{slug}/version")
    got = modrinth_select(versions, entry, latest)
    if got is None:
        raise ValueError(f"{entry['source']}: 条件に合う版が Modrinth にありません")
    return got


# --- GitHub ---

def github_pick_asset(release, glob):
    matched = [a for a in release.get("assets", []) if fnmatch.fnmatch(a["name"], glob)]
    return matched[0] if matched else None


def github_normalize(release, entry):
    asset = github_pick_asset(release, entry["glob"])
    if asset is None:
        raise ValueError(f"{entry['source']} {release.get('tag_name')}: {entry['glob']} に合うアセットがありません")
    got = {
        "version": release["tag_name"],
        "file": asset["name"],
        "url": asset["browser_download_url"],
    }
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        got["sha256_published"] = digest.removeprefix("sha256:")
    return got


def github_resolve(entry, latest):
    repo = entry["source"].partition(":")[2]
    if latest:
        release = api_json(f"{GITHUB_API}/{repo}/releases/latest")
        if release.get("prerelease") or release.get("draft"):
            raise ValueError(f"{entry['source']}: 最新がリリース版ではありません")
        return github_normalize(release, entry)
    releases = api_json(f"{GITHUB_API}/{repo}/releases?per_page=100")
    for release in releases:
        if release.get("tag_name") == entry["version"]:
            return github_normalize(release, entry)
    raise ValueError(f"{entry['source']}: 固定した版 {entry['version']} のリリースがありません")


# --- 共通 ---

def resolve(entry, latest):
    kind = entry["source"].partition(":")[0]
    if kind == "modrinth":
        return modrinth_resolve(entry, latest)
    if kind == "github":
        return github_resolve(entry, latest)
    raise ValueError(f"{entry['source']}: 自動処理の対象外です")


def validated(entry):
    if not entry["version"] or not entry["url"].startswith("https://") or not SHA256.match(entry["sha256"]):
        raise ValueError(f"{entry['source']}: lock の値の形が想定と違います")
    if entry["source"].startswith("modrinth:") and not SHA512.match(entry.get("sha512", "")):
        raise ValueError(f"{entry['source']}: lock の sha512 の形が想定と違います")
    return entry


def check(lock):
    bad = 0
    for name, entry in lock.items():
        if not is_auto(entry):
            print(f"SKIP {name}: {entry['source']} ({entry.get('note', '自動処理の対象外')})")
            continue
        try:
            validated(entry)
            published = resolve(entry, latest=False)
            sha256, sha512 = download_hashes(entry["url"])
        except Exception as e:
            print(f"NG {name}: {e}")
            bad += 1
            continue
        problems = []
        if published["url"] != entry["url"] or published["file"] != entry["file"]:
            problems.append("配布元のファイルが lock と違う")
        if entry["source"].startswith("modrinth:") and published["sha512"] != entry["sha512"]:
            problems.append("配布元の sha512 が lock と違う")
        if entry["source"].startswith("github:") and published.get("sha256_published") not in (None, entry["sha256"]):
            problems.append("配布元の digest が lock と違う")
        if sha256 != entry["sha256"]:
            problems.append("取ったものの sha256 が lock と違う")
        if entry["source"].startswith("modrinth:") and sha512 != entry["sha512"]:
            problems.append("取ったものの sha512 が lock と違う")
        if problems:
            print(f"NG {name}: {'; '.join(problems)}")
            bad += 1
        else:
            print(f"OK {name}: {entry['version']} {sha256}")
    return 1 if bad else 0


def update(lock):
    new = {}
    changed = 0
    for name, entry in lock.items():
        if not is_auto(entry):
            new[name] = entry
            continue
        latest = resolve(entry, latest=True)
        if latest["version"] == entry["version"] and latest["file"] == entry["file"] and latest["url"] == entry["url"]:
            new[name] = entry
            continue
        sha256, sha512 = download_hashes(latest["url"])
        if latest.get("sha512") and sha512 != latest["sha512"]:
            raise ValueError(f"{name}: 取ったものが Modrinth の公開ハッシュと違います")
        if latest.get("sha256_published") and sha256 != latest["sha256_published"]:
            raise ValueError(f"{name}: 取ったものが GitHub の公開ハッシュと違います")
        updated = dict(entry)
        updated.update({"version": latest["version"], "file": latest["file"], "url": latest["url"], "sha256": sha256})
        if latest.get("sha512"):
            updated["sha512"] = latest["sha512"]
        new[name] = validated(updated)
        print(f"{name}: {entry['version']} -> {latest['version']}")
        changed += 1
    if changed:
        tmp = LOCK.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, LOCK)
    else:
        print("更新はありません")
    return 0


def fetch(lock, target):
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, entry in lock.items():
        if not is_auto(entry):
            print(f"SKIP {name}: {entry['source']} は自動でそろえません")
            continue
        dest = target / entry["file"]
        if dest.exists() and file_sha256(dest) == entry["sha256"]:
            print(f"OK {name}: 既に {entry['file']} がそろっています")
        else:
            download_to(entry["url"], dest)
            got = file_sha256(dest)
            if got != entry["sha256"]:
                print(f"NG {name}: 取ったものの sha256 が lock と違います ({got})")
                dest.unlink(missing_ok=True)
                bad += 1
                continue
            print(f"FETCH {name}: {entry['file']} ({entry['version']})")
        for other in sorted(target.glob(entry["glob"])):
            if other.name != entry["file"] and other.is_file():
                other.unlink()
                print(f"REMOVE {name}: 旧い {other.name} を除きました")
    return 1 if bad else 0


def verify(lock, plugins_dir):
    """<dir> の jar を lock の sha256 と比べる。compat / manual を含め、sha256 を持つ
    エントリが対象。sha256 の無いエントリは SKIP、ファイルが無いか違えば NG。"""
    bad = 0
    for name, entry in lock.items():
        want = entry.get("sha256", "")
        if not SHA256.match(want):
            print(f"SKIP {name}: sha256 が lock にありません")
            continue
        path = pathlib.Path(plugins_dir) / entry["file"]
        if not path.is_file():
            print(f"NG {name}: {entry['file']} がありません")
            bad += 1
            continue
        got = file_sha256(path)
        if got != want:
            print(f"NG {name}: {entry['file']} の sha256 が lock と違います ({got})")
            bad += 1
        else:
            print(f"OK {name}: {entry['version']} {got}")
    return 1 if bad else 0


def main():
    args = sys.argv[1:]
    if args not in (["check"], ["update"]) and not (len(args) == 2 and args[0] in ("fetch", "verify")):
        print(__doc__)
        return 2
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if args[0] == "check":
        return check(lock)
    if args[0] == "update":
        return update(lock)
    if args[0] == "verify":
        return verify(lock, args[1])
    return fetch(lock, args[1])


if __name__ == "__main__":
    sys.exit(main())
