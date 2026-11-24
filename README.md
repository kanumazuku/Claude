# AI合議室

Claude、GPT、Gemini、ローカルLLMなど、さまざまなAIが1つの議題について自律的に討論し、合議で結論を出すWebアプリです。

司会役のAIが議論を整理しながら進行し、討論者のAIたちが互いの発言に応答します。最後に司会が結論案を示し、討論者が賛否を表明したうえで、最終結論をまとめます。

## 討論の流れ

1. **開会**：司会が議題を整理し、論点を示します。
2. **ラウンド**：討論者が順に主張し、互いの発言に名前を挙げて応答します。
3. **中間整理**：ラウンドごとに、司会が合意点と対立点をまとめます。さらに議論を続けるかどうかを自分で判断し、論点が出尽くしたら残りのラウンドを省略します。
4. **結論案と最終意見**：司会が結論案を示し、討論者が【賛成】【条件付き賛成】【反対】で賛否を表明します。
5. **最終結論**：司会が賛否を反映して、結論、合意した点、残った相違点、今後の課題をまとめます。

発言はリアルタイムで画面に流れます。終わった討論は、議事録（Markdown）としてコピー・保存できます。

## 使えるAI

| AI | 必要なもの | 既定のモデル |
|---|---|---|
| Claude（Anthropic） | `ANTHROPIC_API_KEY` | `claude-opus-5-5` |
| GPT（OpenAI） | `OPENAI_API_KEY` | `gpt-5.5` |
| Gemini（Google） | `GEMINI_API_KEY` | `gemini-3.8-flash` |
| ローカルLLM（Ollama） | Ollamaを起動しておく | `gemma3` |
| デモ | 不要 | 定型文を返すだけのAI |

参加者ごとに、使うAIとモデルを画面で選べます。APIキーが設定されているAIは自動で検出され、参加者に順番に割り当てられます。キーが1つもなくても、デモAIで流れを試せます。

## はじめかた

Python 3.10以上が必要です。

```bash
python -m venv .venv
source .venv/bin/activate          # Windowsでは .venv\Scripts\activate
pip install -e .

cp .env.example .env               # 使いたいAIのAPIキーを書き込む
python -m ai_council
```

ブラウザーで http://127.0.0.1:8000 を開きます。

### 設定（.env）

APIキーのほかに、次の項目を変更できます。詳しくは`.env.example`を見てください。

| 項目 | 内容 | 既定値 |
|---|---|---|
| `COUNCIL_CLAUDE_MODEL` など | 各AIの既定のモデル | 上の表のとおり |
| `COUNCIL_CLAUDE_EFFORT` | Claudeの思考の深さ（`low`〜`max`） | `medium` |
| `OLLAMA_HOST` | OllamaのURL | `http://localhost:11434` |
| `COUNCIL_MOCK_DELAY` | デモAIが文字を流す間隔（秒） | `0.015` |
| `COUNCIL_HOST`、`COUNCIL_PORT` | サーバーの待ち受け先 | `127.0.0.1`、`8000` |

サーバーは既定で自分のPCからしか接続できない`127.0.0.1`で起動します。APIキーを使って費用が発生するため、外部に公開する場合は認証を追加してください。

## 費用の目安

1回の討論では、AIを次の回数だけ呼び出します（討論者の人数をN、ラウンド数をRとします）。

- 司会：開会1回、中間整理R−1回、結論案1回、最終結論1回
- 討論者：各ラウンドN回、最終意見N回

討論者3人、2ラウンドの場合は合計13回です。発言が進むほど議事録が長くなり、後半の呼び出しほど入力が増えます。

## 構成

```
ai_council/
├── main.py              Webサーバー（FastAPI）。画面の配信と、討論のリアルタイム配信（SSE）
├── debate.py            討論エンジン。発言順、中間判定、賛否の集計
├── prompts.py           AIへの指示文。討論の進め方を変えたいときはここを編集
├── schemas.py           画面から受け取る討論設定
├── config.py            .envの読み込み
├── providers/
│   ├── base.py          AI共通のインターフェース
│   ├── claude_provider.py
│   ├── openai_provider.py
│   ├── gemini_provider.py
│   ├── ollama_provider.py
│   └── mock_provider.py デモAI
└── static/              画面（HTML、CSS、JavaScript）
tests/                   テスト（APIキー不要）
```

### 新しいAIを追加するには

1. `providers/`に`Provider`を継承したクラスを作り、`status()`と`stream()`を実装します。
2. `providers/__init__.py`の`build_registry()`に登録します。
3. 画面の色を付けたい場合は、`static/app.js`の`PROVIDER_COLORS`と`static/style.css`に追加します。

## Claudeの呼び出しについて

- 公式SDK `anthropic` を使い、ストリーミングで発言を受け取ります。
- Claude Opus 5.5は常に思考してから発言します。思考の深さは`COUNCIL_CLAUDE_EFFORT`で調整できます。
- 対応モデル（Opus 5.5など）では、安全分類器が発言を辞退したときに別のモデルが引き継ぐ「サーバー側フォールバック」（ベータ機能、`fallbacks: "default"`）を有効にしています。不要な場合は`providers/claude_provider.py`の`_FALLBACK_MODELS`を空にしてください。

## テスト

```bash
pip install -e ".[dev]"
pytest
```

テストはデモAIとテスト用のAIで動くため、APIキーは不要です。
