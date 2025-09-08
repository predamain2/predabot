# main.py
import discord
from discord.ext import commands
from discord.ui import View, Select, Button
import asyncio, random, uuid, json, pathlib, re, time
import sys
from datetime import datetime, timedelta

# ---------- Intents & Bot ----------
intents = discord.Intents.default()
intents.members = True            # privileged; enable in Dev Portal
intents.presences = False
intents.message_content = True
intents.voice_states = True

# Ensure that other modules importing 'main' get this running module instance (avoid double-import when run as __main__)
sys.modules['main'] = sys.modules.get(__name__)

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Persistence helpers ----------
DATA_FILE = pathlib.Path("players.json")
RESULTS_FILE = pathlib.Path("results.json")  # NEW: scoreboard submissions
PARTIES_FILE = pathlib.Path("parties.json")  # NEW: party data
TIMEOUTS_FILE = pathlib.Path("timeouts.json")

def load_players():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            return {}
    return {}

def load_parties():
    if PARTIES_FILE.exists():
        try:
            return json.loads(PARTIES_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_parties():
    PARTIES_FILE.write_text(json.dumps(party_data, indent=2))

def save_players():
    DATA_FILE.write_text(json.dumps(player_data, indent=2))

def load_results():
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_results():
    RESULTS_FILE.write_text(json.dumps(results_data, indent=2))

def save_timeouts():
    """Save timeout data to file"""
    TIMEOUTS_FILE.write_text(json.dumps(timeouts, indent=2))

def load_timeouts():
    """Load timeout data from file"""
    if TIMEOUTS_FILE.exists():
        try:
            return json.loads(TIMEOUTS_FILE.read_text())
        except Exception:
            return {}
    return {}

# ---------- Constants ----------
COMMANDS_CHANNEL_ID = 1391548335478800494  # The channel where party messages are sent
TIMEOUT_DURATION = 300  # 5 minutes in seconds

# ---------- In-memory DB ----------
player_data = load_players()       # keyed by string id
party_data = load_parties()        # keyed by leader_id -> {members: [member_ids], team: None}
active_picks = {}                  # keyed by text channel id -> pick session
lobby_status = {}                  # keyed by text channel id -> {message_id, state: 'waiting'/'picking'/'mapban'}

# NEW: in-memory submission trackers
results_data = load_results()      # dict: match_id -> submission dict
active_submissions = set()         # match_ids currently being submitted (concurrency lock)
pending_upload = {}                # user_id -> {channel_id, match_id, started_at}

# Party invitation system
party_invites = {}  # keyed by invited_id -> {leader_id, expires_at}

# Timeout system
timeouts = load_timeouts() # user_id -> timeout_end_timestamp

# ---------- Utility helpers ----------
def staff_mod_owner_only():
    """Slash-command check: allow only users with Staff/Moderator/Owner roles configured in config.py"""
    async def predicate(interaction: discord.Interaction) -> bool:
        role_ids = {
            int(getattr(config, 'OWNER_ROLE_ID', 0) or 0),
            int(getattr(config, 'STAFF_ROLE_ID', 0) or 0),
            int(getattr(config, 'MODERATOR_ROLE_ID', 0) or 0),
        }
        role_ids.discard(0)
        if not role_ids:
            # If not configured, fall back to administrators only
            return interaction.user.guild_permissions.administrator
        return any(getattr(r, 'id', 0) in role_ids for r in getattr(interaction.user, 'roles', []))
    return discord.app_commands.check(predicate)
def is_player_timed_out(user_id):
    """Check if a player is currently timed out"""
    current_time = time.time()
    timeout_end = timeouts.get(str(user_id))
    
    if timeout_end and current_time < timeout_end:
        return True
    elif timeout_end and current_time >= timeout_end:
        # Timeout has expired, remove it
        del timeouts[str(user_id)]
        save_timeouts()
    
    return False

def get_timeout_remaining(user_id):
    """Get remaining timeout time in seconds"""
    current_time = time.time()
    timeout_end = timeouts.get(str(user_id))
    
    if timeout_end and current_time < timeout_end:
        return int(timeout_end - current_time)
    return 0

def add_timeout(user_id, duration=TIMEOUT_DURATION):
    """Add a timeout for a user"""
    timeout_end = time.time() + duration
    timeouts[str(user_id)] = timeout_end
    save_timeouts()
    return timeout_end

def key_of(m):
    """Return a stable string key for member-like objects (Member or FakeMember or string/int)"""
    print(f"\n=== key_of called with: {m} (type: {type(m)}) ===")
    if isinstance(m, (int,)):
        result = str(m)
        print(f"key_of: Input was integer, returning: {result}")
        return result
    try:
        result = str(getattr(m, "id"))
        print(f"key_of: Got ID from object: {result}")
        return result
    except Exception as e:
        result = str(m)
        print(f"key_of: Fell back to string conversion due to {type(e)}: {result}")
        return result

def id_of(m):
    """Return integer id where possible. For FakeMember negative ids are preserved."""
    try:
        return int(getattr(m, "id", m))
    except Exception:
        return int(m)

def ensure_player(m):
    k = key_of(m)
    if k not in player_data:
        player_data[k] = {
            "nick": getattr(m, "display_name", f"Player{k}"),
            "id": f"AUTO_{k}",
            "elo": config.DEFAULT_ELO,
            "level": 1,
            "wins": 0,
            "losses": 0
        }
        save_players()
    return player_data[k]

def get_player_avg_kills(player_name: str) -> float:
    """Calculate a player's average kills per match from results.json by name."""
    if not RESULTS_FILE.exists():
        return 0.0

    try:
        with RESULTS_FILE.open("r", encoding="utf-8") as f:
            results = json.load(f)
    except Exception:
        return 0.0

    total_kills = 0
    matches_count = 0

    for match in results.values():  # dict, not list
        for team_key in ("winning_team", "losing_team"):
            for p in match.get(team_key, []):
                if p.get("name") == player_name:
                    if "kills" in p:
                        total_kills += int(p["kills"])
                        matches_count += 1

    return round(total_kills / matches_count, 1) if matches_count > 0 else 0.0

def get_player_winrate(player_id):
    """Calculate winrate percentage for a player"""
    p = player_data.get(str(player_id))
    if not p:
        return 0.0
    total_games = p.get('wins', 0) + p.get('losses', 0)
    if total_games == 0:
        return 0.0
    return round((p.get('wins', 0) / total_games) * 100, 1)

def get_level_role(level):
    """Get the role ID for a given level from config"""
    return config.ROLE_LEVELS.get(level, config.ROLE_LEVELS[1])  # Fallback to level 1 if level not found

def label_for(m):
    """Get the display label for a player in the team list"""
    p = player_data.get(key_of(m)) or ensure_player(m)
    return p['nick']  # Just show the nickname for now

class FakeMember:
    def __init__(self, idx):
        self.id = -(idx+1)
        self.display_name = f"FakePlayer{idx+1}"
        self.mention = f"@{self.display_name}"
        self.bot = False


import aiohttp
import os
import config
from scoreboard_parser import run_llamaocr
from match_processor import calculate_rating
import tracemalloc
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

async def render_html_to_image(match_data, output_path, html_template='scoreboard.html'):
    """Render templates/scoreboard.html (or scoreboard.html in project root) with match_data and save a PNG using Playwright."""
    print("Rendering scoreboard with data:", json.dumps(match_data, indent=2))
    
    # Process each player's rating
    for team in ['ct_team', 't_team']:
        if team in match_data:
            for player in match_data[team]:
                try:
                    # If rating is already calculated
                    if isinstance(player.get('rating'), (int, float)):
                        player['rating'] = f"{float(player['rating']):.2f}"
                    # If no rating but has stats, calculate it
                    elif all(key in player for key in ['kills', 'deaths', 'assists']) and not player.get('was_absent', False):
                        ct_score, t_score = map(int, match_data['score'].split('-'))
                        total_rounds = ct_score + t_score
                        rating = calculate_rating(
                            kills=player['kills'],
                            deaths=player['deaths'],
                            assists=player['assists'],
                            rounds_played=total_rounds
                        )
                        player['rating'] = f"{rating:.2f}"
                    else:
                        player['rating'] = "0.00"
                except Exception as e:
                    print(f"Error calculating rating for player {player.get('name')}: {e}")
                    player['rating'] = "0.00"
    
    # First save the match_data to HTML file
    with open(html_template, 'r', encoding='utf-8') as f:
        template_content = f.read()

    # Create Jinja2 environment and template
    env = Environment(loader=FileSystemLoader('.'))
    template = env.from_string(template_content)
    html = template.render(match_data=match_data)
    
    # Save the rendered HTML for debugging
    tmp_html = 'temp_scoreboard.html'
    with open(tmp_html, 'w', encoding='utf-8') as f:
        f.write(html)
        
    print(f"Saved rendered HTML to {tmp_html}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_viewport_size({'width': 1400, 'height': 900})
        await page.goto('file://' + os.path.abspath(tmp_html))
        
        # Wait longer and ensure the page is fully loaded
        await asyncio.sleep(1)
        await page.wait_for_load_state('networkidle')
        
        # Take the screenshot
        await page.screenshot(path=output_path, full_page=True)
        await browser.close()
        
    print(f"Generated scoreboard image: {output_path}")

tracemalloc.start()

class HostInfoButton(discord.ui.Button):
    def __init__(self, host_mention, host_name, host_id):
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Get Host Information",
            emoji="ℹ️",
            custom_id="host_info"
        )
        self.host_mention = host_mention
        self.host_name = host_name
        self.host_id = host_id

    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Match Host Information",
            color=discord.Color.blue()
        )
        
        info_lines = [
            f"👤 {self.host_mention}",
            f"📝 Nickname: **{self.host_name}**"
        ]
        
        if self.host_id and self.host_id.isdigit():
            info_lines.extend([
                f"🆔 **`{self.host_id}`**",
                "",
                "**Profile Link:**",
                f"https://link.standoff2.com/en/profile/view/{self.host_id}"
            ])
        
        embed.description = "\n".join(info_lines)
        embed.set_footer(text="Powered by Arena | Developed by narcissist.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class HostInfoView(discord.ui.View):
    def __init__(self, host_mention, host_name, host_id):
        super().__init__(timeout=None)
        self.add_item(HostInfoButton(host_mention, host_name, host_id))

# ---------- Registration modal & view ----------
class RegisterModal(discord.ui.Modal, title="Player Registration"):
    nick = discord.ui.TextInput(
        label="Standoff 2 Nickname",
        placeholder="Enter your exact in-game nickname (case sensitive)",
        min_length=1,
        max_length=32
    )
    pid = discord.ui.TextInput(
        label="Standoff 2 ID",
        placeholder="Enter your player ID (numbers only, e.g. 123456789)",
        min_length=1,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        # Get the values from TextInput fields
        nick_value = self.nick.value.strip()
        pid_value = self.pid.value.strip()

        # Defer the response immediately
        await interaction.response.defer(ephemeral=True)

        # Validate that ID contains only numbers
        if not pid_value.isdigit():
            await interaction.followup.send("❌ Player ID must contain only numbers.", ephemeral=True
            )
            return

        # Prevent duplicate nick/id registration across different users
        for k, v in player_data.items():
            if (v.get("nick","").lower() == nick_value.lower() or v.get("id") == pid_value):
                # Allow same user to update; block others
                if k != str(member.id):
                    await interaction.followup.send(
                        "❌ That nickname or ID is already registered by another user.", ephemeral=True
                    )
                    return

        # Create player data
        player_data[str(member.id)] = {
            "nick": nick_value,
            "id": pid_value,
            "level": 1,
            "elo": config.DEFAULT_ELO,
            "wins": 0,
            "losses": 0,
            "banned": False
        }
        save_players()


        # Try changing nickname and adding level 1 role and registered role (best-effort)
        success_msg = f"✅ Registered as **{nick_value}**"

        # Only attempt nickname change if member isn't the guild owner
        try:
            if not member.guild_permissions.administrator:
                await member.edit(nick=nick_value)
            else:
                success_msg += "\nℹ️ Server administrator - please update your nickname manually to match your registration."
        except discord.errors.Forbidden:
            success_msg += "\nℹ️ I don't have permission to change nicknames. Please update your nickname manually."
        except Exception as e:
            success_msg += f"\nℹ️ Failed to update nickname: {e}"

        # Add roles
        try:
            roles_to_add = []
            level1_role = guild.get_role(config.ROLE_LEVELS.get(1))
            if level1_role:
                roles_to_add.append(level1_role)
            registered_role = guild.get_role(1408841094619332778)
            if registered_role:
                roles_to_add.append(registered_role)
            if roles_to_add:
                await member.add_roles(*roles_to_add)
        except Exception as e:
            success_msg += f"\nℹ️ Failed to add roles: {e}"

        await interaction.followup.send(success_msg, ephemeral=True)
        
        # Update leaderboard after registration
        general_cog = bot.get_cog('General')
        if general_cog:
            await general_cog.update_leaderboard_if_needed(guild)

class RegisterView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Register", style=discord.ButtonStyle.green, custom_id="standoff_register_btn")
    async def reg_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RegisterModal())

# ---------- Draft view (Select menu) ----------
class DraftView(View):
    def __init__(self, channel_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.build_select()
        # Add ReHost vote button
        self.add_item(self.RehostButton(self.channel_id))

    def build_select(self):
        # clear previous items
        for c in list(self.children):
            self.remove_item(c)

        st = active_picks.get(self.channel_id)
        if not st:
            return

        # Build a fast lookup of IDs already on teams to avoid duplicates
        team_ids = {str(getattr(m, "id", m)) for m in (st["team1"] + st["team2"])}

        opts = []
        for m in st["waiting"]:
            mid = str(getattr(m, "id", m))
            if mid in team_ids:
                continue  # never show someone who is already in a team

            # If this member is in a party AND their leader is already on any team, hide them
            hide_due_to_party = False
            for leader_id, party in party_data.items():
                if mid in party.get("members", []):
                    if leader_id in team_ids:
                        hide_due_to_party = True
                        break
            if hide_due_to_party:
                continue

            pdata = player_data.get(str(mid)) or ensure_player(m)
            avg_kills = get_player_avg_kills(pdata["nick"])
            winrate = get_player_winrate(mid)
            lvl = int(pdata.get("level", 1))
            opts.append(
                discord.SelectOption(
                    label=pdata["nick"],
                    description=f"Elo: {pdata.get('elo', 1000)} | Avg: {avg_kills:.1f} | WR: {winrate:.1f}%",
                    value=mid,
                    emoji=discord.PartialEmoji(
                        name=f"level{lvl}",
                        id=int(config.LEVEL_EMOJIS.get(lvl, 0) or 0)
                    )
                )
            )

        if not opts:
            return

        select = Select(placeholder="Pick a player...", min_values=1, max_values=1, options=opts)

        async def on_select(interaction: discord.Interaction):
            await handle_pick_select(interaction, self.channel_id, select.values[0])

        select.callback = on_select
        self.add_item(select)

    class RehostButton(discord.ui.Button):
        def __init__(self, channel_id: int):
            super().__init__(label="ReHost", style=discord.ButtonStyle.danger)
            self.channel_id = channel_id

        async def callback(self, interaction: discord.Interaction):
            st = active_picks.get(self.channel_id)
            if not st:
                await interaction.response.send_message("No active draft.", ephemeral=True, delete_after=5)
                return
            # Ensure vote store
            if "rehost_votes" not in st:
                st["rehost_votes"] = set()
            voter_id = str(interaction.user.id)
            # Prevent double voting
            if voter_id in st["rehost_votes"]:
                await interaction.response.send_message("You already voted to rehost.", ephemeral=True, delete_after=5)
                return

            # Must be in lobby voice channel
            vc = interaction.guild.get_channel(config.LOBBY_VOICE_CHANNEL_ID)
            if not vc or interaction.user not in vc.members:
                await interaction.response.send_message("Join the lobby voice to vote.", ephemeral=True, delete_after=5)
                return

            # Record vote
            st["rehost_votes"].add(voter_id)

            # Determine threshold: 70% of non-bot members (default 7/10)
            nonbots = [m for m in vc.members if not getattr(m, 'bot', False)]
            import math
            threshold = max(1, math.ceil(0.7 * len(nonbots)))

            # Update message with vote progress
            chan = bot.get_channel(self.channel_id)
            msg_id = lobby_status.get(self.channel_id, {}).get("message_id")
            if chan and msg_id:
                try:
                    msg = await chan.fetch_message(msg_id)
                    embed = build_roster_embed(st)
                    await msg.edit(embed=embed, view=DraftView(self.channel_id))
                except Exception:
                    pass

            if len(st["rehost_votes"]) >= threshold:
                # Trigger rehost: reset captains and restart picking
                prev_caps = (st.get("captain_ct"), st.get("captain_t"))
                st["rehost_votes"] = set()
                try:
                    await interaction.response.send_message("Rehosting…", ephemeral=True, delete_after=3)
                except Exception:
                    pass
                await rehost_picking(chan, prev_caps)
            else:
                await interaction.response.send_message(
                    f"ReHost vote registered {len(st['rehost_votes'])}/{threshold}.",
                    ephemeral=True,
                    delete_after=5
                )


async def handle_pick_select(interaction: discord.Interaction, channel_id: int, selected_id: str):
    st = active_picks.get(channel_id)
    if not st:
        await interaction.response.send_message("Draft isn't active here.", ephemeral=True)
        return

    # Lock per-lobby to avoid concurrency races
    async with st["lock"]:
        # Identify current picker (captain_ct or captain_t)
        current_picker = st.get("pick_turn")
        if not current_picker:
            await interaction.response.send_message("No picker is set.", ephemeral=True)
            return

        if str(getattr(interaction.user, "id", interaction.user)) != str(getattr(current_picker, "id", current_picker)):
            await interaction.response.send_message("It's not your turn to pick.", ephemeral=True)
            return

        # Find the selected member object in waiting
        picked = None
        for m in list(st["waiting"]):
            if str(getattr(m, "id", m)) == str(selected_id):
                picked = m
                break
        if not picked:
            await interaction.response.send_message("That player is no longer available.", ephemeral=True)
            return

        # Determine which team is picking now
        ct_turn = (str(getattr(current_picker, "id", current_picker)) 
                   == str(getattr(st["captain_ct"], "id", st["captain_ct"])))
        picking_team = st["team1"] if ct_turn else st["team2"]
        other_team   = st["team2"] if ct_turn else st["team1"]

        # Collect party member(s) of the picked player that must follow (max 1 member per your system)
        party_followers = []
        picked_id = str(getattr(picked, "id", picked))
        for leader_id, party in party_data.items():
            members = party.get("members", [])
            if picked_id in members:
                # Add the other member if present, not already on any team, and still in waiting
                for member_id in members:
                    if member_id == picked_id:
                        continue
                    # skip if already on a team
                    already_on_team = any(
                        str(getattr(tm, "id", tm)) == member_id for tm in (st["team1"] + st["team2"])
                    )
                    if already_on_team:
                        continue
                    # find in waiting
                    for m in st["waiting"]:
                        if str(getattr(m, "id", m)) == member_id:
                            party_followers.append(m)
                            break
                break  # only one party applies

        # Enforce team size (5v5)
        if len(picking_team) + 1 + len(party_followers) > 5:
            await interaction.response.send_message(
                "Cannot pick this player—their party would exceed the 5-player limit.",
                ephemeral=True
            )
            return

        # Assign picked + their party follower(s)
        picking_team.append(picked)
        st["waiting"].remove(picked)
        for m in party_followers:
            picking_team.append(m)
            st["waiting"].remove(m)

        # Switch turn **after** the pick:
        # If only one side had a party added at start, we already balanced first turn in start_picking_stage.
        st["pick_turn"] = st["captain_t"] if ct_turn else st["captain_ct"]

        # Auto-assign the very last remaining player (if any) to the team with fewer players
        if len(st["waiting"]) == 1:
            last_player = st["waiting"].pop(0)
            if len(st["team1"]) <= len(st["team2"]):
                st["team1"].append(last_player)
            else:
                st["team2"].append(last_player)

        # If everyone has been assigned, proceed to Map Ban
        chan = bot.get_channel(channel_id)
        msg_id = lobby_status.get(channel_id, {}).get("message_id")
        msg = None
        if msg_id:
            try:
                msg = await chan.fetch_message(msg_id)
            except Exception:
                msg = None

        if not st["waiting"]:
            # mark UI
            if msg:
                try:
                    embed = discord.Embed(title="✅ Teams complete — proceeding to Map Ban", color=discord.Color.green())
                    if "match_id" in st:
                        embed.add_field(name="Match ID", value=f"`{st['match_id']}`", inline=False)
                    await msg.edit(embed=embed, view=None, content=None)
                except Exception:
                    pass

            # Start map ban via the MapBan cog (uses your existing signature)
            map_cog = bot.get_cog("MapBan")
            if map_cog:
                try:
                    await map_cog.start_map_ban(
                        chan,
                        [st["captain_ct"], st["captain_t"]],
                        st["team1"],
                        st["team2"],
                        teams_message=msg
                    )
                except Exception as e:
                    await chan.send("⚠️ Failed to start MapBan: " + str(e))
            else:
                await chan.send("⚠️ MapBan cog not loaded.")

            # cleanup
            active_picks.pop(channel_id, None)
            lobby_status[channel_id] = {"message_id": msg_id, "state": "mapban"}
            await interaction.response.defer()
            return

        # Otherwise, refresh the roster message and keep drafting
        new_embed = build_roster_embed(st)
        new_view = DraftView(channel_id)
        if msg:
            try:
                await msg.edit(embed=new_embed, view=new_view)
            except Exception:
                await chan.send(embed=new_embed, view=new_view)
        else:
            newmsg = await chan.send(embed=new_embed, view=new_view)
            st["message_id"] = newmsg.id
            lobby_status[channel_id] = {"message_id": newmsg.id, "state": "picking"}

        await interaction.response.defer()

# ---------- Embeds / announce ----------
def build_roster_embed(st):
    e = discord.Embed(title="Match Draft — Pick Phase", color=discord.Color.blurple())
    pick_turn = st.get('pick_turn')
    pick_mention = getattr(pick_turn, "mention", f"<@{id_of(pick_turn)}>")
    match_id = st.get('match_id', '—')
    e.description = f"Match ID: `{match_id}`\nNext to pick: {pick_mention}"
    
    # Format team lists with custom level emojis
    def format_team_list(team):
        lines = []
        for m in team:
            p = player_data.get(key_of(m)) or ensure_player(m)
            level = p.get('level', 1)
            level_emoji = f"<:level{level}:{config.LEVEL_EMOJIS.get(level, '')}>"
            lines.append(f"{level_emoji} {p['nick']}")
        return "\n".join(lines) if lines else "—"
    
    t1 = format_team_list(st['team1'])
    t2 = format_team_list(st['team2'])
    
    e.add_field(name="Team 1 (CT)", value=t1, inline=True)
    e.add_field(name="Team 2 (T)", value=t2, inline=True)
    
    # Add empty field to maintain 2-column layout
    votes = st.get("rehost_votes")
    if isinstance(votes, set):
        e.add_field(name="ReHost votes", value=f"{len(votes)} vote(s)", inline=True)
    else:
        e.add_field(name="\u200b", value="\u200b", inline=True)
    return e

async def rehost_picking(channel: discord.TextChannel, previous_captains):
    """Re-select captains while respecting party logic, then restart picking with the same lobby members."""
    try:
        vc = channel.guild.get_channel(config.LOBBY_VOICE_CHANNEL_ID)
        if not vc:
            await channel.send("⚠️ Rehost failed: Lobby voice channel not found.")
            return
        # Shuffle participants to avoid selecting same captains repeatedly
        import random as _r
        members = [m for m in vc.members if not getattr(m, 'bot', False)]
        _r.shuffle(members)
        # Start a fresh picking stage with the current members; start_picking_stage already handles parties
        await start_picking_stage(channel, members)
    except Exception as e:
        try:
            await channel.send(f"⚠️ Rehost failed: {e}")
        except Exception:
            pass

async def announce_teams_final(channel: discord.TextChannel, match_id, chosen_map, st):
    print("\n=== Starting Team Announcement ===")
    print(f"Match ID: {match_id}")
    print(f"Chosen Map: {chosen_map}")
    print("State data:", st)
    print(f"Player data dictionary: {player_data}")
    print(f"Player data keys available: {list(player_data.keys())}")
    
    # Format team data for template
    def format_player_data(members):
        players = []
        for m in members:
            pdata = player_data.get(key_of(m), {})
            name = pdata.get('nick', getattr(m, 'display_name', str(key_of(m))))
            discord_id = str(key_of(m))
            players.append({
                'name': name,
                'mention': f"<@{discord_id}>",
                'id': pdata.get('id', ''),
                'level': pdata.get('level', 1),
            })
        return players
    
    team1 = format_player_data(st['team1'])
    team2 = format_player_data(st['team2'])
    
    # Calculate team statistics
    def calculate_team_stats(team_members):
        """Calculate team statistics from player data"""
        total_elo = 0
        total_wins = 0
        total_games = 0
        valid_players = 0
        
        for member in team_members:
            pdata = player_data.get(key_of(member), {})
            if pdata:
                # Get ELO (default to 1000 if not set)
                elo = pdata.get('elo', 1000)
                total_elo += elo
                
                # Get wins and losses
                wins = pdata.get('wins', 0)
                losses = pdata.get('losses', 0)
                total_wins += wins
                total_games += (wins + losses)
                valid_players += 1
        
        if valid_players == 0:
            return {
                'avg_elo': 1000,
                'total_wins': 0,
                'win_rate': 0,
                'strength_percentage': 50
            }
        
        # Calculate averages
        avg_elo = total_elo / valid_players
        win_rate = (total_wins / total_games * 100) if total_games > 0 else 0
        
        # Calculate strength percentage based on ELO and win rate
        # ELO factor (0-50%): Higher ELO = higher strength
        elo_factor = min(50, (avg_elo - 1000) / 20)  # 20 ELO = 1% strength
        
        # Win rate factor (0-50%): Higher win rate = higher strength  
        win_rate_factor = min(50, win_rate / 2)  # 2% win rate = 1% strength
        
        strength_percentage = max(10, min(90, 50 + elo_factor + win_rate_factor))
        
        return {
            'avg_elo': int(avg_elo),
            'total_wins': total_wins,
            'win_rate': round(win_rate, 1),
            'strength_percentage': int(strength_percentage)
        }
    
    # Calculate stats for both teams
    team1_stats = calculate_team_stats(st['team1'])
    team2_stats = calculate_team_stats(st['team2'])
    
    # Get the host (captain_ct) information
    host = st['captain_ct']
    host_key = str(key_of(host))  # Convert to string ID
    
    # Get host data from player_data
    host_data = player_data.get(host_key, {})
    
    # Get their Standoff2 ID
    host_id = host_data.get('id', '').strip()
    
    # If host doesn't have a valid ID, try to find a replacement
    if not host_id or not host_id.isdigit():
        # Try the other captain first
        other_captain = st['captain_t']
        other_captain_key = str(key_of(other_captain))
        other_captain_data = player_data.get(other_captain_key, {})
        other_captain_id = other_captain_data.get('id', '').strip()
        
        if other_captain_id and other_captain_id.isdigit():
            # Use the other captain as host
            host = other_captain
            host_key = other_captain_key
            host_data = other_captain_data
            host_id = other_captain_id
        else:
            # Find any player with a valid ID from all participants
            all_players = st['team1'] + st['team2']
            for player in all_players:
                player_key = str(key_of(player))
                player_data_item = player_data.get(player_key, {})
                player_id = player_data_item.get('id', '').strip()
                if player_id and player_id.isdigit():
                    # Use this player as host
                    host = player
                    host_key = player_key
                    host_data = player_data_item
                    host_id = player_id
                    break
    
    # Get the display name - use their registered nick, fallback to Discord name if no nick is set
    host_name = host_data.get('nick')  # First try to get registered nickname
    if not host_name:
        if isinstance(host, discord.Member):
            host_name = host.display_name  # Fallback to Discord display name
        else:
            host_name = str(host_key)  # Last resort fallback to ID
    
    # Add host info to the template
    if isinstance(host, discord.Member):
        host_mention = host.mention
    else:
        host_mention = f"<@{host_key}>"
    
    # Prepare match data for the template
    match_data = {
        'match_id': match_id,
        'map_name': chosen_map,
        'team1': team1 or [],
        'team2': team2 or [],
        'team1_stats': team1_stats,
        'team2_stats': team2_stats,
        'host_mention': host_mention,
        'host_name': host_name,
        'host_id': host_id if host_id and host_id.isdigit() else ''
    }
    
    # Render HTML to image
    image_file = 'temp_match.png'
    await render_html_to_image(match_data, image_file, 'match_template.html')
    
    # Create the Discord attachment
    file = discord.File(image_file)
    
    # Create an empty embed with just the button hint
    embed = discord.Embed(
        description="Click the button below to see host information",
        color=discord.Color.blue()
    )
    
    # Create the host profile button view
    view = HostInfoView(host_mention, host_name, host_id)
    
    # Replace or create the announcement message
    msg_id = lobby_status.get(channel.id, {}).get("message_id")
    print(f"Debug - Message ID: {msg_id}")
    
    try:
        if msg_id:
            print("Debug - Attempting to edit existing message")
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=embed, attachments=[file], view=view, content=None)
            print("Debug - Message edited successfully")
        else:
            print("Debug - Sending new message")
            msg = await channel.send(embed=embed, file=file, view=view)
            print("Debug - New message sent")
    except Exception as e:
        print(f"Debug - Error handling message: {e}")
        print("Debug - Sending new message as fallback")
        msg = await channel.send(embed=embed, file=file, view=view)
        print("Debug - New message sent")

    # Clean up the temporary files
    try:
        os.remove(image_file)
    except Exception as e:
        print(f"Warning: Could not remove temp files: {e}")
    
    return msg

# ---------- Start picking stage ----------
async def start_picking_stage(channel: discord.TextChannel, member_list):
    participants = []
    real_members = []

    # Build participant lists and ensure we have player_data
    for m in member_list:
        if getattr(m, "bot", False):
            continue
        participants.append(m)
        real_members.append(m)
        ensure_player(m)

    # Fill with fakes if needed (keeps your debug behavior)
    target = 10
    if config.DEBUG_MODE:
        target = config.DEBUG_PLAYERS if config.DEBUG_PLAYERS else 2
    idx = 0
    while len(participants) < target:
        fake = FakeMember(idx)
        participants.append(fake)
        ensure_player(fake)
        idx += 1

    # Helpers
    def mid(x): return str(getattr(x, "id", x))

    # Index membership -> leader and party lists
    leader_ids = set(party_data.keys())
    member_to_party_leader = {}
    party_member_ids = set()
    for leader_id, party in party_data.items():
        for m_id in party.get("members", []):
            member_to_party_leader[m_id] = leader_id
            party_member_ids.add(m_id)

    # Figure out which leaders are present in this lobby
    present_leaders = []
    for p in participants:
        if mid(p) in leader_ids:
            present_leaders.append(p)

    # Choose captains according to your rules
    if len(present_leaders) >= 2:
        captain1 = present_leaders[0]  # CT
        captain2 = present_leaders[1]  # T
    elif len(present_leaders) == 1:
        captain1 = present_leaders[0]  # CT is the party leader
        # pick an independent as the other captain
        independents = [p for p in participants if mid(p) not in party_member_ids and mid(p) != mid(captain1)]
        random.shuffle(independents)
        captain2 = independents[0] if independents else next(p for p in participants if mid(p) != mid(captain1))
    else:
        # no leaders present, fall back to first two non-party participants
        non_party = [p for p in participants if mid(p) not in party_member_ids]
        if len(non_party) < 2:
            non_party = participants[:]
        captain1, captain2 = non_party[0], non_party[1]

    team1 = [captain1]  # CT
    team2 = [captain2]  # T
    waiting = []

    # Auto-assign each captain's single party member (if present & in the lobby)
    def auto_assign_party_member(captain, team):
        cap_id = mid(captain)
        if cap_id in party_data:
            # find the 1 member that is not the leader
            for member_id in party_data[cap_id].get("members", []):
                if member_id != cap_id:
                    # is this member here?
                    for m in participants:
                        if mid(m) == member_id:
                            # avoid dupes
                            if all(mid(x) != member_id for x in (team1 + team2)):
                                team.append(m)
                            return

    auto_assign_party_member(captain1, team1)
    auto_assign_party_member(captain2, team2)

    # Put everyone else into waiting (who is not already in team1 or team2)
    already = {mid(x) for x in (team1 + team2)}
    for p in participants:
        if mid(p) not in already:
            waiting.append(p)

    # Decide first pick:
    # - If one side has 2 (leader+member) vs the other has 1 -> the smaller team picks first.
    # - If both have 2 (two parties) or both have 1 -> CT picks first.
    if len(team1) < len(team2):
        first_pick = captain1
    elif len(team2) < len(team1):
        first_pick = captain2
    else:
        first_pick = captain1

    chan_id = channel.id
    st = {
        "team1": team1,
        "team2": team2,
        "waiting": waiting,
        "captain_ct": captain1,
        "captain_t": captain2,
        "pick_turn": first_pick,
        "picks_made": 0,
        "lock": asyncio.Lock(),
        "message_id": None,
    }
    active_picks[chan_id] = st

    # Create / update the single status message for this lobby
    status = lobby_status.get(chan_id, {})
    msg = None
    if status.get("message_id"):
        try:
            msg = await channel.fetch_message(status["message_id"])
        except Exception:
            msg = None

    embed = build_roster_embed(st)
    view = DraftView(chan_id)
    if msg:
        try:
            await msg.edit(embed=embed, view=view)
        except Exception:
            newmsg = await channel.send(embed=embed, view=view)
            st["message_id"] = newmsg.id
            lobby_status[chan_id] = {"message_id": newmsg.id, "state": "picking"}
    else:
        newmsg = await channel.send(embed=embed, view=view)
        st["message_id"] = newmsg.id
        lobby_status[chan_id] = {"message_id": newmsg.id, "state": "picking"}


# ---------- Cancel session helper ----------
async def cancel_session_and_reset(channel_id, reason="A player left. Lobby reset.", leaver_id=None):
    """Cancel active picking/mapban in this channel and reset waiting message."""
    # Add timeout for the leaver
    if leaver_id:
        timeout_end = add_timeout(leaver_id)
        timeout_end_dt = datetime.fromtimestamp(timeout_end)
        timeout_msg = f"\n🚫 <@{leaver_id}> has been timed out until {timeout_end_dt.strftime('%H:%M:%S')} for leaving during an active session."
        reason += timeout_msg
    
    # cancel pick session
    st = active_picks.pop(channel_id, None)
    # If map ban cog is active, try to let it cleanup
    map_cog = bot.get_cog('MapBan')
    if map_cog:
        try:
            await map_cog.force_cancel(channel_id)
        except Exception:
            pass

    # Update or delete status message based on state
    ch = bot.get_channel(channel_id)
    if not ch:
        return
    status = lobby_status.get(channel_id, {})
    msg_id = status.get("message_id")
    current_state = status.get("state", "")
    
    if msg_id:
        try:
            msg = await ch.fetch_message(msg_id)
            if current_state == "mapban":
                # Delete the message if it was during map ban
                await msg.delete()
                lobby_status.pop(channel_id, None)  # Clear the status entirely
            else:
                # Reset to waiting state for other cases
                embed = discord.Embed(title="Lobby reset", description=reason + "\n\nWaiting for players...", color=discord.Color.red())
                await msg.edit(embed=embed, view=None, content=None)
                # update lobby_status state
            lobby_status[channel_id] = {"message_id": msg.id, "state": "waiting"}
        except Exception:
            try:
                await ch.send("Lobby reset — " + reason)
            except Exception:
                pass
    else:
        try:
            new = await ch.send("Lobby reset — " + reason)
            lobby_status[channel_id] = {"message_id": new.id, "state": "waiting"}
        except Exception:
            pass


# =========================================================
#               SCOREBOARD SUBMISSION SYSTEM
# =========================================================

# --- Modal for entering Match ID ---
class SubmitResultsModal(discord.ui.Modal, title="Submit Scoreboard"):
    match_id = discord.ui.TextInput(
        label="Match ID",
        placeholder="Enter the match id (e.g. 1, 2, 123, a1b2c3d4)",
        min_length=1, max_length=32
    )

    async def on_submit(self, interaction: discord.Interaction):
            # Normalize and validate ID (allow integer IDs)
            mid = str(self.match_id).strip()
            # Accept only positive integers or alphanumeric IDs
            if not (mid.isdigit() and int(mid) >= 1) and not re.fullmatch(r"[A-Za-z0-9\-_]{4,32}", mid):
                await interaction.response.send_message("❌ Invalid Match ID. Please enter a positive integer or a valid alphanumeric ID.", ephemeral=True)
                return

            # Validate match ID exists in matches.json
            import json
            try:
                with open('matches.json', 'r', encoding='utf-8') as f:
                    matches_json = json.load(f)
                matches_dict = matches_json.get('matches', {})
            except Exception:
                matches_dict = {}
            if mid not in matches_dict:
                await interaction.response.send_message("❌ Match ID does not exist in active matches. Please check and try again.", ephemeral=True)
                return

            # Duplicate check (already submitted)
            if mid in results_data:
                await interaction.response.send_message("⚠️ This Match ID was already submitted.", ephemeral=True)
                return

            # Concurrency check
            if mid in active_submissions:
                await interaction.response.send_message("⚠️ Someone is already submitting this Match ID. Try again in a moment.", ephemeral=True)
                return

            # lock it
            active_submissions.add(mid)
            pending_upload[interaction.user.id] = {
                "channel_id": interaction.channel.id,
                "match_id": mid,
                "started_at": time.time()
            }
            print(f"Match {mid} submitted by {interaction.user.display_name}: active_submissions={active_submissions}, pending_upload={pending_upload}")

            # Response & instructions
            await interaction.response.send_message(
                "✅ Match ID received.\n"
                "📸 Now **upload the scoreboard screenshot** as your **next message** in this channel.\n\n"
                "_Tip: attach exactly one image. The bot will process it and post results to "
                f"<#{getattr(config, 'GAME_RESULTS_CHANNEL_ID', 0)}>._",
                ephemeral=True
            )

# --- View with button in submit channel ---
class SubmitResultsView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit Results", style=discord.ButtonStyle.blurple, custom_id="submit_results_btn")
    async def submit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SubmitResultsModal())

