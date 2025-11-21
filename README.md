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

# On Linux/Mac, load the environment variables
export $(cat .env | xargs)

# Or set them directly
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
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

| Variable | Description | Required |
|----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from @BotFather | Yes |
| `TELEGRAM_CHAT_ID` | Chat ID where alerts will be sent | Yes |

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
├── requirements.txt  # Python dependencies
├── Dockerfile       # Docker configuration for Railway
├── README.md        # This file
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
