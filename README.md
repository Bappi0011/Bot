# Meme Coin Alert Bot

A Telegram bot that monitors and alerts on newly launched meme coins with customizable filters and price change signals.

## 🔄 Migration Notice: WebSocket Streaming

**This bot has been migrated from REST API polling to WebSocket streaming for real-time token data.**

### What Changed?

**Before (v1.x):**
- Polled Solana RPC endpoints for Raydium pool data every ~10 seconds
- Used `getProgramAccountsV2` with pagination
- Higher latency and API rate limiting concerns

**Now (v2.x):**
- Real-time WebSocket streaming from OpenOcean Meme API
- Persistent connection to `wss://meme-api.openocean.finance/ws/public`
- Subscribes to "token" channel for live updates
- Automatic reconnection with configurable intervals
- Lower latency, more efficient, real-time alerts

### Benefits of WebSocket Migration

✅ **Real-time Updates** - No polling delay, instant notifications  
✅ **Reduced API Load** - Single persistent connection vs repeated polling  
✅ **Lower Latency** - Immediate token detection  
✅ **More Efficient** - Less resource usage and network overhead  
✅ **Auto-Reconnect** - Robust connection handling with Telegram alerts on failures

### Configuration

All WebSocket settings are configurable via environment variables in `.env`:

```bash
# WebSocket Configuration
WS_URL=wss://meme-api.openocean.finance/ws/public
WS_CHANNEL=token
WS_RECONNECT_INTERVAL=5
WS_MAX_RECONNECT_ATTEMPTS=0
WS_PING_INTERVAL=30

# Filter Configuration
FILTER_LIQUIDITY_MIN=0
FILTER_LIQUIDITY_MAX=10000000
FILTER_BUY_COUNT_24H_MIN=0
FILTER_TOKEN_STATUS=active
```

See `.env.example` for all available configuration options.

## Quick Start

