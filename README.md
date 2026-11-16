# AutoUpdatePlugins

TORO のゲームサーバー (Paper) で使うプラグインを、バージョンを固定したうえで、配布元が公開しているハッシュ (ファイルの指紋) と照らし合わせて確かめ、そろえるためのツールです。

固定したバージョン・ファイル名・URL・ハッシュは `plugins.lock.json` に書いてあります。これがすべての元になる情報です。

## 使い方

Python 3 だけで動きます (追加の部品は要りません)。

```
python3 auto_update_plugins.py check
python3 auto_update_plugins.py update
python3 auto_update_plugins.py fetch <保存先のフォルダ>
python3 auto_update_plugins.py verify <プラグインのフォルダ>
```

GitHub への問い合わせの回数制限を避けたいときは、環境変数 `GH_TOKEN` を設定します。

各命令の動き、配布元の種類 (`modrinth:` / `github:` / `compat` / `manual`)、環境変数の意味は、引数なしで実行すると表示される説明 (`auto_update_plugins.py` の先頭) にまとめています。

## 関連文書

- 自動検査の内容: `.github/workflows/`
- Velocity 側のプラグインの固定: [TORO-Proxy](https://github.com/TORO-Server/TORO-Proxy) の `ops/both-service/plugins.lock.json`
