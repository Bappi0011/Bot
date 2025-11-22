import os
import logging
import asyncio
import json
import base64
import base58
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import aiohttp
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

# Configuration constants
CHECK_INTERVAL = 0.75  # Seconds between checks (1-2 times per second)
MAX_PAIRS_FETCH = 100  # Maximum number of pairs to fetch per API call
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
    "signals": []  # List of {time_interval: int (minutes), price_change: float (percentage)}
}

# Global configuration
user_config = DEFAULT_CONFIG.copy()


class MemeCoinBot:
    """Main bot class for meme coin alerts"""
    
    def __init__(self):
        self.config = user_config.copy()
        self.session: Optional[aiohttp.ClientSession] = None
        self.solana_client: Optional[AsyncClient] = None
        self.tracked_pairs = {}  # Track pairs for signal monitoring
        self.last_checked_pairs = set()  # Track pairs we've already alerted on
        self.presets = {}  # Stores named configurations with tracking data
        self.active_preset = None  # Currently active preset name
    
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
    
    async def fetch_new_coins(self) -> List[Dict]:
        """Fetch new Raydium pools from Solana blockchain using pagination
        
        Note: This fetches program accounts from the Raydium V4 AMM program.
        Uses getProgramAccountsV2 with pagination to avoid rate limiting.
        
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
                                logger.error(f"HTTP error {resp.status} from RPC endpoint on page {page}")
                                break
                            
                            response = await resp.json()
                            
                    except aiohttp.ClientError as e:
                        logger.error(f"Network error making RPC call on page {page}: {e}")
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON response from RPC endpoint on page {page}: {e}")
                        break
                    except Exception as e:
                        logger.error(f"Unexpected error making RPC call on page {page}: {e}")
                        break
                    
                    # Check if we got a valid response
                    if not response or "result" not in response:
                        logger.warning(f"No result in response for page {page}. "
                                     f"Response keys: {list(response.keys()) if response else 'None'}")
                        
                        # Send Telegram alert if credentials are available
                        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                            try:
                                # Sanitize RPC URL to hide potential API keys
                                sanitized_url = SOLANA_RPC_URL.split('?')[0] if '?' in SOLANA_RPC_URL else SOLANA_RPC_URL
                                
                                # Construct detailed error message
                                alert_text = f"⚠️ **RPC Response Failure**\n\n"
                                alert_text += f"**Page:** {page}\n"
                                alert_text += f"**RPC URL:** `{sanitized_url}`\n\n"
                                
                                # Check if response contains an error
                                if response and "error" in response:
                                    error_obj = response["error"]
                                    if isinstance(error_obj, dict):
                                        error_code = error_obj.get("code", "N/A")
                                        error_message = error_obj.get("message", "N/A")
                                        error_data = error_obj.get("data", "N/A")
                                        
                                        alert_text += f"**Error Code:** `{error_code}`\n"
                                        alert_text += f"**Error Message:** {error_message}\n"
                                        if error_data != "N/A":
                                            alert_text += f"**Error Data:** `{error_data}`\n"
                                    else:
                                        alert_text += f"**Error:** {error_obj}\n"
                                else:
                                    # No error key, report available keys (keys only, not values)
                                    response_keys = list(response.keys()) if response else "None"
                                    alert_text += f"**Response Keys:** {response_keys}\n"
                                    alert_text += f"**Issue:** No 'result' key in response\n"
                                
                                alert_text += f"\n**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                                
                                # Send the alert
                                telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                                telegram_payload = {
                                    "chat_id": TELEGRAM_CHAT_ID,
                                    "text": alert_text,
                                    "parse_mode": "Markdown"
                                }
                                
                                async with self.session.post(telegram_url, json=telegram_payload) as telegram_resp:
                                    if telegram_resp.status == 200:
                                        logger.info("RPC failure alert sent to Telegram")
                                    else:
                                        logger.warning(f"Failed to send Telegram alert: HTTP {telegram_resp.status}")
                                        
                            except Exception as telegram_error:
                                logger.error(f"Error sending Telegram alert: {telegram_error}")
                        
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
                    logger.error(f"Error fetching page {page}: {e}")
                    break
            
            logger.info(f"Fetched {len(pools)} pools from Raydium V4 program")
            return pools
            
        except Exception as e:
            logger.error(f"Error fetching pools from Solana: {e}")
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
    """Handle button callbacks"""
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
    elif data == "back_main":
        await back_to_main(query)
    elif data.startswith("set_network_"):
        await set_network(query, data.split("_")[2])
    elif data.startswith("toggle_social_"):
        await toggle_social(query, data.split("_")[2])
    elif data.startswith("set_age_"):
        await set_age(query, data)
    elif data.startswith("set_mc_"):
        await set_marketcap(query, data)
    elif data.startswith("set_liq_"):
        await set_liquidity(query, data)
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
    
    config_text = f"""📊 **Current Configuration**

