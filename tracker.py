"""
tracker.py — Logique principale du reputation tracker.

Variables d'environnement attendues :
  ANTHROPIC_API_KEY  : clé Anthropic
  PERPLEXITY_API_KEY : clé Perplexity  # NOUVEAU
  DATABASE_URL       : URL PostgreSQL
  RUN_NUMBER         : 1, 2 ou 3 (détermine la variante de prompt)
  MODEL_NAME         : "claude" ou "perplexity" (défaut: "claude")  # NOUVEAU
"""

import os
import json
import logging
import requests  # NOUVEAU
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
PERPLEXITY_MODEL = "sonar"  # NOUVEAU

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

# NOUVEAU — Appel Perplexity Sonar
def send_prompt_perplexity(text: str) -> str:
    """Envoie un prompt à Perplexity Sonar et retourne le texte de la réponse."""
    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        raise ValueError("PERPLEXITY_API_KEY manquante")
    
    response = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": PERPLEXITY_MODEL,
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 1024,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

def load_prompts() -> list[dict]:
    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def compute_aggregates(details: list[dict]) -> dict:
    organic = [d for d in details if d["category_num"] != 6]
    score_global = sum(d["score"] for d in organic)
    score_by_cat = {i: 0 for i in range(1, 7)}
    for d in details:
        score_by_cat[d["category_num"]] += d["score"]
    cited_organic = [d for d in organic if d["cited"]]
    citation_rate = (len(cited_organic) / len(organic)) * 100 if organic else 0.0
    positions = [d["position"] for d in cited_organic if d["position"] is not None]
    avg_rank = (sum(positions) / len(positions)) if positions else None
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
    variant_idx = run_number - 1

    # NOUVEAU — sélection du modèle via env var
    model_name = os.environ.get("MODEL_NAME", "claude").lower()
    if model_name == "perplexity":
        send_fn = send_prompt_perplexity
        model_label = "perplexity-sonar"
    else:
        send_fn = send_prompt
        model_label = "claude-haiku-4-5"

    log.info("=== Reputation Tracker — Run %d | Model: %s ===", run_number, model_label)

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
        variant_text = prompt_def["variants"][variant_idx % len(prompt_def["variants"])]
        log.info("[%s] Sending prompt (cat %d): %s", pid, category_num, variant_text[:80])

        try:
            response_text = send_fn(variant_text)  # MODIFIÉ — dispatch dynamique
        except Exception as exc:
            log.error("[%s] API error: %s", pid, exc)
            response_text = ""

        parsed = parse_response(response_text)
        parsed["prompt_id"] = pid
        parsed["category"] = category
        parsed["category_num"] = category_num
        parsed["model"] = model_label  # NOUVEAU
        parsed["response_text"] = response_text[:1900] if response_text else ""
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

    session = get_session()
    try:
        run_obj = Run(
            date=today,
            iso_week=week,
            run_number=run_number,
            model=model_label,  # NOUVEAU — nécessite migration DB
            **aggregates,
        )
        session.add(run_obj)
        session.flush()
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
                model=d["model"],  # NOUVEAU — nécessite migration DB
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
        "model": model_label,  # NOUVEAU
        **aggregates,
    }
    notion_client.push_run(run_data, details_data)

if __name__ == "__main__":
    run()
