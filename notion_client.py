"""
notion_client.py — Push run results to Notion via REST API.
"""

import os
import logging
import time
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

NOTION_BASE_URL = "https://api.notion.com/v1"
MOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_RUNS_DB_ID = os.environ["NOTION_RUNS_DB_ID"]
NOTION_DETAILS_DB_ID = os.environ["NOTION_DETAILS_DB_ID"]

SCORE_MAX = 540

def _headers():
    token = os.getenv("NOTION_TOKEN", NOTION_TOKEN)
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

def _rich_text(value: str) -> list:
    return [{"type": "text", "text": {"content": str(value)}}]

def _create_page(database_id: str, properties: dict) -> dict:
    url = f"{NOTION_BASE_URL}/pages"
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, headers=_headers(), json=payload, timeout=60)
            if not resp.ok:
                print(f"Notion error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()
        except (requests.ReadTimeout, requests.ConnectionError) as exc:
            if attempt == 3:
                raise
            log.warning("Notion request failed (attempt %d/3): %s — retrying in 5s", attempt, exc)
            time.sleep(5)

def push_run(run_data: dict, details: list) -> None:
    model = run_data.get("model", "claude-haiku-4-5")  # NOUVEAU

    # MODIFIÉ — ajout du modèle dans le titre pour distinguer les entrées
    semaine_title = f"{run_data['iso_week']} — Run {run_data['run_number']} — {model}"

    # --- Runs page ---
    run_props = {
        "Semaine": {"title": _rich_text(semaine_title)},
        "Date": {"date": {"start": str(run_data["date"])}},
        "ISO Week": {"rich_text": _rich_text(run_data["iso_week"])},
        "Run Number": {"number": run_data["run_number"]},
        "Modèle": {"select": {"name": model}},  # NOUVEAU
        "Score Global": {"number": run_data["score_global"]},
        "Score Max": {"number": SCORE_MAX},
        "Citation Rate %": {"number": run_data["citation_rate"]},
        "Score Cat1": {"number": run_data["score_cat1"]},
        "Score Cat2": {"number": run_data["score_cat2"]},
        "Score Cat3": {"number": run_data["score_cat3"]},
        "Score Cat4": {"number": run_data["score_cat4"]},
        "Score Cat5": {"number": run_data["score_cat5"]},
        "Score Cat6": {"number": run_data["score_cat6"]},
        "Brand Cited": {"checkbox": bool(run_data.get("brand_cited", False))},
        "Brand Position Avg": {"number": run_data.get("brand_position_avg")},
        "Avg Rank": {"number": run_data.get("avg_rank")},
    }

    run_page = _create_page(NOTION_RUNS_DB_ID, run_props)
    log.info("Notion run page created: %s", run_page.get("id"))

    # --- Détails pages ---
    errors = 0
    for detail in details:
        position = detail.get("position")
        preview = (detail.get("response_preview") or "")[:2000]

        detail_props = {
            "Prompt": {"title": _rich_text(detail["prompt_id"])},
            "Semaine": {"rich_text": _rich_text(run_data["iso_week"])},
            "Run Number": {"number": run_data["run_number"]},
            "Modèle": {"select": {"name": detail.get("model", model)}},  # NOUVEAU
            "Catégorie": {"select": {"name": detail["category"]}},
            "Cité": {"checkbox": bool(detail["cited"])},
            "Position": {"number": position},
            "Score": {"number": detail["score"]},
            "Response Preview": {"rich_text": _rich_text(preview)},
            "Réponse": {"rich_text": _rich_text(detail.get("response_text", ""))},
        }

        try:
            page = _create_page(NOTION_DETAILS_DB_ID, detail_props)
            log.debug("Notion detail page created: %s", page.get("id"))
        except requests.HTTPError as exc:
            log.error("Notion detail push failed for %s: %s", detail["prompt_id"], exc)
            errors += 1

    if errors:
        log.warning("%d detail(s) failed to push to Notion.", errors)
    else:
        log.info("All %d detail pages pushed to Notion.", len(details))
