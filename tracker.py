"""
tracker.py — Logique principale du reputation tracker.

Variables d'environnement attendues :
  ANTHROPIC_API_KEY  : clé Anthropic
  DATABASE_URL       : URL PostgreSQL
  RUN_NUMBER         : 1, 2 ou 3 (détermine la variante de prompt)
"""

import os
import json
import logging
from datetime import date, datetime

from dotenv import load_dotenv
from anthropic import Anthropic

from database import init_db, get_session, Run, Detail
from parser import parse_response
import notion_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

PROMPTS_FILE = os.path.join(os.path.dirname(__file__), "prompts.json")
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Score max par catégorie (nombre de prompts × 27)
# Cat1=4, Cat2=3, Cat3=4, Cat4=4, Cat5=5, Cat6=2 → total 22 prompts
CATEGORY_PROMPT_COUNTS = {1: 4, 2: 3, 3: 4, 4: 4, 5: 5, 6: 2}
MAX_SCORE_PER_CAT = {cat: count * 27 for cat, count in CATEGORY_PROMPT_COUNTS.items()}

client = Anthropic()


def iso_week(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def send_prompt(text: str) -> str:
    """Envoie un prompt à Claude Haiku et retourne le texte de la réponse."""
    msg = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": text}],
    )
    return msg.content[0].text


def load_prompts() -> list[dict]:
    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_aggregates(details: list[dict]) -> dict:
    """Calcule les agrégats à partir des résultats de parsing."""
    # Score global = cat 1-5 uniquement (cat 6 = brand awareness, hors scoring)
    organic = [d for d in details if d["category_num"] != 6]
    score_global = sum(d["score"] for d in organic)

    score_by_cat = {i: 0 for i in range(1, 7)}
    for d in details:
        score_by_cat[d["category_num"]] += d["score"]

    cited_organic = [d for d in organic if d["cited"]]
    citation_rate = (len(cited_organic) / len(organic)) * 100 if organic else 0.0

    positions = [d["position"] for d in cited_organic if d["position"] is not None]
    avg_rank = (sum(positions) / len(positions)) if positions else None

    # Brand awareness (cat 6)
    cat6 = [d for d in details if d["category_num"] == 6]
    brand_cited = any(d["cited"] for d in cat6)
    cat6_positions = [d["position"] for d in cat6 if d["cited"] and d["position"] is not None]
    brand_position_avg = (sum(cat6_positions) / len(cat6_positions)) if cat6_positions else None

    return {
        "score_global": score_global,
        "score_cat1": score_by_cat[1],
        "score_cat2": score_by_cat[2],
        "score_cat3": score_by_cat[3],
        "score_cat4": score_by_cat[4],
        "score_cat5": score_by_cat[5],
        "score_cat6": score_by_cat[6],
        "citation_rate": round(citation_rate, 1),
        "avg_rank": round(avg_rank, 2) if avg_rank is not None else None,
        "brand_cited": brand_cited,
        "brand_position_avg": round(brand_position_avg, 2) if brand_position_avg is not None else None,
    }


def run():
    run_number = int(os.environ.get("RUN_NUMBER", "1"))
    variant_idx = run_number - 1  # 0-based index into variants list

    log.info("=== Reputation Tracker — Run %d ===", run_number)

    init_db()
    prompts = load_prompts()
    today = date.today()
    week = iso_week(today)

    log.info("Date: %s | Week: %s | Variant: %d", today, week, run_number)

    details_data = []

    for prompt_def in prompts:
        pid = prompt_def["id"]
        category = prompt_def["category"]
        category_num = prompt_def["category_num"]
        variant_text = prompt_def["variants"][variant_idx]

        log.info("[%s] Sending prompt (cat %d): %s", pid, category_num, variant_text[:80])

        try:
            response_text = send_prompt(variant_text)
        except Exception as exc:
            log.error("[%s] API error: %s", pid, exc)
            response_text = ""

        parsed = parse_response(response_text)
        parsed["prompt_id"] = pid
        parsed["category"] = category
        parsed["category_num"] = category_num

        details_data.append(parsed)

        log.info(
            "[%s] cited=%s | position=%s | score=%d",
            pid, parsed["cited"], parsed["position"], parsed["score"],
        )

    aggregates = compute_aggregates(details_data)
    log.info(
        "Score global: %d | Citation rate: %.1f%% | Avg rank: %s",
        aggregates["score_global"],
        aggregates["citation_rate"],
        aggregates["avg_rank"],
    )
    brand_label = "oui" if aggregates["brand_cited"] else "non"
    pos_label = aggregates["brand_position_avg"] if aggregates["brand_position_avg"] is not None else "N/A"
    log.info(
        "Score organique: %d/540 | Brand awareness: %s (pos moyenne: %s)",
        aggregates["score_global"],
        brand_label,
        pos_label,
    )

    session = get_session()
    try:
        run_obj = Run(
            date=today,
            iso_week=week,
            run_number=run_number,
            **aggregates,
        )
        session.add(run_obj)
        session.flush()  # get run_obj.id

        for d in details_data:
            detail = Detail(
                run_id=run_obj.id,
                prompt_id=d["prompt_id"],
                category=d["category"],
                category_num=d["category_num"],
                cited=d["cited"],
                position=d["position"],
                score=d["score"],
                response_preview=d["response_preview"],
            )
            session.add(detail)

        session.commit()
        log.info("Data committed to DB. Run id=%d", run_obj.id)
    except Exception as exc:
        session.rollback()
        log.error("DB error: %s", exc)
        raise
    finally:
        session.close()

    run_data = {
        "date": today.isoformat(),
        "iso_week": week,
        "run_number": run_number,
        **aggregates,
    }
    notion_client.push_run(run_data, details_data)


if __name__ == "__main__":
    run()
