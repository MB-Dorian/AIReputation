"""
generate_dashboard.py — Fetch Notion Runs DB and generate docs/data.json.

Env vars:
  NOTION_TOKEN       : Notion integration token
  NOTION_RUNS_DB_ID  : ID of the Runs database
"""

import os
import json
import requests
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_RUNS_DB_ID = os.environ["NOTION_RUNS_DB_ID"]
NOTION_BASE_URL = "https://api.notion.com/v1"
SCORE_MAX = 540

MODEL_MAP = {
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "sonar": "sonar",
}


def _headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def fetch_all_runs() -> list[dict]:
    """Fetch every page from the Runs database (handles Notion pagination)."""
    pages = []
    url = f"{NOTION_BASE_URL}/databases/{NOTION_RUNS_DB_ID}/query"
    cursor = None

    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor

        resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["results"])

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return pages


def _rich_text_value(props: dict, key: str) -> str | None:
    items = props.get(key, {}).get("rich_text", [])
    return items[0]["text"]["content"] if items else None


def _number(props: dict, key: str) -> float | None:
    return props.get(key, {}).get("number")


def _select(props: dict, key: str) -> str | None:
    sel = props.get(key, {}).get("select")
    return sel["name"] if sel else None


def parse_page(page: dict) -> dict:
    props = page["properties"]
    modele_raw = _select(props, "Modèle")
    return {
        "iso_week": _rich_text_value(props, "ISO Week"),
        "model": MODEL_MAP.get(modele_raw, modele_raw) if modele_raw else None,
        "score": _number(props, "Score Global"),
        "citation_rate": _number(props, "Citation Rate %"),
        "brand_position": _number(props, "Brand Position Avg"),
        "cat1": _number(props, "Score Cat1"),
        "cat2": _number(props, "Score Cat2"),
        "cat3": _number(props, "Score Cat3"),
        "cat4": _number(props, "Score Cat4"),
        "cat5": _number(props, "Score Cat5"),
    }


def _avg(values: list) -> float:
    """Average of non-None values; returns 0 if all are None."""
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0


def _avg_or_none(values: list) -> float | None:
    """Average of non-None values; returns None if all are None."""
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def build_data_json(runs: list[dict]) -> dict:
    # Group by (iso_week, model)
    grouped: dict[tuple, list] = defaultdict(list)
    for r in runs:
        if r["iso_week"] and r["model"]:
            grouped[(r["iso_week"], r["model"])].append(r)

    all_weeks = sorted({w for w, _ in grouped})
    all_models = sorted({m for _, m in grouped})

    # Build weekly_scores per model
    weekly_scores: dict[str, list] = {}
    for model in all_models:
        model_weeks = []
        for week in all_weeks:
            bucket = grouped.get((week, model))
            if not bucket:
                continue
            model_weeks.append({
                "week": week,
                "score": _avg([r["score"] for r in bucket]),
                "max": SCORE_MAX,
                "citation_rate": _avg([r["citation_rate"] for r in bucket]),
                "brand_position": _avg_or_none([r["brand_position"] for r in bucket]),
                "cat1": _avg([r["cat1"] for r in bucket]),
                "cat2": _avg([r["cat2"] for r in bucket]),
                "cat3": _avg([r["cat3"] for r in bucket]),
                "cat4": _avg([r["cat4"] for r in bucket]),
                "cat5": _avg([r["cat5"] for r in bucket]),
            })
        weekly_scores[model] = model_weeks

    current_week = all_weeks[-1] if all_weeks else ""
    prev_week = all_weeks[-2] if len(all_weeks) >= 2 else None

    by_model: dict[str, dict] = {}
    for model in all_models:
        weeks = weekly_scores.get(model, [])
        curr = weeks[-1] if weeks else None
        prev = next((w for w in weeks if w["week"] == prev_week), None)
        if curr:
            delta = round(curr["score"] - prev["score"], 1) if prev else 0
            by_model[model] = {
                "score": curr["score"],
                "delta": delta,
                "citation_rate": curr["citation_rate"],
                "brand_position": curr["brand_position"],
            }

    return {
        "updated_at": current_week,
        "models": all_models,
        "weekly_scores": weekly_scores,
        "last_run": {
            "week": current_week,
            "by_model": by_model,
        },
    }


def main():
    os.makedirs("docs", exist_ok=True)

    print("Fetching runs from Notion…")
    pages = fetch_all_runs()
    print(f"  → {len(pages)} pages fetched")

    runs = [parse_page(p) for p in pages]
    runs = [r for r in runs if r["iso_week"] and r["model"]]
    print(f"  → {len(runs)} valid runs (with ISO Week + Modèle)")

    data = build_data_json(runs)

    out_path = os.path.join("docs", "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  → {out_path} written ({len(data['models'])} models, {len(data.get('weekly_scores', {}).get(data['models'][0], []) if data['models'] else [])} weeks)")


if __name__ == "__main__":
    main()
