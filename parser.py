"""
parser.py — Détecte et score la présence d'Anaba dans une réponse Claude.

Stratégie à deux niveaux :
  1. Regex rapide : cherche "Anaba" dans le texte et tente d'extraire la position.
  2. Juge Haiku  : si la regex ne conclut pas (brand dans une phrase ambiguë,
                   liste non numérotée…), on demande à Claude Haiku de trancher.
"""

import os
import re
import json
from anthropic import Anthropic

BRAND = "Anaba"

# Scores par position
SCORE_TABLE = {1: 27, 2: 18, 3: 9}
SCORE_NOT_CITED = 0

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _find_brand_position_regex(text: str) -> tuple[bool, int | None]:
    """
    Tente de trouver la position ordinale d'Anaba dans une liste.
    Retourne (cited: bool, position: int|None).
    Position = None si citée mais pas en position déterminable par regex.
    """
    text = _normalize(text)
    brand_pattern = re.compile(r'\bAnaba\b', re.IGNORECASE)

    if not brand_pattern.search(text):
        return False, None

    # Cherche des listes numérotées : "1. Anaba", "1) Anaba", "**1.**", etc.
    # On collecte tous les items numérotés et on cherche Anaba parmi eux.
    numbered = re.findall(
        r'(?:^|\n)\s*(\d+)[.)]\s*\*{0,2}([^\n]{0,120})',
        text
    )
    for num_str, content in numbered:
        if brand_pattern.search(content):
            pos = int(num_str)
            return True, pos if pos <= 10 else None

    # Listes à puces / tirets : on compte l'ordre des items
    bullets = re.findall(
        r'(?:^|\n)\s*[-•*]\s+([^\n]{1,120})',
        text
    )
    for idx, item in enumerate(bullets, start=1):
        if brand_pattern.search(item):
            return True, idx if idx <= 10 else None

    # Marqueurs ordinaux en texte : "en premier lieu … Anaba", "d'abord … Anaba"
    first_markers = re.compile(
        r'\b(?:premièr|premier|first|d\'abord|tout d\'abord|en premier)\b',
        re.IGNORECASE
    )
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if brand_pattern.search(line):
            # Regarde les 3 lignes précédentes pour un marqueur ordinal
            context = '\n'.join(lines[max(0, i - 3):i + 1])
            if first_markers.search(context):
                return True, 1

    # Citée mais position indéterminable → laisse le juge décider
    return True, None


# ---------------------------------------------------------------------------
# Juge Claude Haiku
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You are an expert at analyzing AI-generated recommendation lists. "
    "Your only job is to extract structured information as valid JSON."
)

JUDGE_PROMPT = """\
Given the following AI response, determine:
1. Whether the brand "Anaba" is cited (mentioned as a recommendation).
2. If cited, its ordinal position among all recommended brands/products
   (1 = first recommended, 2 = second, etc.).
   Return null if it is mentioned but not as part of an ordered recommendation.

Return ONLY a JSON object like:
{{"cited": true, "position": 2}}
or
{{"cited": false, "position": null}}

AI response to analyze:
\"\"\"
{response}
\"\"\"
"""


def _judge_haiku(response_text: str) -> tuple[bool, int | None]:
    """Appelle Claude Haiku pour déterminer citation + position."""
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = JUDGE_PROMPT.format(response=response_text[:3000])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    # Extrait le JSON même si entouré de backticks
    json_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not json_match:
        return False, None
    data = json.loads(json_match.group())
    cited = bool(data.get("cited", False))
    position = data.get("position")
    if position is not None:
        try:
            position = int(position)
        except (ValueError, TypeError):
            position = None
    return cited, position


# ---------------------------------------------------------------------------
# Interface publique
# ---------------------------------------------------------------------------

def parse_response(response_text: str) -> dict:
    """
    Analyse une réponse Claude et retourne un dict :
    {
        "cited": bool,
        "position": int | None,   # 1-based, None si non déterminable
        "score": int,
        "response_preview": str   # 150 premiers caractères
    }
    """
    cited_regex, position_regex = _find_brand_position_regex(response_text)

    if not cited_regex:
        # Pas de mention → score 0, pas besoin du juge
        cited, position = False, None
    elif position_regex is not None:
        # Regex a trouvé une position claire
        cited, position = True, position_regex
    else:
        # Citée mais position ambiguë → juge Haiku
        cited, position = _judge_haiku(response_text)

    # Scoring
    if not cited:
        score = SCORE_NOT_CITED
    elif position is not None and position in SCORE_TABLE:
        score = SCORE_TABLE[position]
    elif position is not None:
        # Au-delà de la 3e place : score symbolique de 1
        score = 1
    else:
        # Citée sans position déterminable : score minimal de citation
        score = 1

    preview = response_text.replace('\n', ' ').strip()[:150]

    return {
        "cited": cited,
        "position": position,
        "score": score,
        "response_preview": preview,
    }
