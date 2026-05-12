from typing import List, TypedDict
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError
import time
import settings
import logging

ASSET_CLASSES = settings.ASSET_CLASSES
REGIONS = settings.REGIONS


class NewsState(TypedDict, total=False):
    company: str
    title: str
    content: str
    classify_asset_class: bool

    is_relevant: bool
    error_relevance: str

    summary: str
    error_summary: str

    asset_class_scores: dict[str, float]
    asset_class_reasoning: str
    region_scores: dict[str, float]
    region_reasoning: str
    error_proposal: str
    asset_class_labels: list[str]
    needs_cove: bool
    cove_ran: bool

    is_google_rss: bool
    is_korean: bool
    asset_class_final: list[str]
    asset_class_verify_reason: str
    error_cove: str



class RelevanceOut(BaseModel):
    is_relevant: bool = Field(
        ...,
        description="True if article is relevant to the target. False otherwise.",
    )

class SummaryOut(BaseModel):
    summary: str = Field(..., description="2-3 sentence summary of the article")

class ScoreItem(BaseModel):
    name: str = Field(..., description="Exact asset class/region key from the provided list")
    score: float = Field(..., ge=0.0, le=1.0)

class ClassifyProposalOut(BaseModel):
    asset_class_reasoning: str
    asset_class_scores: List[ScoreItem]
    region_reasoning: str
    region_scores: List[ScoreItem]

class CoVEOut(BaseModel):
    corrected_scores: List[ScoreItem] = Field(
        ..., description="Corrected asset-class scores after verification"
    )
    final_asset_class: str = Field(
        ..., description="Single best asset class label (must be one of provided keys)"
    )
    verify_reason: str = Field(
        ..., description="One sentence: what you corrected, or confirmed if no correction was needed"
    )



llm = ChatOpenAI(model=settings.MODEL, temperature=settings.TEMPERATURE)

summary_model = getattr(settings, "SUMMARY_MODEL", settings.MODEL)
summary_base_llm = ChatOpenAI(model=summary_model, temperature=settings.TEMPERATURE)

relevance_llm = llm.with_structured_output(RelevanceOut)
summarize_llm = summary_base_llm.with_structured_output(SummaryOut)
classify_proposal_llm = llm.with_structured_output(ClassifyProposalOut)
cove_llm = llm.with_structured_output(CoVEOut)


def final_stock_relevance_pass(company: str, title: str, content: str) -> RelevanceOut:
    """
    Final pass to aggressively remove stock-price/dividend/ratings style articles
    right before email rendering.
    """
    system = SystemMessage(content=(
        "You are a strict relevance classifier for macro policy news.\n"
        "You will be given a target region/topic.\n"
        "Decide whether the article is relevant to macroeconomic policy, fiscal policy, monetary policy,\n"
        "or political events in that region/topic.\n"
        "Pass only policy-level or geopolitical developments such as: central bank actions,\n"
        "government budget/tax/tariff decisions, election outcomes, sanctions, or major state policy shifts.\n"
        "Mark NOT relevant if it is mainly about company earnings, product launches, individual stocks,\n"
        "analyst ratings, lifestyle/social stories, or local incidents without policy impact.\n"
    ))

    user = HumanMessage(content=f"""
Target region/topic:
{company}

Article title:
{title}

Article content:
{content}
""".strip())

    last_err: str | None = None
    for attempt in range(3):
        try:
            return relevance_llm.invoke([system, user])
        except (ValidationError, ValueError) as e:
            if settings.GUARDRAILS:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.3 * (attempt + 1))
            else:
                raise

    logging.warning(
        "Final relevance pass failed | company=%s | title=%s | err=%s",
        company,
        title,
        last_err,
    )
    return RelevanceOut(
        is_relevant=False,
    )


def apply_final_relevance_pass(articles: list[dict], company: str) -> list[dict]:
    """Apply final macro-focused relevance pass to usable articles only."""
    if not articles:
        return articles

    for article in articles:
        if article.get("usable") is not True:
            continue

        title = str(article.get("title", ""))
        content = str(
            article.get("lede")
            or article.get("AI_summary")
            or article.get("newsstate", {}).get("summary")
            or ""
        )
        out = final_stock_relevance_pass(company, title, content)
        if out.is_relevant is not True:
            article["final_relevance_reason"] = "Final macro relevance reject"
            article["usable"] = False
        else:
            article["final_relevance_reason"] = "Final macro relevance pass"

    return articles

