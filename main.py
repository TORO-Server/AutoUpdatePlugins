import yaml
import requests
import os
import hashlib
import re
import sys

# 定数
YAML_FILE = 'plugins.yaml'
GITHUB_API_URL_BASE = 'https://api.github.com/repos'


def get_file_sha256(filepath):
    """ファイルのSHA-256ハッシュを計算する"""
    if not os.path.exists(filepath):
        return None

    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def download_file(url, save_path):
    """ファイルをダウンロードして保存する"""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()  # HTTPエラーがあれば例外を発生させる

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"    ❌ ダウンロードエラー: {e}")
        return False


def process_plugin(plugin):
    """単一のプラグインを処理する"""
    file_to_save = plugin['file']
    github_url = plugin['url']
    get_pattern = plugin.get('get')

    print(f"🔎 {file_to_save} のチェックを開始...")

    # GitHubのURLから owner/repo を抽出
    match = re.search(r'github\.com/([^/]+)/([^/\s]+)', github_url)
    if not match:
        print(f"    ❓ 無効なGitHub URLです: {github_url}")
        return
    owner, repo = match.groups()
    repo = repo.replace('.git', '')  # .git を削除

    # GitHub APIで最新リリースの情報を取得
    api_url = f"{GITHUB_API_URL_BASE}/{owner}/{repo}/releases/latest"
    try:
        headers = {'Accept': 'application/vnd.github.v3+json'}
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        release_data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"    APIエラー ({repo}): {e}")
        return

    assets = release_data.get('assets', [])
    if not assets:
        print(f"    🤷 アセットが最新リリースに見つかりません: {repo}")
        return

    # ダウンロード対象のアセットを特定
    target_asset = None
    for asset in assets:
        asset_name = asset['name']
        if get_pattern:
            if re.match(get_pattern, asset_name):
                target_asset = asset
                break
        elif re.match(".*\\.jar", asset_name):
            target_asset = asset
            break

    if not target_asset:
        print(
            f"    🤷 条件に一致するアセットが見つかりません (file: {file_to_save}, get: '{get_pattern}')")
        return

    download_url = target_asset['browser_download_url']

    # ローカルファイルのハッシュ値を取得
    local_hash = get_file_sha256(file_to_save)

    # リモートファイルのハッシュ値を計算するために一度ダウンロード
    try:
        remote_response = requests.get(download_url, timeout=30)
        remote_response.raise_for_status()
        remote_data = remote_response.content
        remote_hash = hashlib.sha256(remote_data).hexdigest()
    except requests.exceptions.RequestException as e:
        print(f"    ❌ リモートファイルの取得に失敗: {e}")
        return

    # ハッシュ値を比較して更新を判断
    if local_hash == remote_hash:
        print(f"    ✅ 最新です。スキップします。")
        return

    if local_hash:
        print(f"    🔄 新しいバージョンが見つかりました。更新します...")
    else:
        print(f"    📥 ダウンロードします...")

    # ファイルを保存
    try:
        with open(file_to_save, 'wb') as f:
            f.write(remote_data)
        print(f"    ✔️ {file_to_save} のダウンロード/更新が完了しました。")
    except IOError as e:
        print(f"    ❌ ファイルの保存に失敗: {e}")


def main():
    """メイン処理"""
    if not os.path.exists(YAML_FILE):
        print(f"エラー: 設定ファイル '{YAML_FILE}' が見つかりません。")
        sys.exit(1)

    with open(YAML_FILE, 'r', encoding='utf-8') as f:
        plugins = yaml.safe_load(f)

    if not plugins:
        print("設定ファイルにプラグインが定義されていません。")
        return

    print("--- プラグインの更新チェックを開始します ---")
    for plugin in plugins['list']:
        process_plugin(plugin)
        print("-" * 20)
    print("--- 全てのチェックが完了しました ---")


if __name__ == '__main__':
    main()
