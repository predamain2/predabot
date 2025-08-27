"""Tools for match result processing including missing player detection."""

import json
import re
from pathlib import Path
import config

def calculate_rating(kills: int, deaths: int, assists: int, rounds_played: int) -> float:
    """
    Calculate a HLTV-like rating based on available stats.
    Formula components:
    - KD Impact: (Kills - Deaths) / Rounds
    - Kill Impact: Kills / Rounds
    - Survival Impact: (Rounds - Deaths) / Rounds
    - Assist Factor: Assists / (2 * Rounds)
    Final rating is normalized around 1.0
    """
    # Ensure we don't divide by zero
    if rounds_played == 0:
        return 0.0
        
    kd_impact = (kills - deaths) / rounds_played
    kill_impact = kills / rounds_played
    survival_impact = (rounds_played - deaths) / rounds_played
    assist_factor = assists / (2 * rounds_played)
    
    # Combine factors and normalize
    rating = 0.85 + (kd_impact * 0.4) + (kill_impact * 0.3) + (survival_impact * 0.2) + (assist_factor * 0.1)
    
    # Ensure rating doesn't go below 0 and round to 2 decimal places
    return round(max(0, rating), 2)

def match_player_name(parsed_name: str, expected_name: str, chars: int = 9) -> bool:
    """
    Match a parsed name against an expected name using first N characters.
    Returns True if the first N characters match (case-insensitive).
    """
    parsed_name = parsed_name.lower().strip()
    expected_name = expected_name.lower().strip()
    return parsed_name[:chars] == expected_name[:chars]
    def clean_name(name: str) -> str:
        # Preserve special characters that are commonly used in clan tags
        cleaned = name.lower().strip()
        # Replace multiple spaces with single space
        cleaned = ' '.join(cleaned.split())
        return cleaned

    def get_variations(name: str) -> list:
        # Get different variations of the name to match against
        variations = [name]
        
        # Handle clan tags in brackets
        if '[' in name and ']' in name:
            # Try without clan tag
            no_clan = name[name.find(']')+1:].strip()
            variations.append(no_clan)
            
        # Handle names with common separators
        for sep in ['|', ':', '_', '-']:
            if sep in name:
                parts = [p.strip() for p in name.split(sep)]
                variations.extend(parts)
                # Also add combined parts
                variations.append(''.join(parts))
        
        return variations

    name1_clean = clean_name(name1)
    name2_clean = clean_name(name2)
    
    # Direct match
    if name1_clean == name2_clean:
        return True
        
    # One is prefix of other (handles truncation)
    if name1_clean.startswith(name2_clean) or name2_clean.startswith(name1_clean):
        return True
    
    # Try matching variations
    name1_vars = get_variations(name1_clean)
    name2_vars = get_variations(name2_clean)
    
    for var1 in name1_vars:
        for var2 in name2_vars:
            if var1 and var2:  # Ensure non-empty strings
                # Direct match of variations
                if var1 == var2:
                    return True
                # Prefix match of variations
                if var1.startswith(var2) or var2.startswith(var1):
                    return True
    
    return False