def select_labels_delta_floor(
    scores: dict[str, float],
    delta: float = settings.DELTA,
    floor: float = settings.FLOOR,
    max_labels: int = settings.MAX_LABELS,
) -> list[str]:
    if not scores:
        return []
    top1 = max(scores.values())
    selected = [k for k, v in scores.items() if (v >= top1 - delta) and (v >= floor)]
    selected = sorted(selected, key=lambda k: scores[k], reverse=True)
    if not selected:
        top_label = max(scores, key=scores.get)
        return [top_label]
    return selected[:max_labels]


def is_ambiguous(scores: dict[str, float], top1_floor: float = 0.55) -> bool:
    if not scores:
        return True
    top1 = max(scores.values())
    return top1 < top1_floor


def items_to_dict(items: list[ScoreItem]) -> dict[str, float]:
    return {it.name: float(it.score) for it in items}


def validate_exact_keys(scores: dict[str, float], expected: list[str], label: str):
    missing = set(expected) - set(scores)
    extra = set(scores) - set(expected)
    bad_range = [
        k for k, v in scores.items()
        if not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0)
    ]
    if missing or extra or bad_range:
        raise ValueError(
            f"{label} invalid. missing={missing}, extra={extra}, bad_range={bad_range}"
        )




_KOREAN_RELEVANCE_RULES = (
    "Additional rules for Korean-language news:\n"
    "- Keep only policy-level items such as 한국은행 decisions, 정부 예산/세제, 국회 입법, 대외정책 changes\n"
    "- Exclude routine corporate announcements, product releases, and non-policy local news\n"
)



_CLASSIFY_SYSTEM = """\
Read the following article title and content.
You are given asset class categories and their definitions.
Give each asset class category a relevance score between 0.0 and 1.0.
0.0 means the article is completely unrelated to that asset class.
1.0 means the article is fully about and centered around that asset class.
If the article is about an ETF, classify by the ETF's underlying exposure.

You are also given a set of regions.
Give each region a relevance score from 0.0 to 1.0, similar to asset class scoring.
Korea is an Emerging Market.
1.0 means the article is fully centered around the chosen region.

Also give a very short reasoning for your choices on asset class and region.
You MUST output scores for every provided asset class and every provided region.
Use EXACT keys as provided. Do not add or remove keys.
Scores must be numbers between 0.0 and 1.0.
Multiple asset classes can have 1.0 if the article is explicitly ABOUT those asset classes.
You do not HAVE to assign 1.0 to any asset class. Score low if unsure.
"""



_COVE_SYSTEM = """\
You are verifying an asset-class classification for a financial news article.
You MUST follow these rules:
- Use ONLY the provided asset class definitions.
- Correct keyword latching (e.g., 'credit' != always Fixed_Income; private credit is Alternatives).
- If the article is about an ETF, classify by the ETF's UNDERLYING exposure.
- Output corrected_scores containing EVERY provided asset class key exactly once.
- Scores must be numbers between 0.0 and 1.0.
- final_asset_class MUST be one of the provided keys.
- Keep verify_reason to ONE short sentence.
"""



def summarize_lede_only(company: str, title: str, lede: str) -> str:
    """제목과 lede만 있는 기사를 위한 전용 1문장 요약 생성.

    입력:
    - company: 대상 회사명
    - title: 기사 제목
    - lede: 기사 lede 텍스트

    출력:
    - 요약 문자열 (실패 시 lede 원문 반환)
    """
    system = SystemMessage(
        content=(
            "You are writing a macro briefing.\n"
            "Article body extraction has failed; only the title and lede are available.\n"
            "Write exactly ONE sentence that summarizes the macroeconomic or political significance.\n"
            "Include what changed, who decided it, and expected market/economic impact if explicitly present.\n"
            "Use only information in the title and lede. No speculation."
        )
    )
    user = HumanMessage(
        content=(
            f"Target region/topic: {company}\n\n"
            f"Article title: {title}\n\n"
            f"Lede: {lede}"
        )
    )

    last_err: str | None = None
    for attempt in range(3):
        try:
            out: SummaryOut = summarize_llm.invoke([system, user])
            logging.info("lede-only summarization completed | title=%s", title)
            return out.summary
        except (ValidationError, ValueError) as e:
            if settings.GUARDRAILS:
                logging.debug("lede-only summarize error: %s", e)
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.3 * (attempt + 1))
            else:
                raise

    logging.warning(
        "lede-only summarize failed | company=%s | title=%s | err=%s",
        company, title, last_err,
    )
    return lede


