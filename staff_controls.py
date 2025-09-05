import discord
from discord.ui import Button, View, Modal, TextInput
import json
from pathlib import Path
import asyncio

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
    for team_name, team in [("Winning Team", match_data["winning_team"]), ("Losing Team", match_data["losing_team"])]:
        embed.add_field(
            name=team_name,
            value="\n".join(
                f"{p['name']} (ELO: {get_elo_for_name(p['name'])}) | "
                f"K:{p['kills']} A:{p['assists']} D:{p['deaths']}"
                for p in team
            ) or "—",
            inline=False
        )
    
    embed.set_footer(text="Powered by Arena | Developed by narcissist.")
    return embed
