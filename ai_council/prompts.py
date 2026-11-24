"""AIに渡す指示文（プロンプト）。合議の進め方を変えたいときはここを編集する。

参加者は全員が対等なので、システムプロンプトは全員共通の形で、
違うのは名前と観点・個性だけにしている。
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import CouncilConfig, ParticipantConfig

OPINION_MARK = "【意見】"
DECISION_MARK = "【判断】"
REVISED_MARK = "【修正版】"


@dataclass(frozen=True)
class Entry:
    """議論の記録の1発言。原稿そのものは含めず、意見だけを残す。"""

    speaker: str
    label: str
    text: str


def participant_system(cfg: CouncilConfig, me: ParticipantConfig) -> str:
    stance = me.stance or "特に指定はありません。自分の考えで参加してください。"
    others = "、".join(p.name for p in cfg.participants if p.name != me.name)
    return f"""あなたは「AI合議室」で、ほかのAIと議論しながら、ユーザーのプロンプトへの回答を共同で作っている「{me.name}」です。
あなたの観点・個性：{stance}
ほかの参加者：{others}

この合議のしくみ：
- 参加者は全員が対等です。リーダーや編集長はおらず、誰か1人が決定権を持つことはありません。
- 最初に全員で回答の方針を話し合い、くじ引きで決まった参加者が初稿を書きます。
- その後、全員が順番に原稿を読み、意見を述べたうえで「承認」するか「修正版」を出します。
- 全員が同じ版を承認したら、その版が完成した回答としてユーザーに届きます。

議論と修正のルール：
- 議論は日本語で書いてください。回答の原稿は、プロンプトが求める言語と形式で書いてください。
- 自分の観点を活かしつつ、ユーザーにとっての回答の質を高めることを最優先にしてください。
- ほかの参加者の意見に触れるときは、名前を挙げて具体的に応答してください。
- 修正するときは、ほかの参加者がすでに改善した点を壊さないようにしてください。
- 改善の余地が小さいなら、無理に修正せず承認してください。好みの違いだけで修正を繰り返さないでください。
- 発言の冒頭に自分の名前を付けないでください。"""


def render_context(
    cfg: CouncilConfig, transcript: list[Entry], draft: str = "", version: int = 0, editor: str = ""
) -> str:
    lines = ["# ユーザーのプロンプト", cfg.prompt, "", "# 参加者（全員が対等です）"]
    for p in cfg.participants:
        lines.append(f"- {p.name}（観点：{p.stance or '指定なし'}）")
    lines += ["", "# これまでの議論"]
    if not transcript:
        lines.append("（まだ発言はありません）")
    for entry in transcript:
        lines += ["", f"【{entry.label}】{entry.speaker}", entry.text.strip()]
    if draft:
        lines += ["", f"# 現在の原稿（第{version}版、最後に書いた参加者：{editor}）", draft.strip()]
    return "\n".join(lines)


def planning_instruction(cfg: CouncilConfig, order: list[str]) -> str:
    return (
        f"まず、このプロンプトにどう答えるべきかを全員で話し合います（発言順：{'→'.join(order)}）。"
        "プロンプトの意図の解釈、回答に必ず含めるべき内容、構成案、注意すべき点について、"
        "あなたの観点から意見を述べてください。先に発言した参加者がいれば、その意見に応答してください。"
        f"原稿そのものはまだ書かないでください。{cfg.max_chars}字程度にしてください。"
    )


def draft_instruction() -> str:
    return (
        "くじ引きの結果、あなたが初稿を書くことになりました。"
        "自分の意見だけでなく、方針の議論で出た全員の意見を公平に取り入れて、プロンプトへの回答の初稿を書いてください。"
        "ユーザーにそのまま届けられる回答の本文だけを書いてください（「以下が初稿です」などの前置きは不要です）。"
    )


def revision_instruction(cfg: CouncilConfig, round_no: int, order: list[str], version: int) -> str:
    last = "最終ラウンドです。" if round_no == cfg.rounds else ""
    return (
        f"第{round_no}ラウンドです（発言順：{'→'.join(order)}）。{last}"
        f"現在の原稿（第{version}版）を読み、次の形式で書いてください。\n\n"
        f"{OPINION_MARK}\n"
        f"原稿の良い点と改善すべき点、ほかの参加者の意見への応答を、{cfg.max_chars}字程度で書いてください。\n"
        f"{DECISION_MARK}\n"
        "「承認」か「修正」のどちらか一方だけを書いてください。現在の原稿をそのまま完成としてよいなら「承認」、"
        "直すべき点があるなら「修正」です。\n"
        f"{REVISED_MARK}\n"
        "「修正」の場合だけ、修正した原稿の全文を書いてください。変更しない部分も省略せずに書いてください。"
        "「承認」の場合は、この見出しごと書かないでください。"
    )
