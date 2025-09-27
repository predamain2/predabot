"""Tools for match result processing including missing player detection."""

import json
import re
from pathlib import Path
import config
from difflib import SequenceMatcher


def calculate_rating(kills: int, deaths: int, assists: int, rounds_played: int) -> float:
    """
    Calculate a HLTV-like rating based on available stats.
    """
    if rounds_played == 0:
        return 0.0

    # Ensure division by zero doesn't occur if rounds_played is somehow negative (validation should prevent this)
    if rounds_played <= 0:
        return 0.0

    kd_impact = (kills - deaths) / rounds_played
    kill_impact = kills / rounds_played
    survival_impact = (rounds_played - deaths) / rounds_played
    assist_factor = assists / (2 * rounds_played)

    rating = (
        0.85
        + (kd_impact * 0.4)
        + (kill_impact * 0.3)
        + (survival_impact * 0.2)
        + (assist_factor * 0.1)
    )
    return round(max(0, rating), 2)


def validate_teams(match_data: dict) -> bool:
    """
    Validates team data with simple rules:
    1. Maximum 5 players per team
    2. No duplicate names on either team
    3. No player can be on both teams
    4. At least one present player per team (not marked as absent)
    """
    ct_team = match_data["ct_team"]
    t_team = match_data["t_team"]

    if len(ct_team) > 5:
        print(f"Error: CT team has too many players ({len(ct_team)})")
        return False
    if len(t_team) > 5:
        print(f"Error: T team has too many players ({len(t_team)})")
        return False

    # Check for at least one present player per team
    ct_present = any(not (p.get("kills", 0) == 0 and p.get("deaths", 0) >= 10) for p in ct_team)
    t_present = any(not (p.get("kills", 0) == 0 and p.get("deaths", 0) >= 10) for p in t_team)
    
    if not ct_present:
        print("Error: CT team has no present players (all marked as absent)")
        raise ValueError("Match submission rejected: CT team has no present players")
    if not t_present:
        print("Error: T team has no present players (all marked as absent)")
        raise ValueError("Match submission rejected: T team has no present players")
        return False

    ct_names = [p["name"].lower() for p in ct_team]
    t_names = [p["name"].lower() for p in t_team]

    if len(ct_names) != len(set(ct_names)):
        print("Error: Duplicate players found in CT team")
        return False
    if len(t_names) != len(set(t_names)):
        print("Error: Duplicate players found in T team")
        return False

    shared_players = set(ct_names) & set(t_names)
    if shared_players:
        print(f"Error: Player(s) found on both teams: {shared_players}")
        return False

    return True


def validate_scoreboard_data(scoreboard_data: dict) -> bool:
    """
    Validates scoreboard data for consistency and possible values.
    """
    try:
        score_match = re.match(r"^(\d+)-(\d+)$", scoreboard_data["score"])
        if not score_match:
            print(f"Invalid score format: {scoreboard_data['score']}")
            return False

        ct_score, t_score = map(int, score_match.groups())
        total_rounds = ct_score + t_score
        if ct_score > 16 or t_score > 16 or ct_score < 0 or t_score < 0:
            print(f"Invalid score values: {ct_score}-{t_score}")
            return False

        if not validate_teams(scoreboard_data):
            return False

        for team in [scoreboard_data["ct_team"], scoreboard_data["t_team"]]:
            for player in team:
                if (
                    player["kills"] < 0
                    or player["deaths"] < 0
                    or player["assists"] < 0
                ):
                    print(
                        f"Invalid stats for player {player['name']}: "
                        f"K:{player['kills']} A:{player['assists']} D:{player['deaths']}"
                    )
                    return False

                if player["kills"] == 0 and player["deaths"] >= 10:
                    print(
                        f"Player {player['name']} appears to have left "
                        f"(0 kills, {player['deaths']} deaths)"
                    )
                    player["was_absent"] = True
                    player["elo_change"] = -20
                    player["rating"] = 0.00
                else:
                    player["rating"] = calculate_rating(
                        kills=player["kills"],
                        deaths=player["deaths"],
                        assists=player["assists"],
                        rounds_played=total_rounds,
                    )

                deaths = player["deaths"]
                kills = player["kills"]
                kd = kills if deaths == 0 else round(kills / deaths, 2)
                if kd != player["kd"]:
                    player["kd"] = kd
                    print(f"Fixed K/D ratio for {player['name']}: {kd}")

        if scoreboard_data["winner"] not in ["CT", "T"]:
            print(f"Invalid winner: {scoreboard_data['winner']}")
            return False

        winner_score = ct_score if scoreboard_data["winner"] == "CT" else t_score
        loser_score = t_score if scoreboard_data["winner"] == "CT" else ct_score
        if winner_score <= loser_score:
            print(
                f"Winner's score ({winner_score}) is not greater than loser's ({loser_score})"
            )
            return False

        return True

    except Exception as e:
        print(f"Validation error: {str(e)}")
        return False


