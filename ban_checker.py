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
        print('[BAN CHECK] players.json not found')
        return set()
        
    try:
        # Load current players
        print('[BAN CHECK] Reading players.json...')
        with open(players_file, 'r') as f:
            players = json.load(f)
        print(f'[BAN CHECK] Loaded {len(players)} players from players.json')
            
        # Get all bans in one API call instead of checking each user
        try:
            print('[BAN CHECK] Fetching server bans...')
            ban_list = []
            async for ban_entry in guild.bans():
                ban_list.append(ban_entry.user.id)
            bans = ban_list
            print(f'[BAN CHECK] Found {len(bans)} banned users in server')
        except Exception as e:
            print(f'[BAN CHECK] Failed to fetch bans: {e}')
            
        # Check which players are banned
        removed_players = set()
        for user_id in list(players.keys()):
            try:
                if int(user_id) in bans:  # Player is banned
                    del players[user_id]
                    removed_players.add(user_id)
            except ValueError:
                continue  # Invalid user ID
                
        # Save updated players file if any were removed
        if removed_players:
            with open(players_file, 'w') as f:
                json.dump(players, f, indent=2)
                
        return removed_players
        
    except Exception as e:
        print(f"Error checking banned players: {e}")
        return set()
