import os
import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Configuration constants
DEXSCREENER_API_URL = "https://api.dexscreener.com/latest/dex/pairs/all"
MAX_PAIRS_FETCH = 100  # Maximum number of pairs to fetch per API call
CHECK_INTERVAL = 0.75  # Seconds between checks (1-2 times per second)
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
        self.tracked_pairs = {}  # Track pairs for signal monitoring
        self.last_checked_pairs = set()  # Track pairs we've already alerted on
    
    async def start_session(self):
        """Initialize aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()
    
    async def fetch_new_coins(self) -> List[Dict]:
        """Fetch new coins from DexScreener API"""
        await self.start_session()
        
        try:
            async with self.session.get(DEXSCREENER_API_URL, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    pairs = data.get("pairs", [])
                    return pairs[:MAX_PAIRS_FETCH]
                else:
                    logger.error(f"Failed to fetch coins: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching coins: {e}")
            return []
    
    def apply_filters(self, pair: Dict) -> bool:
        """Apply user-defined filters to a pair"""
        try:
            # Network filter
            if self.config["network"] != "all":
                if pair.get("chainId", "").lower() != self.config["network"].lower():
                    return False
            
            # Social links filter
            social_info = pair.get("info", {})
            if self.config["social_links"]["telegram"]:
                if not social_info.get("socials", {}).get("telegram"):
                    return False
            if self.config["social_links"]["twitter"]:
                if not social_info.get("socials", {}).get("twitter"):
                    return False
            if self.config["social_links"]["website"]:
                if not social_info.get("websites"):
                    return False
            
            # Pair age filter
            pair_created = pair.get("pairCreatedAt")
            if pair_created:
                created_time = datetime.fromtimestamp(pair_created / 1000)
                age_minutes = (datetime.now() - created_time).total_seconds() / 60
                if not (self.config["pair_age_min"] <= age_minutes <= self.config["pair_age_max"]):
                    return False
            
            # Market cap filter
            fdv = pair.get("fdv", 0) or 0
            if not (self.config["market_cap_min"] <= fdv <= self.config["market_cap_max"]):
                return False
            
            # Liquidity filter
            liquidity_usd = pair.get("liquidity", {}).get("usd", 0) or 0
            if not (self.config["liquidity_min"] <= liquidity_usd <= self.config["liquidity_max"]):
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error applying filters: {e}")
            return False
    
    def format_coin_alert(self, pair: Dict) -> str:
        """Format coin information for alert message"""
        base_token = pair.get("baseToken", {})
        quote_token = pair.get("quoteToken", {})
        price_usd = pair.get("priceUsd", "N/A")
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        fdv = pair.get("fdv", 0)
        volume_24h = pair.get("volume", {}).get("h24", 0)
        price_change_24h = pair.get("priceChange", {}).get("h24", 0)
        
        message = f"🚀 **New Meme Coin Alert!**\n\n"
        message += f"**Token:** {base_token.get('name', 'Unknown')} ({base_token.get('symbol', 'N/A')})\n"
        message += f"**Chain:** {pair.get('chainId', 'N/A')}\n"
        message += f"**DEX:** {pair.get('dexId', 'N/A')}\n"
        message += f"**Price:** ${price_usd}\n"
        message += f"**Liquidity:** ${liquidity:,.2f}\n" if liquidity else "**Liquidity:** N/A\n"
        message += f"**Market Cap:** ${fdv:,.2f}\n" if fdv else "**Market Cap:** N/A\n"
        message += f"**24h Volume:** ${volume_24h:,.2f}\n" if volume_24h else "**24h Volume:** N/A\n"
        message += f"**24h Change:** {price_change_24h:.2f}%\n" if price_change_24h else "**24h Change:** N/A\n"
        message += f"\n**URL:** {pair.get('url', 'N/A')}\n"
        
        return message


# Initialize bot instance
bot_instance = MemeCoinBot()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
    keyboard = [
        [InlineKeyboardButton("⚙️ Configure Filters", callback_data="config_main")],
        [InlineKeyboardButton("📊 View Current Config", callback_data="view_config")],
        [InlineKeyboardButton("🚀 Start Monitoring", callback_data="start_monitoring")],
        [InlineKeyboardButton("⏹️ Stop Monitoring", callback_data="stop_monitoring")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "🤖 **Welcome to Meme Coin Alert Bot!**\n\n"
        "This bot monitors new meme coin launches and sends alerts based on your filters.\n\n"
        "Use the buttons below to:\n"
        "- Configure your filters and alert settings\n"
        "- View your current configuration\n"
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
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["pair_age_min"]
    current_max = bot_instance.config["pair_age_max"]
    
    await query.edit_message_text(
        f"⏰ **Pair Age Filter**\n\nCurrent: {current_min}-{current_max} minutes\n\nSelect range:",
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
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["market_cap_min"]
    current_max = bot_instance.config["market_cap_max"]
    
    await query.edit_message_text(
        f"💰 **Market Cap Filter**\n\nCurrent: ${current_min:,} - ${current_max:,}\n\nSelect range:",
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
        [InlineKeyboardButton("◀️ Back", callback_data="config_main")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_min = bot_instance.config["liquidity_min"]
    current_max = bot_instance.config["liquidity_max"]
    
    await query.edit_message_text(
        f"💧 **Liquidity Filter**\n\nCurrent: ${current_min:,} - ${current_max:,}\n\nSelect range:",
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


async def back_to_main(query) -> None:
    """Go back to main menu"""
    keyboard = [
        [InlineKeyboardButton("⚙️ Configure Filters", callback_data="config_main")],
        [InlineKeyboardButton("📊 View Current Config", callback_data="view_config")],
        [InlineKeyboardButton("🚀 Start Monitoring", callback_data="start_monitoring")],
        [InlineKeyboardButton("⏹️ Stop Monitoring", callback_data="stop_monitoring")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🤖 **Meme Coin Alert Bot**\n\nSelect an option:",
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
            # Fetch new coins
            coins = await bot_instance.fetch_new_coins()
            
            for coin in coins:
                pair_address = coin.get("pairAddress")
                
                if not pair_address:
                    continue
                
                # Skip if already alerted
                if pair_address in bot_instance.last_checked_pairs:
                    continue
                
                # Apply filters
                if bot_instance.apply_filters(coin):
                    # Send alert
                    message = bot_instance.format_coin_alert(coin)
                    
                    try:
                        if TELEGRAM_CHAT_ID:
                            await context.bot.send_message(
                                chat_id=TELEGRAM_CHAT_ID,
                                text=message,
                                parse_mode="Markdown"
                            )
                            logger.info(f"Alert sent for {coin.get('baseToken', {}).get('symbol', 'Unknown')}")
                            
                            # Track for signals (safely handle price conversion)
                            if bot_instance.config["signals"]:
                                price_usd = coin.get("priceUsd", "0")
                                try:
                                    initial_price = float(price_usd) if price_usd else 0.0
                                except (ValueError, TypeError):
                                    initial_price = 0.0
                                
                                bot_instance.tracked_pairs[pair_address] = {
                                    "initial_price": initial_price,
                                    "timestamp": datetime.now(),
                                    "symbol": coin.get("baseToken", {}).get("symbol", "Unknown")
                                }
                    except Exception as e:
                        logger.error(f"Error sending alert: {e}")
                    
                    # Mark as alerted
                    bot_instance.last_checked_pairs.add(pair_address)
            
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
            
            # Wait before next check
            await asyncio.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            await asyncio.sleep(5)


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
    
    # Start bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
