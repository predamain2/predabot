import discord
from discord.ui import Button, View, Modal, TextInput
import json
from pathlib import Path
import asyncio
import time
import config

class EditMatchModal(Modal):
    def __init__(self, match_id, player_name, current_kills, current_assists, current_deaths, current_elo, bot):
        super().__init__(title=f"Edit Stats for {player_name}")
        self.match_id = match_id
        self.player_name = player_name
        self.bot = bot
        self.kills = TextInput(label="Kills", default=str(current_kills), required=True, min_length=1, max_length=3)
        self.assists = TextInput(label="Assists", default=str(current_assists), required=True, min_length=1, max_length=3)
        self.deaths = TextInput(label="Deaths", default=str(current_deaths), required=True, min_length=1, max_length=3)
        self.elo = TextInput(label="ELO", default=str(current_elo), required=True, min_length=1, max_length=5)
        self.add_item(self.kills)
        self.add_item(self.assists)
        self.add_item(self.deaths)
        self.add_item(self.elo)
        
    async def on_submit(self, interaction: discord.Interaction):
        # Confirmation step before saving
        confirm_view = ConfirmEditView(self.match_id, self.player_name, self.kills.value, self.assists.value, self.deaths.value, self.elo.value, self.bot)
        await interaction.response.send_message(f"Confirm changes for {self.player_name}?", view=confirm_view, ephemeral=True)

class ConfirmEditView(View):
    def __init__(self, match_id, player_name, kills, assists, deaths, elo, bot):
        super().__init__(timeout=60)
        self.match_id = match_id
        self.player_name = player_name
        self.kills = kills
        self.assists = assists
        self.deaths = deaths
        self.elo = elo
        self.bot = bot

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        try:
            with open("results.json", "r") as f:
                results = json.load(f)
            match_data = results[self.match_id]
            updated = False
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    if player["name"] == self.player_name:
                        player["kills"] = int(self.kills)
                        player["assists"] = int(self.assists)
                        player["deaths"] = int(self.deaths)
                        player["elo"] = int(self.elo)
                        updated = True
                        break
                if updated:
                    break
            with open("results.json", "w") as f:
                json.dump(results, f, indent=2)
            await interaction.response.send_message(f"✅ Changes saved for {self.player_name}. Reposting updated results…", ephemeral=True)

            # Delete old message in game-results and resend edited one
            await repost_game_results(self.bot, interaction.guild, self.match_id, match_data)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Edit cancelled.", ephemeral=True)

class StaffMatchControls(View):
    @discord.ui.button(label="Revert Scoreboard", style=discord.ButtonStyle.danger)
    async def revert_scoreboard(self, interaction: discord.Interaction, button: Button):
        try:
            # Load necessary data
            with open("results.json", "r") as f:
                results = json.load(f)
            with open("players.json", "r") as f:
                player_data = json.load(f)
                
            match_data = results.get(self.match_id)
            if not match_data:
                await interaction.response.send_message("Match not found in database.", ephemeral=True)
                return

            # Revert player stats and ELO
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    player_name = player["name"]
                    elo_change = player.get("elo_change", 0)
                    
                    # Find player in player_data
                    for player_id, data in player_data.items():
                        if data["nick"].lower() == player_name.lower():
                            # Revert wins/losses
                            if team == "winning_team":
                                data["wins"] = max(0, data["wins"] - 1)
                            else:
                                data["losses"] = max(0, data["losses"] - 1)
                            
                            # Revert ELO change
                            current_elo = data.get("elo", config.DEFAULT_ELO)
                            new_elo = current_elo - elo_change
                            data["elo"] = max(config.DEFAULT_ELO, new_elo)
                            
                            # Recalculate level based on new ELO
                            new_level = config.get_level_from_elo(data["elo"])
                            data["level"] = new_level
                            
                            print(f"Reverted {player_name}: ELO {current_elo} -> {data['elo']}, Level -> {new_level}")
                            break

            # Save updated player stats
            with open("players.json", "w") as f:
                json.dump(player_data, f, indent=2)

            # Remove match from results.json
            del results[self.match_id]
            with open("results.json", "w") as f:
                json.dump(results, f, indent=2)

            # Remove from active submissions and pending uploads so it can be submitted again
            import main
            main.active_submissions.discard(self.match_id)
            # Remove from pending_upload if it exists
            for user_id in list(main.pending_upload.keys()):
                if main.pending_upload[user_id] == self.match_id:
                    del main.pending_upload[user_id]
                    break

            # Remove embeds/images from both channels
            await remove_match_embeds(self.bot, self.match_id)

            # Update leaderboard after reverting stats
            general_cog = self.bot.get_cog('General')
            if general_cog:
                await general_cog.update_leaderboard()

            await interaction.response.send_message(
                f"✅ Match {self.match_id} has been reverted:\n"
                f"• Removed from results database\n"
                f"• Player stats and ELO reverted\n"
                f"• Embeds deleted from results channels\n"
                f"• Leaderboard refreshed\n"
                f"• Match ID can be submitted again", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"Error reverting scoreboard: {str(e)}", ephemeral=True)
    def __init__(self, match_id, match_data, bot):
        super().__init__(timeout=None)  # No timeout for staff controls
        self.match_id = match_id
        self.match_data = match_data
        self.bot = bot

    @discord.ui.button(label="Edit Player Stats", style=discord.ButtonStyle.primary)
    async def edit_stats(self, interaction: discord.Interaction, button: Button):
        # Create a select menu with all players
        select = PlayerSelect(self.match_id, self.match_data, self.bot)
        await interaction.response.send_message("Select a player to edit:", view=select, ephemeral=True)