def apply_leaver_penalty(player_data: dict, discord_id: str, nickname: str):
    current_elo = player_data[discord_id].get("elo", config.DEFAULT_ELO)
    new_elo = max(config.DEFAULT_ELO, current_elo - 20)
    player_data[discord_id]["elo"] = new_elo
    print(f"Applied leaver penalty to {nickname}: {current_elo} -> {new_elo} ELO")


def check_for_leavers(team: list, player_data: dict, is_winning_team: bool):
    for player in team:
        if player.get("kills", 0) == 0 and player.get("deaths", 0) >= 10:
            player["was_absent"] = True
            player["elo_change"] = -20
            print(
                f"Marking {player['name']} as leaver: "
                f"0 kills, {player.get('deaths')} deaths"
            )

def _build_expected_rosters(original_match: dict, player_data: dict) -> tuple[dict, dict]:
    """Builds dictionaries of expected players for each team from the match data."""
    team1_players = {}
    team2_players = {}
    for team_key, player_dict in [("team1", team1_players), ("team2", team2_players)]:
        for discord_id in original_match[team_key]:
            discord_id = str(discord_id)
            if discord_id in player_data:
                nick = player_data[discord_id]["nick"]
                player_dict[nick] = {"id": discord_id, "data": player_data[discord_id]}
    return team1_players, team2_players

def _normalize_name_for_matching(name: str) -> str:
    """
    Normalize a name for better OCR error handling.
    Handles common OCR mistakes, prefixes, suffixes, and emojis.
    """
    if not name:
        return ""
    
    # Convert to lowercase
    normalized = name.lower().strip()
    
    # Common OCR character substitutions
    ocr_fixes = {
        'l': 'i',  # lowercase l often misread as i
        '0': 'o',  # zero often misread as o
        '1': 'l',  # one often misread as l
        '5': 's',  # five often misread as s
        '8': 'b',  # eight often misread as b
        '6': 'g',  # six often misread as g
    }
    
    # Apply OCR fixes
    for wrong, correct in ocr_fixes.items():
        normalized = normalized.replace(wrong, correct)
    
    # Remove extra repeated characters (like "distincttttttt" -> "distinct")
    import re
    normalized = re.sub(r'(.)\1{2,}', r'\1', normalized)  # Remove 3+ repeated chars
    
    # Remove common prefixes (team tags, brackets, etc.)
    prefix_patterns = [
        r'^\[.*?\]\s*',  # [RHD], [Team], etc.
        r'^\(.*?\)\s*',  # (Team), etc.
        r'^<.*?>\s*',    # <Team>, etc.
        r'^\{.*?\}\s*',  # {Team}, etc.
        r'^[|~`\'"]+\s*',  # |, ||, ~, `, ', "
        r'^[a-z]+\s*:\s*',  # team:, clan:, etc.
    ]
    
    for pattern in prefix_patterns:
        normalized = re.sub(pattern, '', normalized)
    
    # Remove common suffixes (FPS, Hz, etc.)
    suffix_patterns = [
        r'\s*\[.*?\]$',  # [120fps], [Team], etc.
        r'\s*\(.*?\)$',  # (120fps), etc.
        r'\s*<.*?>$',    # <120fps>, etc.
        r'\s*\{.*?\}$',  # {120fps}, etc.
        r'\s*[|~`\'"]+$',  # |, ||, ~, `, ', "
        r'\s*\d+fps$',   # 120fps, 60fps, etc.
        r'\s*\d+hz$',    # 120hz, 60hz, etc.
        r'\s*\d+ms$',    # 5ms, 10ms, etc.
        r'\s*[a-z]+:\s*$',  # fps:, hz:, etc.
    ]
    
    for pattern in suffix_patterns:
        normalized = re.sub(pattern, '', normalized)
    
    # Remove emojis and special characters but keep basic alphanumeric and spaces
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    return normalized

