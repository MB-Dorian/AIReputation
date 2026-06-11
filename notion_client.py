"""
notion_client.py — Push run results to Notion via REST API.
"""

import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_RUNS_DB_ID = os.getenv("NOTION_RUNS_DB_ID", "34b6e7a6-7b47-81c9-84d7-cd5c064ced21")
NOTION_DETAILS_DB_ID = os.getenv("NOTION_DETAILS_DB_ID", "34b6e7a6-7b47-814c-b5a9-e73c94449737")

SCORE_MAX = 540

_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def _headers():
    """Return headers with the current token (re-reads env in case it changed)."""
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
    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def push_run(run_data: dict, details: list) -> None:
    """
    Push a completed run to Notion.

    run_data keys: date (str ISO), iso_week, run_number, score_global,
                   citation_rate, avg_rank, score_cat1..score_cat6
    details keys:  prompt_id, category, cited, position, score, response_preview
    """
    semaine_title = f"{run_data['iso_week']} \u2014 Run {run_data['run_number']}"

    # --- Runs page ---
    run_props = {
        "Semaine": {"title": _rich_text(semaine_title)},
        "Date": {"date": {"start": str(run_data["date"])}},
        "ISO Week": {"rich_text": _rich_text(run_data["iso_week"])},
        "Run Number": {"number": run_data["run_number"]},
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
    }

    avg_rank = run_data.get("avg_rank")
    run_props["Avg Rank"] = {"number": avg_rank}  # None is valid (null in Notion)

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
            "Catégorie": {"select": {"name": detail["category"]}},
            "Cité": {"checkbox": bool(detail["cited"])},
            "Position": {"number": position},
            "Score": {"number": detail["score"]},
            "Response Preview": {"rich_text": _rich_text(preview)},
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
