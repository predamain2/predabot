import json
import pathlib
import time
from typing import Set
from datetime import datetime

def print_progress_bar(current, total, start_time, prefix='Progress:', length=50):
    """Display a progress bar with completion time estimate"""
    percent = float(current) * 100 / total
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    
    # Calculate estimated time remaining
    elapsed_time = time.time() - start_time
    if current > 0:
        items_per_second = current / elapsed_time
        remaining_items = total - current
        eta_seconds = remaining_items / items_per_second if items_per_second > 0 else 0
        eta = f"ETA: {int(eta_seconds)}s"
    else:
        eta = "ETA: calculating..."
    
    print(f'\r{prefix} |{bar}| {percent:.1f}% - {current}/{total} checked - {eta}', end='', flush=True)
    if current == total:
        print()  # New line when complete

async def check_banned_players(guild) -> Set[str]:
    """
    Check for banned players and remove them from players.json.
    Returns set of removed player IDs.
    """
    start_time = time.time()
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f'[BAN CHECK] {timestamp} Starting banned player check')
    
    players_file = pathlib.Path("players.json")
    if not players_file.exists():
        print('[BAN CHECK] ❌ players.json not found')
        return set()
        
    try:
        # Load current players
        print('[BAN CHECK] 📝 Reading players.json...')
        with open(players_file, 'r') as f:
            players = json.load(f)
        player_count = len(players)
        print(f'[BAN CHECK] ✓ Loaded {player_count:,} players')
            
        # Get all bans in one API call instead of checking each user
        try:
            print('[BAN CHECK] 🔍 Fetching server bans...')
            ban_list = []
            ban_count = 0
            async for ban_entry in guild.bans():
                ban_list.append(ban_entry.user.id)
                ban_count += 1
                if ban_count % 10 == 0:  # Show progress every 10 bans
                    print(f'\r[BAN CHECK] Found {ban_count} bans...', end='', flush=True)
            print(f'\r[BAN CHECK] ✓ Found {ban_count} banned users')
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
