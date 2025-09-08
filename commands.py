import discord
from discord.ext import commands
import main
import time
import json

LEADERBOARD_CHANNEL_ID = 1406361334018211901

class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_leaderboard_update = 0  # Track last update time
        
    async def update_leaderboard_if_needed(self, guild):
        """Updates the leaderboard if enough time has passed since last update"""
        current_time = int(time.time())
        # Update if more than 30 seconds have passed since last update
        if current_time - self._last_leaderboard_update > 30:
            self._last_leaderboard_update = current_time
            leaderboard_channel = discord.utils.get(guild.channels, name="leaderboard")
            if leaderboard_channel:
                try:
                    # Clear the channel
                    await leaderboard_channel.purge()
                except discord.errors.Forbidden:
                    pass  # Can't purge, will just add new message
                
            await self.post_leaderboard(guild)
        
    # Removed deprecated resetnicks command
        
    @commands.hybrid_command(
        name='leaderboard',
        description="Show the current leaderboard standings"
    )
    async def show_leaderboard(self, ctx):
        await ctx.defer(ephemeral=True)
        await self.post_leaderboard(ctx.guild)
        
        # Get the leaderboard channel mention
        leaderboard_channel = discord.utils.get(ctx.guild.channels, name="leaderboard")
        if leaderboard_channel:
            await ctx.send(f"✅ Leaderboard has been updated! Check {leaderboard_channel.mention}", ephemeral=True)
        else:
            await ctx.send("✅ Leaderboard has been updated! Check the #leaderboard channel", ephemeral=True)

    async def post_leaderboard(self, guild):
        from pathlib import Path
        import jinja2
        from PIL import Image
        from io import BytesIO
        import aiohttp
        
        # Load latest data from files
        results_data = {}
        player_data = {}
        
        try:
            with open('results.json', 'r') as f:
                results_data = json.load(f)
        except Exception as e:
            print(f"Could not load results.json: {e}")
            
        try:
            with open('players.json', 'r') as f:
                player_data = json.load(f)
        except Exception as e:
            print(f"Could not load players.json: {e}")
            
        # If no data exists, create a dummy entry
        if not player_data:
            player_data = {"dummy": {
                "nick": "No Players Yet",
                "elo": 1000,
                "wins": 0,
                "losses": 0
            }}

        # Calculate total kills for each player from results
        player_kills = {}
        player_deaths = {}
        for match in results_data.values():
            # Process winning team
            for player in match.get('winning_team', []):
                if player.get('was_absent'):  # Skip players who left
                    continue
                name = player.get('name', '').lower()  # Use lowercase for comparison
                kills = int(player.get('kills', 0))
                deaths = int(player.get('deaths', 0))
                player_kills[name] = player_kills.get(name, 0) + kills
                player_deaths[name] = player_deaths.get(name, 0) + deaths
            
            # Process losing team
            for player in match.get('losing_team', []):
                if player.get('was_absent'):  # Skip players who left
                    continue
                name = player.get('name', '').lower()  # Use lowercase for comparison
                kills = int(player.get('kills', 0))
                deaths = int(player.get('deaths', 0))
                player_kills[name] = player_kills.get(name, 0) + kills
                player_deaths[name] = player_deaths.get(name, 0) + deaths

        # Process all players
        processed_players = []
        for player_id, player_info in player_data.items():
            # Skip dummy entries if we have real players
            if len(player_data) > 1 and player_id == "dummy":
                continue
                
            player_nick = player_info.get('nick', '').lower()  # Use lowercase for comparison
            total_kills = player_kills.get(player_nick, 0)
            total_deaths = player_deaths.get(player_nick, 0)
            wins = player_info.get('wins', 0)
            losses = player_info.get('losses', 0)
            
            # Calculate stats
            if total_deaths > 0:
                kd = round(total_kills / total_deaths, 2)
            else:
                kd = total_kills  # K/D equals kills when no deaths
                
            total_games = wins + losses
            winrate = round((wins / total_games * 100) if total_games > 0 else 0)
            
            processed_players.append({
                'nick': player_info['nick'],
                'kills': total_kills,
                'deaths': total_deaths,
                'kd': kd,
                'wins': wins,
                'losses': losses,
                'winrate': winrate,
                'elo': player_info.get('elo', 1000)  # Use default ELO if not set
            })
            
        # Sort players first by ELO, then by total kills as tiebreaker
        top_players = sorted(
            processed_players,
            key=lambda v: (v['elo'], v['kills']),  # Changed from kd to kills for tiebreaker
            reverse=True
        )[:10]
        
        # Add rank to each player
        for idx, player in enumerate(top_players, 1):
            player['rank'] = idx
            
        # Fill remaining slots with "None" players up to 10
        while len(top_players) < 10:
            rank = len(top_players) + 1
            top_players.append({
                'rank': rank,
                'nick': 'None',
                'kills': 0,
                'deaths': 0,
                'kd': 0,
                'wins': 0,
                'losses': 0,
                'winrate': 0,
                'elo': 0
            })

        # Render the template
        template_path = Path('leaderboard.html')
        template_dir = template_path.parent
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            autoescape=True
        )
        template = env.get_template(template_path.name)
        html_content = template.render(players=top_players)

        # Save the rendered HTML
        temp_html = Path('temp_leaderboard.html')
        temp_html.write_text(html_content, encoding='utf-8')
        
        # Convert HTML to image using Playwright
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1000, 'height': 800})
            await page.goto(f'file:///{temp_html.absolute()}')
            png_data = await page.screenshot()
            await browser.close()
            
            # Convert to discord.File
            file = discord.File(BytesIO(png_data), filename='leaderboard.png')
            
            # Build embed wrapper
            embed = discord.Embed(
                title="Arena Top 10 Players",
                color=discord.Color.orange()
            )
            embed.set_image(url="attachment://leaderboard.png")
            embed.set_footer(text="Powered by Arena | Developed by narcissist.")
            
            # Send to channel
            leaderboard_channel = guild.get_channel(LEADERBOARD_CHANNEL_ID)
            if leaderboard_channel:
                await leaderboard_channel.purge(limit=5)
                await leaderboard_channel.send(file=file, embed=embed)
            
            # Clean up temp file
            temp_html.unlink(missing_ok=True)

async def setup(bot):
    print("Setting up General cog...")
    cog = General(bot)
    await bot.add_cog(cog)

# Patch main.save_players to auto-update leaderboard on ELO change
orig_save_players = main.save_players

def save_players_and_update():
    orig_save_players()
    bot = main.bot
    guild = bot.get_guild(main.config.GUILD_ID)
    if guild:
        cog = bot.get_cog('General')
        if cog:
            coro = cog.post_leaderboard(guild)
            import asyncio
            asyncio.create_task(coro)

# Patch main.save_players to always update leaderboard
main.save_players = save_players_and_update