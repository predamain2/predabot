import aiohttp
import json
import base64
import re

# Paste your Gemini API key directly here
GEMINI_API_KEY = "AIzaSyDVb9o0_Z4xH7OoNcha7H17xRJIHj3e5DU"

async def run_llamaocr(image_url: str) -> dict:
    """
    Uses Google Gemini 2.0 Flash to extract a scoreboard into a structured dict:
    {
      "score": "16-12",
      "winner": "CT" | "T" | "Counter-Terrorists" | "Terrorists",
      "ct_team": [{"name": "...", "kills": 0, "assists": 0, "deaths": 0, "kd": 0.0}, ...],
      "t_team":  [{"name": "...", "kills": 0, "assists": 0, "deaths": 0, "kd": 0.0}, ...]
    }
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API key is missing. Paste it into GEMINI_API_KEY.")

    # --- Fetch the image and base64-encode it ---
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as r:
            r.raise_for_status()
            img_bytes = await r.read()
            content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip() or "image/jpeg"

    b64 = base64.b64encode(img_bytes).decode("ascii")

    # --- Prompt: return ONLY JSON in the schema main.py expects ---
    prompt = (
        "Extract the data from this Standoff 2 scoreboard image into a table. Important rules:\n"
        "1. For player names:\n"
        "   - Remove any text in square brackets like [TAG] or [CLAN]\n"
        "   - Remove any special characters except hyphen (-)\n"
        "   - Preserve exact spelling and capitalization of the remaining name\n"
        "2. Score format:\n"
        "   - Must be exactly 'CTScore-TScore' format\n"
        "   - Validate score is possible (each number 0-16)\n"
        "3. Team identification:\n"
        "   - Left side is ALWAYS Counter-Terrorists (CT)\n"
        "   - Right side is ALWAYS Terrorists (T)\n"
        "4. Stats must be exact numbers from scoreboard:\n"
        "   - Kills (K)\n"
        "   - Assists (A)\n"
        "   - Deaths (D)\n"
        "5. Calculate KD ratio if not shown:\n"
        "   - KD = Kills/Deaths, but if Deaths=0, KD should equal Kills\n"
        "   - Round to 2 decimal places\n"
        "6. Winner determination:\n"
        "   - Based on final score (higher score wins)\n"
        "   - Set winner to 'CT' or 'T' accordingly\n\n"
        "Return ONLY valid JSON with this exact schema and keys (no markdown, no code fences):\n"
        "{\n"
        '  "score": "CTScore-TScore",\n'
        '  "winner": "CT" | "T" | "Counter-Terrorists" | "Terrorists",\n'
        '  "ct_team": [ {"name": "string", "kills": int, "assists": int, "deaths": int, "kd": number}, ... ],\n'
        '  "t_team":  [ {"name": "string", "kills": int, "assists": int, "deaths": int, "kd": number}, ... ]\n'
        "}\n"
        "- Names must have any bracketed clan tags removed, e.g., 'Ace [TAG]' -> 'Ace'.\n"
        "- Do not include any extra keys or commentary."
    )

    # --- Build Gemini request (inline image data) ---
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY
    )
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": content_type, "data": b64}},
                ],
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()

    # --- Extract text response and parse JSON ---
    try:
        text = data["candidates"][0]["content"]["parts"][0].get("text", "").strip()
    except (KeyError, IndexError, AttributeError):
        raise RuntimeError(f"Gemini response unexpected: {json.dumps(data)[:800]}")

    # Strip accidental code fences if present
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # If the model didn't return JSON, surface the first 800 chars to help debug
        raise RuntimeError(f"Gemini did not return JSON. Raw text: {text[:800]}")

    # --- Minimal validation + fill KD if missing ---
    for team_key in ("ct_team", "t_team"):
        players = result.get(team_key, []) or []
        for p in players:
            p["name"] = str(p.get("name", "")).strip()
            p["kills"] = int(p.get("kills", 0))
            p["assists"] = int(p.get("assists", 0))
            p["deaths"] = int(p.get("deaths", 0))
            if "kd" not in p or p["kd"] in (None, ""):
                if p["deaths"] == 0:
                    p["kd"] = "kills"
                else:
                    p["kd"] = round(p["kills"] / max(1, p["deaths"]), 2)

    result["score"] = str(result.get("score", "")).strip()
    result["winner"] = str(result.get("winner", "")).strip()

    return result
