"""
MIGRATION NOTE: Solana SPL Token WebSocket Detection
=====================================================

This bot has been migrated from Ethereum/BSC OpenOcean API to native Solana WebSocket 
for real-time SPL token detection.

Previous Implementation (OpenOcean):
- Connected to OpenOcean Meme API WebSocket (wss://meme-api.openocean.finance/ws/public)
- Subscribed to "token" channel for Ethereum/BSC meme tokens
- Received preprocessed token data from centralized API

Current Implementation (Solana Native):
- Establishes WebSocket connection to Solana mainnet (wss://api.mainnet-beta.solana.com)
- Subscribes to SPL Token Program logs (TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA)
- Listens for MintTo instructions and liquidity add events
- Parses raw transaction logs for new SPL token mints
- Detects real-time token creation on Solana blockchain

Benefits:
- Direct blockchain integration (no third-party API dependency)
- Real-time SPL token mint detection
- Detects liquidity pool creation events
- Lower latency for new Solana meme coins
- More reliable (no API rate limits or downtime)

Configuration:
- SOLANA_WS_URL: Solana WebSocket endpoint
- SPL_TOKEN_PROGRAM_ID: Token program to monitor
- All settings configurable via environment variables
- Automatic reconnection with configurable intervals
- Telegram alerts for connection failures
"""

import os
import logging
import asyncio
import json
import base64
import base58
import time
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
import websockets
from websockets.exceptions import WebSocketException, ConnectionClosed
from construct import Struct, Int64ul, Bytes, Padding
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Import error handler
from error_handler import setup_error_handler, send_error_alert

# Raydium V4 Liquidity Pool State Layout
# This is the binary layout for Raydium AMM pool accounts
# Based on Raydium's on-chain program structure
LIQUIDITY_STATE_LAYOUT_V4 = Struct(
    "status" / Int64ul,
    "nonce" / Int64ul,
    "maxOrder" / Int64ul,
    "depth" / Int64ul,
    "baseDecimal" / Int64ul,
    "quoteDecimal" / Int64ul,
    "state" / Int64ul,
    "resetFlag" / Int64ul,
    "minSize" / Int64ul,
    "volMaxCutRatio" / Int64ul,
    "amountWaveRatio" / Int64ul,
    "baseLotSize" / Int64ul,
    "quoteLotSize" / Int64ul,
    "minPriceMultiplier" / Int64ul,
    "maxPriceMultiplier" / Int64ul,
    "systemDecimalValue" / Int64ul,
    "minSeparateNumerator" / Int64ul,
    "minSeparateDenominator" / Int64ul,
    "tradeFeeNumerator" / Int64ul,
    "tradeFeeDenominator" / Int64ul,
    "pnlNumerator" / Int64ul,
    "pnlDenominator" / Int64ul,
    "swapFeeNumerator" / Int64ul,
    "swapFeeDenominator" / Int64ul,
    "baseNeedTakePnl" / Int64ul,
    "quoteNeedTakePnl" / Int64ul,
    "quoteTotalPnl" / Int64ul,
    "baseTotalPnl" / Int64ul,
    "poolOpenTime" / Int64ul,
    "punishPcAmount" / Int64ul,
    "punishCoinAmount" / Int64ul,
    "orderbookToInitTime" / Int64ul,
    "swapBaseInAmount" / Int64ul,
    "swapQuoteOutAmount" / Int64ul,
    "swapBase2QuoteFee" / Int64ul,
    "swapQuoteInAmount" / Int64ul,
    "swapBaseOutAmount" / Int64ul,
    "swapQuote2BaseFee" / Int64ul,
    # Base and quote vault addresses (32 bytes each)
    "baseVault" / Bytes(32),
    "quoteVault" / Bytes(32),
    # Base and quote mint addresses (32 bytes each)
    "baseMint" / Bytes(32),
    "quoteMint" / Bytes(32),
    "lpMint" / Bytes(32),
    # OpenBook market info
    "openOrders" / Bytes(32),
    "marketId" / Bytes(32),
    "marketProgramId" / Bytes(32),
    "targetOrders" / Bytes(32),
    "withdrawQueue" / Bytes(32),
    "lpVault" / Bytes(32),
    "owner" / Bytes(32),
    # Padding for account alignment and future fields
    # Total: (38 * 8) + (12 * 32) + 57 = 304 + 384 + 57 = 745 bytes
    # This matches the Raydium V4 pool account structure
    Padding(57),
)

