# AutoUpdatePlugins

toro-main (Paper) のプラグインを、版を固定して配布元の公開ハッシュで確かめながらそろえるためのツール。
旧実装 (dev ブランチにあった、最新版を無検証で上書きする方式) は廃止し、TORO-Proxy の
plugin-pins と同じ lock 駆動の設計に作り替えた。

## 設計

- `plugins.lock.json` が正本。プラグインごとに配布元・版・ファイル名・URL・ハッシュを持つ。
- `auto_update_plugins.py` は標準ライブラリだけの Python で、サブコマンドは3つ。
  - `check`: 固定した版について、配布元が現在公開している情報と、実際にダウンロードした
    もののハッシュを、lock と照合する。
  - `update`: 配布元の最新リリースを解決し、ダウンロードして公開ハッシュと一致することを
    確かめてから lock を書き換える。上げるかどうかは PR のレビューで決める。
  - `fetch <dir>`: lock の版をディレクトリへ取りそろえる。既にある jar はハッシュが合えば
    取り直さず、版上げで旧くなった jar は glob に合うものを除く。本番の plugins へ直接
    書く用途ではなく、staging へそろえて運用の段階適用で配置する。
- 配布元 (`source`) は4種。
  - `modrinth:<project>`: Modrinth。版は loader が Paper 系の release から選ぶ。公開ハッシュは
    ファイルの sha512。環境変数 `AUP_GAME_VERSION` があればゲーム版でも絞る。
  - `github:<owner/repo>`: GitHub Releases。アセットは glob で選ぶ。公開ハッシュはアセットの
    digest (sha256)。古いアセットには digest が無いものがあり、その場合は取ったものの
    sha256 を lock として照合する。
  - `compat`: toro-plugin-compat のパッチ済みビルド (CoreProtect、Shopkeepers、EssentialsX、
    MyPet、Vehicles)。上流の自動更新で上書きしてはいけないため自動処理から外す。
  - `manual`: 検証できる公開ハッシュを持つ配布元が無いもの (スナップショット、閉源、公式
    サイト配布) と内製プラグイン。自動処理から外し、理由を note に書く。
- Velocity のプラグインは TORO-Proxy の `ops/both-service/plugins.lock.json` が担うため、
  ここには入れない。
- SignedVelocity-Paper は本番からの撤去が決まっているため収載していない
  (撤去はゲーム設定 Critical 対応の段階適用で行う)。

## lock の初期値について

初期の版とハッシュは、本番の全 jar の出所を実測した plugin-provenance 監査 (2026-11-06) の
結果を種にし、Modrinth と GitHub の実 API で解決して実ダウンロードと照合して埋めた。
自動対象は Modrinth 17件と GitHub 4件、compat 5件、manual 37件の計63件
(本番の64本から SignedVelocity-Paper を除いた数と一致する)。

## CI

- `ci.yml`: 単体テスト (ネットワークなし) と lock の形の検証。
- `plugin-lock.yml`: lock に触れる PR では `check` を実行。週1回と手動では `update` を
  実行し、差分があれば PR を作る。

## 使い方

```
python3 auto_update_plugins.py check
python3 auto_update_plugins.py update
python3 auto_update_plugins.py fetch /path/to/staging
```

GitHub API の回数制限を避ける場合は環境変数 `GH_TOKEN` を設定する。