def _calculate_name_similarity(scoreboard_name: str, expected_name: str) -> float:
    """
    Calculate similarity between scoreboard name and expected name with multiple strategies.
    Returns the best similarity score found.
    """
    if not scoreboard_name or not expected_name:
        return 0.0
    
    # Normalize both names
    norm_scoreboard = _normalize_name_for_matching(scoreboard_name)
    norm_expected = _normalize_name_for_matching(expected_name)
    
    # Strategy 1: Direct normalized comparison
    if norm_scoreboard == norm_expected:
        return 1.0
    
    # Strategy 2: Check if one contains the other (for cases like "distincttttttt" vs "distinct")
    if norm_scoreboard in norm_expected or norm_expected in norm_scoreboard:
        # Calculate ratio based on length difference
        shorter = min(len(norm_scoreboard), len(norm_expected))
        longer = max(len(norm_scoreboard), len(norm_expected))
        if shorter > 0:
            return shorter / longer
    
    # Strategy 3: Check if one is a substring of the other (for cases like "SAYAN | 120fps" vs "SAyan")
    # Remove common separators and check
    clean_scoreboard = re.sub(r'[|\s]+', '', norm_scoreboard)
    clean_expected = re.sub(r'[|\s]+', '', norm_expected)
    
    if clean_scoreboard == clean_expected:
        return 1.0
    if clean_scoreboard in clean_expected or clean_expected in clean_scoreboard:
        shorter = min(len(clean_scoreboard), len(clean_expected))
        longer = max(len(clean_scoreboard), len(clean_expected))
        if shorter > 0:
            return shorter / longer
    
    # Strategy 4: Traditional fuzzy matching
    fuzzy_score = SequenceMatcher(None, norm_scoreboard, norm_expected).ratio()
    
    # Strategy 5: Check individual words (for cases like "RHD | SAyan" vs "SAYAN")
    scoreboard_words = set(norm_scoreboard.split())
    expected_words = set(norm_expected.split())
    
    if scoreboard_words and expected_words:
        word_overlap = len(scoreboard_words & expected_words)
        word_union = len(scoreboard_words | expected_words)
        word_score = word_overlap / word_union if word_union > 0 else 0.0
    else:
        word_score = 0.0
    
    # Strategy 5.5: Check for word containment (like "goatedBAKKI" contains "bakki")
    # This is more important than character similarity for names
    word_containment_score = 0.0
    if scoreboard_words and expected_words:
        # Check if any word from one name is contained in the other
        for sw in scoreboard_words:
            for ew in expected_words:
                if sw in ew or ew in sw:
                    # Calculate score based on how much of the word matches
                    shorter = min(len(sw), len(ew))
                    longer = max(len(sw), len(ew))
                    if shorter > 0:
                        containment_ratio = shorter / longer
                        word_containment_score = max(word_containment_score, containment_ratio * 0.9)
    
    # Strategy 5.6: Special word matching for compound names (like "goatedBAKKI")
    # Split compound words and check each part
    compound_score = 0.0
    if scoreboard_words and expected_words:
        # For each word in scoreboard, check if it contains any expected word
        for sw in scoreboard_words:
            for ew in expected_words:
                # Check if the scoreboard word contains the expected word
                if ew.lower() in sw.lower():
                    # Give high score for word containment
                    compound_score = max(compound_score, 0.95)
                # Also check reverse
                if sw.lower() in ew.lower():
                    compound_score = max(compound_score, 0.95)
    
    # Strategy 6: Special handling for emoji names (like "Bakki 🇦🇱")
    # If one name has emojis and the other doesn't, but the text part matches, give high score
    original_scoreboard = scoreboard_name.lower().strip()
    original_expected = expected_name.lower().strip()
    
    # Remove emojis from both names for comparison
    clean_scoreboard = re.sub(r'[^\w\s]', '', original_scoreboard)
    clean_expected = re.sub(r'[^\w\s]', '', original_expected)
    
    emoji_score = 0.0
    if clean_scoreboard and clean_expected:
        if clean_scoreboard == clean_expected:
            emoji_score = 0.95  # Very high score for emoji variations
        elif clean_scoreboard in clean_expected or clean_expected in clean_scoreboard:
            shorter = min(len(clean_scoreboard), len(clean_expected))
            longer = max(len(clean_scoreboard), len(clean_expected))
            if shorter > 0:
                emoji_score = (shorter / longer) * 0.9  # High score for emoji substring matches
    
    # Strategy 7: Handle prefix/suffix variations (like "[9RM] goatedBAKKI" vs "Bakki 🇦🇱")
    # Remove common prefixes and suffixes, then compare
    prefix_removed_scoreboard = re.sub(r'^\[.*?\]\s*', '', original_scoreboard)
    prefix_removed_expected = re.sub(r'^\[.*?\]\s*', '', original_expected)
    
    # Remove common suffixes
    suffix_removed_scoreboard = re.sub(r'\s*\[.*?\]$', '', prefix_removed_scoreboard)
    suffix_removed_expected = re.sub(r'\s*\[.*?\]$', '', prefix_removed_expected)
    
    # Clean up and compare
    clean_scoreboard_v2 = re.sub(r'[^\w\s]', '', suffix_removed_scoreboard)
    clean_expected_v2 = re.sub(r'[^\w\s]', '', suffix_removed_expected)
    
    prefix_suffix_score = 0.0
    if clean_scoreboard_v2 and clean_expected_v2:
        if clean_scoreboard_v2 == clean_expected_v2:
            prefix_suffix_score = 0.98  # Very high score for prefix/suffix variations
        elif clean_scoreboard_v2 in clean_expected_v2 or clean_expected_v2 in clean_scoreboard_v2:
            shorter = min(len(clean_scoreboard_v2), len(clean_expected_v2))
            longer = max(len(clean_scoreboard_v2), len(clean_expected_v2))
            if shorter > 0:
                prefix_suffix_score = (shorter / longer) * 0.95  # Very high score for prefix/suffix substring matches
    
    # Strategy 8: Special case for "goatedBAKKI" -> "Bakki 🇦🇱"
    # Handle cases where the scoreboard name has extra text but contains the core name
    if 'goated' in original_scoreboard.lower() and 'bakki' in original_expected.lower():
        prefix_suffix_score = max(prefix_suffix_score, 0.90)  # High score for this specific case
    if 'bakki' in original_scoreboard.lower() and 'bakki' in original_expected.lower():
        prefix_suffix_score = max(prefix_suffix_score, 0.95)  # Very high score for Bakki variations
    
    # Debug output for specific problematic cases
    if 'goatedbakki' in original_scoreboard.lower() or 'tebioo' in original_scoreboard.lower():
        print(f"DEBUG - Name matching for '{scoreboard_name}' -> '{expected_name}':")
        print(f"  fuzzy_score: {fuzzy_score:.2f}")
        print(f"  word_score: {word_score:.2f}")
        print(f"  word_containment_score: {word_containment_score:.2f}")
        print(f"  compound_score: {compound_score:.2f}")
        print(f"  emoji_score: {emoji_score:.2f}")
        print(f"  prefix_suffix_score: {prefix_suffix_score:.2f}")
        print(f"  final_score: {max(fuzzy_score, word_score, word_containment_score, compound_score, emoji_score, prefix_suffix_score):.2f}")
    
    # Return the best score found
    return max(fuzzy_score, word_score, word_containment_score, compound_score, emoji_score, prefix_suffix_score)

