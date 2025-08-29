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


def match_player_name(parsed_name: str, expected_name: str, threshold: float = 0.7) -> bool:
    """
    Leniently match parsed scoreboard names with expected player names.
    - Ignores symbols and case
    - Allows substring matches
    - Allows fuzzy similarity (>= threshold)
    """

    def clean_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())

    p = clean_name(parsed_name)
    e = clean_name(expected_name)

    if not p or not e:
        return False

    if p == e:
        return True
    if e in p or p in e:
        return True
    if SequenceMatcher(None, p, e).ratio() >= threshold:
        return True

    return False


def validate_teams(match_data: dict) -> bool:
    """
    Validates team data with simple rules:
    1. Maximum 5 players per team
    2. No duplicate names on either team
    3. No player can be on both teams
    """
    ct_team = match_data["ct_team"]
    t_team = match_data["t_team"]

    if len(ct_team) > 5:
        print(f"Error: CT team has too many players ({len(ct_team)})")
        return False
    if len(t_team) > 5:
        print(f"Error: T team has too many players ({len(t_team)})")
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
    team1_players = {}
    team2_players = {}
    for discord_id in original_match["team1"]:
        discord_id = str(discord_id)
        if discord_id in player_data:
            stored_nick = player_data[discord_id]["nick"]
            team1_players[stored_nick] = {"id": discord_id, "data": player_data[discord_id]}
    for discord_id in original_match["team2"]:
        discord_id = str(discord_id)
        if discord_id in player_data:
            stored_nick = player_data[discord_id]["nick"]
            team2_players[stored_nick] = {"id": discord_id, "data": player_data[discord_id]}

    all_expected_names = list(team1_players.keys()) + list(team2_players.keys())
    used_expected = set()

    # Match players uniquely
    for team_key in ["ct_team", "t_team"]:
        matched_players = []
        for player in match_data[team_key]:
            matched = False
            for expected_name in all_expected_names:
                if expected_name in used_expected:
                    continue
                if match_player_name(player["name"], expected_name):
                    player["name"] = expected_name
                    used_expected.add(expected_name)
                    matched_players.append(player)
                    matched = True
                    break
            if not matched:
                print(f"⚠️ Dropping unmatched player: {player['name']}")
        match_data[team_key] = matched_players  # keep only matched players

    # Decide CT vs T
    ct_team_players = {p["name"].lower(): p for p in match_data["ct_team"]}
    t_team_players = {p["name"].lower(): p for p in match_data["t_team"]}

    ct_matches_team1 = len(set(ct_team_players.keys()) & set(n.lower() for n in team1_players))
    ct_matches_team2 = len(set(ct_team_players.keys()) & set(n.lower() for n in team2_players))
    ct_is_team1 = ct_matches_team1 > ct_matches_team2

    ct_score, t_score = map(int, match_data["score"].split("-"))
    winners_were_ct = ct_score > t_score

    # Add absent players
    ct_expected = team1_players if ct_is_team1 else team2_players
    for stored_nick, player_info in ct_expected.items():
        if stored_nick not in (p["name"] for p in match_data["ct_team"]):
            match_data["ct_team"].append(
                {
                    "name": stored_nick,
                    "kills": 0,
                    "assists": 0,
                    "deaths": 13,
                    "kd": 0.0,
                    "was_absent": True,
                    "elo_change": -20,
                }
            )
            player_data[player_info["id"]]["losses"] = player_data[player_info["id"]].get(
                "losses", 0
            ) + 1
            player_data[player_info["id"]]["elo"] = max(
                config.DEFAULT_ELO, player_data[player_info["id"]].get("elo", config.DEFAULT_ELO) - 20
            )

    t_expected = team2_players if ct_is_team1 else team1_players
    for stored_nick, player_info in t_expected.items():
        if stored_nick not in (p["name"] for p in match_data["t_team"]):
            match_data["t_team"].append(
                {
                    "name": stored_nick,
                    "kills": 0,
                    "assists": 0,
                    "deaths": 13,
                    "kd": 0.0,
                    "was_absent": True,
                    "elo_change": -20,
                }
            )
            player_data[player_info["id"]]["losses"] = player_data[player_info["id"]].get(
                "losses", 0
            ) + 1
            player_data[player_info["id"]]["elo"] = max(
                config.DEFAULT_ELO, player_data[player_info["id"]].get("elo", config.DEFAULT_ELO) - 20
            )

    winning_team = match_data["ct_team"] if winners_were_ct else match_data["t_team"]
    losing_team = match_data["t_team"] if winners_were_ct else match_data["ct_team"]

    return winning_team, losing_team, winners_were_ct