1. Get a Telegram Bot Token from [@BotFather](https://t.me/BotFather)
2. Get your Chat ID from [@userinfobot](https://t.me/userinfobot)
3. Set environment variables `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
4. Run `python main.py`
5. Open your bot on Telegram and send `/start`

## Features

- 🚀 **Real-time WebSocket streaming** for instant meme coin launch detection
- ⚙️ **Customizable filters:**
  - API source selection
  - Network/Chain filtering (Ethereum, BSC, Polygon, Solana, Base, Arbitrum, etc.)
  - Social links requirements (Telegram, Twitter, Website)
  - Liquidity range filtering
  - Token status filtering (active/inactive)
  - Buy count 24h filtering
  - Market cap range
  - Pair age range (min/max in minutes)
- 📈 **Signal alerts** for price changes over time intervals
- 🎯 **Inline keyboard interface** for easy configuration
- 🔌 **OpenOcean Meme API integration** via WebSocket
- 🔄 **Automatic reconnection** with configurable intervals
- 🚨 **Error Alerting System**: Automatically sends all errors and failures to Telegram with detailed information
- 💾 **Preset Management**: Save and load different filter configurations

## Prerequisites

- Python 3.11 or higher
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Telegram Chat ID (your personal chat ID or group chat ID)

## Installation

### Local Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd Bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set environment variables:
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your tokens
# TELEGRAM_BOT_TOKEN="your_bot_token_here"
# TELEGRAM_CHAT_ID="your_chat_id_here"
# TELEGRAM_ERROR_ALERTS_ENABLED=true

# On Linux/Mac, load the environment variables
export $(cat .env | xargs)

# Or set them directly
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
export TELEGRAM_ERROR_ALERTS_ENABLED=true
```

4. Run the bot:
```bash
python main.py
```

### Railway Deployment

1. Fork or clone this repository to your GitHub account

2. Create a new project on [Railway](https://railway.app/)

3. Connect your GitHub repository

4. Add environment variables in Railway:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
   - `TELEGRAM_CHAT_ID`: Your Telegram chat ID

5. Deploy! Railway will automatically detect the Dockerfile and deploy your bot.

## Environment Variables

### Required Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from @BotFather | Yes | - |
| `TELEGRAM_CHAT_ID` | Chat ID where alerts will be sent | Yes | - |

### WebSocket Configuration

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `WS_URL` | OpenOcean WebSocket endpoint | No | wss://meme-api.openocean.finance/ws/public |
| `WS_CHANNEL` | WebSocket channel to subscribe to | No | token |
| `WS_RECONNECT_INTERVAL` | Reconnection interval in seconds | No | 5 |
| `WS_MAX_RECONNECT_ATTEMPTS` | Max reconnect attempts (0 = unlimited) | No | 0 |
| `WS_PING_INTERVAL` | WebSocket ping interval in seconds | No | 30 |

### Filter Configuration

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `FILTER_LIQUIDITY_MIN` | Minimum liquidity threshold in USD | No | 0 |
| `FILTER_LIQUIDITY_MAX` | Maximum liquidity threshold in USD | No | 10000000 |
| `FILTER_BUY_COUNT_24H_MIN` | Minimum buy count in 24h | No | 0 |
| `FILTER_TOKEN_STATUS` | Token status filter (active/inactive/all) | No | active |

### Error Alerting

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `TELEGRAM_ERROR_ALERTS_ENABLED` | Enable/disable error alerts to Telegram (true/false) | No | true |
| `TELEGRAM_ERROR_DEBUG_MODE` | Enable debug mode for additional context in errors (true/false) | No | false |

### Legacy Variables (Deprecated)

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `SOLANA_RPC_URL` | Solana RPC endpoint URL (deprecated, kept for compatibility) | No | https://api.mainnet-beta.solana.com |
| `RAYDIUM_V4_PROGRAM_ID` | Raydium V4 AMM Program ID (deprecated, kept for compatibility) | No | 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 |

## Usage

1. Start the bot by sending `/start` to your bot on Telegram

2. Use the inline keyboard to:
   - **Configure Filters**: Set up your preferences for coin filtering
   - **View Current Config**: See your current filter settings
   - **Start Monitoring**: Begin monitoring for new coins
   - **Stop Monitoring**: Stop the monitoring process

### Configuration Options

#### Network/Chain
Select which blockchain network to monitor:
- All Networks
- Ethereum
- BSC (Binance Smart Chain)
- Polygon
- Solana
- Base
- Arbitrum

#### Social Links
Toggle requirements for:
- Telegram presence
- Twitter presence
- Website presence

#### Pair Age
Set the age range of pairs to monitor (in minutes):
- 0-5 minutes (very new)
- 0-15 minutes
- 0-30 minutes
- 0-1 hour
- 0-24 hours

#### Market Cap
Set minimum and maximum market cap ranges:
- $0 - $50K
- $0 - $100K
- $0 - $500K
- $0 - $1M
- $0 - $10M

#### Liquidity
Set minimum and maximum liquidity ranges:
- $0 - $10K
- $0 - $50K
- $0 - $100K
- $0 - $500K
- $0 - $1M

#### Signal Settings
Add price change alerts:
- 5 min / +10%
- 15 min / +20%
- 30 min / +50%
- 1 hour / +100%

Multiple signals can be active simultaneously.

## How It Works

### WebSocket Streaming Architecture

1. **Connection Establishment**: Bot establishes a persistent WebSocket connection to OpenOcean Meme API on startup
2. **Channel Subscription**: Subscribes to the "token" channel to receive real-time token updates
3. **Live Data Stream**: Continuously receives live token data including:
   - Token status (active/inactive)
   - Liquidity amounts
   - Buy count in 24h
   - Price information
   - Market cap
   - Network/chain information
4. **Filter Application**: Each incoming token is evaluated against your configured filters
5. **Alert Dispatch**: Tokens matching your criteria trigger instant Telegram alerts
6. **Signal Tracking**: Alerted tokens are tracked for price change signal monitoring
7. **Auto-Reconnect**: If connection drops, bot automatically reconnects with configurable intervals and sends Telegram alerts about connection status

### Connection Management

- **Persistent Connection**: Single WebSocket connection maintained throughout bot lifetime
- **Ping/Pong Keep-Alive**: Regular ping messages keep connection active
- **Automatic Reconnection**: Configurable reconnect logic with exponential backoff
- **Connection Monitoring**: Telegram alerts for connection failures and reconnections
- **Graceful Shutdown**: Proper connection cleanup when monitoring stops

## Alert Format

Each WebSocket alert includes:
- Token name and symbol
- Token address
- Blockchain network
- Token status
- Current price
- Liquidity
- Market cap
- 24h trading volume
- 24h buy count
- Direct link to DEX (if available)
- Timestamp

## API Rate Limits

## WebSocket Connection

The bot uses a persistent WebSocket connection for real-time token updates. This eliminates polling and provides instant notifications when new tokens are detected.

**Benefits:**
- No rate limiting concerns (single persistent connection)
- Real-time updates with minimal latency
- Lower resource usage compared to polling
- Automatic reconnection on connection failures

**Connection Monitoring:**
- WebSocket connection status is continuously monitored
- Automatic reconnection with configurable intervals
- Telegram alerts for connection failures and recoveries
- Configurable maximum reconnection attempts

## 🚨 Error Alerting System

The bot includes a comprehensive error alerting system that automatically sends detailed error information to your Telegram chat. This feature helps you monitor the bot's health and quickly identify issues without manually checking logs.

### Features

- **Automatic Error Detection**: Captures all errors and exceptions that occur during bot execution
- **Detailed Information**: Each error alert includes:
  - Timestamp when the error occurred (UTC timezone)
  - Error level (ERROR, CRITICAL, etc.)
  - Logger name (identifies which component logged the error)
  - Module and function where the error occurred
  - Line number in the source code
  - Complete error message
  - Full traceback for debugging (automatically truncated if too long)
- **Smart Truncation**: Messages longer than Telegram's 4096 character limit are automatically truncated with a clear indicator
- **Debug Mode**: Optional debug mode to include additional context (variables, state) in error messages
- **Configurable**: Enable or disable error alerting via environment variable
- **Non-intrusive**: Error alerts don't interrupt normal bot operation
- **Robust**: The error handler itself is designed to never crash the bot - if Telegram API fails, errors are logged to stderr instead

### Setup

1. **Enable Error Alerts** (enabled by default):
```bash
export TELEGRAM_ERROR_ALERTS_ENABLED=true
```

2. **Disable Error Alerts** (if needed):
```bash
export TELEGRAM_ERROR_ALERTS_ENABLED=false
```

3. **Enable Debug Mode** (for additional context in error messages):
```bash
export TELEGRAM_ERROR_DEBUG_MODE=true
```

**⚠️ Warning**: Debug mode may expose sensitive information (variable values, internal state) in error messages. Only enable in development or when actively debugging production issues.

4. **Using Debug Context** (when debug mode is enabled):
```python
# Pass debug context with your log messages
logger.error(
    "Failed to process transaction",
    exc_info=True,
    extra={'debug_context': {
        'user_id': user_id,
        'transaction_amount': amount,
        'account_balance': balance
    }}
)
```

When debug mode is enabled, this additional context will be included in the Telegram error alert.

### Error Alert Format

When an error occurs, you'll receive a message like this:

```
🚨 **ERROR ALERT** 🚨

**Time:** 2025-11-22 19:10:04 UTC
**Level:** ERROR
**Logger:** __main__
**Module:** main
**Function:** fetch_new_coins
**Line:** 245

**Message:**
Network error making RPC call on page 1: Connection timeout

**Traceback:**
```
Traceback (most recent call last):
  File "main.py", line 245, in fetch_new_coins
    async with self.session.post(SOLANA_RPC_URL, json=payload) as resp:
  ...
aiohttp.ClientError: Connection timeout
```
```

If a traceback exceeds 4096 characters (Telegram's limit), it will be truncated with this indicator:
```
... (truncated due to length)
```

### What Errors Are Captured?

The system captures and reports:

1. **RPC/Network Errors**: Connection failures, timeouts, HTTP errors
2. **Data Processing Errors**: JSON parsing errors, data validation failures
3. **Telegram API Errors**: Message sending failures
4. **Monitoring Loop Errors**: Issues in the coin monitoring process
5. **Uncaught Exceptions**: Any unhandled exceptions that could crash the bot
6. **All Logged Errors**: Any error logged through Python's logging framework at ERROR level or above

### Testing Error Alerts

To test if error alerting is working:

1. Start the bot with error alerts enabled
2. The bot will log a message indicating error alerting status on startup
3. Monitor your Telegram chat for any errors that occur
4. You can trigger a test error by:
   - Setting an invalid RPC URL
   - Using an invalid filter configuration
   - Any operation that would normally cause an error

**Note**: The bot initialization includes automatic verification that the error handler is configured correctly. Check the console logs for confirmation.

### Configuration Example

Complete `.env` configuration with error alerting:

```bash
# Required
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789

# WebSocket Configuration
WS_URL=wss://meme-api.openocean.finance/ws/public
WS_CHANNEL=token
WS_RECONNECT_INTERVAL=5
WS_MAX_RECONNECT_ATTEMPTS=0
WS_PING_INTERVAL=30

# Filter Configuration
FILTER_LIQUIDITY_MIN=0
FILTER_LIQUIDITY_MAX=10000000
FILTER_BUY_COUNT_24H_MIN=0
FILTER_TOKEN_STATUS=active

# Optional - Error Alerting
TELEGRAM_ERROR_ALERTS_ENABLED=true
TELEGRAM_ERROR_DEBUG_MODE=false
```

### Best Practices

1. **Keep Error Alerts Enabled**: They help you catch issues early in production
2. **Monitor Regularly**: Check error alerts to identify patterns and recurring issues
3. **Act on Errors**: Investigate and fix recurring errors promptly
4. **Use a Dedicated Chat**: Consider using a separate chat for error alerts to avoid mixing with coin alerts
5. **Debug Mode**: Only enable debug mode when actively troubleshooting - disable it in normal operation to avoid information overload
6. **Review Logs**: Even with Telegram alerts, maintain access to server logs for detailed investigation

### Troubleshooting Error Alerts

#### Not receiving error alerts?

- Verify `TELEGRAM_ERROR_ALERTS_ENABLED=true` is set
- Check that `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are correct
- Ensure the bot has permission to send messages to the chat
- Check the console logs for any warnings about error handler setup

#### Too many error alerts?

- This usually indicates an underlying issue that needs fixing
- Check the error messages to identify the root cause
- Common issues:
  - Invalid RPC endpoint
  - Network connectivity problems
  - Rate limiting from APIs

## Troubleshooting

### WebSocket Connection Issues

#### WebSocket won't connect
- Verify `WS_URL` is correct: `wss://meme-api.openocean.finance/ws/public`
- Check your internet connection and firewall settings
- Ensure WebSocket connections (wss://) are allowed through your firewall
- Review console logs for connection error details
- Check if you're receiving Telegram alerts about connection failures

#### Frequent reconnections
- This may indicate network instability
- Consider increasing `WS_RECONNECT_INTERVAL` to reduce reconnection frequency
- Check console logs for error patterns
- Verify the OpenOcean API endpoint is available
- Monitor Telegram for connection status alerts

#### No token updates received
- Verify the bot shows "WebSocket connected" in logs
- Check that subscription to "token" channel was successful (look for subscription confirmation in logs)
- Ensure `WS_CHANNEL=token` is set correctly
- Verify your filters aren't too restrictive (try relaxing filter settings)
- Check Telegram for any error alerts

#### Bot stops monitoring after many reconnect attempts
- If `WS_MAX_RECONNECT_ATTEMPTS` is set to a number > 0, the bot will stop after that many failed attempts
- Set `WS_MAX_RECONNECT_ATTEMPTS=0` for unlimited reconnection attempts
- Check the root cause of connection failures in error logs
- Verify network stability and API endpoint availability

### Bot doesn't start
- Verify your `TELEGRAM_BOT_TOKEN` is correct
- Ensure the token is set as an environment variable
- Check that all required dependencies are installed: `pip install -r requirements.txt`
- Review console logs for startup errors

### No alerts are sent
- Check that `TELEGRAM_CHAT_ID` is set correctly
- Verify monitoring is started (use "Start Monitoring" button)
- Check that WebSocket connection is established (look for "WebSocket connected" in logs)
- Ensure your filter settings aren't too restrictive
- Review logs for errors
- Check that tokens are being received from WebSocket (enable debug logging)

### Getting your Chat ID
Send a message to [@userinfobot](https://t.me/userinfobot) on Telegram to get your chat ID.

## Development

### Project Structure
```
Bot/
├── main.py           # Main bot application
├── error_handler.py  # Telegram error alerting system
├── requirements.txt  # Python dependencies
├── Dockerfile       # Docker configuration for Railway
├── README.md        # This file
├── .env.example     # Example environment variables
└── .gitignore      # Git ignore patterns
```

### Contributing
Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.

## Disclaimer

This bot is for educational and informational purposes only. Always do your own research before investing in any cryptocurrency. The developers are not responsible for any financial losses.

## Support

For issues and feature requests, please open an issue on GitHub.