# Minimum size check for pool data validation
MIN_POOL_DATA_SIZE = LIQUIDITY_STATE_LAYOUT_V4.sizeof()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
RAYDIUM_V4_PROGRAM_ID = os.getenv("RAYDIUM_V4_PROGRAM_ID", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")
TELEGRAM_ERROR_ALERTS_ENABLED = os.getenv("TELEGRAM_ERROR_ALERTS_ENABLED", "true").lower() in ("true", "1", "yes")
TELEGRAM_ERROR_DEBUG_MODE = os.getenv("TELEGRAM_ERROR_DEBUG_MODE", "false").lower() in ("true", "1", "yes")

# ========================================
# SOLANA WEBSOCKET CONFIGURATION
# ========================================
# Solana WebSocket endpoint for real-time blockchain data
SOLANA_WS_URL = os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com")

# SPL Token Program ID - the program responsible for all SPL tokens on Solana
SPL_TOKEN_PROGRAM_ID = os.getenv("SPL_TOKEN_PROGRAM_ID", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

# Raydium Liquidity Pool Program ID (for detecting liquidity adds)
RAYDIUM_LIQUIDITY_POOL_V4 = os.getenv("RAYDIUM_LIQUIDITY_POOL_V4", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")

# WebSocket reconnection settings
WS_RECONNECT_INTERVAL = int(os.getenv("WS_RECONNECT_INTERVAL", "5"))
WS_MAX_RECONNECT_ATTEMPTS = int(os.getenv("WS_MAX_RECONNECT_ATTEMPTS", "0"))
WS_PING_INTERVAL = int(os.getenv("WS_PING_INTERVAL", "30"))

# ========================================
# FILTER CONFIGURATION
# ========================================
# Minimum liquidity in SOL (will be converted from token account balances)
FILTER_LIQUIDITY_MIN_SOL = float(os.getenv("FILTER_LIQUIDITY_MIN_SOL", "0"))

# Track recently seen token mints to avoid duplicate alerts
TOKEN_ALERT_COOLDOWN_MINUTES = int(os.getenv("TOKEN_ALERT_COOLDOWN_MINUTES", "60"))

# ========================================
# PHOTONSCAN API CONFIGURATION
# ========================================
# PhotonScan API endpoint for token discovery
PHOTON_API_URL = os.getenv("PHOTON_API_URL", "https://api.photon-sol.tinyastro.io/tokens")

# PhotonScan polling interval in seconds
PHOTON_POLL_INTERVAL = int(os.getenv("PHOTON_POLL_INTERVAL", "60"))

# PhotonScan API key (if required)
PHOTON_API_KEY = os.getenv("PHOTON_API_KEY", "")

# Configuration constants
MAX_PAIRS_FETCH = 100  # Maximum number of pairs to fetch per API call (used by deprecated fetch_new_coins and PhotonScan)
MAX_TRACKED_PAIRS = 1000  # Maximum number of pairs to keep in memory
TRACKED_PAIRS_TRIM_SIZE = 500  # Number of pairs to keep when trimming memory

# Default configuration
DEFAULT_CONFIG = {
    "api_source": "dexscreener",
    "network": "all",
    "social_links": {
        "telegram": False,
        "twitter": False,
        "website": False
    },
    "pair_age_min": 0,  # minutes
    "pair_age_max": 1440,  # 24 hours
    "market_cap_min": 0,
    "market_cap_max": 1000000000,
    "liquidity_min": 0,
    "liquidity_max": 10000000,
    "dev_hold_min": 0,  # percentage
    "dev_hold_max": 100,
    "top10_holders_min": 0,  # percentage
    "top10_holders_max": 100,
    "signals": [],  # List of {time_interval: int (minutes), price_change: float (percentage)}
    # PhotonScan-specific filters
    "photon_filters": {
        "social_tg": False,  # Require Telegram social link
        "dex_paid": False,  # Require DEX paid listing
        "mint_auth": True,  # Filter by mint authority (True = revoked/None, False = not revoked)
        "freeze_auth": True,  # Filter by freeze authority (True = revoked/None, False = not revoked)
        "lp_burned": False,  # Require LP tokens burned
        "top10_min": 0,  # Minimum top 10 holders percentage
        "top10_max": 100,  # Maximum top 10 holders percentage
        "audit": False,  # Require token audit
        "bonding_curve": False,  # Require bonding curve
    }
}

# Global configuration
user_config = DEFAULT_CONFIG.copy()


class MemeCoinBot:
    """Main bot class for meme coin alerts with WebSocket streaming"""
    
    def __init__(self):
        self.config = user_config.copy()
        self.session: Optional[aiohttp.ClientSession] = None
        self.solana_client: Optional[AsyncClient] = None
        self.tracked_pairs = {}  # Track pairs for signal monitoring
        self.last_checked_pairs = set()  # Track pairs we've already alerted on
        self.presets = {}  # Stores named configurations with tracking data
        self.active_preset = None  # Currently active preset name
        
        # WebSocket related attributes
        self.ws_connection = None
        self.ws_connected = False
        self.ws_reconnect_count = 0
        self.ws_task = None
        self.ws_monitoring_active = False
        
        # PhotonScan related attributes
        self.photon_tokens = set()  # Track tokens seen from PhotonScan to avoid duplicates
        self.photon_monitoring_active = False
        self.photon_task = None
    
    async def start_session(self):
        """Initialize aiohttp session and Solana client"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        if not self.solana_client:
            self.solana_client = AsyncClient(SOLANA_RPC_URL)
    
    async def close_session(self):
        """Close aiohttp session and Solana client"""
        if self.session:
            await self.session.close()
        if self.solana_client:
            await self.solana_client.close()
        if self.ws_connection:
            await self.ws_connection.close()
    
    async def connect_websocket(self, telegram_bot=None) -> bool:
        """
        ========================================
        SOLANA WEBSOCKET CONNECTION
        ========================================
        Establish WebSocket connection to Solana mainnet RPC
        
        This connects to the native Solana WebSocket endpoint to receive
        real-time blockchain events for SPL token detection.
        
        Args:
            telegram_bot: Optional Telegram bot instance for sending error alerts
            
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to Solana WebSocket: {SOLANA_WS_URL}")
            
            # Connect to Solana WebSocket endpoint
            self.ws_connection = await websockets.connect(
                SOLANA_WS_URL,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=10,
                close_timeout=10
            )
            
            self.ws_connected = True
            self.ws_reconnect_count = 0
            logger.info("Solana WebSocket connection established")
            
            # Subscribe to SPL Token Program logs for real-time token detection
            await self.subscribe_to_spl_token_logs()
            
            return True
            
        except WebSocketException as e:
            error_msg = f"Solana WebSocket connection error: {e}"
            logger.error(error_msg, exc_info=True)
            
            # Send Telegram alert for connection failure
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
            
            # Also send via bot if available
            if telegram_bot and TELEGRAM_CHAT_ID:
                try:
                    await telegram_bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=f"⚠️ Solana WebSocket Connection Failed\n\n{error_msg}"
                    )
                except Exception:
                    pass  # Ignore if bot message fails
            
            self.ws_connected = False
            return False
            
        except Exception as e:
            error_msg = f"Unexpected error connecting to Solana WebSocket: {e}"
            logger.error(error_msg, exc_info=True)
            
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
            
            self.ws_connected = False
            return False
    
    async def subscribe_to_spl_token_logs(self):
        """
        ========================================
        SPL TOKEN PROGRAM SUBSCRIPTION
        ========================================
        Subscribe to Solana logs for the SPL Token Program
        
        This subscribes to all log entries from the SPL Token Program to detect:
        - MintTo instructions (new token mints)
        - Transfer instructions (potential liquidity adds)
        - InitializeMint instructions (new token creation)
        """
        if not self.ws_connection:
            logger.error("Cannot subscribe: WebSocket not connected")
            return
        
        try:
            # Subscribe to logs for the SPL Token Program
            # This will receive all log entries for transactions involving the token program
            subscribe_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [
                    {
                        "mentions": [SPL_TOKEN_PROGRAM_ID]
                    },
                    {
                        "commitment": "confirmed"
                    }
                ]
            }
            
            logger.info(f"Subscribing to SPL Token Program logs: {SPL_TOKEN_PROGRAM_ID}")
            await self.ws_connection.send(json.dumps(subscribe_msg))
            logger.info(f"Subscription message sent for SPL Token Program")
            
            # Also subscribe to Raydium pool program for liquidity detection
            raydium_subscribe_msg = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "logsSubscribe",
                "params": [
                    {
                        "mentions": [RAYDIUM_LIQUIDITY_POOL_V4]
                    },
                    {
                        "commitment": "confirmed"
                    }
                ]
            }
            
            logger.info(f"Subscribing to Raydium Pool Program logs: {RAYDIUM_LIQUIDITY_POOL_V4}")
            await self.ws_connection.send(json.dumps(raydium_subscribe_msg))
            logger.info(f"Subscription message sent for Raydium Pool Program")
            
        except Exception as e:
            error_msg = f"Error subscribing to Solana logs: {e}"
            logger.error(error_msg, exc_info=True)
            
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
    
    async def handle_websocket_message(self, message: str, telegram_bot=None):
        """
        ========================================
        SOLANA LOG MESSAGE PARSER
        ========================================
        Parse and process incoming Solana WebSocket log messages
        
        This method processes log notifications from the SPL Token Program and
        Raydium Pool Program to detect:
        - New token mints (MintTo instructions)
        - Liquidity pool creation and additions
        - Token transfers that indicate trading activity
        
        Args:
            message: Raw WebSocket message string (JSON-RPC format)
            telegram_bot: Optional Telegram bot instance for sending alerts
        """
        try:
            # Parse JSON-RPC message
            data = json.loads(message)
            
            # Log received message for debugging
            logger.debug(f"Received Solana log message: {data}")
            
            # Handle subscription confirmation
            if isinstance(data, dict) and "result" in data:
                logger.info(f"Subscription confirmed: ID {data.get('id')}, Result: {data.get('result')}")
                return
            
            # Handle log notification from logsSubscribe
            if isinstance(data, dict) and data.get("method") == "logsNotification":
                params = data.get("params", {})
                result = params.get("result", {})
                
                # Extract log data
                logs = result.get("value", {}).get("logs", [])
                signature = result.get("value", {}).get("signature", "unknown")
                err = result.get("value", {}).get("err")
                
                # Skip failed transactions
                if err is not None:
                    logger.debug(f"Skipping failed transaction: {signature}")
                    return
                
                # Process the logs to detect token events
                await self.process_solana_logs(logs, signature, telegram_bot)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Solana log message as JSON: {e}")
        except Exception as e:
            error_msg = f"Error handling Solana log message: {e}"
            logger.error(error_msg, exc_info=True)
            
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
    
    async def process_solana_logs(self, logs: List[str], signature: str, telegram_bot=None):
        """
        ========================================
        SOLANA LOG PROCESSOR
        ========================================
        Process Solana transaction logs to detect token mints and liquidity events
        
        This method analyzes log entries to identify:
        - MintTo instructions (new tokens being minted)
        - InitializeMint instructions (brand new token creation)
        - Liquidity pool initialization (Raydium pools)
        
        Args:
            logs: List of log strings from the transaction
            signature: Transaction signature
            telegram_bot: Optional Telegram bot instance for sending alerts
        """
        try:
            # Join all logs into a single string for easier pattern matching
            # Note: Solana instruction logs follow a standardized format where
            # instruction names appear directly in the log output (e.g., "Instruction: MintTo")
            # This string-based parsing is reliable for detecting SPL Token instructions
            log_text = "\n".join(logs)
            
            # Detect MintTo instruction (indicates token minting activity)
            # SPL Token Program logs explicitly include "MintTo" or "Instruction: MintTo"
            if "MintTo" in log_text or "Instruction: MintTo" in log_text:
                logger.info(f"Detected MintTo instruction in transaction {signature}")
                await self.process_mint_event(logs, signature, telegram_bot)
            
            # Detect InitializeMint (brand new token creation)
            # SPL Token Program logs explicitly include "InitializeMint" for new tokens
            elif "InitializeMint" in log_text or "Instruction: InitializeMint" in log_text:
                logger.info(f"Detected InitializeMint in transaction {signature}")
                await self.process_new_token_creation(logs, signature, telegram_bot)
            
            # Detect Raydium pool initialization or liquidity add
            # Raydium logs include terms like "initialize", "liquidity", "pool", "swap"
            elif "initialize" in log_text.lower() and any(
                phrase in log_text for phrase in ["liquidity", "pool", "swap"]
            ):
                logger.info(f"Detected potential liquidity event in transaction {signature}")
                await self.process_liquidity_event(logs, signature, telegram_bot)
                
        except Exception as e:
            error_msg = f"Error processing Solana logs for {signature}: {e}"
            logger.error(error_msg, exc_info=True)
            
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
    
    async def process_mint_event(self, logs: List[str], signature: str, telegram_bot=None):
        """
        ========================================
        MINT EVENT PROCESSOR
        ========================================
        Process a MintTo instruction to extract token information
        
        Args:
            logs: Transaction logs
            signature: Transaction signature
            telegram_bot: Telegram bot for alerts
        """
        try:
            # Skip if already alerted recently
            if signature in self.last_checked_pairs:
                return
            
            # Fetch transaction details from Solana to get token mint address
            await self.start_session()
            
            # Get transaction data
            rpc_url = SOLANA_RPC_URL
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}
                ]
            }
            
            async with self.session.post(rpc_url, json=payload) as resp:
                if resp.status == 200:
                    tx_data = await resp.json()
                    result = tx_data.get("result", {})
                    
                    if result:
                        # Extract token mint addresses from parsed transaction
                        token_data = self.extract_token_from_transaction(result, signature)
                        
                        if token_data:
                            # Send alert for new token mint
                            await self.send_token_alert(token_data, telegram_bot)
                            
                            # Mark as alerted
                            self.last_checked_pairs.add(signature)
                            
                            # Limit memory
                            if len(self.last_checked_pairs) > MAX_TRACKED_PAIRS:
                                self.last_checked_pairs = set(
                                    list(self.last_checked_pairs)[-TRACKED_PAIRS_TRIM_SIZE:]
                                )
                                
        except Exception as e:
            logger.error(f"Error processing mint event {signature}: {e}")
    
    async def process_new_token_creation(self, logs: List[str], signature: str, telegram_bot=None):
        """
        ========================================
        NEW TOKEN CREATION PROCESSOR
        ========================================
        Process InitializeMint instruction for brand new token
        
        Args:
            logs: Transaction logs
            signature: Transaction signature
            telegram_bot: Telegram bot for alerts
        """
        # For now, treat similar to MintTo
        await self.process_mint_event(logs, signature, telegram_bot)
    
    async def process_liquidity_event(self, logs: List[str], signature: str, telegram_bot=None):
        """
        ========================================
        LIQUIDITY EVENT PROCESSOR
        ========================================
        Process Raydium pool initialization or liquidity add
        
        Args:
            logs: Transaction logs
            signature: Transaction signature
            telegram_bot: Telegram bot for alerts
        """
        try:
            # Skip if already alerted recently
            if signature in self.last_checked_pairs:
                return
            
            # Fetch transaction details
            await self.start_session()
            
            rpc_url = SOLANA_RPC_URL
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}
                ]
            }
            
            async with self.session.post(rpc_url, json=payload) as resp:
                if resp.status == 200:
                    tx_data = await resp.json()
                    result = tx_data.get("result", {})
                    
                    if result:
                        # Extract liquidity pool information
                        pool_data = self.extract_pool_from_transaction(result, signature)
                        
                        if pool_data:
                            # Send alert for new liquidity pool
                            await self.send_pool_alert(pool_data, telegram_bot)
                            
                            # Mark as alerted
                            self.last_checked_pairs.add(signature)
                            
                            # Limit memory
                            if len(self.last_checked_pairs) > MAX_TRACKED_PAIRS:
                                self.last_checked_pairs = set(
                                    list(self.last_checked_pairs)[-TRACKED_PAIRS_TRIM_SIZE:]
                                )
                                
        except Exception as e:
            logger.error(f"Error processing liquidity event {signature}: {e}")
    
    def extract_token_from_transaction(self, tx_result: Dict, signature: str) -> Optional[Dict]:
        """
        ========================================
        TOKEN DATA EXTRACTOR
        ========================================
        Extract token mint information from parsed transaction data
        
        Args:
            tx_result: Parsed transaction result from getTransaction
            signature: Transaction signature
            
        Returns:
            Dictionary with token data or None
        """
        try:
            meta = tx_result.get("meta", {})
            transaction = tx_result.get("transaction", {})
            message = transaction.get("message", {})
            instructions = message.get("instructions", [])
            
            # Look for token mints in postTokenBalances
            post_balances = meta.get("postTokenBalances", [])
            
            for balance in post_balances:
                mint = balance.get("mint")
                if mint:
                    # Found a token mint
                    ui_amount = balance.get("uiTokenAmount", {})
                    amount = ui_amount.get("uiAmountString", "0")
                    decimals = ui_amount.get("decimals", 0)
                    
                    return {
                        "mint": mint,
                        "amount": amount,
                        "decimals": decimals,
                        "signature": signature,
                        "timestamp": datetime.now()
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting token from transaction: {e}")
            return None
    
    def extract_pool_from_transaction(self, tx_result: Dict, signature: str) -> Optional[Dict]:
        """
        ========================================
        POOL DATA EXTRACTOR
        ========================================
        Extract Raydium pool information from transaction
        
        Args:
            tx_result: Parsed transaction result
            signature: Transaction signature
            
        Returns:
            Dictionary with pool data or None
        """
        try:
            meta = tx_result.get("meta", {})
            transaction = tx_result.get("transaction", {})
            
            # Extract token mints from postTokenBalances
            post_balances = meta.get("postTokenBalances", [])
            
            if len(post_balances) >= 2:
                # Likely a pool with at least 2 tokens
                tokens = []
                for balance in post_balances[:2]:
                    mint = balance.get("mint")
                    if mint:
                        tokens.append(mint)
                
                if len(tokens) >= 2:
                    return {
                        "baseMint": tokens[0],
                        "quoteMint": tokens[1],
                        "signature": signature,
                        "timestamp": datetime.now()
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting pool from transaction: {e}")
            return None
    
    async def send_token_alert(self, token_data: Dict, telegram_bot=None):
        """
        ========================================
        TOKEN ALERT SENDER
        ========================================
        Send Telegram alert for newly detected SPL token
        
        Args:
            token_data: Token information dictionary
            telegram_bot: Telegram bot instance
        """
        try:
            mint = token_data.get("mint", "Unknown")
            amount = token_data.get("amount", "0")
            signature = token_data.get("signature", "Unknown")
            
            message = f"🚀 **New SPL Token Mint Detected!**\n\n"
            message += f"**Token Mint:** `{mint}`\n"
            message += f"**Amount Minted:** {amount}\n"
            message += f"**Transaction:** `{signature}`\n"
            message += f"**Network:** Solana\n\n"
            message += f"**Explorer:** https://solscan.io/tx/{signature}\n"
            message += f"**Token:** https://solscan.io/token/{mint}\n"
            message += f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            
            if telegram_bot and TELEGRAM_CHAT_ID:
                try:
                    await telegram_bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Alert sent for token mint {mint}")
                    
                except Exception as e:
                    error_msg = f"Error sending alert for token {mint}: {e}"
                    logger.error(error_msg, exc_info=True)
                    
        except Exception as e:
            logger.error(f"Error in send_token_alert: {e}")
    
    async def send_pool_alert(self, pool_data: Dict, telegram_bot=None):
        """
        ========================================
        POOL ALERT SENDER
        ========================================
        Send Telegram alert for newly detected liquidity pool
        
        Args:
            pool_data: Pool information dictionary
            telegram_bot: Telegram bot instance
        """
        try:
            base_mint = pool_data.get("baseMint", "Unknown")
            quote_mint = pool_data.get("quoteMint", "Unknown")
            signature = pool_data.get("signature", "Unknown")
            
            message = f"💧 **New Liquidity Pool Detected!**\n\n"
            message += f"**Base Token:** `{base_mint}`\n"
            message += f"**Quote Token:** `{quote_mint}`\n"
            message += f"**Transaction:** `{signature}`\n"
            message += f"**Network:** Solana\n"
            message += f"**DEX:** Raydium\n\n"
            message += f"**Explorer:** https://solscan.io/tx/{signature}\n"
            message += f"**Base Token:** https://solscan.io/token/{base_mint}\n"
            message += f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            
            if telegram_bot and TELEGRAM_CHAT_ID:
                try:
                    await telegram_bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Alert sent for liquidity pool {base_mint}/{quote_mint}")
                    
                except Exception as e:
                    error_msg = f"Error sending pool alert: {e}"
                    logger.error(error_msg, exc_info=True)
                    
        except Exception as e:
            logger.error(f"Error in send_pool_alert: {e}")
    
    async def fetch_photon_tokens(self) -> List[Dict]:
        """
        ========================================
        PHOTONSCAN TOKEN FETCHER
        ========================================
        Fetch new tokens from PhotonScan API with filters
        
        Returns:
            List of token dictionaries matching filters
        """
        await self.start_session()
        
        try:
            # Build query parameters based on Photon filters
            params = {
                "limit": 100,
                "offset": 0,
            }
            
            # Add API key if configured
            headers = {}
            if PHOTON_API_KEY:
                headers["Authorization"] = f"Bearer {PHOTON_API_KEY}"
            
            logger.info(f"Fetching tokens from PhotonScan API: {PHOTON_API_URL}")
            
            # Make request to PhotonScan API
            async with self.session.get(PHOTON_API_URL, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Extract tokens from response with validation
                    # Try common response formats: {"tokens": [...]}, {"data": [...]}, or direct array
                    tokens = None
                    
                    if isinstance(data, dict):
                        # Try "tokens" key first
                        if "tokens" in data:
                            tokens = data["tokens"]
                        # Fallback to "data" key
                        elif "data" in data:
                            tokens = data["data"]
                            logger.debug("PhotonScan response using 'data' key instead of 'tokens'")
                        else:
                            logger.warning(f"PhotonScan response has unexpected structure. Keys: {list(data.keys())}")
                    elif isinstance(data, list):
                        # Direct array response
                        tokens = data
                        logger.debug("PhotonScan response is a direct array")
                    
                    if tokens is not None and isinstance(tokens, list):
                        logger.info(f"Fetched {len(tokens)} tokens from PhotonScan")
                        return tokens
                    else:
                        logger.warning(f"Unexpected response format from PhotonScan: {type(data)}")
                        return []
                else:
                    error_text = await resp.text()
                    logger.error(f"PhotonScan API error: HTTP {resp.status} - {error_text}")
                    return []
                    
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching from PhotonScan: {e}")
            return []
        except Exception as e:
            error_msg = f"Error fetching tokens from PhotonScan: {e}"
            logger.error(error_msg, exc_info=True)
            
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
            return []
    
    def apply_photon_filters(self, token: Dict) -> bool:
        """
        ========================================
        PHOTON FILTER APPLIER
        ========================================
        Apply PhotonScan-specific filters to a token
        
        Args:
            token: Token data dictionary from PhotonScan
            
        Returns:
            True if token passes all filters, False otherwise
        """
        try:
            photon_filters = self.config.get("photon_filters", {})
            
            # Social Telegram filter
            if photon_filters.get("social_tg", False):
                socials = token.get("socials", {})
                if not socials.get("telegram"):
                    return False
            
            # DEX paid listing filter
            if photon_filters.get("dex_paid", False):
                if not token.get("dex_paid", False):
                    return False
            
            # Mint authority filter (True = must be revoked, False = any)
            if photon_filters.get("mint_auth", True):
                mint_authority = token.get("mint_authority")
                # If filter is True, we want revoked (None, empty string, null, or zero address)
                # Common representations of revoked authority
                revoked_values = [None, "", "null", "0x0", "11111111111111111111111111111111"]
                if mint_authority not in revoked_values:
                    return False
            
            # Freeze authority filter (True = must be revoked, False = any)
            if photon_filters.get("freeze_auth", True):
                freeze_authority = token.get("freeze_authority")
                # If filter is True, we want revoked (None, empty string, null, or zero address)
                revoked_values = [None, "", "null", "0x0", "11111111111111111111111111111111"]
                if freeze_authority not in revoked_values:
                    return False
            
            # LP burned filter
            if photon_filters.get("lp_burned", False):
                if not token.get("lp_burned", False):
                    return False
            
            # Top 10 holders percentage filter
            top10_percent = token.get("top10_holders_percent", 0)
            top10_min = photon_filters.get("top10_min", 0)
            top10_max = photon_filters.get("top10_max", 100)
            
            if not (top10_min <= top10_percent <= top10_max):
                return False
            
            # Audit filter
            if photon_filters.get("audit", False):
                if not token.get("audited", False):
                    return False
            
            # Bonding curve filter
            if photon_filters.get("bonding_curve", False):
                if not token.get("bonding_curve", False):
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error applying Photon filters: {e}")
            return False
    
    def format_photon_alert(self, token: Dict) -> str:
        """
        ========================================
        PHOTON TOKEN ALERT FORMATTER
        ========================================
        Format PhotonScan token information for alert message
        
        Args:
            token: Token data from PhotonScan
            
        Returns:
            Formatted message string
        """
        mint = token.get("mint", token.get("address", "Unknown"))
        name = token.get("name", "Unknown")
        symbol = token.get("symbol", "Unknown")
        
        message = f"🔍 **New Token from PhotonScan!**\n\n"
        message += f"**Name:** {name}\n"
        message += f"**Symbol:** {symbol}\n"
        message += f"**Mint:** `{mint}`\n"
        message += f"**Network:** Solana\n\n"
        
        # Add market data if available
        if "market_cap" in token:
            message += f"**Market Cap:** ${token['market_cap']:,.2f}\n"
        
        if "liquidity" in token:
            message += f"**Liquidity:** ${token['liquidity']:,.2f}\n"
        
        if "price" in token:
            message += f"**Price:** ${token['price']}\n"
        
        # Add security info
        message += f"\n**Security:**\n"
        
        mint_auth = token.get("mint_authority")
        message += f"- Mint Authority: {'✅ Revoked' if not mint_auth else '❌ Not Revoked'}\n"
        
        freeze_auth = token.get("freeze_authority")
        message += f"- Freeze Authority: {'✅ Revoked' if not freeze_auth else '❌ Not Revoked'}\n"
        
        if token.get("lp_burned"):
            message += f"- LP Burned: ✅\n"
        
        if token.get("audited"):
            message += f"- Audited: ✅\n"
        
        if "top10_holders_percent" in token:
            message += f"- Top 10 Holders: {token['top10_holders_percent']:.2f}%\n"
        
        # Add social links
        socials = token.get("socials", {})
        if socials:
            message += f"\n**Socials:**\n"
            if socials.get("telegram"):
                message += f"- [Telegram]({socials['telegram']})\n"
            if socials.get("twitter"):
                message += f"- [Twitter]({socials['twitter']})\n"
            if socials.get("website"):
                message += f"- [Website]({socials['website']})\n"
        
        # Add links
        message += f"\n**Links:**\n"
        message += f"- [Solscan](https://solscan.io/token/{mint})\n"
        message += f"- [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\n"
        
        if token.get("dex_paid"):
            message += f"\n💎 **DEX Paid Listing**\n"
        
        message += f"\n**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        
        return message
    
    async def send_photon_alert(self, token: Dict, telegram_bot=None):
        """
        ========================================
        PHOTON ALERT SENDER
        ========================================
        Send Telegram alert for PhotonScan token
        
        Args:
            token: Token information dictionary
            telegram_bot: Telegram bot instance
        """
        try:
            message = self.format_photon_alert(token)
            
            if telegram_bot and TELEGRAM_CHAT_ID:
                try:
                    await telegram_bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=message,
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                    
                    mint = token.get("mint", token.get("address", "Unknown"))
                    logger.info(f"PhotonScan alert sent for token {mint}")
                    
                except Exception as e:
                    error_msg = f"Error sending PhotonScan alert: {e}"
                    logger.error(error_msg, exc_info=True)
                    
        except Exception as e:
            logger.error(f"Error in send_photon_alert: {e}")
    
    async def fetch_new_coins(self) -> List[Dict]:
        """
        DEPRECATED: This method is no longer used in favor of WebSocket streaming.
        
        Fetch new Raydium pools from Solana blockchain using pagination
        
        Note: This fetches program accounts from the Raydium V4 AMM program.
        Uses getProgramAccountsV2 with pagination to avoid rate limiting.
        
        MIGRATION NOTE:
        This REST API polling approach has been replaced by WebSocket streaming
        from OpenOcean Meme API for real-time token updates. This method is kept
        for backward compatibility but is not actively used in the main monitoring flow.
        
        In production with high RPC usage, consider:
        - Using a dedicated RPC provider (QuickNode, Alchemy, Helius, etc.)
        - Implementing websocket subscriptions for real-time updates
        - Adding memcmp filters to query specific pool states
        - Caching results to reduce RPC calls
        - Rate limiting to avoid hitting RPC endpoint limits
        
        For better performance, you could filter by:
        - Pool creation time (memcmp on poolOpenTime field)
        - Pool status (memcmp on status field)
        - Specific token mints (memcmp on baseMint/quoteMint)
        """
        await self.start_session()
        
        try:
            pools = []
            page = 1
            page_size = 1000  # Fetch in batches of 1000
            should_continue = True  # Flag to control outer pagination loop
            
            # Fetch accounts using pagination
            while len(pools) < MAX_PAIRS_FETCH and should_continue:
                try:
                    # Use getProgramAccountsV2 with pagination via custom RPC call
                    # This is required by Helius RPC to avoid deprioritization
                    params = {
                        "programId": RAYDIUM_V4_PROGRAM_ID,
                        "commitment": "confirmed",
                        "encoding": "base64",
                        "pagination": {
                            "page": page,
                            "limit": page_size
                        }
                    }
                    
                    # Make custom RPC request using direct HTTP POST to the endpoint
                    # This avoids reliance on internal library structures like _provider
                    # which may change in library updates
                    try:
                        # Construct JSON-RPC payload manually
                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getProgramAccountsV2",
                            "params": [params]
                        }
                        
                        # Make direct HTTP POST request using aiohttp
                        async with self.session.post(SOLANA_RPC_URL, json=payload) as resp:
                            # Check HTTP status before parsing
                            if resp.status != 200:
                                error_msg = f"HTTP error {resp.status} from RPC endpoint on page {page}"
                                logger.error(error_msg)
                                # Send error alert to Telegram
                                if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                                    await send_error_alert(
                                        TELEGRAM_BOT_TOKEN,
                                        TELEGRAM_CHAT_ID,
                                        f"RPC Error: {error_msg}"
                                    )
                                break
                            
                            response = await resp.json()
                            
                    except aiohttp.ClientError as e:
                        error_msg = f"Network error making RPC call on page {page}: {e}"
                        logger.error(error_msg, exc_info=True)
                        # Send error alert to Telegram
                        if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                            await send_error_alert(
                                TELEGRAM_BOT_TOKEN,
                                TELEGRAM_CHAT_ID,
                                error_msg,
                                exc_info=sys.exc_info()
                            )
                        break
                    except json.JSONDecodeError as e:
                        error_msg = f"Invalid JSON response from RPC endpoint on page {page}: {e}"
                        logger.error(error_msg, exc_info=True)
                        if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                            await send_error_alert(
                                TELEGRAM_BOT_TOKEN,
                                TELEGRAM_CHAT_ID,
                                error_msg,
                                exc_info=sys.exc_info()
                            )
                        break
                    except Exception as e:
                        error_msg = f"Unexpected error making RPC call on page {page}: {e}"
                        logger.error(error_msg, exc_info=True)
                        if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                            await send_error_alert(
                                TELEGRAM_BOT_TOKEN,
                                TELEGRAM_CHAT_ID,
                                error_msg,
                                exc_info=sys.exc_info()
                            )
                        break
                    
                    # Check if we got a valid response
                    if not response or "result" not in response:
                        logger.warning(f"No result in response for page {page}. "
                                     f"Response keys: {list(response.keys()) if response else 'None'}")
                        break
                    
                    # Extract accounts from the response
                    result = response["result"]
                    
                    # Handle different response formats
                    if isinstance(result, dict) and "accounts" in result:
                        accounts = result["accounts"]
                    elif isinstance(result, list):
                        accounts = result
                    else:
                        logger.warning(f"Unexpected result format: {type(result).__name__}. "
                                     f"Result keys: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
                        break
                    
                    # If no accounts returned, we've reached the end
                    if not accounts:
                        break
                    
                    # Process each account
                    for account_info in accounts:
                        # Stop if we've reached MAX_PAIRS_FETCH
                        if len(pools) >= MAX_PAIRS_FETCH:
                            should_continue = False
                            break
                        
                        try:
                            # Extract pubkey and account data from response
                            # Response format: {"pubkey": "...", "account": {"data": [...], ...}}
                            pubkey = account_info.get("pubkey")
                            account = account_info.get("account")
                            
                            if not pubkey or not account:
                                continue
                            
                            # Get the data field
                            data = account.get("data")
                            if not data:
                                continue
                            
                            # Data is in [base64_string, encoding] format
                            if isinstance(data, list) and len(data) > 0:
                                account_data = base64.b64decode(data[0])
                            else:
                                continue
                            
                            # Skip if data is too small for our layout
                            if len(account_data) < MIN_POOL_DATA_SIZE:
                                continue
                            
                            # Parse using our layout
                            # Note: Construct will only parse the defined fields and ignore extra bytes
                            pool_data = LIQUIDITY_STATE_LAYOUT_V4.parse(account_data)
                            
                            # Convert to dict format
                            pool = {
                                "poolAddress": pubkey,
                                "baseMint": base58.b58encode(pool_data.baseMint).decode('utf-8'),
                                "quoteMint": base58.b58encode(pool_data.quoteMint).decode('utf-8'),
                                "lpMint": base58.b58encode(pool_data.lpMint).decode('utf-8'),
                                "baseVault": base58.b58encode(pool_data.baseVault).decode('utf-8'),
                                "quoteVault": base58.b58encode(pool_data.quoteVault).decode('utf-8'),
                                "marketId": base58.b58encode(pool_data.marketId).decode('utf-8'),
                                "poolOpenTime": pool_data.poolOpenTime,
                                "status": pool_data.status,
                                "baseDecimal": pool_data.baseDecimal,
                                "quoteDecimal": pool_data.quoteDecimal,
                                "swapBaseInAmount": pool_data.swapBaseInAmount,
                                "swapQuoteOutAmount": pool_data.swapQuoteOutAmount,
                                "swapQuoteInAmount": pool_data.swapQuoteInAmount,
                                "swapBaseOutAmount": pool_data.swapBaseOutAmount,
                            }
                            
                            pools.append(pool)
                            
                        except Exception as e:
                            logger.error(f"Error parsing pool account: {e}")
                            continue
                    
                    # If we got fewer accounts than page_size, we've reached the end
                    if len(accounts) < page_size:
                        break
                    
                    # Move to next page
                    page += 1
                    
                except Exception as e:
                    error_msg = f"Error fetching page {page}: {e}"
                    logger.error(error_msg, exc_info=True)
                    if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                        await send_error_alert(
                            TELEGRAM_BOT_TOKEN,
                            TELEGRAM_CHAT_ID,
                            error_msg,
                            exc_info=sys.exc_info()
                        )
                    break
            
            logger.info(f"Fetched {len(pools)} pools from Raydium V4 program")
            return pools
            
        except Exception as e:
            error_msg = f"Error fetching pools from Solana: {e}"
            logger.error(error_msg, exc_info=True)
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
            return []
    
    def apply_filters(self, pool: Dict) -> bool:
        """Apply user-defined filters to a Raydium pool"""
        try:
            # Network filter - Solana only for Raydium pools
            if self.config["network"] != "all" and self.config["network"] != "solana":
                return False
            
            # Pool age filter - check when the pool was opened
            pool_open_time = pool.get("poolOpenTime", 0)
            if pool_open_time > 0:
                # Convert Unix timestamp to datetime
                created_time = datetime.fromtimestamp(pool_open_time)
                age_minutes = (datetime.now() - created_time).total_seconds() / 60
                
                # Apply age filter
                if not (self.config["pair_age_min"] <= age_minutes <= self.config["pair_age_max"]):
                    return False
            
            # For now, we don't have direct liquidity and market cap from on-chain data
            # These would need to be fetched from additional sources or calculated
            # We'll accept pools that pass the basic filters
            
            # Check if pool is active (status should be > 0)
            if pool.get("status", 0) == 0:
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error applying filters: {e}")
            return False
    
    def format_coin_alert(self, pool: Dict) -> str:
        """Format Raydium pool information for alert message"""
        pool_address = pool.get("poolAddress", "Unknown")
        base_mint = pool.get("baseMint", "Unknown")
        quote_mint = pool.get("quoteMint", "Unknown")
        pool_open_time = pool.get("poolOpenTime", 0)
        
        # Format the open time
        if pool_open_time > 0:
            open_datetime = datetime.fromtimestamp(pool_open_time)
            time_str = open_datetime.strftime("%Y-%m-%d %H:%M:%S UTC")
            age_minutes = (datetime.now() - open_datetime).total_seconds() / 60
            age_str = f"{age_minutes:.1f} minutes ago"
        else:
            time_str = "Unknown"
            age_str = "Unknown"
        
        # Calculate approximate liquidity based on swap amounts
        swap_base_in = pool.get("swapBaseInAmount", 0)
        swap_quote_out = pool.get("swapQuoteOutAmount", 0)
        swap_quote_in = pool.get("swapQuoteInAmount", 0)
        swap_base_out = pool.get("swapBaseOutAmount", 0)
        
        message = f"🚀 **New Raydium Pool Detected!**\n\n"
        message += f"**Pool Address:** `{pool_address}`\n"
        message += f"**Chain:** Solana\n"
        message += f"**DEX:** Raydium V4\n\n"
        
        message += f"**Base Token:** `{base_mint}`\n"
        message += f"**Quote Token:** `{quote_mint}`\n\n"
        
        message += f"**Pool Opened:** {time_str}\n"
        message += f"**Age:** {age_str}\n\n"
        
        message += f"**Status:** {'Active' if pool.get('status', 0) > 0 else 'Inactive'}\n"
        
        # Show swap statistics if available
        if swap_base_in > 0 or swap_quote_in > 0:
            message += f"\n**Trading Activity:**\n"
            message += f"- Base In: {swap_base_in}\n"
            message += f"- Quote Out: {swap_quote_out}\n"
            message += f"- Quote In: {swap_quote_in}\n"
            message += f"- Base Out: {swap_base_out}\n"
        
        # Add links
        message += f"\n**Solscan:** https://solscan.io/account/{pool_address}\n"
        message += f"**Base Token:** https://solscan.io/token/{base_mint}\n"
        
        return message


# Initialize bot instance
bot_instance = MemeCoinBot()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    keyboard = [
        [InlineKeyboardButton("⚙️ Configure Filters", callback_data="config_main")],
        [InlineKeyboardButton("📊 View Current Config", callback_data="view_config")],
        [InlineKeyboardButton("💾 Manage Presets", callback_data="presets_main")],
        [InlineKeyboardButton("🚀 Start Monitoring", callback_data="start_monitoring")],
        [InlineKeyboardButton("⏹️ Stop Monitoring", callback_data="stop_monitoring")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    preset_info = f" (Using preset: {bot_instance.active_preset})" if bot_instance.active_preset else ""
    
    welcome_text = (
        f"🤖 **Welcome to Meme Coin Alert Bot!**{preset_info}\n\n"
        "This bot monitors new meme coin launches and sends alerts based on your filters.\n\n"
        "Use the buttons below to:\n"
        "- Configure your filters and alert settings\n"
        "- View your current configuration\n"
        "- Manage configuration presets\n"
        "- Start/Stop monitoring for new coins\n"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks
    
    Note: Conflict errors may occur if multiple bot instances are running simultaneously.
    Ensure only one instance is active to avoid "Message is not modified" errors.
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "config_main":
        await show_config_menu(query)
    elif data == "view_config":
        await view_config(query)
    elif data == "presets_main":
        await show_presets_menu(query, context)
    elif data == "start_monitoring":
        await start_monitoring(query, context)
    elif data == "stop_monitoring":
        await stop_monitoring(query, context)
    elif data == "config_network":
        await config_network(query)
    elif data == "config_social":
        await config_social(query)
    elif data == "config_age":
        await config_age(query)
    elif data == "config_marketcap":
        await config_marketcap(query)
    elif data == "config_liquidity":
        await config_liquidity(query)
    elif data == "config_signals":
        await config_signals(query)
    elif data == "config_photon":
        await config_photon(query)
    elif data == "back_main":
        await back_to_main(query)
    elif data.startswith("set_network_"):
        await set_network(query, data.split("_")[2])
    elif data.startswith("toggle_social_"):
        await toggle_social(query, data.split("_")[2])
    elif data.startswith("toggle_photon_"):
        # Extract filter name correctly for multi-part names (e.g., social_tg, freeze_auth)
        await toggle_photon_filter(query, "_".join(data.split("_")[2:]))
    elif data.startswith("set_age_"):
        await set_age(query, data)
    elif data.startswith("set_mc_"):
        await set_marketcap(query, data)
    elif data.startswith("set_liq_"):
        await set_liquidity(query, data)
    elif data.startswith("set_photon_top10_"):
        await set_photon_top10(query, data)
    elif data.startswith("add_signal_"):
        await add_signal(query, data)
    elif data.startswith("preset_save"):
        await save_preset_prompt(query, context)
    elif data.startswith("preset_load_"):
        preset_name = "_".join(data.split("_")[2:])
        await load_preset(query, context, preset_name)
    elif data.startswith("preset_delete_"):
        preset_name = "_".join(data.split("_")[2:])
        await delete_preset(query, context, preset_name)
    elif data.startswith("preset_view_"):
        preset_name = "_".join(data.split("_")[2:])
        await view_preset_stats(query, context, preset_name)
    elif data.startswith("preset_refresh_"):
        preset_name = "_".join(data.split("_")[2:])
        await refresh_preset_stats(query, context, preset_name)
    elif data.startswith("custom_"):
        await handle_custom_button(query, context, data)


async def show_config_menu(query) -> None:
    """Show main configuration menu"""
    keyboard = [
        [InlineKeyboardButton("🌐 Network/Chain", callback_data="config_network")],
        [InlineKeyboardButton("📱 Social Links", callback_data="config_social")],
        [InlineKeyboardButton("⏰ Pair Age", callback_data="config_age")],
        [InlineKeyboardButton("💰 Market Cap", callback_data="config_marketcap")],
        [InlineKeyboardButton("💧 Liquidity", callback_data="config_liquidity")],
        [InlineKeyboardButton("📈 Signal Settings", callback_data="config_signals")],
        [InlineKeyboardButton("🔍 Photon Filters", callback_data="config_photon")],
        [InlineKeyboardButton("◀️ Back", callback_data="back_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚙️ **Configuration Menu**\n\nSelect a filter to configure:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def config_network(query) -> None:
    """Configure network/chain filter"""
    keyboard = [
        [InlineKeyboardButton("All Networks", callback_data="set_network_all")],
        [InlineKeyboardButton("Ethereum", callback_data="set_network_ethereum")],
        [InlineKeyboardButton("BSC", callback_data="set_network_bsc")],
        [InlineKeyboardButton("Polygon", callback_data="set_network_polygon")],
        [InlineKeyboardButton("Solana", callback_data="set_network_solana")],
        [InlineKeyboardButton("Base", callback_data="set_network_base")],
        [InlineKeyboardButton("Arbitrum", callback_data="set_network_arbitrum")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current = bot_instance.config["network"]
    await query.edit_message_text(
        f"🌐 **Network/Chain Filter**\n\nCurrent: {current}\n\nSelect a network:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def set_network(query, network: str) -> None:
    """Set network filter"""
    bot_instance.config["network"] = network
    await query.answer(f"Network set to: {network}")
    await config_network(query)


async def config_social(query) -> None:
    """Configure social links filter"""
    config = bot_instance.config["social_links"]
    
    keyboard = [
        [InlineKeyboardButton(
            f"Telegram: {'✅' if config['telegram'] else '❌'}",
            callback_data="toggle_social_telegram"
        )],
        [InlineKeyboardButton(
            f"Twitter: {'✅' if config['twitter'] else '❌'}",
            callback_data="toggle_social_twitter"
        )],
        [InlineKeyboardButton(
            f"Website: {'✅' if config['website'] else '❌'}",
            callback_data="toggle_social_website"
        )],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📱 **Social Links Filter**\n\nToggle required social links:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def toggle_social(query, social_type: str) -> None:
    """Toggle social link requirement"""
    bot_instance.config["social_links"][social_type] = not bot_instance.config["social_links"][social_type]
    await query.answer(f"{social_type.capitalize()} toggled")
    await config_social(query)


async def config_age(query) -> None:
    """Configure pair age filter"""
    keyboard = [
        [InlineKeyboardButton("0-5 min", callback_data="set_age_0_5")],
        [InlineKeyboardButton("0-15 min", callback_data="set_age_0_15")],
        [InlineKeyboardButton("0-30 min", callback_data="set_age_0_30")],
        [InlineKeyboardButton("0-1 hour", callback_data="set_age_0_60")],
        [InlineKeyboardButton("0-24 hours", callback_data="set_age_0_1440")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_age_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_age_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["pair_age_min"]
    current_max = bot_instance.config["pair_age_max"]
    
    await query.edit_message_text(
        f"⏰ **Pair Age Filter**\n\nCurrent: {current_min}-{current_max} minutes\n\nSelect range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def set_age(query, data: str) -> None:
    """Set pair age filter"""
    parts = data.split("_")
    min_age = int(parts[2])
    max_age = int(parts[3])
    
    bot_instance.config["pair_age_min"] = min_age
    bot_instance.config["pair_age_max"] = max_age
    
    await query.answer(f"Pair age set to: {min_age}-{max_age} minutes")
    await config_age(query)


async def config_marketcap(query) -> None:
    """Configure market cap filter"""
    keyboard = [
        [InlineKeyboardButton("$0 - $50K", callback_data="set_mc_0_50000")],
        [InlineKeyboardButton("$0 - $100K", callback_data="set_mc_0_100000")],
        [InlineKeyboardButton("$0 - $500K", callback_data="set_mc_0_500000")],
        [InlineKeyboardButton("$0 - $1M", callback_data="set_mc_0_1000000")],
        [InlineKeyboardButton("$0 - $10M", callback_data="set_mc_0_10000000")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_mc_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_mc_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["market_cap_min"]
    current_max = bot_instance.config["market_cap_max"]
    
    await query.edit_message_text(
        f"💰 **Market Cap Filter**\n\nCurrent: ${current_min:,} - ${current_max:,}\n\nSelect range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def set_marketcap(query, data: str) -> None:
    """Set market cap filter"""
    parts = data.split("_")
    min_mc = int(parts[2])
    max_mc = int(parts[3])
    
    bot_instance.config["market_cap_min"] = min_mc
    bot_instance.config["market_cap_max"] = max_mc
    
    await query.answer(f"Market cap set")
    await config_marketcap(query)


async def config_liquidity(query) -> None:
    """Configure liquidity filter"""
    keyboard = [
        [InlineKeyboardButton("$0 - $10K", callback_data="set_liq_0_10000")],
        [InlineKeyboardButton("$0 - $50K", callback_data="set_liq_0_50000")],
        [InlineKeyboardButton("$0 - $100K", callback_data="set_liq_0_100000")],
        [InlineKeyboardButton("$0 - $500K", callback_data="set_liq_0_500000")],
        [InlineKeyboardButton("$0 - $1M", callback_data="set_liq_0_1000000")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_liq_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_liq_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["liquidity_min"]
    current_max = bot_instance.config["liquidity_max"]
    
    await query.edit_message_text(
        f"💧 **Liquidity Filter**\n\nCurrent: ${current_min:,} - ${current_max:,}\n\nSelect range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def set_liquidity(query, data: str) -> None:
    """Set liquidity filter"""
    parts = data.split("_")
    min_liq = int(parts[2])
    max_liq = int(parts[3])
    
    bot_instance.config["liquidity_min"] = min_liq
    bot_instance.config["liquidity_max"] = max_liq
    
    await query.answer(f"Liquidity set")
    await config_liquidity(query)


async def config_signals(query) -> None:
    """Configure signal settings"""
    keyboard = [
        [InlineKeyboardButton("5 min / +10%", callback_data="add_signal_5_10")],
        [InlineKeyboardButton("15 min / +20%", callback_data="add_signal_15_20")],
        [InlineKeyboardButton("30 min / +50%", callback_data="add_signal_30_50")],
        [InlineKeyboardButton("1 hour / +100%", callback_data="add_signal_60_100")],
        [InlineKeyboardButton("✏️ Custom Signal", callback_data="custom_signal")],
        [InlineKeyboardButton("Clear All Signals", callback_data="add_signal_clear_0")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    signals_text = "\n".join([
        f"- {s['time_interval']} min / +{s['price_change']}%"
        for s in bot_instance.config["signals"]
    ]) or "None"
    
    await query.edit_message_text(
        f"📈 **Signal Settings**\n\nCurrent signals:\n{signals_text}\n\nAdd a signal:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def add_signal(query, data: str) -> None:
    """Add or clear signal settings"""
    parts = data.split("_")
    
    if parts[2] == "clear":
        bot_instance.config["signals"] = []
        await query.answer("All signals cleared")
    else:
        time_interval = int(parts[2])
        price_change = float(parts[3])
        
        signal = {"time_interval": time_interval, "price_change": price_change}
        if signal not in bot_instance.config["signals"]:
            bot_instance.config["signals"].append(signal)
            await query.answer(f"Signal added: {time_interval}min / +{price_change}%")
        else:
            await query.answer("Signal already exists")
    
    await config_signals(query)


async def config_photon(query) -> None:
    """Configure PhotonScan filters"""
    photon_filters = bot_instance.config.get("photon_filters", {})
    
    keyboard = [
        [InlineKeyboardButton(
            f"Telegram Social: {'✅' if photon_filters.get('social_tg') else '❌'}",
            callback_data="toggle_photon_social_tg"
        )],
        [InlineKeyboardButton(
            f"DEX Paid: {'✅' if photon_filters.get('dex_paid') else '❌'}",
            callback_data="toggle_photon_dex_paid"
        )],
        [InlineKeyboardButton(
            f"Mint Authority Revoked: {'✅' if photon_filters.get('mint_auth') else '❌'}",
            callback_data="toggle_photon_mint_auth"
        )],
        [InlineKeyboardButton(
            f"Freeze Authority Revoked: {'✅' if photon_filters.get('freeze_auth') else '❌'}",
            callback_data="toggle_photon_freeze_auth"
        )],
        [InlineKeyboardButton(
            f"LP Burned: {'✅' if photon_filters.get('lp_burned') else '❌'}",
            callback_data="toggle_photon_lp_burned"
        )],
        [InlineKeyboardButton(
            f"Audit Required: {'✅' if photon_filters.get('audit') else '❌'}",
            callback_data="toggle_photon_audit"
        )],
        [InlineKeyboardButton(
            f"Bonding Curve: {'✅' if photon_filters.get('bonding_curve') else '❌'}",
            callback_data="toggle_photon_bonding_curve"
        )],
        [InlineKeyboardButton("📊 Top 10 Holders %", callback_data="config_photon_top10")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    top10_min = photon_filters.get("top10_min", 0)
    top10_max = photon_filters.get("top10_max", 100)
    
    await query.edit_message_text(
        f"🔍 **PhotonScan Filters**\n\n"
        f"Top 10 Holders: {top10_min}% - {top10_max}%\n\n"
        f"Toggle filters below:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def toggle_photon_filter(query, filter_name: str) -> None:
    """Toggle PhotonScan filter"""
    photon_filters = bot_instance.config.get("photon_filters", {})
    
    if filter_name in photon_filters:
        photon_filters[filter_name] = not photon_filters[filter_name]
        await query.answer(f"{filter_name.replace('_', ' ').title()} toggled")
    
    await config_photon(query)


async def config_photon_top10(query) -> None:
    """Configure top 10 holders percentage range for PhotonScan"""
    keyboard = [
        [InlineKeyboardButton("0% - 30%", callback_data="set_photon_top10_0_30")],
        [InlineKeyboardButton("0% - 50%", callback_data="set_photon_top10_0_50")],
        [InlineKeyboardButton("0% - 70%", callback_data="set_photon_top10_0_70")],
        [InlineKeyboardButton("0% - 100%", callback_data="set_photon_top10_0_100")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_photon_top10_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_photon_top10_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_photon")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    photon_filters = bot_instance.config.get("photon_filters", {})
    current_min = photon_filters.get("top10_min", 0)
    current_max = photon_filters.get("top10_max", 100)
    
    await query.edit_message_text(
        f"📊 **Top 10 Holders Percentage**\n\n"
        f"Current: {current_min}% - {current_max}%\n\n"
        f"Select range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def set_photon_top10(query, data: str) -> None:
    """Set PhotonScan top 10 holders percentage"""
    parts = data.split("_")
    min_percent = int(parts[3])
    max_percent = int(parts[4])
    
    photon_filters = bot_instance.config.get("photon_filters", {})
    photon_filters["top10_min"] = min_percent
    photon_filters["top10_max"] = max_percent
    
    await query.answer(f"Top 10 holders set to {min_percent}%-{max_percent}%")
    await config_photon_top10(query)


async def handle_custom_button(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Handle custom value button clicks"""
    
    if data == "custom_signal":
        # Custom signal - need time AND price
        context.user_data["input_state"] = {"type": "signal_time"}
        await query.edit_message_text(
            "📈 **Custom Signal**\n\n"
            "Please enter the time interval in minutes:\n"
            "Example: 10",
            parse_mode="Markdown"
        )
    elif data == "custom_age_min":
        context.user_data["input_state"] = {"type": "age_min"}
        await query.edit_message_text(
            "⏰ **Custom Minimum Pair Age**\n\n"
            "Please enter minimum age in minutes:\n"
            "Example: 5",
            parse_mode="Markdown"
        )
    elif data == "custom_age_max":
        context.user_data["input_state"] = {"type": "age_max"}
        await query.edit_message_text(
            "⏰ **Custom Maximum Pair Age**\n\n"
            "Please enter maximum age in minutes:\n"
            "Example: 60",
            parse_mode="Markdown"
        )
    elif data == "custom_mc_min":
        context.user_data["input_state"] = {"type": "mc_min"}
        await query.edit_message_text(
            "💰 **Custom Minimum Market Cap**\n\n"
            "Please enter minimum market cap in USD:\n"
            "Example: 50000",
            parse_mode="Markdown"
        )
    elif data == "custom_mc_max":
        context.user_data["input_state"] = {"type": "mc_max"}
        await query.edit_message_text(
            "💰 **Custom Maximum Market Cap**\n\n"
            "Please enter maximum market cap in USD:\n"
            "Example: 1000000",
            parse_mode="Markdown"
        )
    elif data == "custom_liq_min":
        context.user_data["input_state"] = {"type": "liq_min"}
        await query.edit_message_text(
            "💧 **Custom Minimum Liquidity**\n\n"
            "Please enter minimum liquidity in USD:\n"
            "Example: 10000",
            parse_mode="Markdown"
        )
    elif data == "custom_liq_max":
        context.user_data["input_state"] = {"type": "liq_max"}
        await query.edit_message_text(
            "💧 **Custom Maximum Liquidity**\n\n"
            "Please enter maximum liquidity in USD:\n"
            "Example: 500000",
            parse_mode="Markdown"
        )
    elif data == "custom_photon_top10_min":
        context.user_data["input_state"] = {"type": "photon_top10_min"}
        await query.edit_message_text(
            "📊 **Custom Minimum Top 10 Holders %**\n\n"
            "Please enter minimum percentage (0-100):\n"
            "Example: 20",
            parse_mode="Markdown"
        )
    elif data == "custom_photon_top10_max":
        context.user_data["input_state"] = {"type": "photon_top10_max"}
        await query.edit_message_text(
            "📊 **Custom Maximum Top 10 Holders %**\n\n"
            "Please enter maximum percentage (0-100):\n"
            "Example: 80",
            parse_mode="Markdown"
        )


async def send_liquidity_menu(update: Update) -> None:
    """Send liquidity configuration menu as a new message"""
    keyboard = [
        [InlineKeyboardButton("$0 - $10K", callback_data="set_liq_0_10000")],
        [InlineKeyboardButton("$0 - $50K", callback_data="set_liq_0_50000")],
        [InlineKeyboardButton("$0 - $100K", callback_data="set_liq_0_100000")],
        [InlineKeyboardButton("$0 - $500K", callback_data="set_liq_0_500000")],
        [InlineKeyboardButton("$0 - $1M", callback_data="set_liq_0_1000000")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_liq_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_liq_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["liquidity_min"]
    current_max = bot_instance.config["liquidity_max"]
    
    await update.message.reply_text(
        f"💧 **Liquidity Filter**\n\nCurrent: ${current_min:,} - ${current_max:,}\n\nSelect range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def send_age_menu(update: Update) -> None:
    """Send age configuration menu as a new message"""
    keyboard = [
        [InlineKeyboardButton("0-5 min", callback_data="set_age_0_5")],
        [InlineKeyboardButton("0-15 min", callback_data="set_age_0_15")],
        [InlineKeyboardButton("0-30 min", callback_data="set_age_0_30")],
        [InlineKeyboardButton("0-1 hour", callback_data="set_age_0_60")],
        [InlineKeyboardButton("0-24 hours", callback_data="set_age_0_1440")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_age_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_age_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["pair_age_min"]
    current_max = bot_instance.config["pair_age_max"]
    
    await update.message.reply_text(
        f"⏰ **Pair Age Filter**\n\nCurrent: {current_min}-{current_max} minutes\n\nSelect range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def send_marketcap_menu(update: Update) -> None:
    """Send market cap configuration menu as a new message"""
    keyboard = [
        [InlineKeyboardButton("$0 - $50K", callback_data="set_mc_0_50000")],
        [InlineKeyboardButton("$0 - $100K", callback_data="set_mc_0_100000")],
        [InlineKeyboardButton("$0 - $500K", callback_data="set_mc_0_500000")],
        [InlineKeyboardButton("$0 - $1M", callback_data="set_mc_0_1000000")],
        [InlineKeyboardButton("$0 - $10M", callback_data="set_mc_0_10000000")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_mc_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_mc_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["market_cap_min"]
    current_max = bot_instance.config["market_cap_max"]
    
    await update.message.reply_text(
        f"💰 **Market Cap Filter**\n\nCurrent: ${current_min:,} - ${current_max:,}\n\nSelect range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def send_signals_menu(update: Update) -> None:
    """Send signals configuration menu as a new message"""
    keyboard = [
        [InlineKeyboardButton("5 min / +10%", callback_data="add_signal_5_10")],
        [InlineKeyboardButton("15 min / +20%", callback_data="add_signal_15_20")],
        [InlineKeyboardButton("30 min / +50%", callback_data="add_signal_30_50")],
        [InlineKeyboardButton("1 hour / +100%", callback_data="add_signal_60_100")],
        [InlineKeyboardButton("✏️ Custom Signal", callback_data="custom_signal")],
        [InlineKeyboardButton("Clear All Signals", callback_data="add_signal_clear_0")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    signals_text = "\n".join([
        f"- {s['time_interval']} min / +{s['price_change']}%"
        for s in bot_instance.config["signals"]
    ]) or "None"
    
    await update.message.reply_text(
        f"📈 **Signal Settings**\n\nCurrent signals:\n{signals_text}\n\nAdd a signal:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def send_presets_menu(update: Update) -> None:
    """Send presets management menu as a new message"""
    keyboard = []
    
    # Show existing presets with load and delete buttons
    if bot_instance.presets:
        for preset_name in bot_instance.presets.keys():
            active_indicator = "✅ " if preset_name == bot_instance.active_preset else ""
            keyboard.append([
                InlineKeyboardButton(
                    f"{active_indicator}{preset_name}",
                    callback_data=f"preset_view_{preset_name}"
                )
            ])
    
    # Add save and back buttons
    keyboard.append([InlineKeyboardButton("💾 Save Current as Preset", callback_data="preset_save")])
    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    presets_text = f"📑 **Preset Management**\n\n"
    if bot_instance.presets:
        presets_text += f"You have {len(bot_instance.presets)} preset(s).\n"
        if bot_instance.active_preset:
            presets_text += f"Active: {bot_instance.active_preset}\n\n"
        else:
            presets_text += "No preset is currently active.\n\n"
        presets_text += "Tap a preset to view stats, load, or delete it."
    else:
        presets_text += "No presets saved yet.\n\n"
        presets_text += "Save your current configuration as a preset!"
    
    await update.message.reply_text(presets_text, reply_markup=reply_markup, parse_mode="Markdown")


async def send_photon_top10_menu(update: Update) -> None:
    """Send Photon top10 configuration menu as a new message"""
    keyboard = [
        [InlineKeyboardButton("0% - 30%", callback_data="set_photon_top10_0_30")],
        [InlineKeyboardButton("0% - 50%", callback_data="set_photon_top10_0_50")],
        [InlineKeyboardButton("0% - 70%", callback_data="set_photon_top10_0_70")],
        [InlineKeyboardButton("0% - 100%", callback_data="set_photon_top10_0_100")],
        [InlineKeyboardButton("✏️ Custom Min", callback_data="custom_photon_top10_min"),
         InlineKeyboardButton("✏️ Custom Max", callback_data="custom_photon_top10_max")],
        [InlineKeyboardButton("◀️ Back", callback_data="config_photon")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    photon_filters = bot_instance.config.get("photon_filters", {})
    current_min = photon_filters.get("top10_min", 0)
    current_max = photon_filters.get("top10_max", 100)
    
    await update.message.reply_text(
        f"📊 **Top 10 Holders Percentage**\n\n"
        f"Current: {current_min}% - {current_max}%\n\n"
        f"Select range or custom:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def handle_custom_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user text input for custom values"""
    
    if "input_state" not in context.user_data:
        # No state for this user, ignore the message
        return
    
    state = context.user_data["input_state"]
    input_type = state["type"]
    user_input = update.message.text.strip()
    
    try:
        if input_type == "preset_name":
            # Save current config as a named preset
            preset_name = user_input
            if not preset_name or len(preset_name) > 50:
                await update.message.reply_text(
                    "❌ Invalid preset name. Must be 1-50 characters.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            # Save the preset with current configuration
            bot_instance.presets[preset_name] = {
                "config": bot_instance.config.copy(),
                "coins": {}  # Will track coins alerted under this preset
            }
            bot_instance.active_preset = preset_name
            
            await update.message.reply_text(
                f"✅ Preset '{preset_name}' saved and activated!\n\n"
                "This preset will now track all coins alerted while it's active."
            )
            context.user_data.pop("input_state", None)
            
            # Send presets menu
            await send_presets_menu(update)
            
        elif input_type == "signal_time":
            # First step: get time interval
            time_interval = int(user_input)
            if time_interval <= 0:
                await update.message.reply_text(
                    "❌ Invalid value. Time must be greater than 0.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            # Store time and ask for price
            context.user_data["input_state"] = {"type": "signal_price", "time_interval": time_interval}
            await update.message.reply_text(
                f"✅ Time interval: {time_interval} minutes\n\n"
                "📈 Now enter the price change percentage:\n"
                "Example: 50 (for +50%)",
                parse_mode="Markdown"
            )
            
        elif input_type == "signal_price":
            # Second step: get price change
            price_change = float(user_input)
            if price_change <= 0:
                await update.message.reply_text(
                    "❌ Invalid value. Price change must be greater than 0.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            time_interval = state["time_interval"]
            signal = {"time_interval": time_interval, "price_change": price_change}
            
            if signal not in bot_instance.config["signals"]:
                bot_instance.config["signals"].append(signal)
                await update.message.reply_text(
                    f"✅ Signal added: {time_interval} min / +{price_change}%"
                )
            else:
                await update.message.reply_text(
                    f"⚠️ Signal already exists: {time_interval} min / +{price_change}%"
                )
            
            # Clear state
            context.user_data.pop("input_state", None)
            
            # Send signals menu
            await send_signals_menu(update)
            
        elif input_type == "age_min":
            value = int(user_input)
            if value < 0:
                await update.message.reply_text(
                    "❌ Invalid value. Minimum age must be 0 or greater.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            bot_instance.config["pair_age_min"] = value
            await update.message.reply_text(
                f"✅ Minimum pair age set to: {value} minutes"
            )
            context.user_data.pop("input_state", None)
            
            # Send age menu
            await send_age_menu(update)
            
        elif input_type == "age_max":
            value = int(user_input)
            if value <= 0:
                await update.message.reply_text(
                    "❌ Invalid value. Maximum age must be greater than 0.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            bot_instance.config["pair_age_max"] = value
            await update.message.reply_text(
                f"✅ Maximum pair age set to: {value} minutes"
            )
            context.user_data.pop("input_state", None)
            
            # Send age menu
            await send_age_menu(update)
            
        elif input_type == "mc_min":
            value = float(user_input)
            if value < 0:
                await update.message.reply_text(
                    "❌ Invalid value. Minimum market cap must be 0 or greater.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            bot_instance.config["market_cap_min"] = value
            await update.message.reply_text(
                f"✅ Minimum market cap set to: ${value:,.0f}"
            )
            context.user_data.pop("input_state", None)
            
            # Send market cap menu
            await send_marketcap_menu(update)
            
        elif input_type == "mc_max":
            value = float(user_input)
            if value <= 0:
                await update.message.reply_text(
                    "❌ Invalid value. Maximum market cap must be greater than 0.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            bot_instance.config["market_cap_max"] = value
            await update.message.reply_text(
                f"✅ Maximum market cap set to: ${value:,.0f}"
            )
            context.user_data.pop("input_state", None)
            
            # Send market cap menu
            await send_marketcap_menu(update)
            
        elif input_type == "liq_min":
            value = float(user_input)
            if value < 0:
                await update.message.reply_text(
                    "❌ Invalid value. Minimum liquidity must be 0 or greater.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            bot_instance.config["liquidity_min"] = value
            await update.message.reply_text(
                f"✅ Minimum liquidity set to: ${value:,.0f}"
            )
            context.user_data.pop("input_state", None)
            
            # Send liquidity menu
            await send_liquidity_menu(update)
            
        elif input_type == "liq_max":
            value = float(user_input)
            if value <= 0:
                await update.message.reply_text(
                    "❌ Invalid value. Maximum liquidity must be greater than 0.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            bot_instance.config["liquidity_max"] = value
            await update.message.reply_text(
                f"✅ Maximum liquidity set to: ${value:,.0f}"
            )
            context.user_data.pop("input_state", None)
            
            # Send liquidity menu
            await send_liquidity_menu(update)
            
        elif input_type == "photon_top10_min":
            value = float(user_input)
            if value < 0 or value > 100:
                await update.message.reply_text(
                    "❌ Invalid value. Percentage must be between 0 and 100.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            photon_filters = bot_instance.config.get("photon_filters", {})
            photon_filters["top10_min"] = value
            await update.message.reply_text(
                f"✅ Minimum top 10 holders set to: {value:.1f}%"
            )
            context.user_data.pop("input_state", None)
            
            # Send photon top10 menu
            await send_photon_top10_menu(update)
            
        elif input_type == "photon_top10_max":
            value = float(user_input)
            if value < 0 or value > 100:
                await update.message.reply_text(
                    "❌ Invalid value. Percentage must be between 0 and 100.\n"
                    "Please try again or use /start to cancel."
                )
                return
            
            photon_filters = bot_instance.config.get("photon_filters", {})
            photon_filters["top10_max"] = value
            await update.message.reply_text(
                f"✅ Maximum top 10 holders set to: {value:.1f}%"
            )
            context.user_data.pop("input_state", None)
            
            # Send photon top10 menu
            await send_photon_top10_menu(update)
            
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid input. Please enter a valid number.\n"
            "Use /start to cancel and try again."
        )


async def view_config(query) -> None:
    """Display current configuration"""
    config = bot_instance.config
    
    social_links = ", ".join([k for k, v in config["social_links"].items() if v]) or "None"
    signals = "\n".join([
        f"  - {s['time_interval']} min / +{s['price_change']}%"
        for s in config["signals"]
    ]) or "  None"
    
    # PhotonScan filters
    photon_filters = config.get("photon_filters", {})
    photon_status = []
    if photon_filters.get("social_tg"):
        photon_status.append("Telegram✅")
    if photon_filters.get("dex_paid"):
        photon_status.append("DEX Paid✅")
    if photon_filters.get("mint_auth"):
        photon_status.append("Mint Revoked✅")
    if photon_filters.get("freeze_auth"):
        photon_status.append("Freeze Revoked✅")
    if photon_filters.get("lp_burned"):
        photon_status.append("LP Burned✅")
    if photon_filters.get("audit"):
        photon_status.append("Audit✅")
    if photon_filters.get("bonding_curve"):
        photon_status.append("Bonding Curve✅")
    
    photon_summary = ", ".join(photon_status) if photon_status else "None"
    
    config_text = f"""📊 **Current Configuration**

🌐 Network: {config['network']}
📱 Social Links: {social_links}
⏰ Pair Age: {config['pair_age_min']}-{config['pair_age_max']} min
💰 Market Cap: ${config['market_cap_min']:,} - ${config['market_cap_max']:,}
💧 Liquidity: ${config['liquidity_min']:,} - ${config['liquidity_max']:,}
📈 Signals:
{signals}

🔍 Photon Filters: {photon_summary}
📊 Top 10 Holders: {photon_filters.get('top10_min', 0)}%-{photon_filters.get('top10_max', 100)}%
"""
    
    keyboard = [[InlineKeyboardButton("◀️ Back", callback_data="back_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(config_text, reply_markup=reply_markup, parse_mode="Markdown")


async def show_presets_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show preset management menu"""
    keyboard = []
    
    # Show existing presets with load and delete buttons
    if bot_instance.presets:
        for preset_name in bot_instance.presets.keys():
            active_indicator = "✅ " if preset_name == bot_instance.active_preset else ""
            keyboard.append([
                InlineKeyboardButton(
                    f"{active_indicator}{preset_name}",
                    callback_data=f"preset_view_{preset_name}"
                )
            ])
    
    # Add save and back buttons
    keyboard.append([InlineKeyboardButton("💾 Save Current as Preset", callback_data="preset_save")])
    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    presets_text = f"📑 **Preset Management**\n\n"
    if bot_instance.presets:
        presets_text += f"You have {len(bot_instance.presets)} preset(s).\n"
        if bot_instance.active_preset:
            presets_text += f"Active: {bot_instance.active_preset}\n\n"
        else:
            presets_text += "No preset is currently active.\n\n"
        presets_text += "Tap a preset to view stats, load, or delete it."
    else:
        presets_text += "No presets saved yet.\n\n"
        presets_text += "Save your current configuration as a preset!"
    
    await query.edit_message_text(presets_text, reply_markup=reply_markup, parse_mode="Markdown")


async def save_preset_prompt(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter preset name"""
    context.user_data["input_state"] = {"type": "preset_name"}
    await query.edit_message_text(
        "💾 **Save Preset**\n\n"
        "Please enter a name for this preset:\n"
        "Example: High Liquidity, Quick Flips, etc.\n\n"
        "Use /start to cancel.",
        parse_mode="Markdown"
    )


async def view_preset_stats(query, context: ContextTypes.DEFAULT_TYPE, preset_name: str) -> None:
    """View stats and options for a specific preset"""
    if preset_name not in bot_instance.presets:
        await query.answer("Preset not found!")
        return
    
    preset_data = bot_instance.presets[preset_name]
    coins = preset_data.get("coins", {})
    
    # Calculate win ratio
    total_coins = len(coins)
    if total_coins > 0:
        wins = sum(1 for coin in coins.values() if coin.get("profit_percent", 0) > 0)
        losses = sum(1 for coin in coins.values() if coin.get("profit_percent", 0) < 0)
        neutral = total_coins - wins - losses
        win_ratio = (wins / total_coins * 100) if total_coins > 0 else 0
    else:
        wins = losses = neutral = 0
        win_ratio = 0
    
    stats_text = f"📊 **Preset: {preset_name}**\n\n"
    stats_text += f"**Performance Stats:**\n"
    stats_text += f"Total Coins: {total_coins}\n"
    stats_text += f"Wins: {wins} ({win_ratio:.1f}%)\n"
    stats_text += f"Losses: {losses}\n"
    stats_text += f"Neutral: {neutral}\n\n"
    
    # Show config summary
    config = preset_data.get("config", {})
    stats_text += f"**Configuration:**\n"
    stats_text += f"Network: {config.get('network', 'N/A')}\n"
    stats_text += f"Age: {config.get('pair_age_min', 0)}-{config.get('pair_age_max', 0)} min\n"
    stats_text += f"Signals: {len(config.get('signals', []))}\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Stats", callback_data=f"preset_refresh_{preset_name}")],
        [InlineKeyboardButton("📋 Load This Preset", callback_data=f"preset_load_{preset_name}")],
        [InlineKeyboardButton("🗑️ Delete Preset", callback_data=f"preset_delete_{preset_name}")],
        [InlineKeyboardButton("◀️ Back to Presets", callback_data="presets_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(stats_text, reply_markup=reply_markup, parse_mode="Markdown")


async def refresh_preset_stats(query, context: ContextTypes.DEFAULT_TYPE, preset_name: str) -> None:
    """Refresh stats for a preset by fetching current pool data"""
    if preset_name not in bot_instance.presets:
        await query.answer("Preset not found!")
        return
    
    await query.answer("Refreshing stats... This may take a moment.")
    
    preset_data = bot_instance.presets[preset_name]
    coins = preset_data.get("coins", {})
    
    if not coins:
        await query.answer("No coins to refresh!")
        await view_preset_stats(query, context, preset_name)
        return
    
    # Fetch current pool data for tracked coins
    try:
        await bot_instance.start_session()
        
        for pool_address, coin_data in coins.items():
            try:
                # Fetch current pool state
                pool_pubkey = Pubkey.from_string(pool_address)
                account_info = await bot_instance.solana_client.get_account_info(pool_pubkey)
                
                if account_info.value and account_info.value.data:
                    account_data = base64.b64decode(account_info.value.data[0])
                    
                    if len(account_data) >= MIN_POOL_DATA_SIZE:
                        pool_data = LIQUIDITY_STATE_LAYOUT_V4.parse(account_data)
                        
                        # Calculate activity change based on swap amounts
                        # Note: This is a proxy metric, not actual profit/loss
                        initial_base_in = coin_data.get("initial_swap_base_in", 0)
                        current_base_in = pool_data.swapBaseInAmount
                        
                        if initial_base_in > 0:
                            activity_change = ((current_base_in - initial_base_in) / initial_base_in) * 100
                            # Simple heuristic: positive activity change = potential win
                            coin_data["profit_percent"] = activity_change
                            coin_data["current_swap_base_in"] = current_base_in
                        elif current_base_in > initial_base_in:
                            # New activity detected
                            coin_data["profit_percent"] = 100  # Treat as potential win
                            coin_data["current_swap_base_in"] = current_base_in
                        else:
                            # No change or initial data missing
                            coin_data["profit_percent"] = 0
                            coin_data["current_swap_base_in"] = current_base_in
                        
            except Exception as e:
                logger.error(f"Error refreshing pool {pool_address}: {e}")
                continue
        
        # Show updated stats
        await view_preset_stats(query, context, preset_name)
        
    except Exception as e:
        logger.error(f"Error refreshing preset stats: {e}")
        await query.answer("Error refreshing stats!")


async def load_preset(query, context: ContextTypes.DEFAULT_TYPE, preset_name: str) -> None:
    """Load a preset configuration"""
    if preset_name not in bot_instance.presets:
        await query.answer("Preset not found!")
        return
    
    # Load the configuration
    preset_config = bot_instance.presets[preset_name].get("config", {})
    bot_instance.config = preset_config.copy()
    bot_instance.active_preset = preset_name
    
    await query.answer(f"Preset '{preset_name}' loaded!")
    await show_presets_menu(query, context)


async def delete_preset(query, context: ContextTypes.DEFAULT_TYPE, preset_name: str) -> None:
    """Delete a preset"""
    if preset_name not in bot_instance.presets:
        await query.answer("Preset not found!")
        return
    
    del bot_instance.presets[preset_name]
    
    # Clear active preset if it was deleted
    if bot_instance.active_preset == preset_name:
        bot_instance.active_preset = None
    
    await query.answer(f"Preset '{preset_name}' deleted!")
    await show_presets_menu(query, context)


async def back_to_main(query) -> None:
    """Go back to main menu"""
    keyboard = [
        [InlineKeyboardButton("⚙️ Configure Filters", callback_data="config_main")],
        [InlineKeyboardButton("📊 View Current Config", callback_data="view_config")],
        [InlineKeyboardButton("💾 Manage Presets", callback_data="presets_main")],
        [InlineKeyboardButton("🚀 Start Monitoring", callback_data="start_monitoring")],
        [InlineKeyboardButton("⏹️ Stop Monitoring", callback_data="stop_monitoring")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    preset_info = f" (Using preset: {bot_instance.active_preset})" if bot_instance.active_preset else ""
    
    await query.edit_message_text(
        f"🤖 **Meme Coin Alert Bot**{preset_info}\n\nSelect an option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def start_monitoring(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start monitoring for new coins"""
    if "monitoring" not in context.bot_data:
        context.bot_data["monitoring"] = False
    
    if context.bot_data["monitoring"]:
        await query.answer("Already monitoring!")
        return
    
    context.bot_data["monitoring"] = True
    await query.answer("Monitoring started!")
    
    # Start the WebSocket monitoring task
    context.application.create_task(monitor_coins(context))
    
    # Start the PhotonScan polling task
    context.application.create_task(monitor_photon_tokens(context))


async def stop_monitoring(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop monitoring for new coins"""
    if "monitoring" in context.bot_data:
        context.bot_data["monitoring"] = False
        await query.answer("Monitoring stopped!")
    else:
        await query.answer("Not currently monitoring")


async def monitor_coins(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Background task to monitor new coins via WebSocket streaming
    
    This replaces the old REST API polling approach with real-time WebSocket streaming.
    Automatically reconnects on connection failure with configurable intervals.
    """
    logger.info("Starting WebSocket coin monitoring...")
    bot_instance.ws_monitoring_active = True
    
    while context.bot_data.get("monitoring", False):
        try:
            # Connect to WebSocket
            connected = await bot_instance.connect_websocket(telegram_bot=context.bot)
            
            if not connected:
                # Connection failed, wait before retry
                bot_instance.ws_reconnect_count += 1
                
                # Check if we've exceeded max reconnect attempts
                if WS_MAX_RECONNECT_ATTEMPTS > 0 and bot_instance.ws_reconnect_count >= WS_MAX_RECONNECT_ATTEMPTS:
                    error_msg = f"Max reconnection attempts ({WS_MAX_RECONNECT_ATTEMPTS}) reached. Stopping monitoring."
                    logger.error(error_msg)
                    
                    # Send alert to Telegram
                    if TELEGRAM_CHAT_ID:
                        try:
                            await context.bot.send_message(
                                chat_id=TELEGRAM_CHAT_ID,
                                text=f"🚨 **WebSocket Monitoring Stopped**\n\n{error_msg}\n\nPlease check the configuration and restart monitoring."
                            )
                        except Exception:
                            pass
                    
                    # Stop monitoring
                    context.bot_data["monitoring"] = False
                    break
                
                logger.warning(f"WebSocket connection failed (attempt {bot_instance.ws_reconnect_count}). "
                             f"Retrying in {WS_RECONNECT_INTERVAL} seconds...")
                await asyncio.sleep(WS_RECONNECT_INTERVAL)
                continue
            
            # Successfully connected, listen for messages
            logger.info("WebSocket connected. Listening for token updates...")
            
            try:
                async for message in bot_instance.ws_connection:
                    # Check if monitoring should stop
                    if not context.bot_data.get("monitoring", False):
                        logger.info("Monitoring stopped by user")
                        break
                    
                    # Process the incoming message
                    await bot_instance.handle_websocket_message(message, telegram_bot=context.bot)
                    
                    # Clean up old tracked pairs periodically
                    max_signal_time = max([s["time_interval"] for s in bot_instance.config["signals"]], default=60)
                    cutoff_time = datetime.now() - timedelta(minutes=max_signal_time + 10)
                    bot_instance.tracked_pairs = {
                        k: v for k, v in bot_instance.tracked_pairs.items()
                        if v["timestamp"] > cutoff_time
                    }
                    
            except ConnectionClosed as e:
                error_msg = f"WebSocket connection closed: {e}"
                logger.warning(error_msg)
                
                # Send alert to Telegram
                if TELEGRAM_CHAT_ID:
                    try:
                        await context.bot.send_message(
                            chat_id=TELEGRAM_CHAT_ID,
                            text=f"⚠️ **WebSocket Disconnected**\n\nConnection closed unexpectedly. Attempting to reconnect..."
                        )
                    except Exception:
                        pass
                
                # Mark as disconnected and retry
                bot_instance.ws_connected = False
                bot_instance.ws_reconnect_count += 1
                
                logger.info(f"Reconnecting in {WS_RECONNECT_INTERVAL} seconds...")
                await asyncio.sleep(WS_RECONNECT_INTERVAL)
                continue
                
            except WebSocketException as e:
                error_msg = f"WebSocket error: {e}"
                logger.error(error_msg, exc_info=True)
                
                if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                    await send_error_alert(
                        TELEGRAM_BOT_TOKEN,
                        TELEGRAM_CHAT_ID,
                        error_msg,
                        exc_info=sys.exc_info()
                    )
                
                # Mark as disconnected and retry
                bot_instance.ws_connected = False
                bot_instance.ws_reconnect_count += 1
                
                await asyncio.sleep(WS_RECONNECT_INTERVAL)
                continue
                
        except Exception as e:
            error_msg = f"Error in WebSocket monitoring loop: {e}"
            logger.error(error_msg, exc_info=True)
            
            # Send error alert to Telegram
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
            
            # Wait before retry
            await asyncio.sleep(WS_RECONNECT_INTERVAL)
    
    # Clean up WebSocket connection when monitoring stops
    if bot_instance.ws_connection:
        try:
            await bot_instance.ws_connection.close()
            bot_instance.ws_connected = False
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error closing WebSocket connection: {e}")
    
    bot_instance.ws_monitoring_active = False
    logger.info("WebSocket monitoring stopped")


async def monitor_photon_tokens(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    ========================================
    PHOTONSCAN POLLING MONITOR
    ========================================
    Background task to poll PhotonScan API for new tokens
    
    This runs alongside WebSocket monitoring to provide additional
    token discovery via PhotonScan API with 60-second polling interval.
    Filters tokens and sends alerts only for new tokens not seen before.
    """
    logger.info("Starting PhotonScan token monitoring...")
    bot_instance.photon_monitoring_active = True
    
    while context.bot_data.get("monitoring", False):
        try:
            # Fetch tokens from PhotonScan API
            tokens = await bot_instance.fetch_photon_tokens()
            
            # Process each token
            new_tokens_count = 0
            for token in tokens:
                # Get token identifier (mint address)
                mint = token.get("mint", token.get("address"))
                
                if not mint:
                    continue
                
                # Skip if we've already alerted on this token
                if mint in bot_instance.photon_tokens:
                    continue
                
                # Apply PhotonScan-specific filters
                if not bot_instance.apply_photon_filters(token):
                    continue
                
                # Apply general filters (network, age, liquidity, etc.)
                # Note: We use a simplified check here since PhotonScan tokens
                # may have different data structure than Raydium pools
                
                # Send alert for new token
                await bot_instance.send_photon_alert(token, telegram_bot=context.bot)
                
                # Mark as seen
                bot_instance.photon_tokens.add(mint)
                new_tokens_count += 1
                
                # Track in preset if active
                if bot_instance.active_preset and bot_instance.active_preset in bot_instance.presets:
                    preset_data = bot_instance.presets[bot_instance.active_preset]
                    if "coins" in preset_data:
                        preset_data["coins"][mint] = {
                            "timestamp": datetime.now(),
                            "name": token.get("name", "Unknown"),
                            "symbol": token.get("symbol", "Unknown"),
                            "source": "photon"
                        }
                
                # Limit memory usage
                if len(bot_instance.photon_tokens) > MAX_TRACKED_PAIRS:
                    # Remove oldest tokens (simple approach - could be improved)
                    tokens_list = list(bot_instance.photon_tokens)
                    bot_instance.photon_tokens = set(tokens_list[-TRACKED_PAIRS_TRIM_SIZE:])
            
            if new_tokens_count > 0:
                logger.info(f"PhotonScan: Found and alerted {new_tokens_count} new tokens")
            else:
                logger.debug("PhotonScan: No new tokens matching filters")
            
            # Wait for next polling interval
            await asyncio.sleep(PHOTON_POLL_INTERVAL)
            
        except asyncio.CancelledError:
            logger.info("PhotonScan monitoring task cancelled")
            break
            
        except Exception as e:
            error_msg = f"Error in PhotonScan monitoring loop: {e}"
            logger.error(error_msg, exc_info=True)
            
            # Send error alert to Telegram
            if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                await send_error_alert(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    error_msg,
                    exc_info=sys.exc_info()
                )
            
            # Wait before retry
            await asyncio.sleep(PHOTON_POLL_INTERVAL)
    
    bot_instance.photon_monitoring_active = False
    logger.info("PhotonScan monitoring stopped")


async def post_init(application: Application) -> None:
    """Post initialization tasks"""
    application.bot_data["monitoring"] = False


def main() -> None:
    """Start the bot"""
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return
    
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set - alerts will not be sent to a specific chat")
    
    # Set up Telegram error handler for logging
    if TELEGRAM_ERROR_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            setup_error_handler(
                bot_token=TELEGRAM_BOT_TOKEN,
                chat_id=TELEGRAM_CHAT_ID,
                enabled=True,
                debug_mode=TELEGRAM_ERROR_DEBUG_MODE
            )
            logger.info(f"Telegram error alerting enabled (debug_mode={TELEGRAM_ERROR_DEBUG_MODE})")
            logger.info("Error handler initialized - errors at ERROR level and above will be sent to Telegram")
                
        except Exception as e:
            logger.warning(f"Failed to set up Telegram error handler: {e}")
            # Continue running even if error handler setup fails
    else:
        logger.info("Telegram error alerting disabled")
    
    # Set up global exception handler
    def handle_exception(exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions"""
        if issubclass(exc_type, KeyboardInterrupt):
            # Allow keyboard interrupt to exit
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        # Log the exception - this will trigger the Telegram error handler
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    
    sys.excepthook = handle_exception
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_input))
    
    # Start bot
    logger.info("Starting bot...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        error_msg = f"Fatal error in bot main loop: {e}"
        logger.critical(error_msg, exc_info=True)
        raise


if __name__ == "__main__":
    main()