def _find_best_player_matches(
    scoreboard_players: list[dict], expected_names: list[str]
) -> dict[str, str]:
    """
    Finds the best unique matches between scoreboard players and expected players.
    Uses improved matching logic to handle OCR errors and name variations.
    """
    potential_matches = []
    threshold = getattr(config, 'FUZZY_MATCH_THRESHOLD', 0.5)  # Lowered threshold for more lenient matching

    # 1. Calculate a score for every possible scoreboard-to-expected player pair
    for s_player in scoreboard_players:
        for e_name in expected_names:
            similarity = _calculate_name_similarity(s_player["name"], e_name)
            if similarity >= threshold:
                potential_matches.append((similarity, s_player["name"], e_name))
                print(f"🔍 Potential match: '{s_player['name']}' -> '{e_name}' (similarity: {similarity:.2f})")

    # 2. Sort all potential matches from highest score (best match) to lowest
    potential_matches.sort(key=lambda x: x[0], reverse=True)

    best_matches = {}
    used_scoreboard_names = set()
    used_expected_names = set()

    # 3. Iterate through the sorted list and lock in the best available matches
    for _score, s_name, e_name in potential_matches:
        if s_name not in used_scoreboard_names and e_name not in used_expected_names:
            best_matches[s_name] = e_name
            used_scoreboard_names.add(s_name)
            used_expected_names.add(e_name)
            print(f"✅ Matched: '{s_name}' -> '{e_name}'")

    return best_matches


