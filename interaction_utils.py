# interaction_utils.py
import asyncio
import time
import discord
from typing import Optional, Dict, Any, Callable
from collections import defaultdict
import logging

# Rate limiting tracker
_api_call_times = defaultdict(list)
_interaction_locks = {}

class InteractionSafety:
    """Utility class to handle Discord interactions safely"""
    
    @staticmethod
    async def safe_respond(interaction: discord.Interaction, *args, **kwargs) -> bool:
        """
        Safely respond to an interaction, handling common error cases
        Returns True if successful, False otherwise
        """
        try:
            # Check if interaction is expired
            if hasattr(interaction, 'is_expired') and interaction.is_expired():
                print(f"Interaction {interaction.id} is expired, cannot respond")
                return False
                
            # Check if already responded
            if interaction.response.is_done():
                # Try followup instead
                try:
                    await interaction.followup.send(*args, **kwargs)
                    return True
                except Exception as e:
                    print(f"Failed to send followup: {e}")
                    return False
            
            # Apply rate limiting
            await InteractionSafety._rate_limit_check(interaction.guild_id if interaction.guild else None)
            
            # Try to respond normally
            await interaction.response.send_message(*args, **kwargs)
            return True
            
        except discord.errors.NotFound:
            print(f"Interaction {interaction.id} not found (expired or invalid)")
            return False
        except discord.errors.InteractionResponded:
            print(f"Interaction {interaction.id} already responded to")
            return False
        except Exception as e:
            print(f"Failed to respond to interaction {interaction.id}: {e}")
            return False
    
    @staticmethod
    async def safe_defer(interaction: discord.Interaction, ephemeral: bool = False) -> bool:
        """
        Safely defer an interaction response
        Returns True if successful, False otherwise
        """
        try:
            if hasattr(interaction, 'is_expired') and interaction.is_expired():
                return False
                
            if interaction.response.is_done():
                return True  # Already handled
            
            await interaction.response.defer(ephemeral=ephemeral)
            return True
            
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            return False
        except Exception as e:
            print(f"Failed to defer interaction {interaction.id}: {e}")
            return False
    
    @staticmethod
    async def safe_edit_message(message: discord.Message, **kwargs) -> bool:
        """
        Safely edit a message with rate limiting
        Returns True if successful, False otherwise
        """
        try:
            guild_id = message.guild.id if message.guild else None
            await InteractionSafety._rate_limit_check(guild_id)
            
            await message.edit(**kwargs)
            return True
            
        except discord.errors.NotFound:
            # Message was likely deleted or inaccessible
            try:
                mid = getattr(message, 'id', '<unknown>')
            except Exception:
                mid = '<unknown>'
            print(f"Message {mid} not found for editing (deleted or no access)")
            return False
        except discord.errors.Forbidden:
            print(f"No permission to edit message {message.id}")
            return False
        except Exception as e:
            print(f"Failed to edit message {message.id}: {e}")
            return False
    
    @staticmethod
    async def _rate_limit_check(guild_id: Optional[int]):
        """Apply rate limiting to prevent 429 errors"""
        now = time.time()
        key = guild_id or "global"
        
        # Clean old timestamps (older than 1 second)
        _api_call_times[key] = [t for t in _api_call_times[key] if now - t < 1.0]
        
        # If we have too many recent calls, wait
        if len(_api_call_times[key]) >= 4:  # Conservative limit
            sleep_time = 1.0 - (now - _api_call_times[key][0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        
        # Record this call
        _api_call_times[key].append(now)
    
    @staticmethod
    async def with_interaction_lock(interaction: discord.Interaction, coro: Callable):
        """
        Execute a coroutine with an interaction-specific lock to prevent concurrent responses
        """
        lock_key = f"{interaction.id}"
        
        if lock_key not in _interaction_locks:
            _interaction_locks[lock_key] = asyncio.Lock()
        
        async with _interaction_locks[lock_key]:
            try:
                result = await coro()
                return result
            finally:
                # Clean up lock after use
                if lock_key in _interaction_locks:
                    del _interaction_locks[lock_key]

class SafeView(discord.ui.View):
    """Enhanced View class with better timeout and error handling"""
    
    def __init__(self, *, timeout: Optional[float] = 900.0):  # 15 minutes default
        super().__init__(timeout=timeout)
        self._message: Optional[discord.Message] = None
        self._cleanup_callbacks = []
    
    async def on_timeout(self):
        """Handle view timeout gracefully"""
        try:
            if self._message:
                # Disable all components
                for item in self.children:
                    item.disabled = True

                # If the message object seems stale or deleted, safe_edit_message will handle NotFound
                try:
                    await InteractionSafety.safe_edit_message(
                        self._message,
                        view=self,
                        content="*This interaction has timed out.*"
                    )
                except Exception as e:
                    # Ensure the timeout handler doesn't crash if editing fails unexpectedly
                    print(f"SafeView: failed to edit timed-out message: {e}")
        except Exception as e:
            print(f"Error handling view timeout: {e}")
        
        # Run cleanup callbacks
        for callback in self._cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                print(f"Error in cleanup callback: {e}")
    
    def add_cleanup_callback(self, callback):
        """Add a callback to run when the view times out"""
        self._cleanup_callbacks.append(callback)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        """Handle view errors gracefully"""
        print(f"View error in {type(self).__name__}: {error}")
        
        # Try to send an error message to the user
        await InteractionSafety.safe_respond(
            interaction,
            "❌ An error occurred while processing your request. Please try again.",
            ephemeral=True
        )
        
        # Log the error using your existing error logger
        try:
            from error_logger import log_view_exception
            log_view_exception(self, item, error)
        except ImportError:
            pass

# Decorator for safe interaction handling
def safe_interaction(func):
    """Decorator to wrap interaction callbacks with safety checks"""
    async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
        async def execute():
            return await func(self, interaction, *args, **kwargs)
        
        return await InteractionSafety.with_interaction_lock(interaction, execute)
    
    return wrapper

class SafeSelect(discord.ui.Select):
    """Safe Select component with proper callback handling"""
    
    def __init__(self, *, channel_id: int, callback_func=None, **kwargs):
        super().__init__(**kwargs)
        self.channel_id = channel_id
        self._callback_func = callback_func
    
    @safe_interaction
    async def callback(self, interaction: discord.Interaction):
        """Safe callback wrapper"""
        if self._callback_func:
            await self._callback_func(interaction, self.channel_id, self.values[0])

class SafeButton(discord.ui.Button):
    """Safe Button component with proper callback handling"""
    
    def __init__(self, *, callback_func=None, **kwargs):
        super().__init__(**kwargs)
        self._callback_func = callback_func
    
    @safe_interaction
    async def callback(self, interaction: discord.Interaction):
        """Safe callback wrapper"""
        if self._callback_func:
            await self._callback_func(interaction)