def validate_teams(match_data: dict) -> bool:
    """
    Validates team data with simple rules:
    1. Maximum 5 players per team
    2. No duplicate names on either team
    3. No player can be on both teams
    """
    ct_team = match_data['ct_team']
    t_team = match_data['t_team']
    
    # Check team sizes
    if len(ct_team) > 5:
        print(f"Error: CT team has too many players ({len(ct_team)})")
        return False
    if len(t_team) > 5:
        print(f"Error: T team has too many players ({len(t_team)})")
        return False

    # Get player names (case-insensitive)
    ct_names = [p['name'].lower() for p in ct_team]
    t_names = [p['name'].lower() for p in t_team]

    # Check for duplicates within CT team
    ct_duplicates = []
    seen_ct = set()
    for i, name in enumerate(ct_names):
        if name in seen_ct:
            ct_duplicates.append(ct_team[i]['name'])  # show original name in error
        seen_ct.add(name)
    if ct_duplicates:
        print(f"Error: Duplicate players found in CT team: {ct_duplicates}")
        return False
        
    # Check for duplicates within T team
    t_duplicates = []
    seen_t = set()
    for i, name in enumerate(t_names):
        if name in seen_t:
            t_duplicates.append(t_team[i]['name'])  # show original name in error
        seen_t.add(name)
    if t_duplicates:
        print(f"Error: Duplicate players found in T team: {t_duplicates}")
        return False
        
    # Check for players in both teams (case-insensitive)
    shared_players = set(ct_names) & set(t_names)
    if shared_players:
        # Get original names for players that are shared
        shared_originals = []
        for name in shared_players:
            ct_players = [p['name'] for p in ct_team if p['name'].lower() == name]
            t_players = [p['name'] for p in t_team if p['name'].lower() == name]
            for ct_original in ct_players:
                for t_original in t_players:
                    shared_originals.append(f"'{ct_original}' (CT) and '{t_original}' (T)")
        print(f"Error: Player(s) found on both teams: {shared_originals}")
        return False
        
    # Additional validation for duplicate names with slightly different cases
    all_names = [(p['name'], 'CT') for p in ct_team] + [(p['name'], 'T') for p in t_team]
    for i, (name1, team1) in enumerate(all_names):
        for name2, team2 in all_names[i+1:]:
            if name1.lower() == name2.lower() and name1 != name2:
                print(f"Error: Same player with different capitalization: '{name1}' ({team1}) and '{name2}' ({team2})")
                return False
        
    return True

def validate_scoreboard_data(scoreboard_data: dict) -> bool:
    """
    Validates scoreboard data for consistency and possible values.
    Returns True if valid, False if invalid.
    """
    try:
        # Validate score format
        score_match = re.match(r'^(\d+)-(\d+)$', scoreboard_data['score'])
        if not score_match:
            print(f"Invalid score format: {scoreboard_data['score']}")
            return False
            
        ct_score, t_score = map(int, score_match.groups())
        total_rounds = ct_score + t_score
        if ct_score > 16 or t_score > 16 or ct_score < 0 or t_score < 0:
            print(f"Invalid score values: {ct_score}-{t_score}")
            return False
            
        # Also validate team assignments
        if not validate_teams(scoreboard_data):
            return False
            
        # Validate stats and calculate ratings
        for team in [scoreboard_data['ct_team'], scoreboard_data['t_team']]:
            for player in team:
                # Validate stat values
                if player['kills'] < 0 or player['deaths'] < 0 or player['assists'] < 0:
                    print(f"Invalid stats for player {player['name']}: K:{player['kills']} A:{player['assists']} D:{player['deaths']}")
                    return False
                    
                # Check for players who left (0 kills and high deaths)
                if player['kills'] == 0 and player['deaths'] >= 10:
                    print(f"Player {player['name']} appears to have left the game (0 kills, {player['deaths']} deaths)")
                    player['was_absent'] = True
                    player['elo_change'] = -20  # Apply leaver penalty
                    player['rating'] = 0.00  # Set rating to 0 for leavers
                else:
                    # Calculate HLTV-like rating
                    player['rating'] = calculate_rating(
                        kills=player['kills'],
                        deaths=player['deaths'],
                        assists=player['assists'],
                        rounds_played=total_rounds
                    )
                
                # Calculate and validate K/D ratio
                deaths = player['deaths']
                kills = player['kills']
                kd = kills if deaths == 0 else round(kills / deaths, 2)
                if kd != player['kd']:
                    player['kd'] = kd  # Fix incorrect K/D ratio
                    print(f"Fixed K/D ratio for {player['name']}: {kd}")
                    
        # Validate winner declaration
        if scoreboard_data['winner'] not in ['CT', 'T']:
            print(f"Invalid winner: {scoreboard_data['winner']}")
            return False
            
        # Verify winner matches score
        winner_score = ct_score if scoreboard_data['winner'] == 'CT' else t_score
        loser_score = t_score if scoreboard_data['winner'] == 'CT' else ct_score
        if winner_score <= loser_score:
            print(f"Winner's score ({winner_score}) is not greater than loser's score ({loser_score})")
            return False
            
        return True
        
    except Exception as e:
        print(f"Validation error: {str(e)}")
        return False

