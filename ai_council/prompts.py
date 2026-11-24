"""AIに渡す指示文（プロンプト）。討論の進め方を変えたいときはここを編集する。

参加者は全員が対等なので、システムプロンプトは全員共通の形で、
違うのは名前と立場・個性だけにしている。
"""

from __future__ import annotations

from dataclasses import dataclass

from .schemas import DebateConfig, ParticipantConfig

RATING_LABELS = ("賛成", "条件付き賛成", "反対")
CONTINUE_MARK = "【継続】"
STOP_MARK = "【終了】"


@dataclass(frozen=True)
class Entry:
    """議事録の1発言。"""

    speaker: str
    label: str
    text: str


def participant_system(cfg: DebateConfig, me: ParticipantConfig) -> str:
    stance = me.stance or "特に指定はありません。自分の考えで論じてください。"
    others = "、".join(p.name for p in cfg.participants if p.name != me.name)
    return f"""あなたは「AI合議室」という合議の場に参加している「{me.name}」です。
あなたの立場・個性：{stance}
ほかの参加者：{others}

この合議のしくみ：
- 参加者は全員が対等です。司会や議長はおらず、誰か1人が議論を仕切ることはありません。
- 発言の順番は、ラウンドごとに入れ替わります。
- 議論を続けるかどうかは、参加者全員の判断の多数決で決まります。
- 最後に全員が同時に結論案を出し、自分以外の案を作成者を伏せて評価します。最も支持を集めた案が結論として採択されます。

発言のルール：
- 日本語で、話し言葉に近い自然な文体で書いてください。
- 自分の立場・個性を一貫して保ってください。
- ほかの参加者の発言に触れるときは、名前を挙げて具体的に応答してください。
- 事実と意見を区別し、根拠のない断定は避けてください。
- 目的は相手を言い負かすことではなく、全員でより良い結論に近づくことです。説得力のある意見には、立場が違っても同意してかまいません。
- 冒頭に自分の名前や見出しを付けないでください。本文だけを書いてください。"""


def render_context(cfg: DebateConfig, transcript: list[Entry]) -> str:
    lines = [f"# 議題\n{cfg.topic}", "", "# 参加者（全員が対等です）"]
    for p in cfg.participants:
        lines.append(f"- {p.name}（立場：{p.stance or '指定なし'}）")
    lines += ["", "# これまでの議事録"]
    if not transcript:
        lines.append("（まだ発言はありません）")
    for entry in transcript:
        lines += ["", f"【{entry.label}】{entry.speaker}", entry.text.strip()]
    return "\n".join(lines)


def render_proposals(proposals: list[tuple[int, str]]) -> str:
    """評価用に結論案を並べる。作成者の名前は出さない。"""
    lines = ["# 結論案（作成者は伏せています）"]
    for no, text in proposals:
        lines += ["", f"## 案{no}", text.strip()]
    return "\n".join(lines)


def argument_instruction(cfg: DebateConfig, round_no: int, order: list[str], asks_vote: bool) -> str:
    n = cfg.max_chars
    order_text = "→".join(order)
    if round_no <= 1:
        body = (
            f"第1ラウンドです（発言順：{order_text}）。議題について、あなたの立場からの主張を述べてください。"
            f"根拠を2つ程度示してください。{n}字程度にしてください。"
        )
    else:
        last = "最終ラウンドです。" if round_no == cfg.rounds else ""
        body = (
            f"第{round_no}ラウンドです（発言順：{order_text}）。{last}"
            "これまでの発言を踏まえて応答してください。同意できる点は認め、反論すべき点には根拠を示してください。"
            f"合意に近づくための具体的な提案があれば述べてください。{n}字程度にしてください。"
        )
    if asks_vote:
        body += (
            "\n\n発言の最後の行には、議論をさらに続けるべきかどうかのあなたの判断を、次のどちらか一方だけで書いてください。"
            "この判断は参加者全員の多数決に使われます。\n"
            f"- まだ議論を深める価値があるなら：{CONTINUE_MARK}\n"
            f"- 論点が出尽くし、結論案を出す段階だと思うなら：{STOP_MARK}"
        )
    return body


def proposal_instruction(cfg: DebateConfig) -> str:
    return (
        "議論は以上です。これまでの議論を踏まえ、参加者全員が受け入れやすい結論案を書いてください。"
        "自分の立場だけでなく、ほかの参加者の懸念にも応える案にしてください。"
        "ほかの参加者も同時に結論案を書いており、あとで作成者を伏せて互いに評価します。\n"
        "次の見出しで書いてください。\n"
        "**結論** / **合意できる点** / **残る相違点** / **今後の課題**\n"
        f"全体で{cfg.max_chars + 200}字程度にしてください。"
    )


def evaluation_instruction(numbers: list[int]) -> str:
    targets = "、".join(f"案{n}" for n in numbers)
    return (
        f"上の結論案（{targets}）を、1つずつ評価してください。あなた自身の案は含まれていません。"
        "作成者が誰かを推測せず、内容だけで公平に評価してください。\n"
        "案ごとに1行ずつ、次の形式で書いてください。\n"
        "案（番号）：【賛成】【条件付き賛成】【反対】のいずれか1つ　理由（条件付きの場合はその条件）を60字程度で"
    )
