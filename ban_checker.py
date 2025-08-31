import json
import pathlib
from typing import Set

async def check_banned_players(guild) -> Set[str]:
    """
    Check for banned players and remove them from players.json.
    Returns set of removed player IDs.
    """
    players_file = pathlib.Path("players.json")
    if not players_file.exists():
        return set()
        
    try:
        # Load current players
        with open(players_file, 'r') as f:
            players = json.load(f)
            
        # Get banned members from guild
        removed_players = set()
        for user_id in list(players.keys()):
            try:
                member = await guild.fetch_member(int(user_id))
                if not member:  # Member not in guild, check ban status
                    try:
                        ban_entry = await guild.fetch_ban(int(user_id))
                        if ban_entry:  # Player is banned
                            del players[user_id]
                            removed_players.add(user_id)
                    except:
                        pass  # User not banned
            except:
                pass  # User not in guild
                
        # Save updated players file if any were removed
        if removed_players:
            with open(players_file, 'w') as f:
                json.dump(players, f, indent=2)
                
        return removed_players
        
    except Exception as e:
        print(f"Error checking banned players: {e}")
        return set()
