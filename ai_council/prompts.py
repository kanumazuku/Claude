"""AIに渡す指示文（プロンプト）。討論の進め方を変えたいときはここを編集する。"""

from __future__ import annotations

from dataclasses import dataclass

from .providers import TurnKind
from .schemas import DebateConfig, ParticipantConfig

VOTE_LABELS = ("賛成", "条件付き賛成", "反対")


@dataclass(frozen=True)
class Entry:
    """議事録の1発言。"""

    speaker: str
    label: str
    text: str


def moderator_system(cfg: DebateConfig) -> str:
    persona = cfg.moderator.stance or "落ち着いて公平に議論を整理する。"
    return f"""あなたは「AI合議室」という討論の場で司会を務める「{cfg.moderator.name}」です。
あなたの個性：{persona}

司会の役割：
- 中立の立場で議論を整理し、参加者が建設的に合意を形成できるよう導いてください。
- 自分の意見を強く主張したり、特定の参加者に肩入れしたりしないでください。
- 参加者の発言を正確に要約し、名前を挙げて誰がどう述べたかを示してください。

書き方のルール：
- 日本語で書いてください。
- 見出しや箇条書きは使ってかまいませんが、冗長にならないようにしてください。
- 発言の冒頭に自分の名前や「司会：」などの見出しを付けないでください。本文だけを書いてください。"""


def debater_system(cfg: DebateConfig, me: ParticipantConfig) -> str:
    stance = me.stance or "特に指定はありません。自分の考えで論じてください。"
    others = "、".join(d.name for d in cfg.debaters if d.name != me.name)
    return f"""あなたは「AI合議室」という討論の場に参加する討論者「{me.name}」です。
あなたの立場・個性：{stance}
ほかの討論者：{others}
司会：{cfg.moderator.name}

討論のルール：
- 日本語で、話し言葉に近い自然な文体で発言してください。
- 1回の発言は{cfg.max_chars}字程度に収めてください。
- 自分の立場・個性を一貫して保ってください。
- ほかの参加者の発言に触れるときは、名前を挙げて具体的に応答してください。
- 事実と意見を区別し、根拠のない断定は避けてください。
- 目的は相手を言い負かすことではなく、より良い結論に近づくことです。説得力のある反論を受けたら、主張を修正してもかまいません。
- 発言の冒頭に自分の名前や見出しを付けないでください。本文だけを書いてください。"""


def render_context(cfg: DebateConfig, transcript: list[Entry]) -> str:
    lines = [f"# 議題\n{cfg.topic}", "", "# 参加者", f"- 司会：{cfg.moderator.name}"]
    for d in cfg.debaters:
        lines.append(f"- 討論者：{d.name}（立場：{d.stance or '指定なし'}）")
    lines += ["", "# これまでの議事録"]
    if not transcript:
        lines.append("（まだ発言はありません）")
    for entry in transcript:
        lines += ["", f"【{entry.label}】{entry.speaker}", entry.text.strip()]
    return "\n".join(lines)


def instruction(kind: TurnKind, cfg: DebateConfig, round_no: int = 0) -> str:
    n = cfg.max_chars
    if kind == TurnKind.OPENING:
        return (
            "司会として討論を開会してください。議題の背景を簡潔に整理し、"
            "この討論で扱うべき論点を2〜4個、番号付きで示してください。"
            f"最後に、討論者に第1ラウンドの発言を促してください。全体で{n}字程度にしてください。"
        )
    if kind == TurnKind.ARGUMENT:
        if round_no <= 1:
            return (
                f"第1ラウンドです。あなたの立場から、議題についての主張を述べてください。"
                f"根拠を2つ程度示してください。{n}字程度にしてください。"
            )
        last = "最終ラウンドです。" if round_no == cfg.rounds else ""
        return (
            f"第{round_no}ラウンドです。{last}司会の整理と、ほかの討論者の発言を踏まえて応答してください。"
            "同意できる点は認め、反論すべき点には根拠を示して反論してください。"
            f"合意に近づくための具体的な提案があれば述べてください。{n}字程度にしてください。"
        )
    if kind == TurnKind.INTERIM:
        return (
            f"第{round_no}ラウンドが終わりました。司会として議論を整理してください。\n"
            "1. 合意しつつある点\n2. 意見が分かれている点\n3. 次のラウンドで焦点を当てるべき問い\n"
            f"を、{n}字程度で示してください。\n\n"
            "最後の行には、判定を次のどちらか一方だけで書いてください。\n"
            "- まだ議論を深める価値があるなら：【判定】継続\n"
            "- 十分に合意が形成された、または論点が出尽くしたなら：【判定】終了"
        )
    if kind == TurnKind.PROPOSAL:
        return (
            "議論はここまでです。司会として、これまでの議論を踏まえた「結論案」を提示してください。"
            "合意できた点を中心に、意見が分かれた点にも配慮した、参加者が受け入れやすい案にしてください。"
            f"最後に、討論者に結論案への賛否を求めてください。{n}字程度にしてください。"
        )
    if kind == TurnKind.FINAL:
        return (
            "司会が示した結論案に対するあなたの最終的な賛否を表明してください。"
            "冒頭に【賛成】【条件付き賛成】【反対】のいずれか1つを書き、"
            "続けて理由（条件付きの場合はその条件）を150字程度で述べてください。"
        )
    # CONCLUSION
    return (
        "司会として、討論の最終結論をまとめてください。結論案への各討論者の賛否を反映し、"
        "次の見出しで整理してください。\n"
        "**結論** / **合意した点** / **残った相違点** / **今後の課題**\n"
        f"全体で{n + 200}字程度にしてください。"
    )
