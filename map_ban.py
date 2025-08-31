# map_ban.py
import discord
from discord.ext import commands
import config, match_manager, asyncio, random, json, pathlib, main
from typing import Optional, List

class MapBan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active = {}  # channel_id -> state

    async def start_map_ban(
        self,
        channel: discord.TextChannel,
        captains,
        team1_members,
        team2_members,
        *,
        status_message: Optional[discord.Message | int | None] = None,
        teams_message: Optional[discord.Message] = None
    ):
        """
        Start map ban. If status_message is provided (Message or message id), the cog will edit it;
        otherwise it will send a new message and store it in state['message'].
        """
        channel_id = channel.id
        maps = list(config.MAPS)
        first = random.choice([0,1])
        turn = getattr(captains[first], "id", captains[first])
        try:
            turn = int(turn)
        except Exception:
            pass

        state = {
            "maps": maps,
            "banned": set(),
            "turn": int(turn),
            "captain_ct": captains[0],
            "captain_t": captains[1],
            "team1": [m for m in team1_members],
            "team2": [m for m in team2_members],
            "message": None,
            "teams_message": teams_message,  # Store teams message for later deletion
            "timeout_task": None
        }
        self.active[channel_id] = state

        embed = self._build_embed(channel_id)
        view = self._build_view(channel_id)

        msg = None
        # use provided status_message if present
        if status_message is not None:
            try:
                if isinstance(status_message, discord.Message):
                    msg = status_message
                    await msg.edit(embed=embed, view=view)
                else:
                    mid = int(status_message)
                    try:
                        msg = await channel.fetch_message(mid)
                        await msg.edit(embed=embed, view=view)
                    except Exception:
                        msg = await channel.send(embed=embed, view=view)
            except Exception:
                msg = await channel.send(embed=embed, view=view)
        else:
            msg = await channel.send(embed=embed, view=view)

        state["message"] = msg
        state["timeout_task"] = asyncio.create_task(self._turn_timeout(channel_id, state["turn"], 45))

    def _build_embed(self, channel_id):
        st = self.active[channel_id]
        remaining = [m for m in st["maps"] if m not in st["banned"]]
        # Always mention by ID to ensure mention works even if we store just ints
        next_cap_mention = f"<@{st['turn']}>"
        embed = discord.Embed(
            title="🗺 Map Ban Phase",
            description=f"Next to ban: {next_cap_mention}",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Maps",
            value="\n".join([f"~~{m}~~" if m in st['banned'] else m for m in st['maps']]),
            inline=False
        )
        embed.set_footer(text=f"Maps remaining: {len(remaining)}")
        return embed

    def _build_view(self, channel_id):
        st = self.active[channel_id]
        remaining = [m for m in st["maps"] if m not in st["banned"]]
        view = discord.ui.View(timeout=None)
        options = [discord.SelectOption(label=m, value=m) for m in remaining]
        select = discord.ui.Select(placeholder="Select a map to ban...", min_values=1, max_values=1, options=options)

        async def on_select(interaction: discord.Interaction):
            await self._handle_ban(interaction, channel_id, select.values[0])

        select.callback = on_select
        view.add_item(select)
        return view

    async def _handle_ban(self, interaction: discord.Interaction, channel_id, map_name):
        st = self.active.get(channel_id)
        if not st:
            await interaction.response.send_message("This map ban session is not active.", ephemeral=True)
            return

        # ensure user is exactly the id stored in st['turn']
        if interaction.user.id != int(st["turn"]):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return

        if map_name in st["banned"]:
            await interaction.response.send_message("That map is already banned.", ephemeral=True)
            return

        st["banned"].add(map_name)
        remaining = [m for m in st["maps"] if m not in st["banned"]]

        # SWITCH TURN FIRST so the embed update shows the correct next captain immediately
        try:
            ct_id = int(getattr(st['captain_ct'], 'id', st['captain_ct']))
            t_id = int(getattr(st['captain_t'], 'id', st['captain_t']))
            st['turn'] = t_id if st['turn'] == ct_id else ct_id
        except Exception:
            # fallback flip if anything odd
            st['turn'] = st['captain_t'] if st['turn'] == st['captain_ct'] else st['captain_ct']

        # update the same status message (interaction response)
        try:
            await interaction.response.edit_message(embed=self._build_embed(channel_id), view=self._build_view(channel_id))
        except Exception:
            # if the interaction edit fails, try editing stored message
            try:
                if st.get("message"):
                    await st["message"].edit(embed=self._build_embed(channel_id), view=self._build_view(channel_id))
                else:
                    await interaction.followup.send("Ban registered.", ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send("Ban registered.", ephemeral=True)
                except Exception:
                    pass

        # finalize when one map left
        if len(remaining) == 1:
            chosen = remaining[0]

            def norm_to_int_list(lst):
                ids = []
                for x in lst:
                    if hasattr(x, "id"):
                        try:
                            ids.append(int(getattr(x, "id")))
                        except Exception:
                            pass
                    else:
                        try:
                            ids.append(int(x))
                        except Exception:
                            pass
                return ids

            team1_ids = norm_to_int_list(st["team1"])
            team2_ids = norm_to_int_list(st["team2"])

            match_id = match_manager.create_match(
                chosen,
                team1_ids,
                team2_ids,
                captain1_id=int(getattr(st['captain_ct'],'id',st['captain_ct'])),
                captain2_id=int(getattr(st['captain_t'],'id',st['captain_t']))
            )

            # Delete the teams complete message if it exists
            if st.get('teams_message'):
                try:
                    await st['teams_message'].delete()
                except:
                    pass  # Message might already be deleted
                    
            # Delete the map ban message
            try:
                await interaction.message.delete()
            except:
                pass  # Message might already be deleted

            # Send the final match announcement
            await main.announce_teams_final(interaction.channel, match_id, chosen, st)
            
            # Clean up the active session BEFORE disconnecting players to prevent lobby reset messages
            self.active.pop(channel_id, None)  # Remove from active map ban sessions
            
            # Wait 2 seconds then disconnect players
            await asyncio.sleep(2)
            
            # Disconnect all players from voice
            for member in st['team1'] + st['team2']:
                try:
                    if isinstance(member, discord.Member) and member.voice and member.voice.channel:
                        await member.move_to(None)
                except:
                    pass  # Skip if can't disconnect someone
                    
            return

            # load registered nicknames
            reg = {}
            try:
                pfile = pathlib.Path("players.json")
                if pfile.exists():
                    reg = json.loads(pfile.read_text())
            except Exception:
                reg = {}

            async def render_team_list_ints(uids: List[int]) -> str:
                lines = []
                for uid in uids:
                    reg_entry = reg.get(str(uid))
                    if isinstance(reg_entry, dict) and reg_entry.get("nick"):
                        lines.append(f"• {reg_entry.get('nick')} — <@{uid}>")
                    else:
                        try:
                            user = await self.bot.fetch_user(uid)
                            name = getattr(user, "display_name", None) or getattr(user, "name", None) or f"<@{uid}>"
                            lines.append(f"• {name} — <@{uid}>")
                        except Exception:
                            lines.append(f"• <@{uid}>")
                return "\n".join(lines) if lines else "—"

            t1_text = await render_team_list_ints(team1_ids)
            t2_text = await render_team_list_ints(team2_ids)

            final_embed.add_field(name="Team 1 (CT)", value=t1_text, inline=False)
            final_embed.add_field(name="Team 2 (T)", value=t2_text, inline=False)
            final_embed.set_footer(text=f"Match ID: {match_id}")

            try:
                if st.get("message"):
                    await st["message"].edit(embed=final_embed, view=None)
                else:
                    await interaction.channel.send(embed=final_embed)
            except Exception:
                try:
                    await interaction.channel.send(embed=final_embed)
                except Exception:
                    pass

            # DM players with final details (including host info and side)
            await self._dm_players(channel_id, match_id, chosen, team1_ids, team2_ids)

            # cancel timeout
            if st.get("timeout_task"):
                try:
                    st["timeout_task"].cancel()
                except Exception:
                    pass

            # cleanup
            del self.active[channel_id]
            return

        # reset timeout (we changed turn earlier)
        if st.get("timeout_task"):
            try:
                st["timeout_task"].cancel()
            except Exception:
                pass
        st["timeout_task"] = asyncio.create_task(self._turn_timeout(channel_id, st["turn"], 45))

    async def _turn_timeout(self, channel_id, expected_turn, delay):
        await asyncio.sleep(delay)
        st = self.active.get(channel_id)
        if not st:
            return
        if st['turn'] == expected_turn:
            ch = self.bot.get_channel(channel_id)
            try:
                await ch.send(f"⏳ <@{expected_turn}> took too long — skipping turn.")
            except Exception:
                pass

            # skip to other captain
            try:
                ct_id = int(getattr(st['captain_ct'], 'id', st['captain_ct']))
                t_id = int(getattr(st['captain_t'], 'id', st['captain_t']))
                st['turn'] = t_id if st['turn'] == ct_id else ct_id
            except Exception:
                st['turn'] = st['captain_t'] if st['turn'] == st['captain_ct'] else st['captain_ct']

            try:
                if st.get('message'):
                    await st['message'].edit(embed=self._build_embed(channel_id), view=self._build_view(channel_id))
            except Exception:
                pass

            try:
                st["timeout_task"] = asyncio.create_task(self._turn_timeout(channel_id, st["turn"], delay))
            except Exception:
                pass

    async def _dm_players(self, channel_id, match_id, map_name, team1_ids, team2_ids):
        """
        DM a final embed to each player with:
         - Match # and Map
         - Host display + registered SO2 ID (inline code `123456` to allow tap-to-copy on mobile)
         - Which side the player is on
        """
        st = self.active.get(channel_id)
        host_obj = None
        host_int = None
        
        if st:
            def get_player_id(player):
                """Helper function to get a player's registered ID"""
                try:
                    player_int = int(getattr(player, "id", player))
                    with open("players.json", 'r') as f:
                        players = json.load(f)
                    player_data = players.get(str(player_int))
                    if player_data and player_data.get("id"):
                        return player_int
                    return None
                except:
                    return None
            
            # First try both captains
            captain_ct = st.get("captain_ct")
            captain_t = st.get("captain_t")
            
            # Check CT captain
            if captain_ct and get_player_id(captain_ct):
                host_obj = captain_ct
            # Check T captain
            elif captain_t and get_player_id(captain_t):
                host_obj = captain_t
            else:
                # Try to find any player with a registered ID from either team
                all_players = st.get("team1", []) + st.get("team2", [])
                for player in all_players:
                    if player_id := get_player_id(player):
                        host_obj = player
                        break
            
            # Get the host's integer ID
            if host_obj is not None:
                try:
                    host_int = int(getattr(host_obj, "id", host_obj))
                except Exception:
                    try:
                        host_int = int(host_obj)
                    except Exception:
                        host_int = None

        # load registered data
        reg_id = None
        reg_nick = None
        try:
            players_path = pathlib.Path("players.json")
            if players_path.exists():
                pj = json.loads(players_path.read_text())
                if host_int is not None:
                    reg_entry = pj.get(str(host_int))
                    if isinstance(reg_entry, dict):
                        reg_id = reg_entry.get("id")
                        reg_nick = reg_entry.get("nick")
        except Exception:
            reg_id = None

        if not reg_id:
            reg_id = "N/A"
        if not reg_nick:
            reg_nick = getattr(host_obj, "display_name", None) or getattr(host_obj, "name", None) or "Host"

        base_embed = discord.Embed(
            title="🎯 Match Host Information",
            description=f"Match #{match_id}  •  Map: **{map_name}**",
            color=discord.Color.dark_red()
        )
        host_mention = getattr(host_obj, "mention", None) or (f"<@{host_int}>" if host_int else reg_nick)
        base_embed.add_field(name="🎮 Lobby Host", value=f"{host_mention}", inline=False)

        # Inline code (single backticks) — usually tap-to-copy on mobile for Discord
        base_embed.add_field(name="🆔 SO2 ID", value=f"`{reg_id}`", inline=False)
        base_embed.add_field(name="\u200b", value="You can copy the ID above to invite the host on mobile. Tap the ID to copy it.", inline=False)
        base_embed.set_footer(text="Good luck in your match!")

        try:
            t1_ints = [int(x) for x in team1_ids]
        except Exception:
            t1_ints = []
        try:
            t2_ints = [int(x) for x in team2_ids]
        except Exception:
            t2_ints = []

        # DM each player only once
        sent_uids = set()
        for uid in t1_ints + t2_ints:
            if uid in sent_uids:
                continue
            sent_uids.add(uid)
            try:
                user = await self.bot.fetch_user(int(uid))
            except Exception:
                user = None
            embed = discord.Embed.from_dict(base_embed.to_dict())
            try:
                if user:
                    await user.send(embed=embed)
            except Exception:
                pass

        # After DMs are sent, kick all players from the voice channel using config.LOBBY_VOICE_CHANNEL_ID
        try:
            guild = self.bot.get_guild(config.GUILD_ID)
            voice_channel = guild.get_channel(config.LOBBY_VOICE_CHANNEL_ID) if guild else None
            if voice_channel and hasattr(voice_channel, 'members'):
                for member in list(voice_channel.members):
                    member_id = int(getattr(member, "id", member))
                    if member_id in sent_uids:
                        try:
                            await member.move_to(None)
                        except Exception:
                            pass
        except Exception:
            pass

# async setup for modern extension loading
async def setup(bot):
    await bot.add_cog(MapBan(bot))
