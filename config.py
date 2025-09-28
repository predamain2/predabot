TOKEN = ""
GUILD_ID = 1390707369712812124

# Channel IDs
LOBBY_VOICE_CHANNEL_ID = 1408840485337956475
LOBBY_TEXT_CHANNEL_ID = 1408840443847770344
SUBMIT_RESULTS_CHANNEL_ID = 1406361535751782420
LEADERBOARD_CHANNEL_ID = 1406361334018211901
REGISTER_CHANNEL_ID = 1408840623900852224
TIMEOUT_NOTIFICATION_CHANNEL_ID = 1411848489272475769  # Channel for timeout notifications

# Role IDs for levels 1..10
ROLE_LEVELS = {
    1: 1408841577094320238,
    2: 1408841624959844513,
    3: 1408841713216262246,
    4: 1408841751950524446,
    5: 1408841805428166858,
    6: 1408841857194266665,
    7: 1408841906900828252,
    8: 1408841962014113793,
    9: 1408842053202219089,
    10: 1408842093341970442
}

MAPS = ["Sandstone", "Rust", "Province", "Hanami", "Breeze", "Zone7", "Dune"]

ELO_TIERS = [
    (1, 100, 500),
    (2, 501, 750),
    (3, 751, 900),
    (4, 901, 1050),
    (5, 1051, 1200),
    (6, 1201, 1350),
    (7, 1351, 1530),
    (8, 1531, 1750),
    (9, 1751, 2000),
    (10, 2001, 99999)
]

DEFAULT_ELO = 100
AFK_PENALTY = -30

# Level-based ELO changes
LEVEL_ELO_CHANGES = {
    1: {'win': 75, 'lose': 0},
    2: {'win': 65, 'lose': -5},
    3: {'win': 50, 'lose': -10},
    4: {'win': 45, 'lose': -15},
    5: {'win': 40, 'lose': -17},
    6: {'win': 37, 'lose': -18},
    7: {'win': 34, 'lose': -20},
    8: {'win': 30, 'lose': -22},
    9: {'win': 26, 'lose': -24},
    10: {'win': 26, 'lose': -29}
}

def get_level_from_elo(elo: int) -> int:
    """Get player's level based on their ELO."""
    for level, min_elo, max_elo in ELO_TIERS:
        if min_elo <= elo <= max_elo:
            return level
    return 1  # Default to level 1 if no match found

def get_elo_change(level: int, is_winner: bool) -> int:
    """Calculate ELO change based on player's current level and match result."""
    return LEVEL_ELO_CHANGES[level]['win' if is_winner else 'lose']

OCR_SPACE_API_KEY = ""

# Player matching settings
FUZZY_MATCH_THRESHOLD = 0.70  # Balanced threshold for player name matching

DEBUG_MODE = False
DEBUG_PLAYERS = 3