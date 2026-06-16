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
NOTION_DETAILS_DB_ID = os.environ["NOTION_DETAILS_DB_ID"]
NOTION_BASE_URL = "https://api.notion.com/v1"
SCORE_MAX = 540

PROMPTS_FILE = os.path.join(os.path.dirname(__file__), "prompts.json")

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


def _title_value(props: dict, key: str) -> str | None:
    items = props.get(key, {}).get("title", [])
    return items[0]["text"]["content"] if items else None


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


def fetch_details(week: str) -> list[dict]:
    """Fetch detail pages for the given ISO week (server-side filter)."""
    pages = []
    url = f"{NOTION_BASE_URL}/databases/{NOTION_DETAILS_DB_ID}/query"
    cursor = None

    while True:
        payload: dict = {
            "page_size": 100,
            "filter": {"property": "Semaine", "rich_text": {"equals": week}},
        }
        if cursor:
            payload["start_cursor"] = cursor

        resp = requests.post(url, headers=_headers(), json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["results"])

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return pages


def parse_detail(page: dict) -> dict:
    props = page["properties"]
    modele_raw = _select(props, "Modèle")
    return {
        "prompt_id": _title_value(props, "Prompt"),
        "run_number": _number(props, "Run Number"),
        "model": MODEL_MAP.get(modele_raw, modele_raw) if modele_raw else None,
        "category": _select(props, "Catégorie"),
        "cited": props.get("Cité", {}).get("checkbox", False),
        "position": _number(props, "Position"),
        "score": _number(props, "Score"),
    }


def load_prompts() -> dict[str, dict]:
    """Return prompt_id → {category_num, text} from prompts.json."""
    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {
        p["id"]: {"category_num": p["category_num"], "text": p["variants"][0]}
        for p in raw
    }


def build_runs_detail(detail_pages: list[dict], prompts_map: dict) -> dict:
    """
    For each (model, prompt_id) keep the row with the highest run_number,
    then annotate with prompt text. Returns dict keyed by model.
    """
    best: dict[tuple, dict] = {}
    for d in detail_pages:
        if not d["model"] or not d["prompt_id"]:
            continue
        key = (d["model"], d["prompt_id"])
        if key not in best or (d["run_number"] or 0) > (best[key]["run_number"] or 0):
            best[key] = d

    by_model: dict[str, list] = defaultdict(list)
    for (model, pid), d in best.items():
        info = prompts_map.get(pid, {})
        by_model[model].append({
            "prompt_id": pid,
            "category": info.get("category_num", 0),
            "prompt_text": info.get("text", ""),
            "cited": d["cited"],
            "position": d["position"],
            "score": int(d["score"]) if d["score"] is not None else 0,
        })

    for rows in by_model.values():
        rows.sort(key=lambda r: (r["category"], r["prompt_id"]))

    return dict(by_model)


def main():
    os.makedirs("docs", exist_ok=True)

    print("Fetching runs from Notion…")
    pages = fetch_all_runs()
    print(f"  → {len(pages)} pages fetched")

    runs = [parse_page(p) for p in pages]
    runs = [r for r in runs if r["iso_week"] and r["model"]]
    print(f"  → {len(runs)} valid runs (with ISO Week + Modèle)")

    data = build_data_json(runs)
    current_week = data["updated_at"]

    print(f"Fetching details for {current_week} from Notion…")
    detail_pages_raw = fetch_details(current_week) if current_week else []
    print(f"  → {len(detail_pages_raw)} detail pages fetched")
    detail_pages = [parse_detail(p) for p in detail_pages_raw]

    prompts_map = load_prompts()
    data["runs_detail"] = build_runs_detail(detail_pages, prompts_map)

    out_path = os.path.join("docs", "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    n_weeks = len(data.get("weekly_scores", {}).get(data["models"][0], [])) if data["models"] else 0
    print(f"  → {out_path} written ({len(data['models'])} models, {n_weeks} weeks, {len(detail_pages)} detail rows)")


if __name__ == "__main__":
    main()