def relevance_gate(state: NewsState) -> NewsState:
    company = state["company"]
    title = state["title"]
    content = state["content"]
    logging.info("starting relevance gate on: %s", title)

    base_prompt = (
        "You are a strict classifier for macro and policy relevance.\n"
        "Question: Is this article about macroeconomic policy, fiscal policy, monetary policy,\n"
        "or political events in the given region/topic?\n"
        "Focus on government decisions, central bank actions, and geopolitical developments.\n"
        "Mark NOT relevant for company earnings, product launches, individual stock moves,\n"
        "analyst ratings, routine corporate announcements, or stories where the region/topic is incidental.\n"
        "Mark relevant only when the article's core is policy/politics with potential market/economic impact.\n"
    )

    if state.get("is_korean"):
        base_prompt += _KOREAN_RELEVANCE_RULES

    system = SystemMessage(content=base_prompt)
    user = HumanMessage(
        content=f"""
        Target region/topic:
        {company}

        Article title:
        {title}

        Article content:
        {content}
        """.strip()
    )

    last_err: str | None = None

    for attempt in range(3):
        try:
            out: RelevanceOut = relevance_llm.invoke([system, user])
            logging.info(
                "relevance gate completed | title=%s | result=%s",
                title,
                out.is_relevant,
            )
            return {
                "is_relevant": out.is_relevant,
            }

        except (ValidationError, ValueError) as e:
            if settings.GUARDRAILS:
                logging.debug("Validation/ValueError error: %s", e)
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.3 * (attempt + 1))
            else:
                raise

    logging.debug("all loops failed relevance gate for: %s", title)
    return {
        "is_relevant": False,
        "error_relevance": last_err,
    }


def summarize(state: NewsState) -> NewsState:
    company = state["company"]
    title = state["title"]
    content = state["content"]
    logging.info("starting summarization gate on: %s", title)

    system = SystemMessage(content=(
        "You are writing a macro policy briefing.\n"
        "Summarize the macroeconomic or political significance of this event in 2-3 sentences.\n"
        "Include: (1) what changed, (2) who decided it, and (3) expected market/economic impact.\n"
        "Exclude company-level earnings/product/stock commentary unless directly tied to policy impact."
    ))
    user = HumanMessage(
        content=(
            f"Target region/topic: {company}\n\n"
            f"Article title: {title}\n\n"
            f"Article content:\n{content}"
        )
    )

    last_err: str | None = None

    for attempt in range(3):
        try:
            out: SummaryOut = summarize_llm.invoke([system, user])
            logging.info("summarization completed | title=%s", title)
            return {"summary": out.summary}

        except (ValidationError, ValueError) as e:
            if settings.GUARDRAILS:
                logging.debug("Validation/ValueError error: %s", e)
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.3 * (attempt + 1))
            else:
                raise

    logging.debug("all loops failed summary gate for: %s", title)
    return {
        "summary": "SUMMARY ERROR",
        "error_summary": last_err,
    }


def classify_asset_proposal(state: NewsState) -> NewsState:
    title = state["title"]
    content = state["content"]
    logging.info("starting classification gate on: %s", title)

    asset_def_text = "\n".join([f"- {k}: {v}" for k, v in ASSET_CLASSES.items()])
    region_text = "\n".join([f"- {r}" for r in REGIONS])

    system = SystemMessage(content=_CLASSIFY_SYSTEM)
    user = HumanMessage(
        content=(
            f"Article title: {title}\n\n"
            f"Article content:\n{content}\n\n"
            f"Asset class keys and definitions (use EXACT keys):\n{asset_def_text}\n\n"
            f"Region keys (use EXACT keys):\n{region_text}"
        )
    )

    last_err: str | None = None

    for attempt in range(3):
        try:
            out: ClassifyProposalOut = classify_proposal_llm.invoke([system, user])
            asset_scores = items_to_dict(out.asset_class_scores)
            region_scores = items_to_dict(out.region_scores)

            validate_exact_keys(asset_scores, list(ASSET_CLASSES.keys()), "asset_class_scores")
            validate_exact_keys(region_scores, REGIONS, "region_scores")

            asset_labels = select_labels_delta_floor(
                asset_scores,
                delta=settings.DELTA,
                floor=settings.FLOOR,
                max_labels=settings.MAX_LABELS,
            )
            needs_cove = is_ambiguous(asset_scores, top1_floor=0.55) or (len(asset_labels) > 1)

            logging.info(
                "classification completed | title=%s | scores=%s | needs_cove=%s",
                title,
                asset_scores,
                needs_cove,
            )
            return {
                "asset_class_scores": asset_scores,
                "asset_class_reasoning": out.asset_class_reasoning,
                "asset_class_labels": asset_labels,
                "asset_class_final": asset_labels,
                "needs_cove": needs_cove,
                "cove_ran": False,
                "region_scores": region_scores,
                "region_reasoning": out.region_reasoning,
            }

        except (ValidationError, ValueError) as e:
            if settings.GUARDRAILS:
                logging.debug("Validation/ValueError error: %s", e)
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.3 * (attempt + 1))
            else:
                raise

    logging.debug("all loops failed classification gate for: %s", title)
    return {
        "asset_class_scores": {},
        "region_scores": {},
        "asset_class_final": [],
        "needs_cove": False,
        "error_proposal": last_err or "unknown",
        "cove_ran": False,
    }


