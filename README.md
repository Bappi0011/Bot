# Meme Coin Alert Bot

A Telegram bot that monitors and alerts on newly launched meme coins with customizable filters and price change signals.

## Quick Start

1. Get a Telegram Bot Token from [@BotFather](https://t.me/BotFather)
2. Get your Chat ID from [@userinfobot](https://t.me/userinfobot)
3. Set environment variables `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
4. Run `python main.py`
5. Open your bot on Telegram and send `/start`

## Features

- 🚀 Real-time monitoring of new meme coin launches
- ⚙️ Customizable filters:
  - API source selection
  - Network/Chain filtering (Ethereum, BSC, Polygon, Solana, Base, Arbitrum, etc.)
  - Social links requirements (Telegram, Twitter, Website)
  - Pair age range (min/max in minutes)
  - Market cap range
  - Liquidity range
  - Dev hold percentage range
  - Top 10 holders percentage range
- 📈 Signal alerts for price changes over time intervals
- 🎯 Inline keyboard interface for easy configuration
- 📊 Integration with DexScreener API
- 🔄 Checks for new coins 1-2 times per second
- 🚨 **Error Alerting System**: Automatically sends all errors and failures to Telegram with detailed information

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

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from @BotFather | Yes | - |
| `TELEGRAM_CHAT_ID` | Chat ID where alerts will be sent | Yes | - |
| `TELEGRAM_ERROR_ALERTS_ENABLED` | Enable/disable error alerts to Telegram (true/false) | No | true |
| `SOLANA_RPC_URL` | Solana RPC endpoint URL | No | https://api.mainnet-beta.solana.com |
| `RAYDIUM_V4_PROGRAM_ID` | Raydium V4 AMM Program ID | No | 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 |

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

1. The bot fetches the latest coin pairs from the DexScreener API
2. Applies your configured filters to each pair
3. Sends alerts for coins that match your criteria
4. Tracks alerted coins for signal monitoring
5. Sends additional alerts when price change thresholds are met

## Alert Format

Each alert includes:
- Token name and symbol
- Blockchain network
- DEX name
- Current price
- Liquidity
- Market cap
- 24h trading volume
- 24h price change
- Direct link to DexScreener

## API Rate Limits

The bot checks for new coins 1-2 times per second. Please be aware of DexScreener API rate limits. The bot includes error handling for API failures.

## 🚨 Error Alerting System

The bot includes a comprehensive error alerting system that automatically sends detailed error information to your Telegram chat. This feature helps you monitor the bot's health and quickly identify issues without manually checking logs.

### Features

- **Automatic Error Detection**: Captures all errors and exceptions that occur during bot execution
- **Detailed Information**: Each error alert includes:
  - Timestamp when the error occurred
  - Error level (ERROR, CRITICAL)
  - Module and function where the error occurred
  - Complete error message
  - Full traceback for debugging
- **Configurable**: Enable or disable error alerting via environment variable
- **Non-intrusive**: Error alerts don't interrupt normal bot operation

### Setup

1. **Enable Error Alerts** (enabled by default):
```bash
export TELEGRAM_ERROR_ALERTS_ENABLED=true
```

2. **Disable Error Alerts** (if needed):
```bash
export TELEGRAM_ERROR_ALERTS_ENABLED=false
```

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

### What Errors Are Captured?

The system captures and reports:

1. **RPC/Network Errors**: Connection failures, timeouts, HTTP errors
2. **Data Processing Errors**: JSON parsing errors, data validation failures
3. **Telegram API Errors**: Message sending failures
4. **Monitoring Loop Errors**: Issues in the coin monitoring process
5. **Uncaught Exceptions**: Any unhandled exceptions that could crash the bot

### Testing Error Alerts

To test if error alerting is working:

1. Start the bot with error alerts enabled
2. The bot will log a message indicating error alerting status
3. You can trigger a test error by:
   - Setting an invalid RPC URL
   - Using an invalid filter configuration
   - Any operation that would normally cause an error

### Configuration Example

Complete `.env` configuration with error alerting:

```bash
# Required
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789

# Optional - Error Alerting
TELEGRAM_ERROR_ALERTS_ENABLED=true

# Optional - Solana Configuration
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
RAYDIUM_V4_PROGRAM_ID=675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8
```

### Best Practices

1. **Keep Error Alerts Enabled**: They help you catch issues early
2. **Monitor Regularly**: Check error alerts to identify patterns
3. **Act on Errors**: Investigate and fix recurring errors
4. **Use a Dedicated Chat**: Consider using a separate chat for error alerts to avoid mixing with coin alerts

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

### Bot doesn't start
- Verify your `TELEGRAM_BOT_TOKEN` is correct
- Ensure the token is set as an environment variable

### No alerts are sent
- Check that `TELEGRAM_CHAT_ID` is set correctly
- Verify monitoring is started (use "Start Monitoring" button)
- Check your filter settings aren't too restrictive
- Review logs for errors

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
