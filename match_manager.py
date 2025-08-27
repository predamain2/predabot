# match_manager.py - simple match registry for testing
import json, pathlib, time
DATA = pathlib.Path("matches.json")

def _load():
    if DATA.exists():
        text = DATA.read_text().strip()
        if not text:
            return {"next": 1, "matches": {}}
        return json.loads(text)
    return {"next": 1, "matches": {}}

def _save(d):
    DATA.write_text(json.dumps(d, indent=2))

def create_match(map_name, team1_ids, team2_ids, captain1_id=None, captain2_id=None):
    d = _load()
    mid = d["next"]
    d["matches"][mid] = {
        "map": map_name,
        "team1": team1_ids,
        "team2": team2_ids,
        "captain1": captain1_id,
        "captain2": captain2_id,
        "ts": int(time.time())
    }
    d["next"] += 1
    _save(d)
    return mid
