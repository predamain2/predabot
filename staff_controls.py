import discord
from discord.ui import Button, View, Modal, TextInput
import json
from pathlib import Path
import asyncio

class EditMatchModal(Modal):
    def __init__(self, match_id, player_name, current_kills, current_assists, current_deaths):
        super().__init__(title=f"Edit Stats for {player_name}")
        self.match_id = match_id
        self.player_name = player_name
        
        self.kills = TextInput(
            label="Kills",
            default=str(current_kills),
            required=True,
            min_length=1,
            max_length=3
        )
        self.assists = TextInput(
            label="Assists",
            default=str(current_assists),
            required=True,
            min_length=1,
            max_length=3
        )
        self.deaths = TextInput(
            label="Deaths",
            default=str(current_deaths),
            required=True,
            min_length=1,
            max_length=3
        )
        
        self.add_item(self.kills)
        self.add_item(self.assists)
        self.add_item(self.deaths)
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Load current results
            with open("results.json", "r") as f:
                results = json.load(f)
                
            match_data = results[self.match_id]
            
            # Update player stats in both teams
            updated = False
            for team in ["winning_team", "losing_team"]:
                for player in match_data[team]:
                    if player["name"] == self.player_name:
                        player["kills"] = int(self.kills.value)
                        player["assists"] = int(self.assists.value)
                        player["deaths"] = int(self.deaths.value)
                        updated = True
                        break
                if updated:
                    break
                    
            # Save updated results
            with open("results.json", "w") as f:
                json.dump(results, f, indent=2)
                
            # Trigger a match reprocess and embed update
            await interaction.response.send_message(f"Updated stats for {self.player_name}", ephemeral=True)
            
            # Note: The calling code will handle the embed update
            
        except ValueError as e:
            await interaction.response.send_message(f"Invalid input: {str(e)}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

class StaffMatchControls(View):
    def __init__(self, match_id, match_data, bot):
        super().__init__(timeout=None)  # No timeout for staff controls
        self.match_id = match_id
        self.match_data = match_data
        self.bot = bot

    @discord.ui.button(label="Edit Player Stats", style=discord.ButtonStyle.primary)
    async def edit_stats(self, interaction: discord.Interaction, button: Button):
        # Create a select menu with all players
        select = PlayerSelect(self.match_id, self.match_data)
        await interaction.response.send_message("Select a player to edit:", view=select, ephemeral=True)

class PlayerSelect(View):
    def __init__(self, match_id, match_data):
        super().__init__()
        self.match_id = match_id
        self.match_data = match_data
        
        # Create select menu with all players
        select = discord.ui.Select(placeholder="Choose a player")
        
        # Add players from both teams
        for team in ["winning_team", "losing_team"]:
            for player in match_data[team]:
                select.add_option(
                    label=player["name"],
                    value=f"{player['name']}|{player['kills']}|{player['assists']}|{player['deaths']}"
                )
        
        select.callback = self.select_callback
        self.add_item(select)
        
    async def select_callback(self, interaction: discord.Interaction):
        player_name, kills, assists, deaths = interaction.data["values"][0].split("|")
        modal = EditMatchModal(
            self.match_id,
            player_name,
            int(kills),
            int(assists),
            int(deaths)
        )
        await interaction.response.send_modal(modal)

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
    
    embed.set_footer(text="Last edited: " + discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    return embed