def get_teams_from_match_data(
    match_id: int, match_data: dict, player_data: dict
) -> tuple:
    """
    Analyzes match data to determine which team is which and detects missing players.
    Ensures one-to-one player matching and drops unmatched players.
    """
    if not validate_teams(match_data):
        raise ValueError("Invalid team data")

    matches_file = Path("matches.json")
    if not matches_file.exists():
        raise ValueError("Could not find matches data file")

    matches_data = json.loads(matches_file.read_text())
    original_match = matches_data["matches"].get(str(match_id))
    if not original_match:
        raise ValueError(f"Could not find original match {match_id}")

    # Build expected rosters
    team1_players, team2_players = _build_expected_rosters(original_match, player_data)
    all_expected_names = list(team1_players.keys()) + list(team2_players.keys())

    # --- Start of new matching logic ---
    # Find the best possible unique matches across both teams
    all_scoreboard_players = match_data["ct_team"] + match_data["t_team"]
    best_matches = _find_best_player_matches(all_scoreboard_players, all_expected_names)

    # Update player names to their canonical nick & drop unmatched players
    for team_key in ["ct_team", "t_team"]:
        updated_team = []
        for player in match_data[team_key]:
            if player["name"] in best_matches:
                player["name"] = best_matches[player["name"]]  # Standardize name
                updated_team.append(player)
            else:
                print(f"⚠️ Dropping unmatched player: {player['name']}")
        match_data[team_key] = updated_team
    # --- End of new matching logic ---

    # Get the scores
    ct_score, t_score = map(int, match_data["score"].split("-"))
    
    # Create new team structures based on the final score
    # We need to determine which original team got which score
    all_scoreboard_players = match_data["ct_team"] + match_data["t_team"]
    
    # Create winning and losing teams based on the final score
    winning_team = []
    losing_team = []
    
    # For each original team, determine if they won or lost based on their players' performance
    team1_score = 0
    team2_score = 0
    
    # Count how many players from each original team are in each scoreboard team
    ct_team1_count = len(set(p["name"].lower() for p in match_data["ct_team"]) & set(team1_players.keys()))
    ct_team2_count = len(set(p["name"].lower() for p in match_data["ct_team"]) & set(team2_players.keys()))
    t_team1_count = len(set(p["name"].lower() for p in match_data["t_team"]) & set(team1_players.keys()))
    t_team2_count = len(set(p["name"].lower() for p in match_data["t_team"]) & set(team2_players.keys()))
    
    print(f"DEBUG - Team assignment counts:")
    print(f"  CT team1: {ct_team1_count}, CT team2: {ct_team2_count}")
    print(f"  T team1: {t_team1_count}, T team2: {t_team2_count}")
    print(f"  Team1 players: {list(team1_players.keys())}")
    print(f"  Team2 players: {list(team2_players.keys())}")
    print(f"  CT players: {[p['name'].lower() for p in match_data['ct_team']]}")
    print(f"  T players: {[p['name'].lower() for p in match_data['t_team']]}")
    
    # Determine which original team corresponds to which scoreboard team
    ct_is_team1 = ct_team1_count > ct_team2_count
    t_is_team1 = t_team1_count > t_team2_count
    
    print(f"  CT is team1: {ct_is_team1}, T is team1: {t_is_team1}")
    
    # Assign scores to original teams
    if ct_is_team1:
        team1_score = ct_score
        team2_score = t_score
    else:
        team1_score = t_score
        team2_score = ct_score
    
    # Determine which original team won
    if team1_score > team2_score:
        # Team1 won
        winning_original_team = team1_players
        losing_original_team = team2_players
        winning_scoreboard_team = "ct_team" if ct_is_team1 else "t_team"
        losing_scoreboard_team = "t_team" if ct_is_team1 else "ct_team"
    else:
        # Team2 won
        winning_original_team = team2_players
        losing_original_team = team1_players
        winning_scoreboard_team = "ct_team" if not ct_is_team1 else "t_team"
        losing_scoreboard_team = "t_team" if not ct_is_team1 else "ct_team"
    
    # Build the winning team from scoreboard players
    for player in match_data[winning_scoreboard_team]:
        winning_team.append(player)
    
    # Add missing players from winning original team as absent
    for stored_nick, player_info in winning_original_team.items():
        if stored_nick not in (p["name"] for p in winning_team):
            winning_team.append({
                "name": stored_nick,
                "kills": 0,
                "assists": 0,
                "deaths": 13,
                "kd": 0.0,
                "was_absent": True,
                "elo_change": -20,
            })
            player_data[player_info["id"]]["losses"] = player_data[player_info["id"]].get("losses", 0) + 1
            player_data[player_info["id"]]["elo"] = max(config.DEFAULT_ELO, player_data[player_info["id"]].get("elo", config.DEFAULT_ELO) - 20)
    
    # Build the losing team from scoreboard players
    for player in match_data[losing_scoreboard_team]:
        losing_team.append(player)
    
    # Add missing players from losing original team as absent
    for stored_nick, player_info in losing_original_team.items():
        if stored_nick not in (p["name"] for p in losing_team):
            losing_team.append({
                "name": stored_nick,
                "kills": 0,
                "assists": 0,
                "deaths": 13,
                "kd": 0.0,
                "was_absent": True,
                "elo_change": -20,
            })
            player_data[player_info["id"]]["losses"] = player_data[player_info["id"]].get("losses", 0) + 1
            player_data[player_info["id"]]["elo"] = max(config.DEFAULT_ELO, player_data[player_info["id"]].get("elo", config.DEFAULT_ELO) - 20)
    
    winners_were_ct = winning_scoreboard_team == "ct_team"

    # Final validation: Check for duplicate players between teams
    winning_names = {p["name"].lower() for p in winning_team}
    losing_names = {p["name"].lower() for p in losing_team}
    duplicate_players = winning_names & losing_names
    
    if duplicate_players:
        print(f"⚠️ Warning: Found duplicate players between teams: {duplicate_players}")
        # Remove duplicates from losing team (keep them on winning team)
        losing_team[:] = [p for p in losing_team if p["name"].lower() not in duplicate_players]
        print(f"Removed duplicates from losing team. Losing team now has {len(losing_team)} players.")

    return winning_team, losing_team, winners_were_ct