def classify_asset_cove(state: NewsState) -> NewsState:
    title = state["title"]
    content = state["content"]
    logging.info("entering CoVE for: %s", title)

    proposal_scores = state.get("asset_class_scores", {})
    proposal_labels = state.get("asset_class_labels", [])
    proposal_reason = state.get("asset_class_reasoning", "")

    asset_def_text = "\n".join([f"- {k}: {v}" for k, v in ASSET_CLASSES.items()])
    asset_keys = list(ASSET_CLASSES.keys())

    system = SystemMessage(content=_COVE_SYSTEM)
    user = HumanMessage(
        content=(
            f"Article title: {title}\n\n"
            f"Article content:\n{content}\n\n"
            f"Asset class definitions (use EXACT keys):\n{asset_def_text}\n\n"
            f"Proposal from earlier step:\n"
            f"- proposed_labels: {proposal_labels}\n"
            f"- proposed_scores: {proposal_scores}\n"
            f"- proposed_reason: {proposal_reason}\n\n"
            "Check each failure pattern, correct if needed, then output corrected_scores and final_asset_class."
        )
    )

    last_err: str | None = None

    for attempt in range(3):
        try:
            out: CoVEOut = cove_llm.invoke([system, user])

            corrected = items_to_dict(out.corrected_scores)
            validate_exact_keys(corrected, asset_keys, "corrected_scores")

            if out.final_asset_class not in asset_keys:
                raise ValueError(f"final_asset_class not in asset keys: {out.final_asset_class}")

            corrected_labels = select_labels_delta_floor(
                corrected,
                delta=0.15,
                floor=0.45,
                max_labels=3,
            )
            logging.info(
                "CoVE completed | title=%s | corrected_labels=%s",
                title,
                corrected_labels,
            )
            return {
                "asset_class_scores": corrected,
                "asset_class_labels": corrected_labels,
                "asset_class_final": [out.final_asset_class],
                "asset_class_verify_reason": out.verify_reason,
                "needs_cove": False,
                "cove_ran": True,
            }

        except (ValidationError, ValueError, KeyError, TypeError) as e:
            if settings.GUARDRAILS:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.3 * (attempt + 1))
            else:
                raise

    fallback = max(proposal_scores, key=proposal_scores.get) if proposal_scores else ""
    logging.debug("all loops failed CoVE gate for: %s", title)
    return {
        "asset_class_final": [fallback] if fallback else [],
        "asset_class_verify_reason": "CoVe verification failed; using proposal result.",
        "error_cove": last_err or "unknown",
        "needs_cove": False,
        "cove_ran": True,
    }



def route_after_relevance(state: NewsState) -> str:
    return "keep" if state.get("is_relevant") else "discard"


def fork_keep(state: NewsState) -> NewsState:
    return {}


def route_after_fork_keep(state: NewsState) -> str:
    if not state.get("classify_asset_class", True):
        if state.get("is_google_rss", False) or state.get("is_korean", False):
            return "with_summary"
        return "done"
    if state.get("is_google_rss", False) or state.get("is_korean", False):
        return "with_summary"
    return "no_summary"


def route_after_summarize(state: NewsState) -> str:
    return "proposal" if state.get("classify_asset_class", True) else "done"


def route_after_proposal(state: NewsState) -> str:
    return "cove" if state.get("needs_cove") else "done"