🌐 Network: {config['network']}
📱 Social Links: {social_links}
⏰ Pair Age: {config['pair_age_min']}-{config['pair_age_max']} min
💰 Market Cap: ${config['market_cap_min']:,} - ${config['market_cap_max']:,}
💧 Liquidity: ${config['liquidity_min']:,} - ${config['liquidity_max']:,}
📈 Signals:
{signals}
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
    
    # Start the monitoring task
    context.application.create_task(monitor_coins(context))


async def stop_monitoring(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop monitoring for new coins"""
    if "monitoring" in context.bot_data:
        context.bot_data["monitoring"] = False
        await query.answer("Monitoring stopped!")
    else:
        await query.answer("Not currently monitoring")


async def monitor_coins(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background task to monitor new coins"""
    logger.info("Starting coin monitoring...")
    
    while context.bot_data.get("monitoring", False):
        try:
            # Fetch new pools
            pools = await bot_instance.fetch_new_coins()
            
            for pool in pools:
                pool_address = pool.get("poolAddress")
                
                if not pool_address:
                    continue
                
                # Skip if already alerted
                if pool_address in bot_instance.last_checked_pairs:
                    continue
                
                # Apply filters
                if bot_instance.apply_filters(pool):
                    # Send alert
                    message = bot_instance.format_coin_alert(pool)
                    
                    try:
                        if TELEGRAM_CHAT_ID:
                            await context.bot.send_message(
                                chat_id=TELEGRAM_CHAT_ID,
                                text=message,
                                parse_mode="Markdown"
                            )
                            logger.info(f"Alert sent for pool {pool_address}")
                            
                            # Track coin in active preset
                            if bot_instance.active_preset and bot_instance.active_preset in bot_instance.presets:
                                bot_instance.presets[bot_instance.active_preset]["coins"][pool_address] = {
                                    "base_mint": pool.get("baseMint", "Unknown"),
                                    "quote_mint": pool.get("quoteMint", "Unknown"),
                                    "initial_swap_base_in": pool.get("swapBaseInAmount", 0),
                                    "initial_swap_quote_out": pool.get("swapQuoteOutAmount", 0),
                                    "timestamp": datetime.now().isoformat(),
                                    "profit_percent": 0  # Will be updated on refresh
                                }
                            
                            # Track for signals
                            if bot_instance.config["signals"]:
                                bot_instance.tracked_pairs[pool_address] = {
                                    "initial_price": 0.0,  # Would need price oracle
                                    "timestamp": datetime.now(),
                                    "base_mint": pool.get("baseMint", "Unknown")
                                }
                    except Exception as e:
                        logger.error(f"Error sending alert: {e}")
                    
                    # Mark as alerted
                    bot_instance.last_checked_pairs.add(pool_address)
            
            # Clean up old tracked pairs (older than max signal time + buffer)
            max_signal_time = max([s["time_interval"] for s in bot_instance.config["signals"]], default=60)
            cutoff_time = datetime.now() - timedelta(minutes=max_signal_time + 10)
            bot_instance.tracked_pairs = {
                k: v for k, v in bot_instance.tracked_pairs.items()
                if v["timestamp"] > cutoff_time
            }
            
            # Limit memory of checked pairs
            if len(bot_instance.last_checked_pairs) > MAX_TRACKED_PAIRS:
                bot_instance.last_checked_pairs = set(
                    list(bot_instance.last_checked_pairs)[-TRACKED_PAIRS_TRIM_SIZE:]
                )
            
            # Wait before next check - delay added to prevent 429 Too Many Requests error
            await asyncio.sleep(10)
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            await asyncio.sleep(10)


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
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_input))
    
    # Start bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