# --- Helper: Post the submit message in submit channel ---
async def post_submit_instructions():
    submit_chan = bot.get_channel(getattr(config, "SUBMIT_RESULTS_CHANNEL_ID", 0))
    if not submit_chan:
        return
    try:
        await submit_chan.purge(limit=5)
    except Exception:
        pass

    # FIX: Set the correct game results channel ID
    game_results_id = 1406361378792407253  # <-- your #game-results channel ID
    game_results_mention = f"<#{game_results_id}>"

    embed = discord.Embed(
        title="Scoreboard Submission",
        description=(
            f"Scoreboard results will be posted to {game_results_mention}.\n\n"
            "**How to submit:**\n"
            "1. Click the button below (**Submit Results**)\n"
            "2. Enter your **Match ID**\n"
            "3. **Upload the scoreboard screenshot** in this channel\n"
            "4. Wait for processing\n"
        ),
        color=discord.Color.orange()
    )
    view = SubmitResultsView()
    await submit_chan.send(embed=embed, view=view)

# --- OCR and result processing ---
async def parse_scoreboard_from_url(image_url: str):
    # Call llamaOCR service which returns markdown or dict
    markdown_text = await run_llamaocr(image_url)
    match_data = markdown_text  # assume already dict; otherwise parse markdown

    # Add MVP and winning/losing teams
    all_players = match_data.get("ct_team", []) + match_data.get("t_team", [])
    if all_players:
        mvp = max(all_players, key=lambda p: p["kills"])
        match_data["mvp"] = mvp["name"]
    if match_data.get("score"):
        ct_score, t_score = map(int, match_data["score"].split("-"))
        if ct_score > t_score:
            match_data["winning_team"] = match_data["ct_team"]
            match_data["losing_team"] = match_data["t_team"]
        else:
            match_data["winning_team"] = match_data["t_team"]
            match_data["losing_team"] = match_data["ct_team"]
    return match_data

