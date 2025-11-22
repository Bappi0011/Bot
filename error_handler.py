"""
Telegram Error Handler Module

This module provides error handling functionality that sends detailed error
information to a Telegram chat for debugging and monitoring purposes.
"""

import logging
import traceback
import asyncio
from datetime import datetime
from typing import Optional
from telegram import Bot
from telegram.error import TelegramError


class TelegramErrorHandler(logging.Handler):
    """
    Custom logging handler that sends error messages to Telegram.
    
    This handler captures log messages at ERROR level and above,
    formats them with timestamp and traceback information, and
    sends them to a configured Telegram chat.
    """
    
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        level: int = logging.ERROR
    ):
        """
        Initialize the Telegram error handler.
        
        Args:
            bot_token: Telegram bot API token
            chat_id: Telegram chat ID to send errors to
            enabled: Whether error alerting is enabled
            level: Minimum logging level to send (default: ERROR)
        """
        super().__init__(level)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.bot: Optional[Bot] = None
        self._pending_errors = []
        self._max_message_length = 4096  # Telegram max message length
        
        # Initialize bot if enabled
        if self.enabled and self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
            except Exception as e:
                logging.warning(f"Failed to initialize Telegram bot for error handler: {e}")
    
    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record by sending it to Telegram.
        
        Args:
            record: The log record to emit
        """
        if not self.enabled or not self.bot or not self.chat_id:
            return
        
        try:
            # Format the error message
            message = self.format_error_message(record)
            
            # Send to Telegram asynchronously
            # We need to handle this carefully to avoid blocking
            asyncio.create_task(self._send_error_async(message))
            
        except Exception as e:
            # Don't let error handler failures break the application
            # Log to stderr instead
            logging.warning(f"Failed to send error to Telegram: {e}")
    
    def format_error_message(self, record: logging.LogRecord) -> str:
        """
        Format a log record into a detailed error message.
        
        Args:
            record: The log record to format
            
        Returns:
            Formatted error message string
        """
        # Build the error message
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        message_parts = [
            "🚨 **ERROR ALERT** 🚨\n",
            f"**Time:** {timestamp}",
            f"**Level:** {record.levelname}",
            f"**Logger:** {record.name}",
            f"**Module:** {record.module}",
            f"**Function:** {record.funcName}",
            f"**Line:** {record.lineno}\n",
            f"**Message:**\n{record.getMessage()}\n"
        ]
        
        # Add exception info if available
        if record.exc_info:
            message_parts.append("**Traceback:**")
            tb_lines = traceback.format_exception(*record.exc_info)
            tb_text = "".join(tb_lines)
            # Truncate traceback if too long
            if len(tb_text) > 2000:
                tb_text = tb_text[:2000] + "\n... (truncated)"
            message_parts.append(f"```\n{tb_text}```")
        
        message = "\n".join(message_parts)
        
        # Ensure message isn't too long for Telegram
        if len(message) > self._max_message_length:
            message = message[:self._max_message_length - 100] + "\n\n... (message truncated)"
        
        return message
    
    async def _send_error_async(self, message: str) -> None:
        """
        Send error message to Telegram asynchronously.
        
        Args:
            message: The formatted error message to send
        """
        try:
            if self.bot and self.chat_id:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode="Markdown"
                )
        except TelegramError as e:
            logging.warning(f"Telegram error while sending alert: {e}")
        except Exception as e:
            logging.warning(f"Unexpected error sending to Telegram: {e}")
    
    def send_error_sync(self, message: str, exc_info: Optional[tuple] = None) -> None:
        """
        Send an error message synchronously (for use outside async context).
        
        Args:
            message: Error message description
            exc_info: Optional exception info tuple
        """
        if not self.enabled or not self.bot or not self.chat_id:
            return
        
        # Create a log record manually
        record = logging.LogRecord(
            name="manual",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=exc_info
        )
        
        # Use asyncio to send
        try:
            formatted_message = self.format_error_message(record)
            # Run in the event loop if one exists, otherwise create one
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._send_error_async(formatted_message))
                else:
                    loop.run_until_complete(self._send_error_async(formatted_message))
            except RuntimeError:
                # No event loop, create a new one
                asyncio.run(self._send_error_async(formatted_message))
        except Exception as e:
            logging.warning(f"Failed to send sync error to Telegram: {e}")
    
    def set_enabled(self, enabled: bool) -> None:
        """
        Enable or disable error alerting.
        
        Args:
            enabled: Whether to enable error alerting
        """
        self.enabled = enabled


def setup_error_handler(
    bot_token: str,
    chat_id: str,
    enabled: bool = True,
    logger: Optional[logging.Logger] = None
) -> TelegramErrorHandler:
    """
    Set up the Telegram error handler for a logger.
    
    Args:
        bot_token: Telegram bot API token
        chat_id: Telegram chat ID to send errors to
        enabled: Whether error alerting is enabled
        logger: Logger to attach handler to (default: root logger)
        
    Returns:
        The configured TelegramErrorHandler instance
    """
    if logger is None:
        logger = logging.getLogger()
    
    # Create and configure the handler
    handler = TelegramErrorHandler(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=enabled,
        level=logging.ERROR
    )
    
    # Add handler to logger
    logger.addHandler(handler)
    
    return handler


async def send_error_alert(
    bot_token: str,
    chat_id: str,
    error_message: str,
    exc_info: Optional[tuple] = None
) -> bool:
    """
    Send a standalone error alert to Telegram.
    
    This is a convenience function for sending error alerts
    without using the logging handler.
    
    Args:
        bot_token: Telegram bot API token
        chat_id: Telegram chat ID to send errors to
        error_message: Description of the error
        exc_info: Optional exception info tuple
        
    Returns:
        True if message was sent successfully, False otherwise
    """
    try:
        bot = Bot(token=bot_token)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        message_parts = [
            "🚨 **ERROR ALERT** 🚨\n",
            f"**Time:** {timestamp}",
            f"**Message:** {error_message}\n"
        ]
        
        # Add traceback if exception info provided
        if exc_info:
            message_parts.append("**Traceback:**")
            tb_lines = traceback.format_exception(*exc_info)
            tb_text = "".join(tb_lines)
            if len(tb_text) > 2000:
                tb_text = tb_text[:2000] + "\n... (truncated)"
            message_parts.append(f"```\n{tb_text}```")
        
        message = "\n".join(message_parts)
        
        # Ensure message isn't too long
        if len(message) > 4096:
            message = message[:4000] + "\n\n... (message truncated)"
        
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown"
        )
        return True
        
    except Exception as e:
        logging.warning(f"Failed to send error alert to Telegram: {e}")
        return False