class PlayerSelect(View):
    def __init__(self, match_id, match_data, bot):
        super().__init__()
        self.match_id = match_id
        self.match_data = match_data
        self.bot = bot
        
        # Create select menu with all players
        select = discord.ui.Select(placeholder="Choose a player")
        
        # Prefer ELO from this match's stored data (results.json), falling back to 100
        def elo_for_name(name):
            for team in ["winning_team", "losing_team"]:
                for p in self.match_data.get(team, []):
                    if p.get("name", "").lower() == name.lower():
                        try:
                            return int(p.get("elo", 100))
                        except Exception:
                            return 100
            return 100

        # Add players from both teams
        for team in ["winning_team", "losing_team"]:
            for player in match_data[team]:
                select.add_option(
                    label=player["name"],
                    value=f"{player['name']}|{player['kills']}|{player['assists']}|{player['deaths']}|{elo_for_name(player['name'])}"
                )
        
        select.callback = self.select_callback
        self.add_item(select)
        
    async def select_callback(self, interaction: discord.Interaction):
        player_name, kills, assists, deaths, elo = interaction.data["values"][0].split("|")
        modal = EditMatchModal(
            self.match_id,
            player_name,
            int(kills),
            int(assists),
            int(deaths),
            int(elo),
            self.bot
        )
        await interaction.response.send_modal(modal)

async def repost_game_results(bot, guild, match_id, match_data):
    """Delete the old game-results message for this match and resend an updated one.
    Uses the same image generation as initial posting.
    """
    # Load player data for ELO display
    try:
        with open("players.json", "r") as f:
            player_data = json.load(f)
    except Exception:
        player_data = {}

    game_results_id = 1406361378792407253
    channel = guild.get_channel(game_results_id)
    if not channel:
        return

    # Remove existing messages for this match from game-results only
    try:
        async for message in channel.history(limit=100):
            if message.embeds:
                emb = message.embeds[0]
                if emb.description and f"Match ID: `{match_id}`" in emb.description:
                    try:
                        await message.delete()
                    except Exception:
                        pass
    except Exception:
        pass

    # Ensure ct_team/t_team exist for template by deriving from winner/loser if missing
    if not match_data.get('ct_team') or not match_data.get('t_team'):
        winner = (match_data.get('winner') or '').upper()
        win_list = match_data.get('winning_team', []) or []
        lose_list = match_data.get('losing_team', []) or []
        if winner == 'CT':
            match_data['ct_team'] = win_list
            match_data['t_team'] = lose_list
        elif winner == 'T':
            match_data['ct_team'] = lose_list
            match_data['t_team'] = win_list
        else:
            # Fallback: keep existing if any, else assign both to winning/losing order
            match_data.setdefault('ct_team', win_list)
            match_data.setdefault('t_team', lose_list)

    # Build edited embed and image
    embed = create_updated_embed(match_data, player_data, match_id)

    # Generate a fresh scoreboard image and send
    output_file = f"scoreboard_{match_id}.png"
    try:
        from main import render_html_to_image
        await render_html_to_image(match_data, output_file)
    except Exception:
        output_file = None

    if output_file:
        file = discord.File(output_file)
        embed.set_image(url="attachment://" + output_file)
        await channel.send(file=file, embed=embed)
        try:
            import os
            os.remove(output_file)
        except Exception:
            pass
    else:
        await channel.send(embed=embed)
