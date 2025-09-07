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

            # Revert player stats
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    player_name = player["name"]
                    # Find player in player_data
                    for player_id, data in player_data.items():
                        if data["nick"].lower() == player_name.lower():
                            # Revert wins/losses
                            if team == "winning_team":
                                data["wins"] = max(0, data["wins"] - 1)
                            else:
                                data["losses"] = max(0, data["losses"] - 1)
                            break

            # Save updated player stats
            with open("players.json", "w") as f:
                json.dump(player_data, f, indent=2)

            # Remove match from results.json
            del results[self.match_id]
            with open("results.json", "w") as f:
                json.dump(results, f, indent=2)

            # Remove embeds/images from both channels
            await remove_match_embeds(self.bot, self.match_id)

            # Update leaderboard after reverting stats
            general_cog = self.bot.get_cog('General')
            if general_cog:
                await general_cog.update_leaderboard()

            await interaction.response.send_message(
                f"✅ Match {self.match_id} has been reverted:\n"
                f"• Removed from results database\n"
                f"• Player stats updated\n"
                f"• Embeds deleted from results channels\n"
                f"• Leaderboard refreshed\n"
                f"The match ID can be submitted again.", 
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
            # Import the submission tracking from main.py
            from main import active_submissions, pending_upload
            
            # Debug logging
            print(f"DEBUG - active_submissions: {active_submissions}")
            print(f"DEBUG - pending_upload: {pending_upload}")
            print(f"DEBUG - active_submissions type: {type(active_submissions)}")
            print(f"DEBUG - pending_upload type: {type(pending_upload)}")
            
            if not active_submissions and not pending_upload:
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
            from main import active_submissions, pending_upload
            
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
            from main import active_submissions, pending_upload
            
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
    async def create_test_submission(self, interaction: discord.Interaction, match_id: str):
        """Create a test stuck submission"""
        try:
            from main import active_submissions, pending_upload
            
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
