"""
Enhanced Error Handler for Telegram Bot

This module provides a comprehensive error handling system that sends
detailed error information to a configured Telegram chat.
"""

import logging
import traceback
import sys
import asyncio
from datetime import datetime
from typing import Optional, Tuple
import aiohttp


# Maximum Telegram message length
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Debug mode flag (set via setup_error_handler)
DEBUG_MODE = False

# Telegram configuration
_bot_token: Optional[str] = None
_chat_id: Optional[str] = None
_enabled: bool = False


class TelegramErrorHandler(logging.Handler):
    """
    Custom logging handler that sends error messages to Telegram.
    
    This handler captures log records and formats them with detailed
    information including timestamp, level, logger name, function,
    line number, and full traceback.
    """
    
    def __init__(self, bot_token: str, chat_id: str, debug_mode: bool = False):
        """
        Initialize the Telegram error handler.
        
        Args:
            bot_token: Telegram bot token for API calls
            chat_id: Telegram chat ID where errors will be sent
            debug_mode: If True, include additional debug information
        """
        super().__init__()
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.debug_mode = debug_mode
        self.session: Optional[aiohttp.ClientSession] = None
        
    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by sending it to Telegram.
        
        This method is called automatically by the logging framework.
        It formats the error and sends it to Telegram asynchronously.
        
        Args:
            record: The log record to emit
        """
        try:
            # Format the error message
            message = self.format_error_message(record)
            
            # Send to Telegram asynchronously
            # We need to run this in the event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, create a task
                    asyncio.create_task(self._send_message(message))
                else:
                    # If no loop is running, run it synchronously
                    loop.run_until_complete(self._send_message(message))
            except RuntimeError:
                # No event loop available, try to create one
                asyncio.run(self._send_message(message))
                
        except Exception as e:
            # Prevent the error handler from crashing the application
            # Fall back to standard error output
            print(f"Error in TelegramErrorHandler: {e}", file=sys.stderr)
            self.handleError(record)
    
    def format_error_message(self, record: logging.LogRecord) -> str:
        """
        Format a log record into a detailed error message.
        
        Args:
            record: The log record to format
            
        Returns:
            Formatted error message string
        """
        # Build the error message with detailed information
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S UTC')
        
        message_parts = [
            "🚨 **ERROR ALERT** 🚨\n",
            f"**Time:** {timestamp}",
            f"**Level:** {record.levelname}",
            f"**Logger:** {record.name}",
            f"**Module:** {record.module}",
            f"**Function:** {record.funcName}",
            f"**Line:** {record.lineno}\n",
            f"**Message:**",
            f"{record.getMessage()}\n",
        ]
        
        # Add traceback if available
        if record.exc_info:
            message_parts.append("**Traceback:**")
            message_parts.append("```")
            
            # Format the full traceback
            tb_lines = traceback.format_exception(*record.exc_info)
            tb_text = ''.join(tb_lines)
            
            message_parts.append(tb_text)
            message_parts.append("```")
        
        # Add debug information if debug mode is enabled
        if self.debug_mode and hasattr(record, 'extra_context'):
            message_parts.append("\n**Debug Info:**")
            message_parts.append(f"```\n{record.extra_context}\n```")
        
        # Join all parts
        full_message = "\n".join(message_parts)
        
        # Truncate if necessary to fit Telegram's message limit
        if len(full_message) > TELEGRAM_MAX_MESSAGE_LENGTH:
            # Calculate how much we need to truncate
            truncate_warning = "\n\n... (truncated due to length)"
            max_content_length = TELEGRAM_MAX_MESSAGE_LENGTH - len(truncate_warning)
            
            # Truncate the message
            full_message = full_message[:max_content_length] + truncate_warning
        
        return full_message
    
    async def _send_message(self, message: str) -> None:
        """
        Send a message to Telegram asynchronously.
        
        Args:
            message: The message to send
        """
        try:
            # Create session if it doesn't exist
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            # Telegram API endpoint
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            # Prepare the payload
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            # Send the request
            async with self.session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"Failed to send error alert to Telegram: HTTP {response.status} - {error_text}", 
                          file=sys.stderr)
                    
        except asyncio.TimeoutError:
            print("Timeout while sending error alert to Telegram", file=sys.stderr)
        except aiohttp.ClientError as e:
            print(f"Network error while sending error alert to Telegram: {e}", file=sys.stderr)
        except Exception as e:
            # Prevent any exception from crashing the application
            print(f"Unexpected error while sending error alert to Telegram: {e}", file=sys.stderr)
    
    async def close_async(self) -> None:
        """Close the aiohttp session asynchronously."""
        if self.session:
            await self.session.close()
            self.session = None
    
    def close(self) -> None:
        """Close the handler (synchronous wrapper)."""
        # Close session synchronously if it exists
        if self.session:
            try:
                # Try to close synchronously
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is running, schedule the close
                    asyncio.create_task(self.close_async())
                else:
                    # If no loop is running, run it
                    loop.run_until_complete(self.close_async())
            except Exception:
                # If async close fails, just clear the reference
                self.session = None


def setup_error_handler(
    bot_token: str,
    chat_id: str,
    enabled: bool = True,
    debug_mode: bool = False,
    level: int = logging.ERROR
) -> None:
    """
    Set up the Telegram error handler for the logging system.
    
    This function configures a custom logging handler that sends all errors
    to a specified Telegram chat with detailed information.
    
    Args:
        bot_token: Telegram bot token for API calls
        chat_id: Telegram chat ID where errors will be sent
        enabled: Whether error alerting is enabled (default: True)
        debug_mode: If True, include additional debug information (default: False)
        level: Minimum logging level to send to Telegram (default: logging.ERROR)
        
    Raises:
        ValueError: If bot_token or chat_id is empty when enabled=True
    """
    global _bot_token, _chat_id, _enabled, DEBUG_MODE
    
    # Store configuration
    _bot_token = bot_token
    _chat_id = chat_id
    _enabled = enabled
    DEBUG_MODE = debug_mode
    
    if not enabled:
        logging.info("Telegram error alerting is disabled")
        return
    
    # Validate inputs
    if not bot_token or not chat_id:
        raise ValueError("bot_token and chat_id are required when error alerting is enabled")
    
    try:
        # Create and configure the Telegram handler
        telegram_handler = TelegramErrorHandler(bot_token, chat_id, debug_mode)
        telegram_handler.setLevel(level)
        
        # Add the handler to the root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(telegram_handler)
        
        logging.info("Telegram error handler configured successfully")
        
    except Exception as e:
        # Don't let error handler setup crash the application
        logging.warning(f"Failed to set up Telegram error handler: {e}")
        raise


async def send_error_alert(
    bot_token: str,
    chat_id: str,
    error_message: str,
    exc_info: Optional[Tuple] = None,
    extra_context: Optional[dict] = None
) -> None:
    """
    Send an error alert directly to Telegram.
    
    This is a standalone function for sending error alerts without going
    through the logging system. Useful for ad-hoc error reporting.
    
    Args:
        bot_token: Telegram bot token for API calls
        chat_id: Telegram chat ID where the error will be sent
        error_message: The error message to send
        exc_info: Optional exception info tuple (type, value, traceback)
        extra_context: Optional dictionary with additional context
    """
    try:
        # Build the message
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')
        
        message_parts = [
            "🚨 **ERROR ALERT** 🚨\n",
            f"**Time:** {timestamp}",
            f"**Message:**",
            f"{error_message}\n",
        ]
        
        # Add traceback if available
        if exc_info:
            message_parts.append("**Traceback:**")
            message_parts.append("```")
            
            # Format the full traceback
            tb_lines = traceback.format_exception(*exc_info)
            tb_text = ''.join(tb_lines)
            
            message_parts.append(tb_text)
            message_parts.append("```")
        
        # Add extra context if provided
        if extra_context and DEBUG_MODE:
            message_parts.append("\n**Additional Context:**")
            message_parts.append("```")
            for key, value in extra_context.items():
                message_parts.append(f"{key}: {value}")
            message_parts.append("```")
        
        # Join all parts
        full_message = "\n".join(message_parts)
        
        # Truncate if necessary
        if len(full_message) > TELEGRAM_MAX_MESSAGE_LENGTH:
            truncate_warning = "\n\n... (truncated due to length)"
            max_content_length = TELEGRAM_MAX_MESSAGE_LENGTH - len(truncate_warning)
            full_message = full_message[:max_content_length] + truncate_warning
        
        # Send to Telegram
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": full_message,
            "parse_mode": "Markdown"
        }
        
        # Create a temporary session for this request
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"Failed to send error alert: HTTP {response.status} - {error_text}", 
                          file=sys.stderr)
                    
    except Exception as e:
        # Prevent the error alert function from crashing the application
        print(f"Error in send_error_alert: {e}", file=sys.stderr)