# --- on_message: capture the image upload for pending submission ---
@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)
    if message.author.bot:
        return

    submit_channel_id = getattr(config, "SUBMIT_RESULTS_CHANNEL_ID", 0)
    if not submit_channel_id or message.channel.id != submit_channel_id:
        return

    pend = pending_upload.get(message.author.id)
    if not pend:
        return

    if not message.attachments:
        return

    attachment = message.attachments[0]
    if not attachment.content_type or not attachment.content_type.startswith("image"):
        await message.channel.send(f"❌ {message.author.mention} Please upload an **image** of the scoreboard.")
        return
        
    # Send loading message
    loading_msg = await message.channel.send(
        embed=discord.Embed(
            title="⚙️ Processing Scoreboard",
            description=(
                "```\n"
                "🔍 Analyzing screenshot...\n"
                "📊 Extracting player stats...\n"
                "⭐ Calculating ratings...\n"
                "```\n"
                "_Please wait while I process your submission..._"
            ),
            color=discord.Color.blue()
        )
    )

    match_id = pend["match_id"]
    if match_id in results_data:
        try:
            active_submissions.discard(match_id)
            del pending_upload[message.author.id]
        except Exception:
            pass
        await loading_msg.edit(embed=discord.Embed(title="Error", description=f"⚠️ {message.author.mention} This Match ID was already submitted.", color=discord.Color.red()))
        return

    # --- OCR and result processing ---
    try:
        match_data = await parse_scoreboard_from_url(attachment.url)
    except Exception as e:
        print(f"OCR Error for match {match_id}: {e}")
        await loading_msg.edit(embed=discord.Embed(title="Error", description=f"❌ Failed to process scoreboard: {e}", color=discord.Color.red()))
        active_submissions.discard(match_id)
        del pending_upload[message.author.id]
        print(f"Cleaned up after OCR error: active_submissions={active_submissions}, pending_upload={pending_upload}")
        return

    # Process match data with missing players
    from match_processor import get_teams_from_match_data, calculate_rating, validate_teams
    try:
        if not validate_teams(match_data):  # Add validation check here
            print(f"Validation failed for match {match_id}: insufficient players")
            active_submissions.discard(match_id)  # Free up the match ID
            del pending_upload[message.author.id]
            print(f"Cleaned up after validation error: active_submissions={active_submissions}, pending_upload={pending_upload}")
            await loading_msg.edit(embed=discord.Embed(
                title="Error",
                description="❌ Match submission rejected: At least one player must be present on each team.",
                color=discord.Color.red()
            ))
            return

        winning_team, losing_team, winners_were_ct = get_teams_from_match_data(match_id, match_data, player_data)
        
        # Get the original match data for additional info
        matches_data = json.loads(pathlib.Path("matches.json").read_text())
        original_match = matches_data["matches"].get(str(match_id))
        
        # Determine which team won and get their captain
        score_ct, score_t = map(int, match_data['score'].split('-'))
        ct_won = score_ct > score_t
        
        # Get the list of players on the winning team
        winning_team_players = [p['name'].lower() for p in (match_data['ct_team'] if ct_won else match_data['t_team'])]
        
        # Find which captain is on the winning team
        winning_captain = "Unknown"
        for captain_id in [original_match['captain1'], original_match['captain2']]:
            if str(captain_id) in player_data:
                captain_name = player_data[str(captain_id)]['nick']
                if captain_name.lower() in winning_team_players:
                    winning_captain = captain_name
                    print(f"Found winning captain: {winning_captain}")
                    break
                
        print(f"Match result - Score: {score_ct}-{score_t}, CT Won: {ct_won}, Captain: {winning_captain}")
        
        # Add to match data
        match_data['map'] = original_match['map']
        match_data['winning_captain'] = winning_captain
        match_data['winning_team'] = winning_team
        match_data['losing_team'] = losing_team
    except Exception as e:
        print(f"Match processing error for match {match_id}: {e}")
        await loading_msg.edit(embed=discord.Embed(title="Error", description=f"❌ Error processing match data: {str(e)}", color=discord.Color.red()))
        active_submissions.discard(match_id)
        del pending_upload[message.author.id]
        print(f"Cleaned up after processing error: active_submissions={active_submissions}, pending_upload={pending_upload}")
        return

    guild = message.guild  # Ensure guild is defined for member edits
    # --- Update player stats ---
    def calculate_elo_change(level, win=True):
        return config.get_elo_change(level, win)

    winner_team = match_data.get("winning_team", [])
    loser_team = match_data.get("losing_team", [])

    # Find MVP (player with most kills in winning team)
    mvp_player = None
    max_kills = -1
    for player in winner_team:
        kills = int(player.get("kills", 0))
        if kills > max_kills:
            max_kills = kills
            mvp_player = player.get("name")

    # Track elo changes for each player
    player_elo_changes = {}

    for p in winner_team:
        for k, v in player_data.items():
            if v["nick"].lower() == p["name"].lower():
                # Skip leavers as they're handled separately
                if not (p.get('was_absent', False) or (p.get('kills', 0) == 0 and p.get('deaths', 0) >= 10)):
                    # Normal win processing
                    v["wins"] = v.get("wins", 0) + 1
                    current_level = v.get("level", 1)  # Get current level
                    change = calculate_elo_change(current_level, win=True)
                    
                    # Add MVP bonus if this player is MVP
                    if p["name"] == mvp_player:
                        change += 5  # MVP bonus
                        p["mvp"] = True  # Mark as MVP in results
                    
                    # Update ELO and track changes
                    v["elo"] = v.get("elo", config.DEFAULT_ELO) + change
                    player_elo_changes[p["name"]] = change
                    
                    # Recalculate level based on new ELO
                    new_level = config.get_level_from_elo(v["elo"])
                    old_level = v.get("level", 1)
                    
                    # Get member object first
                    member = guild.get_member(int(k)) if guild else None
                    
                    # Update level if changed
                    if new_level != old_level:
                        v["level"] = new_level
                        
                        # Update Discord roles
                        if member:
                            try:
                                # Remove old level role
                                old_role = guild.get_role(config.ROLE_LEVELS.get(old_level))
                                if old_role and old_role in member.roles:
                                    await member.remove_roles(old_role)
                                
                                # Add new level role
                                new_role = guild.get_role(config.ROLE_LEVELS.get(new_level))
                                if new_role:
                                    await member.add_roles(new_role)
                                    
                                # Update nickname
                                await member.edit(nick=v['nick'])
                            except Exception as e:
                                print(f"Error updating roles for {v['nick']}: {e}")
                    elif member:  # Just update nickname if no level change
                        try:
                            await member.edit(nick=v['nick'])
                        except Exception:
                            pass

    for p in loser_team:
        for k, v in player_data.items():
            if v["nick"].lower() == p["name"].lower():
                # Skip leavers as they're handled separately
                if not (p.get('was_absent', False) or (p.get('kills', 0) == 0 and p.get('deaths', 0) >= 10)):
                    # Normal loss processing
                    v["losses"] = v.get("losses", 0) + 1
                    current_level = v.get("level", 1)  # Get current level
                    change = calculate_elo_change(current_level, win=False)
                    # Update ELO and track changes
                    v["elo"] = max(config.DEFAULT_ELO, v.get("elo", config.DEFAULT_ELO) + change)
                    player_elo_changes[p["name"]] = change
                    
                    # Recalculate level based on new ELO
                    new_level = config.get_level_from_elo(v["elo"])
                    old_level = v.get("level", 1)
                    
                    # Get member object first
                    member = guild.get_member(int(k)) if guild else None
                    
                    # Update level if changed
                    if new_level != old_level:
                        v["level"] = new_level
                        
                        # Update Discord roles
                        if member:
                            try:
                                # Remove old level role
                                old_role = guild.get_role(config.ROLE_LEVELS.get(old_level))
                                if old_role and old_role in member.roles:
                                    await member.remove_roles(old_role)
                                
                                # Add new level role
                                new_role = guild.get_role(config.ROLE_LEVELS.get(new_level))
                                if new_role:
                                    await member.add_roles(new_role)
                                    
                                # Update nickname
                                await member.edit(nick=v['nick'])
                            except Exception as e:
                                print(f"Error updating roles for {v['nick']}: {e}")
                    elif member:  # Just update nickname if no level change
                        try:
                            await member.edit(nick=v['nick'])
                        except Exception:
                            pass
    save_players()

    # First mark leavers
    for team_key in ["ct_team", "t_team"]:
        for p in match_data.get(team_key, []):
            # Mark players as leavers if they have 0 kills and high deaths
            if p.get('kills', 0) == 0 and p.get('deaths', 0) >= 10:
                p['was_absent'] = True
                print(f"Detected leaver {p['name']}: 0 kills, {p.get('deaths')} deaths")
    
    # Process all players' stats
    for p in match_data.get("ct_team", []) + match_data.get("t_team", []):
        is_leaver = p.get('was_absent', False) or (p.get('kills', 0) == 0 and p.get('deaths', 0) >= 10)
        
        if is_leaver:
            # Set ELO change for display
            p['elo_change'] = -20
            
            # Update player data
            for k, v in player_data.items():
                if v["nick"].lower() == p["name"].lower():
                    current_elo = v.get("elo", config.DEFAULT_ELO)
                    v["elo"] = current_elo - 20  # Allow ELO to go below default
                    
                    # No win/loss count for leavers, only apply the penalty
                    print(f"Applied leaver penalty to {p['name']}: {current_elo} -> {v['elo']} ELO (No W/L counted)")
                    
                    # Save the ELO change for the results message
                    player_elo_changes[p["name"]] = -20
                    break  # Found the player, no need to continue searching
        else:
            # Normal ELO change for active players
            p["elo_change"] = player_elo_changes.get(p["name"], 0)

    # Ensure missing players are included in the final results
    # Attach snapshot ELO to each player for this match
    def snapshot_with_elo(players_list):
        snap = []
        for p in players_list:
            p_copy = dict(p)
            # pull current ELO from players database at submission time
            for v in player_data.values():
                if v.get("nick", "").lower() == p_copy.get("name", "").lower():
                    p_copy["elo"] = int(v.get("elo", config.DEFAULT_ELO))
                    break
            if "elo" not in p_copy:
                p_copy["elo"] = int(config.DEFAULT_ELO)
            snap.append(p_copy)
        return snap

    # Store the submission in results.json
    results_data[match_id] = {
        "match_id": match_id,
        "submitter_id": message.author.id,
        "attachment_url": attachment.url,
        "submitted_at": int(time.time()),
        "winner": "CT" if ct_won else "T",
        "score": match_data.get("score"),
        "map": original_match["map"],
        "mvp": mvp_player,  # Use our newly calculated MVP
        "mvp_kills": max_kills,  # Store MVP's kill count
        "winning_team": snapshot_with_elo(winning_team),  # Include per-player ELO snapshot
        "losing_team": snapshot_with_elo(losing_team)  # Include per-player ELO snapshot
    }
    save_results()

    active_submissions.discard(match_id)
    del pending_upload[message.author.id]
    print(f"Successfully processed match {match_id}: active_submissions={active_submissions}, pending_upload={pending_upload}")


    # Channel IDs for results
    game_results_id = 1406361378792407253
    staff_results_id = 1411756785383243847
    
    # Update loading message with completion
    try:
        await loading_msg.edit(
            embed=discord.Embed(
                title="✅ Scoreboard Processed!",
                description=(
                    f"Match ID: `{match_id}`\n\n"
                    f"Results have been posted to {bot.get_channel(game_results_id).mention}\n"
                    "Click the channel link above to view them!"
                ),
                color=discord.Color.green()
            ),
            view=None
        )
        # Auto-delete the confirmation after 7 seconds to keep the channel tidy
        try:
            await asyncio.sleep(7)
            await loading_msg.delete()
        except Exception:
            pass
    except Exception as e:
        print(f"Failed to edit loading message: {e}")
        try:
            await message.channel.send(
                embed=discord.Embed(
                    title="✅ Submission Received", 
                    description=f"Match ID: `{match_id}`\nYour scoreboard was processed and sent to <#{game_results_id}>.",
                    color=discord.Color.green()
                ),
                ephemeral=True,
                delete_after=7
            )
        except Exception as e:
            print(f"Failed to send completion message: {e}")

    # delete the user's uploaded screenshot message
    try:
        await message.delete()
    except Exception:
        pass
        
    # Update leaderboard after match results
    general_cog = bot.get_cog('General')
    if general_cog:
        await general_cog.update_leaderboard_if_needed(message.guild)

    # Send to both channels
    from staff_controls import StaffMatchControls
    
    for channel_id in [game_results_id, staff_results_id]:
        dest = message.guild.get_channel(channel_id)
        if not dest:
            continue
            
        try:
            # Generate scoreboard image
            output_file = f"scoreboard_{match_id}.png"
            await render_html_to_image(match_data, output_file)
            
            # Create the MVP string with kills
            mvp_string = f"{mvp_player} ({max_kills} kills) 🏆" if mvp_player else "None"
            
            embed = discord.Embed(
                title="📊 Match Results",
                description=(
                    f"**Match ID:** `{match_id}`\n"
                    f"**Winner:** {'CT' if ct_won else 'T'}\n"
                    f"**Score:** {match_data.get('score')}\n"
                    f"**MVP:** {mvp_string} (+5 ELO bonus)\n"
                    f"**Submitted by:** {message.author.mention}\n"
                ),
                color=discord.Color.blue()
            )
            
            def get_elo_for_name(name):
                for v in player_data.values():
                    if v["nick"].lower() == name.lower():
                        return v.get("elo", config.DEFAULT_ELO)
                return config.DEFAULT_ELO
            
            # Calculate rounds played from score
            ct_score, t_score = map(int, match_data['score'].split('-'))
            total_rounds = ct_score + t_score
            
            # Calculate ratings for all players
            print("Calculating ratings for players...")
            for team in [match_data['ct_team'], match_data['t_team']]:
                for player in team:
                    try:
                        rating = calculate_rating(
                            kills=int(player.get('kills', 0)),
                            deaths=int(player.get('deaths', 0)),
                            assists=int(player.get('assists', 0)),
                            rounds_played=total_rounds
                        )
                        player['rating'] = f"{rating:.2f}"  # Format to 2 decimal places as string
                        print(f"Calculated rating for {player['name']}: {player['rating']}")
                    except Exception as e:
                        print(f"Failed to calculate rating for {player['name']}: {e}")
                        player['rating'] = "0.00"
            # Add team fields to embed
            def format_player_line(p):
                name = p['name']
                kills = p['kills']
                deaths = p['deaths']
                elo_change = p.get('elo_change', 0)
                current_elo = get_elo_for_name(name)
                before_elo = current_elo - elo_change
                elo_change_str = f"+{elo_change}" if elo_change >= 0 else str(elo_change)
                return f"**{name}** {before_elo} → {current_elo} | K:{kills} D:{deaths} | {elo_change_str}"
            
            embed.add_field(
                name="Winning Team",
                value="\n".join(format_player_line(p) for p in match_data.get("winning_team", [])) or "—",
                inline=False
            )
            embed.add_field(
                name="Losing Team",
                value="\n".join(format_player_line(p) for p in match_data.get("losing_team", [])) or "—",
                inline=False
            )
            
            # Create file object for the scoreboard
            file = discord.File(output_file)
            
            # Different footer for staff channel
            if channel_id == staff_results_id:
                embed.set_footer(text="Powered by Arena | Developed by narcissist.")
                view = StaffMatchControls(match_id, match_data, bot)
                embed.set_image(url="attachment://" + output_file)
                await dest.send(file=file, embed=embed, view=view)
            else:
                embed.set_footer(text="Powered by Arena | Developed by narcissist.")
                embed.set_image(url="attachment://" + output_file)
                await dest.send(file=file, embed=embed)
            
            # Clean up the PNG file after sending
            import os
            try:
                os.remove(output_file)
            except OSError:
                pass
                
        except Exception as e:
            error_msg = f"⚠️ Failed to send match results to {dest.mention}: {str(e)}"
            print(error_msg)  # Log the error
            await message.channel.send(error_msg)

