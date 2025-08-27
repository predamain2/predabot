import json
import re
from pathlib import Path

def is_valid_player_name(name: str) -> tuple[bool, str]:
    """
    Validates a player name to ensure it only uses standard characters.
    Returns a tuple of (is_valid, error_message).
    
    Rules:
    1. Only standard ASCII letters (A-Z, a-z)
    2. Numbers (0-9)
    3. Basic punctuation (underscore, dot, hyphen)
    4. No custom fonts or special characters
    5. Length between 3 and 20 characters
    """
    # Check length
    if len(name) < 3:
        return False, "Name must be at least 3 characters long"
    if len(name) > 20:
        return False, "Name must be no more than 20 characters long"
        
    # Check for valid characters
    valid_pattern = re.compile(r'^[a-zA-Z0-9._-]+$')
    if not valid_pattern.match(name):
        return False, "Name can only contain letters, numbers, dots, underscores, and hyphens"
        
    # Don't allow names that are just numbers
    if name.isdigit():
        return False, "Name cannot be just numbers"
        
    # Don't allow names with too many special characters
    special_chars = sum(1 for c in name if c in '._-')
    if special_chars > 2:
        return False, "Name cannot have more than 2 special characters"
        
    # Must start with a letter
    if not name[0].isalpha():
        return False, "Name must start with a letter"
        
    return True, ""

def load_players():
    players_file = Path("players.json")
    if players_file.exists():
        with open(players_file) as f:
            return json.load(f)
    return {}

def load_matches():
    matches_file = Path("matches.json")
    if matches_file.exists():
        with open(matches_file) as f:
            return json.load(f)
    return {"next": 1, "matches": {}}

def validate_player_registration(discord_id: str, nickname: str) -> tuple[bool, str]:
    """
    Validates a player's registration.
    Returns a tuple of (is_valid, error_message).
    """
    # First check if the name format is valid
    is_valid, error = is_valid_player_name(nickname)
    if not is_valid:
        return False, error
        
    # Load existing players to check for duplicates
    players = load_players()
    
    # Check if this Discord ID is already registered
    if discord_id in players:
        return False, "You are already registered"
        
    # Check if nickname is already taken (case-insensitive)
    for player in players.values():
        if player['nick'].lower() == nickname.lower():
            return False, "This nickname is already taken"
            
    return True, ""

def validate_and_complete_match_players(match_id: int, match_data: dict) -> dict:
    """
    Validates the match data against the original match players and adds any missing players
    with default stats.
    
    Args:
        match_id: The ID of the match from matches.json
        match_data: The parsed scoreboard data from OCR
    
    Returns:
        Updated match data with any missing players added
    """
    # Load required data
    players_data = load_players()
    matches_data = load_matches()
    
    if str(match_id) not in matches_data["matches"]:
        raise ValueError(f"Match {match_id} not found in matches.json")
        
    original_match = matches_data["matches"][str(match_id)]
    
    # Create lookup dictionaries for both teams from the original match
    team1_players = {}
    team2_players = {}
    for k, v in players_data.items():
        if v.get('id') in original_match['team1']:
            team1_players[v['nick'].lower()] = {'id': k, 'data': v}
        elif v.get('id') in original_match['team2']:
            team2_players[v['nick'].lower()] = {'id': k, 'data': v}

    # Get current players in the match results
    ct_players = {p['name'].lower() for p in match_data['ct_team']}
    t_players = {p['name'].lower() for p in match_data['t_team']}
    
    # Determine which team is CT based on player overlap
    ct_team1_overlap = len(ct_players & set(p.lower() for p in team1_players.keys()))
    ct_team2_overlap = len(ct_players & set(p.lower() for p in team2_players.keys()))
    ct_is_team1 = ct_team1_overlap > ct_team2_overlap

    # Add missing players to their respective teams with default stats
    if ct_is_team1:
        # Check CT (Team 1)
        for player_name, player_info in team1_players.items():
            if player_name not in ct_players:
                match_data['ct_team'].append({
                    'name': player_info['data']['nick'],
                    'kills': 0,
                    'assists': 0,
                    'deaths': 13,
                    'kd': 0.0,
                    'was_absent': True
                })
        
        # Check T (Team 2)
        for player_name, player_info in team2_players.items():
            if player_name not in t_players:
                match_data['t_team'].append({
                    'name': player_info['data']['nick'],
                    'kills': 0,
                    'assists': 0,
                    'deaths': 13,
                    'kd': 0.0,
                    'was_absent': True
                })
    else:
        # Check CT (Team 2)
        for player_name, player_info in team2_players.items():
            if player_name not in ct_players:
                match_data['ct_team'].append({
                    'name': player_info['data']['nick'],
                    'kills': 0,
                    'assists': 0,
                    'deaths': 13,
                    'kd': 0.0,
                    'was_absent': True
                })
        
        # Check T (Team 1)
        for player_name, player_info in team1_players.items():
            if player_name not in t_players:
                match_data['t_team'].append({
                    'name': player_info['data']['nick'],
                    'kills': 0,
                    'assists': 0,
                    'deaths': 13,
                    'kd': 0.0,
                    'was_absent': True
                })

    return match_data
