# error_logger.py
import logging
import traceback
import json
import os
from datetime import datetime
from pathlib import Path
import discord
from typing import Optional, Dict, Any

class DiscordBotErrorLogger:
    """
    Comprehensive error logging system for Discord bots.
    Captures full tracebacks, context, and interaction details.
    """
    
    def __init__(self, log_dir: str = "logs", max_file_size: int = 10 * 1024 * 1024):  # 10MB default
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.max_file_size = max_file_size
        
        # Create different log files for different types of errors
        self.error_log_file = self.log_dir / "discord_errors.log"
        self.interaction_log_file = self.log_dir / "interaction_errors.log"
        self.general_log_file = self.log_dir / "general_errors.log"
        
        # Setup logging
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup logging configuration"""
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Setup file handlers for different error types
        self._setup_file_handler(self.error_log_file, formatter, "discord_errors")
        self._setup_file_handler(self.interaction_log_file, formatter, "interaction_errors")
        self._setup_file_handler(self.general_log_file, formatter, "general_errors")
    
    def _setup_file_handler(self, log_file: Path, formatter: logging.Formatter, logger_name: str):
        """Setup individual file handler"""
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setFormatter(formatter)
        
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.addHandler(handler)
    
    def _get_interaction_context(self, interaction: Optional[discord.Interaction] = None) -> Dict[str, Any]:
        """Extract context information from Discord interaction"""
        context = {}
        
        if interaction:
            try:
                context.update({
                    "interaction_id": str(interaction.id) if interaction.id else None,
                    "interaction_type": str(interaction.type) if interaction.type else None,
                    "user_id": str(interaction.user.id) if interaction.user else None,
                    "user_name": str(interaction.user) if interaction.user else None,
                    "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
                    "channel_name": str(interaction.channel) if interaction.channel else None,
                    "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
                    "guild_name": str(interaction.guild) if interaction.guild else None,
                    "message_id": str(interaction.message.id) if interaction.message else None,
                    "application_id": str(interaction.application_id) if interaction.application_id else None,
                    "token": interaction.token[:20] + "..." if interaction.token else None,  # Truncated for security
                    "is_expired": interaction.is_expired() if hasattr(interaction, 'is_expired') else None,
                    "response_done": interaction.response.is_done() if hasattr(interaction.response, 'is_done') else None,
                })
            except Exception as e:
                context["context_extraction_error"] = str(e)
        
        return context
    
    def _get_view_context(self, view: Optional[discord.ui.View] = None) -> Dict[str, Any]:
        """Extract context information from Discord UI View"""
        context = {}
        
        if view:
            try:
                context.update({
                    "view_type": type(view).__name__,
                    "view_timeout": getattr(view, 'timeout', None),
                    "view_children_count": len(view.children) if hasattr(view, 'children') else 0,
                    "view_items": []
                })
                
                # Get details about view items
                if hasattr(view, 'children'):
                    for i, item in enumerate(view.children):
                        item_info = {
                            "index": i,
                            "type": type(item).__name__,
                            "custom_id": getattr(item, 'custom_id', None),
                            "disabled": getattr(item, 'disabled', None),
                        }
                        
                        # Add specific info for Select components
                        if isinstance(item, discord.ui.Select):
                            item_info.update({
                                "placeholder": getattr(item, 'placeholder', None),
                                "min_values": getattr(item, 'min_values', None),
                                "max_values": getattr(item, 'max_values', None),
                                "options_count": len(item.options) if hasattr(item, 'options') else 0,
                            })
                        
                        context["view_items"].append(item_info)
                        
            except Exception as e:
                context["view_context_error"] = str(e)
        
        return context
    
    def log_discord_error(self, 
                         error: Exception, 
                         interaction: Optional[discord.Interaction] = None,
                         view: Optional[discord.ui.View] = None,
                         additional_context: Optional[Dict[str, Any]] = None):
        """
        Log Discord-specific errors with full context
        """
        timestamp = datetime.now().isoformat()
        
        # Extract context information
        interaction_context = self._get_interaction_context(interaction)
        view_context = self._get_view_context(view)
        
        # Prepare error data
        error_data = {
            "timestamp": timestamp,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "error_code": getattr(error, 'code', None),
            "interaction_context": interaction_context,
            "view_context": view_context,
            "additional_context": additional_context or {},
            "traceback": traceback.format_exc(),
            "full_traceback_lines": traceback.format_exc().split('\n')
        }
        
        # Log to appropriate file based on error type
        if isinstance(error, (discord.errors.NotFound, discord.errors.InteractionResponded)):
            self._log_to_file(self.interaction_log_file, error_data, "INTERACTION_ERROR")
        elif isinstance(error, discord.errors.DiscordException):
            self._log_to_file(self.error_log_file, error_data, "DISCORD_ERROR")
        else:
            self._log_to_file(self.general_log_file, error_data, "GENERAL_ERROR")
        
        # Also log to console for immediate visibility
        print(f"\n{'='*80}")
        print(f"ERROR LOGGED: {error_data['error_type']} at {timestamp}")
        print(f"Error: {error_data['error_message']}")
        if interaction_context.get('user_name'):
            print(f"User: {interaction_context['user_name']} (ID: {interaction_context['user_id']})")
        if interaction_context.get('channel_name'):
            print(f"Channel: {interaction_context['channel_name']} (ID: {interaction_context['channel_id']})")
        print(f"Full details logged to: {self._get_log_file_for_error(error)}")
        print(f"{'='*80}\n")
    
    def _get_log_file_for_error(self, error: Exception) -> Path:
        """Determine which log file to use for a given error"""
        if isinstance(error, (discord.errors.NotFound, discord.errors.InteractionResponded)):
            return self.interaction_log_file
        elif isinstance(error, discord.errors.DiscordException):
            return self.error_log_file
        else:
            return self.general_log_file
    
    def _log_to_file(self, log_file: Path, error_data: Dict[str, Any], error_category: str):
        """Write error data to log file in JSON format"""
        try:
            # Check if file needs rotation
            if log_file.exists() and log_file.stat().st_size > self.max_file_size:
                self._rotate_log_file(log_file)
            
            # Append error data as JSON
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*100}\n")
                f.write(f"{error_category} - {error_data['timestamp']}\n")
                f.write(f"{'='*100}\n")
                f.write(json.dumps(error_data, indent=2, ensure_ascii=False))
                f.write(f"\n{'='*100}\n")
                
        except Exception as e:
            print(f"Failed to write to log file {log_file}: {e}")
    
    def _rotate_log_file(self, log_file: Path):
        """Rotate log file when it gets too large"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rotated_name = f"{log_file.stem}_{timestamp}{log_file.suffix}"
            rotated_path = log_file.parent / rotated_name
            log_file.rename(rotated_path)
            print(f"Log file rotated: {log_file} -> {rotated_path}")
        except Exception as e:
            print(f"Failed to rotate log file {log_file}: {e}")
    
    def log_view_exception(self, view: discord.ui.View, item: discord.ui.Item, error: Exception):
        """
        Specifically for logging Discord UI View exceptions
        """
        additional_context = {
            "view_exception": True,
            "item_type": type(item).__name__,
            "item_custom_id": getattr(item, 'custom_id', None),
            "item_placeholder": getattr(item, 'placeholder', None),
            "item_disabled": getattr(item, 'disabled', None),
        }
        
        # Try to get interaction from the item if possible
        interaction = None
        if hasattr(item, '_interaction'):
            interaction = item._interaction
        
        self.log_discord_error(error, interaction, view, additional_context)
    
    def get_error_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get a summary of errors from the last N hours
        """
        summary = {
            "total_errors": 0,
            "error_types": {},
            "interaction_errors": 0,
            "discord_errors": 0,
            "general_errors": 0,
            "recent_errors": []
        }
        
        cutoff_time = datetime.now().timestamp() - (hours * 3600)
        
        for log_file in [self.error_log_file, self.interaction_log_file, self.general_log_file]:
            if log_file.exists():
                try:
                    with open(log_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Simple parsing - in production you might want more robust parsing
                        error_count = content.count("ERROR -")
                        summary["total_errors"] += error_count
                        
                        if "interaction_errors" in str(log_file):
                            summary["interaction_errors"] += error_count
                        elif "discord_errors" in str(log_file):
                            summary["discord_errors"] += error_count
                        else:
                            summary["general_errors"] += error_count
                            
                except Exception as e:
                    print(f"Error reading log file {log_file}: {e}")
        
        return summary

# Global instance
error_logger = DiscordBotErrorLogger()

# Convenience functions
def log_discord_error(error: Exception, interaction: Optional[discord.Interaction] = None, 
                     view: Optional[discord.ui.View] = None, additional_context: Optional[Dict[str, Any]] = None):
    """Convenience function to log Discord errors"""
    error_logger.log_discord_error(error, interaction, view, additional_context)

def log_view_exception(view: discord.ui.View, item: discord.ui.Item, error: Exception):
    """Convenience function to log view exceptions"""
    error_logger.log_view_exception(view, item, error)

def get_error_summary(hours: int = 24) -> Dict[str, Any]:
    """Convenience function to get error summary"""
    return error_logger.get_error_summary(hours)