def apply_leaver_penalty(player_data: dict, discord_id: str, nickname: str):
    """Apply -20 ELO penalty to a player who left the game."""
    current_elo = player_data[discord_id].get("elo", config.DEFAULT_ELO)
    new_elo = max(config.DEFAULT_ELO, current_elo - 20)
    player_data[discord_id]["elo"] = new_elo
    print(f"Applied leaver penalty to {nickname}: {current_elo} -> {new_elo} ELO")

def check_for_leavers(team: list, player_data: dict, is_winning_team: bool):
    """Check for players who left during the match and apply penalties."""
    for player in team:
        # Consider a player as leaver if they have 0 kills and high deaths
        if player.get('kills', 0) == 0 and player.get('deaths', 0) >= 10:
            # Mark as absent
            player['was_absent'] = True
            player['elo_change'] = -20  # Set the ELO change for display purposes
            print(f"Marking {player['name']} as leaver: 0 kills, {player.get('deaths')} deaths")

def get_teams_from_match_data(match_id: int, match_data: dict, player_data: dict) -> tuple:
    """
    Analyzes match data to determine which team is which and detects missing players.
    Returns a tuple of (winning_team, losing_team, winners_were_ct).
    """
    # First validate the teams data
    if not validate_teams(match_data):
        raise ValueError("Invalid team data")
    
    matches_file = Path("matches.json")
    if not matches_file.exists():
        raise ValueError("Could not find matches data file")
        
    print("DEBUG: Original match data:")
    # Load original match data
    matches_data = json.loads(matches_file.read_text())
    original_match = matches_data["matches"].get(str(match_id))
    if not original_match:
        raise ValueError(f"Could not find original match data for Match ID {match_id}")

    print(f"DEBUG: Match {match_id} teams from matches.json:")
    print(f"Team 1: {original_match['team1']}")
    print(f"Team 2: {original_match['team2']}")

    # Map Discord IDs to their stored nicknames
    print("\nDEBUG: Mapping Discord IDs to stored nicknames:")
    team1_players = {}
    team2_players = {}
    
    # Build team mappings using exact nicknames
    for discord_id in original_match['team1']:
        discord_id = str(discord_id)
        if discord_id in player_data:
            stored_nick = player_data[discord_id]['nick']
            team1_players[stored_nick] = {
                'id': discord_id,
                'data': player_data[discord_id]
            }
            print(f"DEBUG: Added to team1: {stored_nick}")
    
    for discord_id in original_match['team2']:
        discord_id = str(discord_id)
        if discord_id in player_data:
            stored_nick = player_data[discord_id]['nick']
            team2_players[stored_nick] = {
                'id': discord_id,
                'data': player_data[discord_id]
            }
            print(f"DEBUG: Added to team2: {stored_nick}")
    
    print("\nDEBUG: Players from scoreboard (before name correction):")
    print("CT team:", [p['name'] for p in match_data['ct_team']])
    print("T team:", [p['name'] for p in match_data['t_team']])

    print("\nDEBUG: Expected players from DB:")
    print("Team 1:", list(team1_players.keys()))
    print("Team 2:", list(team2_players.keys()))
    
    # Replace truncated names with their full versions
    all_expected_names = list(team1_players.keys()) + list(team2_players.keys())
    
    # Fix CT team names
    for player in match_data['ct_team']:
        for expected_name in all_expected_names:
            if match_player_name(player['name'], expected_name):
                if player['name'] != expected_name:
                    print(f"Fixing truncated name: '{player['name']}' -> '{expected_name}'")
                    player['name'] = expected_name
                break
                
    # Fix T team names
    for player in match_data['t_team']:
        for expected_name in all_expected_names:
            if match_player_name(player['name'], expected_name):
                if player['name'] != expected_name:
                    print(f"Fixing truncated name: '{player['name']}' -> '{expected_name}'")
                    player['name'] = expected_name
                break
                
    print("\nDEBUG: Players from scoreboard (after name correction):")
    print("CT team:", [p['name'] for p in match_data['ct_team']])
    print("T team:", [p['name'] for p in match_data['t_team']])
    
    def validate_team_assignments(ct_team, t_team):
        """
        Validates team assignments according to rules:
        1. No player can be on both teams
        2. Maximum 5 players per team
        """
        # Check team sizes
        if len(ct_team) > 5:
            raise ValueError(f"CT team has {len(ct_team)} players, maximum allowed is 5")
        if len(t_team) > 5:
            raise ValueError(f"T team has {len(t_team)} players, maximum allowed is 5")
            
        # Get player names
        ct_names = [p['name'] for p in ct_team]
        t_names = [p['name'] for p in t_team]
        
        # Check for players in both teams
        shared_players = set(ct_names) & set(t_names)
        if shared_players:
            raise ValueError(f"Players found on both teams: {shared_players}")
        
        return True
        
    # Validate team assignments before processing
    try:
        validate_team_assignments(match_data['ct_team'], match_data['t_team'])
    except ValueError as e:
        print(f"Team validation error: {str(e)}")
        raise
    
    # Map players to their teams
    ct_team_players = match_data['ct_team']
    t_team_players = match_data['t_team']
    
    print("\nDEBUG: Processing teams:")
    print("CT team players:", [p['name'] for p in match_data['ct_team']])
    print("T team players:", [p['name'] for p in match_data['t_team']])
    
    # Remove extra players that aren't in the original teams
    ct_to_remove = []
    t_to_remove = []
    
    # Check CT team for extra players
    for player in match_data['ct_team']:
        player_name = player['name']
        if not any(match_player_name(player_name, stored_nick) for stored_nick in team1_players.keys()) and \
           not any(match_player_name(player_name, stored_nick) for stored_nick in team2_players.keys()):
            print(f"Found extra player in CT team: {player_name}")
            ct_to_remove.append(player)
    
    # Check T team for extra players
    for player in match_data['t_team']:
        player_name = player['name']
        if not any(match_player_name(player_name, stored_nick) for stored_nick in team1_players.keys()) and \
           not any(match_player_name(player_name, stored_nick) for stored_nick in team2_players.keys()):
            print(f"Found extra player in T team: {player_name}")
            t_to_remove.append(player)
            
    # Remove extra players
    for player in ct_to_remove:
        match_data['ct_team'].remove(player)
        print(f"Removed extra player from CT team: {player['name']}")
    
    for player in t_to_remove:
        match_data['t_team'].remove(player)
        print(f"Removed extra player from T team: {player['name']}")
        
    # Update the player mappings after removing extras
    ct_team_players = {p['name'].lower(): p for p in match_data['ct_team']}
    t_team_players = {p['name'].lower(): p for p in match_data['t_team']}
    
    # Count how many players match with each original team
    ct_matches_team1 = len(set(ct_team_players.keys()) & set(team1_players.keys()))
    ct_matches_team2 = len(set(ct_team_players.keys()) & set(team2_players.keys()))
    
    # Determine if CT is team1 or team2 based on player overlap
    ct_is_team1 = ct_matches_team1 > ct_matches_team2
    print(f"\nTeam matching - CT matches with team1: {ct_matches_team1}, with team2: {ct_matches_team2}")
    print(f"CT is team1: {ct_is_team1}")

    # Determine winner based on score
    ct_score, t_score = map(int, match_data['score'].split('-'))
    winners_were_ct = ct_score > t_score

    # Add missing players to CT team
    ct_expected = team1_players if ct_is_team1 else team2_players
    print("\nDEBUG: Checking CT team for missing players...")
    print(f"Current CT players: {list(ct_team_players.keys())}")
    print(f"Expected CT players: {list(ct_expected.keys())}")
    
    for stored_nick, player_info in ct_expected.items():
        # Check if player is in CT team using improved name matching
        if not any(match_player_name(stored_nick, p['name']) for p in match_data['ct_team']):
            print(f"Found missing CT player: {stored_nick}")
            # Calculate ELO change based on current level
            current_elo = player_data[player_info['id']].get("elo", config.DEFAULT_ELO)
            current_level = player_data[player_info['id']].get("level", 1)
            elo_change = config.get_elo_change(current_level, False)  # False for loss
            
            # Always give -20 ELO to missing players and count as a loss
            match_data['ct_team'].append({
                "name": stored_nick,  # Use the stored nickname
                "kills": 0,
                "assists": 0,
                "deaths": 13,
                "kd": 0.0,
                "was_absent": True,
                "elo_change": -20  # Fixed -20 ELO penalty for missing players
            })
            # Update player data immediately
            player_data[player_info['id']]["losses"] = player_data[player_info['id']].get("losses", 0) + 1
            player_data[player_info['id']]["elo"] = max(config.DEFAULT_ELO, 
                current_elo - 20)  # Always -20 ELO penalty
            print(f"Updated stats for {stored_nick}: {elo_change} ELO, +1 loss")

    # Add missing players to T team
    t_expected = team2_players if ct_is_team1 else team1_players
    print("\nDEBUG: Checking T team for missing players...")
    print(f"Current T players: {list(t_team_players.keys())}")
    print(f"Expected T players: {list(t_expected.keys())}")
    
    for stored_nick, player_info in t_expected.items():
        # Check if player is in T team using improved name matching
        if not any(match_player_name(stored_nick, p['name']) for p in match_data['t_team']):
            print(f"Found missing T player: {stored_nick}")
            # Calculate ELO change based on current level
            current_elo = player_data[player_info['id']].get("elo", config.DEFAULT_ELO)
            current_level = player_data[player_info['id']].get("level", 1)
            elo_change = config.get_elo_change(current_level, False)  # False for loss
            
            # Always give -20 ELO to missing players and count as a loss
            match_data['t_team'].append({
                "name": stored_nick,  # Use the stored nickname
                "kills": 0,
                "assists": 0,
                "deaths": 13,
                "kd": 0.0,
                "was_absent": True,
                "elo_change": -20  # Fixed -20 ELO penalty for missing players
            })
            # Update player data immediately
            player_data[player_info['id']]["losses"] = player_data[player_info['id']].get("losses", 0) + 1
            player_data[player_info['id']]["elo"] = max(config.DEFAULT_ELO, 
                current_elo - 20)  # Always -20 ELO penalty
            print(f"Updated stats for {stored_nick}: {elo_change} ELO, +1 loss")

    winning_team = match_data['ct_team'] if winners_were_ct else match_data['t_team']
    losing_team = match_data['t_team'] if winners_were_ct else match_data['ct_team']

    return winning_team, losing_team, winners_were_ct

    # Get actual players from submitted results
    ct_team_players = {p['name'].lower(): p for p in match_data['ct_team']}
    t_team_players = {p['name'].lower(): p for p in match_data['t_team']}
    
    # Count how many players match with each original team
    ct_matches_team1 = len(set(ct_team_players.keys()) & set(team1_players.keys()))
    ct_matches_team2 = len(set(ct_team_players.keys()) & set(team2_players.keys()))
    
    # Determine if CT is team1 or team2 based on player overlap
    ct_is_team1 = ct_matches_team1 > ct_matches_team2

    # Determine winner based on score
    ct_score, t_score = map(int, match_data['score'].split('-'))
    winners_were_ct = ct_score > t_score
    
    # Check for players who left during the game
    check_for_leavers(match_data['ct_team'], player_data, winners_were_ct)
    check_for_leavers(match_data['t_team'], player_data, not winners_were_ct)

    print("\nDEBUG: Expected players from DB:")
    print("Team 1 expected:", list(team1_players.keys()))
    print("Team 2 expected:", list(team2_players.keys()))
    
    # Add missing players to CT team
    ct_expected = team1_players if ct_is_team1 else team2_players
    print("\nDEBUG: Checking CT team for missing players...")
    print(f"Current CT players: {list(ct_team_players.keys())}")
    print(f"Expected CT players: {list(ct_expected.keys())}")
    
    # Create normalized dictionaries for comparison using the improved normalize_name function
    normalized_ct_players = {normalize_name(name)['compare']: name for name in ct_team_players.keys()}
    normalized_t_players = {normalize_name(name)['compare']: name for name in t_team_players.keys()}
    
    for missing_player_name, player_info in ct_expected.items():
        normalized_name = normalize_name(missing_player_name)['compare']
        if normalized_name not in normalized_ct_players:
            print(f"Found missing CT player: {player_info['data']['nick']}")
            was_winner = winners_were_ct  # Check if this player's team won
            elo_change = -20  # Always -20 for leaving, even if team won
            
            match_data['ct_team'].append({
                "name": player_info['data']['nick'],
                "kills": 0,
                "assists": 0,
                "deaths": 13,
                "kd": 0.0,
                "was_absent": True,
                "elo_change": elo_change
            })
            
            # Update player data
            if was_winner:
                player_data[player_info['id']]["wins"] = player_data[player_info['id']].get("wins", 0) + 1
            else:
                player_data[player_info['id']]["losses"] = player_data[player_info['id']].get("losses", 0) + 1
                
            # Always apply -20 ELO for leaving
            player_data[player_info['id']]["elo"] = max(config.DEFAULT_ELO, 
                player_data[player_info['id']].get("elo", config.DEFAULT_ELO) + elo_change)
            print(f"Updated stats for {player_info['data']['nick']}: {elo_change} ELO, +1 {'win' if was_winner else 'loss'} (left game)")

    # Add missing players to T team
    t_expected = team2_players if ct_is_team1 else team1_players
    print("\nDEBUG: Checking T team for missing players...")
    print(f"Current T players: {list(t_team_players.keys())}")
    print(f"Expected T players: {list(t_expected.keys())}")
    
    for missing_player_name, player_info in t_expected.items():
        normalized_name = normalize_name(missing_player_name)['compare']
        if normalized_name not in normalized_t_players:
            print(f"Found missing T player: {player_info['data']['nick']}")
            match_data['t_team'].append({
                "name": player_info['data']['nick'],
                "kills": 0,
                "assists": 0,
                "deaths": 13,
                "kd": 0.0,
                "was_absent": True,
                "elo_change": -20
            })
            # Update player data immediately
            player_data[player_info['id']]["losses"] = player_data[player_info['id']].get("losses", 0) + 1
            player_data[player_info['id']]["elo"] = max(config.DEFAULT_ELO, 
                player_data[player_info['id']].get("elo", config.DEFAULT_ELO) - 20)
            print(f"Updated stats for {player_info['data']['nick']}: -20 ELO, +1 loss")

    winning_team = match_data['ct_team'] if winners_were_ct else match_data['t_team']
    losing_team = match_data['t_team'] if winners_were_ct else match_data['ct_team']

    # Update player data with losses for absent players
    for team in [winning_team, losing_team]:
        for player in team:
            if player.get('was_absent', False):
                for k, v in player_data.items():
                    if v['nick'].lower() == player['name'].lower():
                        player_data[k]["losses"] = player_data[k].get("losses", 0) + 1
                        player_data[k]["elo"] = max(config.DEFAULT_ELO, 
                            player_data[k].get("elo", config.DEFAULT_ELO) - 20)

    return winning_team, losing_team, winners_were_ct
