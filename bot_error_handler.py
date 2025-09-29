# bot_error_handler.py
import discord
from discord.ext import commands
import traceback
import asyncio
from error_logger import log_discord_error
from interaction_utils import InteractionSafety

class BotErrorHandler:
    """Global error handler for the Discord bot"""
    
    def __init__(self, bot):
        self.bot = bot
        
    async def setup_error_handlers(self):
        """Setup global error handlers for the bot"""
        
        @self.bot.event
        async def on_error(event, *args, **kwargs):
            """Handle general bot errors"""
            error = traceback.format_exc()
            print(f"Bot error in event {event}: {error}")
            
            # Log to file
            try:
                from error_logger import error_logger
                error_logger.log_discord_error(
                    Exception(f"Bot error in {event}"),
                    additional_context={"event": event, "args": str(args), "kwargs": str(kwargs)}
                )
            except Exception:
                pass
        
        @self.bot.event
        async def on_command_error(ctx, error):
            """Handle command errors"""
            if isinstance(error, commands.CommandNotFound):
                return  # Ignore command not found errors
            
            if isinstance(error, commands.MissingPermissions):
                await ctx.send("❌ You don't have permission to use this command.", ephemeral=True)
                return
            
            if isinstance(error, commands.BotMissingPermissions):
                await ctx.send("❌ I don't have the required permissions to execute this command.", ephemeral=True)
                return
            
            if isinstance(error, commands.CommandOnCooldown):
                await ctx.send(f"⏳ Command is on cooldown. Try again in {error.retry_after:.1f} seconds.", ephemeral=True)
                return
            
            # Log unexpected errors
            print(f"Command error in {ctx.command}: {error}")
            log_discord_error(error, additional_context={"command": str(ctx.command), "channel": str(ctx.channel)})
            
            await ctx.send("❌ An unexpected error occurred. The issue has been logged.", ephemeral=True)
        
        @self.bot.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
            """Handle application command errors"""
            
            if isinstance(error, discord.app_commands.CommandOnCooldown):
                await InteractionSafety.safe_respond(
                    interaction,
                    f"⏳ Command is on cooldown. Try again in {error.retry_after:.1f} seconds.",
                    ephemeral=True
                )
                return
            
            if isinstance(error, discord.app_commands.MissingPermissions):
                await InteractionSafety.safe_respond(
                    interaction,
                    "❌ You don't have permission to use this command.",
                    ephemeral=True
                )
                return
            
            if isinstance(error, discord.app_commands.BotMissingPermissions):
                await InteractionSafety.safe_respond(
                    interaction,
                    "❌ I don't have the required permissions to execute this command.",
                    ephemeral=True
                )
                return
            
            # Log unexpected errors
            print(f"App command error: {error}")
            log_discord_error(error, interaction, additional_context={"command": str(interaction.command)})
            
            await InteractionSafety.safe_respond(
                interaction,
                "❌ An unexpected error occurred. The issue has been logged.",
                ephemeral=True
            )

    async def handle_view_timeout_cleanup(self):
        """Periodic cleanup of timed out views and stale data"""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
                # Clean up any stale interaction locks
                from interaction_utils import _interaction_locks
                current_time = asyncio.get_event_loop().time()
                
                # Remove locks older than 15 minutes
                stale_locks = [
                    key for key, lock in _interaction_locks.items()
                    if hasattr(lock, '_created_at') and current_time - getattr(lock, '_created_at', 0) > 900
                ]
                
                for key in stale_locks:
                    _interaction_locks.pop(key, None)
                
                if stale_locks:
                    print(f"Cleaned up {len(stale_locks)} stale interaction locks")
                    
            except Exception as e:
                print(f"Error in view timeout cleanup: {e}")
                await asyncio.sleep(60)  # Wait before retrying

# Monkey patch to add creation time to locks
original_lock_init = asyncio.Lock.__init__

def patched_lock_init(self, *args, **kwargs):
    original_lock_init(self, *args, **kwargs)
    self._created_at = asyncio.get_event_loop().time()

asyncio.Lock.__init__ = patched_lock_init