# =========================================================
#                       EVENTS
# =========================================================

@bot.event
async def on_ready():
    global timeouts
    timeouts = load_timeouts()

    print('Bot ready', bot.user)

    print("Starting bot setup...")
    
    # Load all cogs/extensions first
    try:
        print('Loading commands cog...')
        await bot.load_extension('commands')
        print('Commands cog loaded.')
    except Exception as e:
        print(f"Failed to load commands cog: {e}")
        
    try:
        await bot.load_extension('map_ban')
        print('map_ban loaded')
    except Exception as e:
        print('map_ban load error', e)
        
    try:
        from staff_controls import SubmissionManagementCog
        await bot.add_cog(SubmissionManagementCog(bot))
        print('✅ Loaded SubmissionManagementCog')
    except Exception as e:
        print(f'❌ Error loading SubmissionManagementCog: {e}')

    guild = bot.get_guild(config.GUILD_ID)
    if not guild:
        print("Failed to find guild!")
        return

    # Post initial leaderboard
    cog = bot.get_cog('General')
    if cog:
        print("Posting initial leaderboard...")
        await cog.post_leaderboard(guild)
        print("Posted initial leaderboard")
    else:
        print("General cog not found!")

    # Update all registered players' nicknames
    for k, v in player_data.items():
        member = guild.get_member(int(k))
        if member:
            try:
                await member.edit(nick=v['nick'])
            except Exception:
                pass
        
    # Sync commands with Discord
    print("Syncing commands...")
    try:
        if guild:
            # First clear all commands to ensure clean slate
            bot.tree.clear_commands(guild=guild)
            # Then sync both global and guild commands
            await bot.tree.sync()
            await bot.tree.sync(guild=guild)
            print("Commands synced successfully!")
        else:
            print("Failed to find guild for command sync")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        
    print("Bot setup completed.")

    # post register message
    reg_chan = bot.get_channel(config.REGISTER_CHANNEL_ID)
    if reg_chan:
        try:
            await reg_chan.purge(limit=5)
        except Exception:
            pass
        view = RegisterView()
        embed = discord.Embed(
            title='📝 Register for Matches',
            description=(
                "**Register to participate in matches!**\n\n"
                "You'll need:\n"
                "• Your **Standoff 2 Nickname**\n"
                "• Your **Standoff 2 ID** (numbers only)\n\n"
                "**What happens when you register:**\n"
                "• Your Discord nickname needs to be the same as your so2 name\n"
                "• You'll get a Level 1 role and start at base ELO\n"
                "• You can join matches and gain/lose ELO\n"
                "• Your stats will be tracked on the leaderboard\n\n"
                "Click the button below to register! 👇"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Already registered? Use /set_nick and /set_id to update your info")
        await reg_chan.send(embed=embed, view=view)

    # post submit results message (NEW)
    await post_submit_instructions()

    print('DEBUG_MODE', getattr(config, 'DEBUG_MODE', None), 'DEBUG_PLAYERS', getattr(config, 'DEBUG_PLAYERS', None))

@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    left_lobby = before.channel and before.channel.id == config.LOBBY_VOICE_CHANNEL_ID and (not after.channel or after.channel.id != config.LOBBY_VOICE_CHANNEL_ID)
    moved_into_lobby = after.channel and after.channel.id == config.LOBBY_VOICE_CHANNEL_ID and (not before.channel or before.channel.id != config.LOBBY_VOICE_CHANNEL_ID)

    async def build_and_apply_waiting_embed(text_channel: discord.TextChannel, lobby_vc: discord.VoiceChannel):
        """Create or update the lobby waiting embed with richer context and branding.

        - Shows player count and required players
        - Lists current player mentions (up to 10)
        - Adds brief instructions
        - Uses a thumbnail logo if `af_logo.png` is present in the project folder
        """
        # Count non-bot members
        members = [m for m in lobby_vc.members if not getattr(m, 'bot', False)]
        count = len(members)
        required = 10
        if config.DEBUG_MODE:
            required = config.DEBUG_PLAYERS if config.DEBUG_PLAYERS else 2

        # Prepare a prettier embed
        description = (
            f"Waiting for players: **{count}/{required}**\n\n"
            "- Join the lobby voice channel to be counted.\n"
            "- The match will start automatically when full.\n"
            "- Be respectful and ready to play."
        )
        embed = discord.Embed(
            title="ARENA FACEIT — Lobby",
            description=description,
            color=discord.Color.from_rgb(220, 20, 60)
        )

        # List current players (up to 10)
        if members:
            player_list = "\n".join(m.mention for m in members[:10])
            extra = len(members) - 10
            if extra > 0:
                player_list += f"\n…and {extra} more"
            embed.add_field(name="Current players", value=player_list, inline=False)
        else:
            embed.add_field(name="Current players", value="No players yet — be the first!", inline=False)

        embed.set_footer(text="Powered by Arena | Developed by narcissist.")

        # Try to add a thumbnail logo if present
        logo_filename = "af_logo.png"
        file_obj = None
        try:
            with open(logo_filename, "rb") as f:
                file_obj = discord.File(f, filename=logo_filename)
            embed.set_thumbnail(url=f"attachment://{logo_filename}")
        except Exception:
            # Fallback to existing image.png if available
            try:
                with open("image.png", "rb") as f:
                    file_obj = discord.File(f, filename="image.png")
                embed.set_thumbnail(url="attachment://image.png")
            except Exception:
                pass

        # get or create a status message for this text channel
        # Use a per-channel lock to prevent race conditions
        status = lobby_status.get(text_channel.id, {})
        lock = status.get("lock")
        if lock is None:
            lock = asyncio.Lock()
            status["lock"] = lock
            lobby_status[text_channel.id] = status

        async with lock:
            msg = None
            if status.get("message_id"):
                try:
                    msg = await text_channel.fetch_message(status["message_id"])
                except Exception:
                    msg = None

            # If we don't have a tracked message, try to reuse a recent one from this bot
            if not msg:
                # If a match flow is active, avoid creating a new waiting message to prevent it from appearing on top
                current_state = status.get("state")
                if current_state in {"picking", "mapban"}:
                    # Verify it's truly active; if stale, reset to waiting
                    is_active = False
                    if current_state == "picking" and active_picks.get(text_channel.id):
                        is_active = True
                    elif current_state == "mapban":
                        try:
                            map_cog = bot.get_cog('MapBan')
                            is_active = bool(map_cog and getattr(map_cog, 'active', {}) and text_channel.id in map_cog.active)
                        except Exception:
                            is_active = False
                    if is_active:
                        return
                    else:
                        status["state"] = "waiting"
                        lobby_status[text_channel.id] = status
                try:
                    async for m in text_channel.history(limit=50):
                        if m.author.id == bot.user.id and m.embeds:
                            t = (m.embeds[0].title or "").lower()
                            if "lobby" in t:
                                msg = m
                                status["message_id"] = m.id
                                break
                except Exception:
                    pass

            if not msg:
                if file_obj is not None:
                    new_msg = await text_channel.send(embed=embed, file=file_obj)
                else:
                    new_msg = await text_channel.send(embed=embed)
                status["message_id"] = new_msg.id
                status["state"] = "waiting"
                lobby_status[text_channel.id] = status

                # Clean up older duplicate lobby messages
                try:
                    to_delete = []
                    async for m in text_channel.history(limit=50):
                        if m.id == new_msg.id:
                            continue
                        if m.author.id == bot.user.id and m.embeds:
                            t = (m.embeds[0].title or "").lower()
                            if "lobby" in t:
                                to_delete.append(m)
                    for m in to_delete:
                        try:
                            await m.delete()
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                try:
                    # When editing, the original attachment remains, so we only edit the embed
                    await msg.edit(embed=embed, view=None)
                    status["state"] = "waiting"
                    lobby_status[text_channel.id] = status
                except Exception:
                    pass

    # If someone left the lobby and there's an active session, reset
    if left_lobby:
        text_ch = guild.get_channel(config.LOBBY_TEXT_CHANNEL_ID)
        if text_ch:
            chan_id = text_ch.id
            if active_picks.get(chan_id):
                await cancel_session_and_reset(chan_id, reason=f"{member.display_name} left during an active session. Lobby reset.", leaver_id=member.id)
                return
            map_cog = bot.get_cog('MapBan')
            if map_cog and chan_id in map_cog.active:
                st = map_cog.active[chan_id]
                if len([m for m in st["maps"] if m not in st["banned"]]) > 1:
                    await cancel_session_and_reset(chan_id, reason=f"{member.display_name} left during map ban. Lobby reset.", leaver_id=member.id)
                    try:
                        del map_cog.active[chan_id]
                    except:
                        pass
                    return
            # If no active session, update the waiting embed to reflect the new count
            try:
                lobby_vc = guild.get_channel(config.LOBBY_VOICE_CHANNEL_ID)
                if lobby_vc:
                    await build_and_apply_waiting_embed(text_ch, lobby_vc)
            except Exception:
                pass

    # When someone joins, check if they're timed out first
    if moved_into_lobby:
        # Check if player is timed out
        if is_player_timed_out(member.id):
            remaining = get_timeout_remaining(member.id)
            minutes = remaining // 60
            seconds = remaining % 60
            
            try:
                # Move them out of the lobby
                await member.move_to(None)
                
                # Send timeout message
                text_ch = guild.get_channel(config.LOBBY_TEXT_CHANNEL_ID)
                if text_ch:
                    embed = discord.Embed(
                        title="🚫 Player Timed Out",
                        description=f"{member.mention} is timed out for leaving during an active session.\n\nTime remaining: {minutes}m {seconds}s",
                        color=discord.Color.red()
                    )
                    await text_ch.send(embed=embed, delete_after=10)
                    
                # Try to DM the user
                try:
                    dm_embed = discord.Embed(
                        title="🚫 Lobby Timeout",
                        description=f"You are timed out from joining lobbies for leaving during an active session.\n\nTime remaining: {minutes}m {seconds}s",
                        color=discord.Color.red()
                    )
                    await member.send(embed=dm_embed)
                except:
                    pass  # User might have DMs disabled
                    
                return  # Don't process normal lobby join logic
                
            except discord.Forbidden:
                # Bot doesn't have permission to move member
                pass
        
        # Normal lobby join logic (only if not timed out)
        lobby = after.channel
        text_ch = guild.get_channel(config.LOBBY_TEXT_CHANNEL_ID)
        if not text_ch:
            return
        await build_and_apply_waiting_embed(text_ch, lobby)

        # start if enough players present
        members_now = [m for m in lobby.members if not getattr(m, 'bot', False)]
        needed = config.DEBUG_PLAYERS if config.DEBUG_MODE and config.DEBUG_PLAYERS else 10
        if len(members_now) >= needed:
            # edit status message to show starting picking
            status_msg_id = lobby_status[text_ch.id]["message_id"]
            try:
                status_msg = await text_ch.fetch_message(status_msg_id)
                embed = discord.Embed(title="✅ Lobby full — starting picking stage...", color=discord.Color.green())
                await status_msg.edit(embed=embed, view=None)
            except Exception:
                pass
            await start_picking_stage(text_ch, lobby.members)

# ---------- Commands ----------
@bot.command(name='forcestart')
async def forcestart(ctx):
    vc = ctx.author.voice.channel if ctx.author.voice else None
    if not vc:
        await ctx.send('You must be in the lobby voice channel to force start.')
        return
    await start_picking_stage(ctx.channel, vc.members)

@bot.command(name='listplayers')
async def listplayers(ctx):
    lines = [f"{v['nick']} (key={k}) L{v['level']} ELO:{v['elo']} W:{v.get('wins',0)} L:{v.get('losses',0)}" for k,v in player_data.items()]
    await ctx.send('Registered players:\n' + ("\n".join(lines) if lines else 'None'))

@bot.tree.command(name='elo', description='Check your ELO')
async def elo(interaction: discord.Interaction):
    key = str(interaction.user.id)
    pdata = player_data.get(key)
    if not pdata:
        await interaction.response.send_message('❌ You are not registered.', ephemeral=True)
        return
    await interaction.response.send_message(f"📊 {pdata['nick']} — Level {pdata['level']} — ELO {pdata['elo']} (W:{pdata.get('wins',0)} L:{pdata.get('losses',0)})", ephemeral=True)

@bot.tree.command(name='level_info', description='View ELO changes for each level')
async def level_info(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    try:
        with open('image.png', 'rb') as f:
            picture = discord.File(f)
            await interaction.followup.send("📊 Level & ELO Information:", file=picture, ephemeral=True)
    except Exception as e:
        await interaction.followup.send("❌ An error occurred while sending the level information.", ephemeral=True)
        print(f"Error in level_info command: {str(e)}")

# Removed deprecated /register slash command in favor of Register button only

class EditProfileModal(discord.ui.Modal, title="Edit Profile"):
    nick = discord.ui.TextInput(label="New Nickname", placeholder="Enter your new in-game nickname")
    pid = discord.ui.TextInput(label="New Player ID", placeholder="Enter your new Standoff2 ID")

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user

        # Prevent duplicate nick/id registration across different users
        for k, v in player_data.items():
            if (v.get("nick","").lower() == str(self.nick).lower() or v.get("id") == str(self.pid)):
                if k != str(member.id):
                    await interaction.response.send_message(
                        "❌ That nickname or ID is already registered by another user.", ephemeral=True
                    )
                    return

        pdata = player_data.get(str(member.id))
        if not pdata:
            await interaction.response.send_message("❌ You are not registered. Use /register first.", ephemeral=True)
            return
            
        # Set the new nickname
        clean_nick = str(self.nick).strip()
        pdata["nick"] = clean_nick
        pdata["id"] = str(self.pid)
        save_players()

        # Update leaderboard after profile update
        general_cog = bot.get_cog('General')
        if general_cog:
            await general_cog.update_leaderboard_if_needed(interaction.guild)

        # Create the embed with profile update info
        embed = discord.Embed(
            title="✅ Profile Updated",
            description=f"Nickname: **{clean_nick}**\nPlayer ID: **{self.pid}**",
            color=discord.Color.green()
        )

        # Handle nickname updates based on permissions
        if interaction.guild:
            if member.guild_permissions.administrator:
                embed.add_field(
                    name="ℹ️ Note",
                    value="As a server administrator, please update your nickname manually to match your registration.",
                    inline=False
                )
            else:
                try:
                    await member.edit(nick=clean_nick)
                except discord.errors.Forbidden:
                    embed.add_field(
                        name="ℹ️ Note",
                        value="I don't have permission to change nicknames. Please update your nickname manually.",
                        inline=False
                    )
                except Exception as e:
                    embed.add_field(
                        name="⚠️ Warning",
                        value=f"Failed to update nickname: {str(e)}",
                        inline=False
                    )
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="sync_nicknames", description="Update all registered players' Discord nicknames to match their registered nicknames")
@commands.has_permissions(administrator=True)
async def sync_nicknames(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        return
        
    updated = 0
    failed = 0
    skipped_admins = 0
    
    for user_id, data in player_data.items():
        member = guild.get_member(int(user_id))
        if member:
            # Skip administrators - they need to update their nicknames manually
            if member.guild_permissions.administrator:
                skipped_admins += 1
                continue
                
            try:
                await member.edit(nick=data["nick"])
                updated += 1
            except discord.errors.Forbidden:
                failed += 1
            except Exception as e:
                print(f"Failed to update nickname for {member.name}: {e}")
                failed += 1
                
    await interaction.followup.send(
        f"✅ Nickname sync complete:\n"
        f"- {updated} nicknames updated successfully\n"
        f"- {failed} updates failed\n"
        f"- {skipped_admins} administrators skipped (must update manually)",
        ephemeral=True
    )

# Split edit_profile into two explicit commands
@bot.tree.command(name="set_nick", description="Set your registered nickname")
@discord.app_commands.describe(nick="Your new in-game nickname")
async def set_nick(interaction: discord.Interaction, nick: str):
    await interaction.response.defer(ephemeral=True)
    key = str(interaction.user.id)
    pdata = player_data.get(key)
    if not pdata:
        await interaction.followup.send('❌ You are not registered. Use the Register button to sign up first.', ephemeral=True)
        return
    clean_nick = nick.strip()
    # Duplicate check
    for k, v in player_data.items():
        if v.get('nick', '').lower() == clean_nick.lower() and k != key:
            await interaction.followup.send('❌ That nickname is already registered by another user.', ephemeral=True)
            return
    pdata['nick'] = clean_nick
    save_players()
    # Update Discord nickname where possible
    member = interaction.user
    if interaction.guild and not member.guild_permissions.administrator:
        try:
            await member.edit(nick=clean_nick)
        except Exception:
            pass
    await interaction.followup.send(f"✅ Nickname updated to **{clean_nick}**.", ephemeral=True)

@bot.tree.command(name="set_id", description="Set your registered Standoff 2 ID")
@discord.app_commands.describe(pid="Your Standoff 2 numeric ID")
async def set_id(interaction: discord.Interaction, pid: str):
    await interaction.response.defer(ephemeral=True)
    key = str(interaction.user.id)
    pdata = player_data.get(key)
    if not pdata:
        await interaction.followup.send('❌ You are not registered. Use the Register button to sign up first.', ephemeral=True)
        return
    if not pid.isdigit():
        await interaction.followup.send('❌ Player ID must contain only numbers.', ephemeral=True)
        return
    # Duplicate check
    for k, v in player_data.items():
        if v.get('id') == pid and k != key:
            await interaction.followup.send('❌ That ID is already registered by another user.', ephemeral=True)
            return
    pdata['id'] = pid
    save_players()
    await interaction.followup.send(f"✅ Player ID updated to `{pid}`.", ephemeral=True)

# Initialize Jinja2 environment
env = Environment(loader=FileSystemLoader('.'))

@bot.tree.command(name="stats", description="View your Standoff2 stats")
async def stats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    output_path = f"temp_stats_{interaction.user.id}.png"
    
    try:
        key = str(interaction.user.id)
        pdata = player_data.get(key)
        if not pdata:
            await interaction.followup.send('❌ You are not registered. Use `/register` first.', ephemeral=True)
            return
        
        # Calculate basic stats
        wins = pdata.get('wins', 0)
        losses = pdata.get('losses', 0)
        total_games = wins + losses
        
        # Calculate kills and deaths from results.json
        total_kills, total_deaths = 0, 0
        player_name = pdata.get('nick', '').lower()
        for match in results_data.values():
            for team_key in ['winning_team', 'losing_team']:
                for player in match.get(team_key, []):
                    if player.get('name', '').lower() == player_name:
                        total_kills += player.get('kills', 0)
                        total_deaths += player.get('deaths', 0)
                        
        win_rate = f"{(wins / total_games * 100):.1f}" if total_games > 0 else "0"
        kd_ratio = f"{(total_kills / total_deaths):.2f}" if total_deaths > 0 else str(total_kills)
        
        # Prepare stats data
        stats_data = {
            'nickname': pdata.get('nick', 'Unknown'),
            'level': pdata.get('level', 0),
            'points': pdata.get('elo', 1000),
            'total_games': total_games,
            'wins': wins,
            'losses': losses,
            'kills': total_kills,
            'deaths': total_deaths,
            'kd': kd_ratio,
            'win_rate': win_rate
        }
        
        # Generate HTML and render to image
        temp_html = 'temp_stats.html'
        template = env.get_template('stats.html')
        html = template.render(stats=stats_data)
        with open(temp_html, 'w', encoding='utf-8') as f:
            f.write(html)

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_viewport_size({'width': 1000, 'height': 800})
            await page.goto('file://' + os.path.abspath(temp_html))
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(0.5)
            await page.screenshot(path=output_path)
            await browser.close()
        
        # Get recent matches
        recent_matches = []
        all_results = load_results()
        player_nick = pdata["nick"]
        
        for match_id, match_data in reversed(list(all_results.items())):
            player_found_in_match = False
            for team_key in ["winning_team", "losing_team"]:
                for player in match_data.get(team_key, []):
                    if player.get("name") == player_nick:
                        is_winner = team_key == "winning_team"
                        elo_change = player.get("elo_change", 0)
                        recent_matches.append({
                            "result": "Victory 🏆" if is_winner else "Defeat 💔",
                            "elo_change": elo_change
                        })
                        player_found_in_match = True
                        break
                if player_found_in_match:
                    break
            if len(recent_matches) >= 5:
                break
                
        embed = discord.Embed(title=f"📊 Stats for {pdata['nick']}", color=discord.Color.blue())
        if recent_matches:
            match_lines = []
            for match in recent_matches:
                emoji = "📈" if match["elo_change"] > 0 else "📉" if match["elo_change"] < 0 else "📊"
                elo_text = f"{match['elo_change']:+}" if match["elo_change"] != 0 else "±0"
                match_lines.append(f"{emoji} {match['result']} ({elo_text} ELO)")
            embed.add_field(name="Recent Matches", value="\n".join(match_lines), inline=False)
        else:
            embed.add_field(name="Recent Matches", value="No recent matches found", inline=False)
                
        await interaction.followup.send(file=discord.File(output_path), embed=embed, ephemeral=True)
            
    except Exception as e:
        await interaction.followup.send(f"❌ Error generating stats: {str(e)}", ephemeral=True)
        print(f"Error in stats command: {str(e)}")
        
    finally:
        # Clean up temporary files
        for f in [output_path, 'temp_stats.html']:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    print(f"Error cleaning up file {f}: {e}")

@bot.tree.command(name="party_invite", description="Invite a player to your party")
@discord.app_commands.describe(player="The player to invite to your party")
async def party_invite(interaction: discord.Interaction, player: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    leader_id = str(interaction.user.id)
    invited_id = str(player.id)
    
    # Check if player is registered
    if leader_id not in player_data or invited_id not in player_data:
        await interaction.followup.send("Both players must be registered to use party features.", ephemeral=True)
        return
    
    # Check if invited player is already in a party
    for party in party_data.values():
        if invited_id in party['members']:
            await interaction.followup.send("This player is already in a party.", ephemeral=True)
            return
    
    # Check if inviter is already in someone else's party
    for leader, party in party_data.items():
        if leader != leader_id and leader_id in party['members']:
            await interaction.followup.send("You are already in someone else's party.", ephemeral=True)
            return
            
    # Check if the party is already full (max 2 players)
    if leader_id in party_data and len(party_data[leader_id]['members']) >= 2:
        await interaction.followup.send("Your party is already full (maximum 2 players).", ephemeral=True)
        return
        
    # Create invite
    party_invites[invited_id] = {
        'leader_id': leader_id,
        'expires_at': time.time() + 60  # Expires in 60 seconds
    }
    
    class InviteView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            
        async def on_timeout(self):
            # Clean up the message on timeout
            try:
                await self.message.edit(content="*This party invite has expired.*", view=None)
            except:
                pass

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
        async def accept(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            if button_interaction.user.id != int(invited_id):
                await button_interaction.response.send_message("This invite is not for you.", ephemeral=True)
                return
            
            invite = party_invites.get(invited_id)
            if not invite or invite['leader_id'] != leader_id:
                await button_interaction.response.send_message("This invite has expired or is invalid.", ephemeral=True)
                await self.message.edit(content="*This party invite has expired.*", view=None)
                return
            
            # Create or update party
            if leader_id not in party_data:
                party_data[leader_id] = {'members': [leader_id], 'team': None}
            party_data[leader_id]['members'].append(invited_id)
            save_parties()
            
            del party_invites[invited_id]
            
            await button_interaction.message.edit(content=f"**{button_interaction.user.display_name}** has joined **{interaction.user.display_name}**'s party!", view=None)
            self.stop()
            
        @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
        async def decline(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            if button_interaction.user.id != int(invited_id):
                await button_interaction.response.send_message("This invite is not for you.", ephemeral=True)
                return
            
            if invited_id in party_invites:
                del party_invites[invited_id]
            
            await button_interaction.message.edit(content=f"**{button_interaction.user.display_name}** declined **{interaction.user.display_name}**'s party invite.", view=None)
            self.stop()
    
    view = InviteView()
    commands_channel = interaction.guild.get_channel(COMMANDS_CHANNEL_ID)
    if commands_channel:
        message = await commands_channel.send(
            f"{interaction.user.mention} has invited {player.mention} to their party!",
            view=view
        )
        view.message = message # Store message in view for on_timeout
        await interaction.followup.send(f"Party invite sent!", ephemeral=True)
    else:
        await interaction.followup.send("Could not find the commands channel to send the invite.", ephemeral=True)
    
@bot.tree.command(name="party_leave", description="Leave your current party")
async def party_leave(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    member_id = str(interaction.user.id)
    
    # Find the party the user is in
    for leader_id, party in party_data.items():
        if member_id in party['members']:
            if leader_id == member_id:
                # If leader leaves, disband the party
                del party_data[leader_id]
                save_parties()
                await interaction.followup.send("Your party has been disbanded.", ephemeral=True)
            else:
                # Remove member from party
                party['members'].remove(member_id)
                save_parties()
                await interaction.followup.send("You have left the party.", ephemeral=True)
            return
            
    await interaction.followup.send("You are not in a party.", ephemeral=True)

@bot.tree.command(name="party_kick", description="Kick a player from your party")
@discord.app_commands.describe(player="The player to kick from your party")
async def party_kick(interaction: discord.Interaction, player: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    leader_id = str(interaction.user.id)
    if leader_id not in party_data:
        await interaction.followup.send("You are not a party leader.", ephemeral=True)
        return
    members = party_data[leader_id]['members']
    to_kick = [m for m in members if m != leader_id]
    if not to_kick:
        await interaction.followup.send("No invited member to kick.", ephemeral=True)
        return
    kicked_id = to_kick[0]
    party_data[leader_id]['members'].remove(kicked_id)
    save_parties()
    commands_channel = interaction.guild.get_channel(COMMANDS_CHANNEL_ID)
    if commands_channel:
        await commands_channel.send(f"<@{kicked_id}> has been kicked from <@{leader_id}>'s party.")
    await interaction.followup.send(f"Kicked <@{kicked_id}> from your party.", ephemeral=True)
@bot.tree.command(name="timeout", description="Timeout a user from the voice channel")
@staff_mod_owner_only()
@discord.app_commands.describe(user="The user to timeout", minutes="Timeout duration in minutes", reason="Reason for the timeout")
async def timeout(interaction: discord.Interaction, user: discord.Member, minutes: int = 5, reason: str = "No reason provided"):
    await interaction.response.defer(ephemeral=True)
    timeout_end = time.time() + minutes * 60
    timeouts[str(user.id)] = timeout_end
    save_timeouts()
    
    # Move user out of voice channel if present
    if user.voice and user.voice.channel:
        try:
            await user.move_to(None)
        except Exception:
            pass
    
    # Create a nice embed for the timeout
    embed = discord.Embed(
        title="🚫 User Timed Out",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    
    embed.add_field(name="👤 User", value=f"{user.mention} ({user.display_name})", inline=True)
    embed.add_field(name="⏱️ Duration", value=f"{minutes} minutes", inline=True)
    embed.add_field(name="📅 Until", value=f"<t:{int(timeout_end)}:F>", inline=True)
    embed.add_field(name="📝 Reason", value=reason, inline=False)
    embed.add_field(name="👮 Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="🕐 Time", value=f"<t:{int(time.time())}:F>", inline=True)
    
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="Arena FACEIT Moderation System")
    
    # Send confirmation to the command user
    await interaction.followup.send("✅ User has been timed out successfully.", ephemeral=True)
    
    # Send the embed to the timeout notification channel
    timeout_channel = bot.get_channel(config.TIMEOUT_NOTIFICATION_CHANNEL_ID)
    if timeout_channel:
        await timeout_channel.send(embed=embed)
    else:
        print(f"❌ Could not find timeout notification channel with ID: {config.TIMEOUT_NOTIFICATION_CHANNEL_ID}")

@bot.tree.command(name="timeout_status", description="Check if you're currently timed out")
async def timeout_status(interaction: discord.Interaction):
    if is_player_timed_out(interaction.user.id):
        remaining = get_timeout_remaining(interaction.user.id)
        minutes = remaining // 60
        seconds = remaining % 60
        
        embed = discord.Embed(
            title="🚫 You are timed out",
            description=f"You cannot join lobbies for leaving during an active session.\n\nTime remaining: {minutes}m {seconds}s",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(
            title="✅ No timeout",
            description="You are not currently timed out and can join lobbies normally.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remove_timeout", description="Remove a player's timeout (Staff/Mod/Owner)")
@discord.app_commands.describe(player="The player to remove timeout from")
@staff_mod_owner_only()
async def remove_timeout(interaction: discord.Interaction, player: discord.Member):
    user_id = str(player.id)
    
    if user_id in timeouts:
        del timeouts[user_id]
        save_timeouts()
        await interaction.response.send_message(f"✅ Timeout removed for {player.display_name}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {player.display_name} is not timed out", ephemeral=True)


if __name__ == '__main__':
    bot.run(config.TOKEN)