async def remove_match_embeds(bot, match_id):
    game_results_id = 1406361378792407253
    staff_results_id = 1411756785383243847
    channels = [
        bot.get_channel(game_results_id),
        bot.get_channel(staff_results_id)
    ]
    for channel in channels:
        if channel:
            async for message in channel.history(limit=100):
                if message.embeds:
                    embed = message.embeds[0]
                    if embed.description and f"Match ID: `{match_id}`" in embed.description:
                        try:
                            await message.delete()
                        except Exception as e:
                            print(f"Failed to delete message: {e}")

async def update_match_embeds(bot, match_id, match_data):
    """Updates all embeds for a given match in both results channels"""
    # Load the player data for ELO
    with open("players.json", "r") as f:
        player_data = json.load(f)
        
    # Channel IDs
    game_results_id = 1406361378792407253
    staff_results_id = 1411756785383243847
    
    channels = [
        bot.get_channel(game_results_id),
        bot.get_channel(staff_results_id)
    ]
    
    for channel in channels:
        if channel:
            async for message in channel.history(limit=100):
                if message.embeds:
                    embed = message.embeds[0]
                    if embed.description and f"Match ID: `{match_id}`" in embed.description:
                        # Update the embed
                        new_embed = create_updated_embed(match_data, player_data, match_id)
                        await message.edit(embed=new_embed)
                        
                        # Update the scoreboard image if it exists
                        output_file = f"scoreboard_{match_id}.png"
                        try:
                            from main import render_html_to_image
                            await render_html_to_image(match_data, output_file)
                            await message.attachments[0].edit(file=discord.File(output_file))
                            # Clean up the PNG file after sending
                            import os
                            try:
                                os.remove(output_file)
                            except OSError:
                                pass
                        except Exception as e:
                            print(f"Failed to update scoreboard image: {e}")

def create_updated_embed(match_data, player_data, match_id):
    """Creates an updated embed with the new match data"""
    # Calculate stats and create embed similar to the original match posting code
    embed = discord.Embed(
        title="📊 Match Results (Edited)",
        description=(
            f"**Match ID:** `{match_id}`\n"
            f"**Winner:** {match_data['winner']}\n"
            f"**Score:** {match_data['score']}\n"
            f"**MVP:** {match_data.get('mvp', 'None')} ({match_data.get('mvp_kills', 0)} kills) 🏆\n"
        ),
        color=discord.Color.blue()
    )
    
    def get_elo_for_name(name):
        for v in player_data.values():
            if v["nick"].lower() == name.lower():
                return v.get("elo", 1000)
        return 1000
    
    # Add team fields
    def format_player_line(p):
        name = p['name']
        kills = p['kills']
        deaths = p['deaths']
        elo_change = p.get('elo_change', 0)
        current_elo = get_elo_for_name(name)
        before_elo = current_elo - elo_change
        elo_change_str = f"+{elo_change}" if elo_change >= 0 else str(elo_change)
        return f"**{name}** {before_elo} → {current_elo} | K:{kills} D:{deaths} | {elo_change_str}"
    
    for team_name, team in [("Winning Team", match_data["winning_team"]), ("Losing Team", match_data["losing_team"])]:
        embed.add_field(
            name=team_name,
            value="\n".join(format_player_line(p) for p in team) or "—",
            inline=False
        )
    
    embed.set_footer(text="Powered by Arena | Developed by narcissist.")
    return embed

# Staff submission management commands
def staff_only_check():
    """Check if user has staff, moderator, or owner role"""
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