builder = StateGraph(NewsState)
builder.add_node("relevance", relevance_gate)
builder.add_node("fork_keep", fork_keep)
builder.add_node("proposal", classify_asset_proposal)
builder.add_node("cove", classify_asset_cove)
builder.add_node("summarize", summarize)

builder.set_entry_point("relevance")

builder.add_conditional_edges(
    "relevance",
    route_after_relevance,
    {"keep": "fork_keep", "discard": END},
)

builder.add_conditional_edges(
    "fork_keep",
    route_after_fork_keep,
    {
        "with_summary": "summarize",
        "no_summary": "proposal",
        "done": END,
    },
)

builder.add_conditional_edges(
    "summarize",
    route_after_summarize,
    {
        "proposal": "proposal",
        "done": END,
    },
)

builder.add_conditional_edges(
    "proposal",
    route_after_proposal,
    {"cove": "cove", "done": END},
)

builder.add_edge("cove", END)

graph = builder.compile()



def run_news(
    company: str,
    title: str,
    content: str,
    is_google_rss: bool = False,
    is_korean: bool = False,
    classify_asset_class: bool = True,
) -> NewsState:
    init_state: NewsState = {
        "company": company,
        "title": title,
        "content": content,
        "is_google_rss": is_google_rss,
        "is_korean": is_korean,
        "classify_asset_class": classify_asset_class,
    }
    return graph.invoke(init_state)


def get_final_asset_classes(state: NewsState) -> list[str]:
    """최종 asset class 라벨 목록을 반환한다.

    입력:
    - state: 분류 결과가 포함된 NewsState

    출력:
    - CoVE 실행 전에는 다중 라벨 목록, 실행 후에는 단일 라벨 목록

    동작:
    - cove_ran 값을 기준으로 반환 라벨 수를 정한다.
    """
    finals = state.get("asset_class_final", [])
    if not finals:
        return []
    if state.get("cove_ran"):
        return finals[:1]
    return finals



class DedupGroup(BaseModel):
    """동일 이슈를 다루는 중복 기사 인덱스 그룹을 표현한다."""
    indices: list[int] = Field(description="0-based indices of articles that cover the same story")

class DedupOut(BaseModel):
    """중복 그룹 목록을 표현한다."""
    groups: list[DedupGroup] = Field(default_factory=list)

_dedup_llm = ChatOpenAI(
    model=settings.DEDUP_MODEL,
    temperature=0,
).with_structured_output(DedupOut)

def deduplicate_articles(articles: list[dict]) -> list[dict]:
    """LLM 결과를 이용해 동일 이슈 중복 기사를 제거한다.

    입력:
    - articles: 기사 dict 목록

    출력:
    - 그룹별 첫 기사만 유지한 기사 목록

    동작:
    - usable 기사 제목 목록을 LLM에 전달하고, 각 중복 그룹의 후속 인덱스를 제거한다.
    """
    if len(articles) <= 1:
        return articles

    usable = [(i, a) for i, a in enumerate(articles) if a.get("usable")]
    if len(usable) <= 1:
        return articles

    numbered = "\n".join(
        f"[{idx}] {a.get('title', '')}"
        for idx, a in usable
    )
    system = SystemMessage(
        content=(
            "You identify groups of news articles that cover the same story. "
            "Return groups of indices for duplicate articles. "
            "Indices are 0-based and must exactly match the bracketed IDs I provide "
            "(for example, [0], [1], [2]). "
            "Do not create or use any indices that are not present in the list. "
            "Articles not in any group are considered unique."
        )
    )
    user = HumanMessage(
        content=(
            "Find groups of articles that cover the same story. "
            "Use only the 0-based indices shown in brackets for each article.\n"
            f"{numbered}"
        )
    )

    try:
        out: DedupOut = _dedup_llm.invoke([system, user])
    except Exception as e:
        logging.warning("Dedup LLM failed, skipping: %s", e)
        return articles

    allowed_indices = {idx for idx, _ in usable}
    remove = set()
    for group in out.groups:
        sorted_indices = sorted(group.indices)
        for idx in sorted_indices[1:]:
            if idx in allowed_indices:
                remove.add(idx)
            else:
                logging.warning(
                    "Dedup LLM returned invalid index %s; allowed: %s",
                    idx,
                    sorted(allowed_indices),
                )

    return [a for i, a in enumerate(articles) if i not in remove]
