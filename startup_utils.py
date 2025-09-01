"""Startup utilities for efficient bot initialization"""
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

def safe_load_json(file_path: str, default: Optional[dict] = None) -> dict:
    """Safely load a JSON file with error handling"""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default or {}
    except json.JSONDecodeError:
        print(f'[STARTUP] Invalid JSON in {file_path}')
        return default or {}
    except Exception as e:
        print(f'[STARTUP] Error reading {file_path}: {e}')
        return default or {}

def load_startup_data() -> List[Tuple[str, str, str]]:
    """Load all startup data files with comprehensive error handling"""
    status = []
    
    # Load timeouts
    timeouts_file = Path('timeouts.json')
    if timeouts_file.exists():
        timeouts = safe_load_json(timeouts_file)
        status.append(('✓', 'Timeouts', f'{len(timeouts)} active'))
    else:
        timeouts = {}
        status.append(('!', 'Timeouts', 'File not found'))
    
    # Load players
    players_file = Path('players.json')
    if players_file.exists():
        players = safe_load_json(players_file)
        status.append(('✓', 'Players', f'{len(players)} registered'))
    else:
        players = {}
        status.append(('!', 'Players', 'File not found'))
    
    # Load parties
    parties_file = Path('parties.json')
    if parties_file.exists():
        parties = safe_load_json(parties_file)
        status.append(('✓', 'Parties', f'{len(parties)} active'))
    else:
        parties = {}
        status.append(('!', 'Parties', 'File not found'))
    
    return status, {'timeouts': timeouts, 'players': players, 'parties': parties}

def save_json_safe(data: Dict, file_path: str) -> bool:
    """Safely save JSON data with error handling"""
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f'[ERROR] Failed to save {file_path}: {e}')
        return False