class SubmissionManagementCog(discord.ext.commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="submissions", description="View currently submitting matches")
    @staff_only_check()
    async def view_submissions(self, interaction: discord.Interaction):
        """View all currently submitting matches"""
        try:
            # Access the submission tracking through the bot's module
            import main
            active_submissions = main.active_submissions
            pending_upload = main.pending_upload
            
            # Debug logging
            print(f"DEBUG - active_submissions: {active_submissions}")
            print(f"DEBUG - pending_upload: {pending_upload}")
            print(f"DEBUG - active_submissions type: {type(active_submissions)}")
            print(f"DEBUG - pending_upload type: {type(pending_upload)}")
            print(f"DEBUG - active_submissions length: {len(active_submissions) if active_submissions else 'None'}")
            print(f"DEBUG - pending_upload length: {len(pending_upload) if pending_upload else 'None'}")
            
            # Check if both are empty
            has_active = active_submissions and len(active_submissions) > 0
            has_pending = pending_upload and len(pending_upload) > 0
            
            print(f"DEBUG - has_active: {has_active}, has_pending: {has_pending}")
            
            if not has_active and not has_pending:
                embed = discord.Embed(
                    title="📋 Active Submissions",
                    description="No matches are currently being submitted.",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            embed = discord.Embed(
                title="📋 Active Submissions",
                description="Currently submitting matches:",
                color=discord.Color.orange()
            )
            
            # Add active submissions
            if active_submissions:
                submission_list = []
                for match_id in active_submissions:
                    # Find the user who is submitting this match
                    submitter_info = None
                    for user_id, data in pending_upload.items():
                        if data.get('match_id') == match_id:
                            submitter_info = data
                            break
                    
                    if submitter_info:
                        user = self.bot.get_user(int(user_id))
                        user_name = user.display_name if user else f"User {user_id}"
                        elapsed = int(time.time() - submitter_info.get('started_at', time.time()))
                        submission_list.append(f"**Match {match_id}** - {user_name} ({elapsed}s ago)")
                    else:
                        submission_list.append(f"**Match {match_id}** - Unknown user")
                
                embed.add_field(
                    name="🔄 Currently Submitting",
                    value="\n".join(submission_list) if submission_list else "None",
                    inline=False
                )
            
            # Add pending uploads without active submissions (orphaned)
            orphaned = []
            for user_id, data in pending_upload.items():
                if data.get('match_id') not in active_submissions:
                    user = self.bot.get_user(int(user_id))
                    user_name = user.display_name if user else f"User {user_id}"
                    elapsed = int(time.time() - data.get('started_at', time.time()))
                    orphaned.append(f"**Match {data.get('match_id')}** - {user_name} ({elapsed}s ago)")
            
            if orphaned:
                embed.add_field(
                    name="⚠️ Orphaned Submissions",
                    value="\n".join(orphaned),
                    inline=False
                )
            
            embed.set_footer(text="Use /clear_submission <match_id> to clear stuck submissions")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error viewing submissions: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="clear_submission", description="Clear a stuck match submission")
    @staff_only_check()
    async def clear_submission(self, interaction: discord.Interaction, match_id: str):
        """Clear a stuck match submission"""
        try:
            import main
            active_submissions = main.active_submissions
            pending_upload = main.pending_upload
            
            # Check if match ID exists in active submissions
            if match_id not in active_submissions:
                # Check if it exists in pending uploads
                found_in_pending = False
                user_to_remove = None
                for user_id, data in pending_upload.items():
                    if data.get('match_id') == match_id:
                        found_in_pending = True
                        user_to_remove = user_id
                        break
                
                if not found_in_pending:
                    await interaction.response.send_message(
                        f"❌ Match ID `{match_id}` is not currently being submitted.", 
                        ephemeral=True
                    )
                    return
                else:
                    # Remove from pending uploads
                    del pending_upload[user_to_remove]
                    await interaction.response.send_message(
                        f"✅ Cleared orphaned submission for Match ID `{match_id}`.", 
                        ephemeral=True
                    )
                    return
            
            # Find the user who was submitting this match
            submitter_user = None
            for user_id, data in pending_upload.items():
                if data.get('match_id') == match_id:
                    submitter_user = self.bot.get_user(int(user_id))
                    break
            
            # Remove from both tracking systems
            active_submissions.discard(match_id)
            for user_id, data in list(pending_upload.items()):
                if data.get('match_id') == match_id:
                    del pending_upload[user_id]
                    break
            
            user_name = submitter_user.display_name if submitter_user else "Unknown user"
            await interaction.response.send_message(
                f"✅ Cleared submission for Match ID `{match_id}` (was being submitted by {user_name}).\n"
                f"The match ID is now available for submission again.",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error clearing submission: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="clear_all_submissions", description="Clear all stuck match submissions")
    @staff_only_check()
    async def clear_all_submissions(self, interaction: discord.Interaction):
        """Clear all stuck match submissions"""
        try:
            import main
            active_submissions = main.active_submissions
            pending_upload = main.pending_upload
            
            if not active_submissions and not pending_upload:
                await interaction.response.send_message(
                    "ℹ️ No active submissions to clear.", 
                    ephemeral=True
                )
                return
            
            # Count submissions before clearing
            active_count = len(active_submissions)
            pending_count = len(pending_upload)
            
            # Clear all submissions
            active_submissions.clear()
            pending_upload.clear()
            
            await interaction.response.send_message(
                f"✅ Cleared all submissions:\n"
                f"• {active_count} active submissions\n"
                f"• {pending_count} pending uploads\n\n"
                f"All match IDs are now available for submission again.",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error clearing all submissions: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="test_submission_commands", description="Test if submission commands are working")
    @staff_only_check()
    async def test_commands(self, interaction: discord.Interaction):
        """Test command to verify the cog is working"""
        try:
            await interaction.response.send_message(
                "✅ Submission management commands are working!\n"
                "You should be able to use:\n"
                "• `/submissions` - View active submissions\n"
                "• `/clear_submission <match_id>` - Clear specific submission\n"
                "• `/clear_all_submissions` - Clear all submissions",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error in test command: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="create_test_submission", description="Create a test stuck submission for debugging")
    @staff_only_check()
    @discord.app_commands.describe(match_id="The match ID to create a test submission for")
    async def create_test_submission(self, interaction: discord.Interaction, match_id: str):
        """Create a test stuck submission"""
        try:
            import main
            import time
            active_submissions = main.active_submissions
            pending_upload = main.pending_upload
            
            # Add to active submissions
            active_submissions.add(match_id)
            
            # Add to pending upload
            pending_upload[str(interaction.user.id)] = {
                "channel_id": interaction.channel.id,
                "match_id": match_id,
                "started_at": time.time()
            }
            
            await interaction.response.send_message(
                f"✅ Created test submission for Match ID `{match_id}`\n"
                f"Now try `/submissions` to see if it shows up!",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error creating test submission: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="debug_submissions", description="Debug submission system variables")
    @staff_only_check()
    async def debug_submissions(self, interaction: discord.Interaction):
        """Debug the submission system variables"""
        try:
            import main
            
            embed = discord.Embed(
                title="🔍 Submission System Debug",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="Active Submissions", 
                value=f"```{main.active_submissions}```", 
                inline=False
            )
            embed.add_field(
                name="Pending Upload", 
                value=f"```{main.pending_upload}```", 
                inline=False
            )
            embed.add_field(
                name="Active Count", 
                value=str(len(main.active_submissions)), 
                inline=True
            )
            embed.add_field(
                name="Pending Count", 
                value=str(len(main.pending_upload)), 
                inline=True
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error debugging submissions: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="list_matches", description="List all available matches in the database")
    @staff_only_check()
    async def list_matches(self, interaction: discord.Interaction):
        """List all matches in results.json"""
        try:
            with open("results.json", "r") as f:
                results = json.load(f)
            
            if not results:
                await interaction.response.send_message("📋 No matches found in the database.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="📋 Available Matches",
                description=f"Found {len(results)} matches in the database:",
                color=discord.Color.blue()
            )
            
            # Show match IDs with their details
            match_list = []
            for match_id, match_data in list(results.items())[:20]:  # Show first 20
                # Get match date if available
                match_date = match_data.get('timestamp', 'Unknown date')
                if isinstance(match_date, (int, float)):
                    from datetime import datetime
                    match_date = datetime.fromtimestamp(match_date).strftime('%Y-%m-%d %H:%M')
                
                # Get winning team info
                winning_team = match_data.get('winning_team', [])
                winning_players = [p.get('name', 'Unknown') for p in winning_team[:2]]  # First 2 players
                winning_str = ', '.join(winning_players)
                if len(winning_team) > 2:
                    winning_str += f" +{len(winning_team)-2} more"
                
                match_list.append(f"**{match_id}** - {winning_str} ({match_date})")
            
            if len(results) > 20:
                match_list.append(f"... and {len(results) - 20} more matches")
            
            embed.add_field(
                name="Matches", 
                value="\n".join(match_list) if match_list else "No matches",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Error listing matches: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="revert_scoreboard", description="Revert a match scoreboard and remove it from results")
    @staff_only_check()
    @discord.app_commands.describe(match_id="The match ID to revert")
    async def revert_scoreboard(self, interaction: discord.Interaction, match_id: str):
        """Revert a match scoreboard"""
        try:
            # Load necessary data
            with open("results.json", "r") as f:
                results = json.load(f)
            with open("players.json", "r") as f:
                player_data = json.load(f)
            
            # Debug: Show available match IDs
            available_matches = list(results.keys())
            print(f"DEBUG - Available match IDs: {available_matches}")
            print(f"DEBUG - Looking for match ID: '{match_id}'")
            print(f"DEBUG - Match ID type: {type(match_id)}")
            print(f"DEBUG - Available match ID types: {[type(m) for m in available_matches]}")
            
            # Try different matching strategies
            match_found = False
            actual_match_id = None
            
            # First try exact match
            if match_id in results:
                match_found = True
                actual_match_id = match_id
            else:
                # Try with stripped whitespace
                stripped_id = match_id.strip()
                if stripped_id in results:
                    match_found = True
                    actual_match_id = stripped_id
                else:
                    # Try case-insensitive match
                    for key in results.keys():
                        if str(key).lower() == str(match_id).lower():
                            match_found = True
                            actual_match_id = key
                            break
                
            if not match_found:
                # Show available matches for debugging
                available_list = "\n".join(available_matches[:10])  # Show first 10
                if len(available_matches) > 10:
                    available_list += f"\n... and {len(available_matches) - 10} more"
                
                embed = discord.Embed(
                    title="❌ Match Not Found",
                    description=f"Match ID `{match_id}` not found in results database.",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="Available Match IDs", 
                    value=f"```{available_list}```" if available_list else "No matches found",
                    inline=False
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Use the actual match ID that was found
            match_data = results[actual_match_id]

            # Revert player stats and ELO
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    player_name = player["name"]
                    elo_change = player.get("elo_change", 0)
                    
                    # Find player in player_data
                    for player_id, data in player_data.items():
                        if data["nick"].lower() == player_name.lower():
                            # Revert wins/losses
                            if team == "winning_team":
                                data["wins"] = max(0, data["wins"] - 1)
                            else:
                                data["losses"] = max(0, data["losses"] - 1)
                            
                            # Revert ELO change
                            current_elo = data.get("elo", config.DEFAULT_ELO)
                            new_elo = current_elo - elo_change
                            data["elo"] = max(config.DEFAULT_ELO, new_elo)
                            
                            # Recalculate level based on new ELO
                            new_level = config.get_level_from_elo(data["elo"])
                            data["level"] = new_level
                            
                            print(f"Reverted {player_name}: ELO {current_elo} -> {data['elo']}, Level -> {new_level}")
                            break

            # Save updated player stats
            with open("players.json", "w") as f:
                json.dump(player_data, f, indent=2)

            # Remove match from results.json
            del results[actual_match_id]
            with open("results.json", "w") as f:
                json.dump(results, f, indent=2)

            # Remove from active submissions and pending uploads so it can be submitted again
            import main
            main.active_submissions.discard(actual_match_id)
            # Remove from pending_upload if it exists
            for user_id in list(main.pending_upload.keys()):
                if main.pending_upload[user_id] == actual_match_id:
                    del main.pending_upload[user_id]
                    break

            # Remove embeds/images from both channels
            await remove_match_embeds(self.bot, actual_match_id)

            # Update leaderboard after reverting stats
            leaderboard_updated = False
            try:
                general_cog = self.bot.get_cog('General')
                if general_cog and hasattr(general_cog, 'update_leaderboard'):
                    await general_cog.update_leaderboard()
                    leaderboard_updated = True
            except Exception as e:
                print(f"Warning: Could not update leaderboard: {e}")

            # Prepare success message
            success_parts = [
                f"✅ Match {actual_match_id} has been reverted:",
                "• Removed from results database",
                "• Player stats and ELO reverted", 
                "• Embeds deleted from results channels"
            ]
            
            if leaderboard_updated:
                success_parts.append("• Leaderboard refreshed")
            else:
                success_parts.append("• Leaderboard update skipped (cog not available)")
                
            success_parts.append("The match ID can be submitted again.")

            await interaction.response.send_message(
                "\n".join(success_parts), 
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error reverting scoreboard: {str(e)}", ephemeral=True)

    @discord.app_commands.command(name="edit_player_stats", description="Edit a player's stats in a specific match")
    @staff_only_check()
    @discord.app_commands.describe(
        match_id="The match ID to edit",
        player_name="The player's name to edit"
    )
    async def edit_player_stats(self, interaction: discord.Interaction, match_id: str, player_name: str):
        """Edit a player's stats in a match using a modal"""
        try:
            with open("results.json", "r") as f:
                results = json.load(f)
            
            if match_id not in results:
                await interaction.response.send_message("❌ Match not found in results database.", ephemeral=True)
                return

            match_data = results[match_id]
            player_found = None
            
            # Find the player in both teams
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    if player["name"].lower() == player_name.lower():
                        player_found = player
                        break
                if player_found:
                    break
            
            if not player_found:
                await interaction.response.send_message(f"❌ Player '{player_name}' not found in match {match_id}.", ephemeral=True)
                return

            # Create and send the modal with current values
            modal = EditPlayerStatsModal(match_id, player_name, player_found, self.bot)
            await interaction.response.send_modal(modal)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error loading player stats: {str(e)}", ephemeral=True)

class EditPlayerStatsModal(Modal):
    def __init__(self, match_id: str, player_name: str, player_data: dict, bot):
        super().__init__(title=f"Edit Stats for {player_name}")
        self.match_id = match_id
        self.player_name = player_name
        self.player_data = player_data
        self.bot = bot
        
        # Create text inputs with current values pre-filled
        self.kills = TextInput(
            label="Kills", 
            default=str(player_data.get("kills", 0)), 
            required=True, 
            min_length=1, 
            max_length=3
        )
        self.assists = TextInput(
            label="Assists", 
            default=str(player_data.get("assists", 0)), 
            required=True, 
            min_length=1, 
            max_length=3
        )
        self.deaths = TextInput(
            label="Deaths", 
            default=str(player_data.get("deaths", 0)), 
            required=True, 
            min_length=1, 
            max_length=3
        )
        self.elo_change = TextInput(
            label="ELO Change", 
            default=str(player_data.get("elo_change", 0)), 
            required=True, 
            min_length=1, 
            max_length=5,
            placeholder="Positive for wins, negative for losses"
        )
        
        self.add_item(self.kills)
        self.add_item(self.assists)
        self.add_item(self.deaths)
        self.add_item(self.elo_change)
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validate inputs
            try:
                kills = int(self.kills.value)
                assists = int(self.assists.value)
                deaths = int(self.deaths.value)
                elo_change = int(self.elo_change.value)
            except ValueError:
                await interaction.response.send_message("❌ All values must be valid numbers.", ephemeral=True)
                return
            
            # Load match data
            with open("results.json", "r") as f:
                results = json.load(f)
            
            match_data = results[self.match_id]
            updated = False
            
            # Find and update the player in both teams
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    if player["name"].lower() == self.player_name.lower():
                        # Store old values for logging
                        old_kills = player["kills"]
                        old_assists = player["assists"]
                        old_deaths = player["deaths"]
                        old_elo_change = player.get("elo_change", 0)
                        
                        # Update stats
                        player["kills"] = kills
                        player["assists"] = assists
                        player["deaths"] = deaths
                        player["elo_change"] = elo_change
                        
                        # Recalculate K/D ratio
                        kd = kills if deaths == 0 else round(kills / deaths, 2)
                        player["kd"] = kd
                        
                        updated = True
                        break
                if updated:
                    break
            
            if not updated:
                await interaction.response.send_message(f"❌ Player '{self.player_name}' not found in match {self.match_id}.", ephemeral=True)
                return

            # Save the updated results
            with open("results.json", "w") as f:
                json.dump(results, f, indent=2)

            # Repost the updated results
            await repost_game_results(self.bot, interaction.guild, self.match_id, match_data)

            await interaction.response.send_message(
                f"✅ Updated stats for {self.player_name} in match {self.match_id}:\n"
                f"• Kills: {old_kills} → {kills}\n"
                f"• Assists: {old_assists} → {assists}\n"
                f"• Deaths: {old_deaths} → {deaths}\n"
                f"• ELO Change: {old_elo_change:+} → {elo_change:+}\n"
                f"• K/D Ratio: {player['kd']:.2f}\n\n"
                f"Results have been reposted with updated information.",
                ephemeral=True
            )

        except Exception as e:
            await interaction.response.send_message(f"❌ Error editing player stats: {str(e)}", ephemeral=True)
