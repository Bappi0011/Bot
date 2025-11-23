# ========================================
# DEXSCREENER API CONFIGURATION
# ========================================
# Dexscreener API endpoint for fetching latest Solana pairs (new pairs)
PHOTON_API_URL = os.getenv("PHOTON_API_URL", "https://api.dexscreener.com/latest/dex/pairs/solana")

# Dexscreener polling interval in seconds
PHOTON_POLL_INTERVAL = int(os.getenv("PHOTON_POLL_INTERVAL", "60"))

# No API key required for Dexscreener
PHOTON_API_KEY = ""

# Configuration constants
MAX_PAIRS_FETCH = 100  # Maximum number of pairs to fetch per API call

    async def fetch_photon_tokens(self) -> List[Dict]:
        """
        ========================================
        DEXSCREENER PAIR FETCHER (formerly PhotonScan)
        ========================================
        Fetch latest pairs from Dexscreener API and filter for new tokens
        
        Returns:
            List of token dictionaries matching filters (Raydium pairs only)
        """
        await self.start_session()
        
        try:
            # Build query parameters for Dexscreener API
            params = {
                "limit": MAX_PAIRS_FETCH,
            }
            
            logger.info(f"Fetching latest pairs from Dexscreener API: {PHOTON_API_URL}")
            
            # Make request to Dexscreener API (no auth needed)
            async with self.session.get(PHOTON_API_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Extract pairs from response
                    pairs = data.get("pairs", [])
                    
                    if isinstance(pairs, list):
                        # Filter for Raydium pairs only
                        raydium_pairs = [pair for pair in pairs if pair.get("platformId") == "raydium"]
                        
                        # Sort by pairCreatedAt in descending order (newest first)
                        raydium_pairs = sorted(raydium_pairs, key=lambda x: x.get("pairCreatedAt", ""), reverse=True)
                        
                        logger.info(f"Fetched {len(pairs)} pairs, {len(raydium_pairs)} Raydium pairs from Dexscreener")
                        
                        # Convert pairs to token format for compatibility
                        tokens = []
                        for pair in raydium_pairs:
                            base_token = pair.get("baseToken", {})
                            token = {
                                "address": base_token.get("address", ""),
                                "name": base_token.get("name", "Unknown"),
                                "symbol": base_token.get("symbol", "Unknown"),
                                "createdAt": pair.get("pairCreatedAt", ""),
                                "platform": "Raydium",
                                "marketCap": pair.get("marketCap", 0),
                                "liquidity": pair.get("liquidity", {}).get("usd", 0),
                                "price": pair.get("priceUsd", 0),
                                "socials": {},  # Dexscreener doesn't provide socials
                                "freezeAuthority": None,  # Not available
                                "pairAddress": pair.get("pairAddress", ""),
                                "dexId": pair.get("dexId", ""),
                            }
                            tokens.append(token)
                        
                        return tokens
                    else:
                        logger.warning("Unexpected response format from Dexscreener")
                        return []
                else:
                    error_text = await resp.text()
                    logger.error(f"Dexscreener API error: HTTP {resp.status} - {error_text}")
                    return []
                    
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching from Dexscreener: {e}")
            return []
        except Exception as e:
            error_msg = f"Error fetching pairs from Dexscreener: {e}"
            logger.error(error_msg, exc_info=True)
            return []

    def apply_general_filters_to_photon_token(self, token: Dict) -> bool:
        """
        ========================================
        GENERAL FILTER APPLIER FOR DEXSCREENER TOKENS
        ========================================
        Apply general user-defined filters to a Dexscreener token
        
        Args:
            token: Token data dictionary from Dexscreener
            
        Returns:
            True if token passes all general filters, False otherwise
        """
        try:
            # Network filter - Dexscreener tokens are Solana-only
            if self.config["network"] != "all" and self.config["network"] != "solana":
                logger.debug(f"Token filtered out by network filter: {self.config['network']}")
                return False
            
            # Pair age filter - check when the pair was created
            created_at = token.get("createdAt")
            
            if created_at:
                try:
                    created_time = None
                    
                    # Handle ISO format string from Dexscreener
                    if isinstance(created_at, str):
                        # ISO format string - replace 'Z' with '+00:00' for UTC
                        timestamp_str = created_at
                        if timestamp_str.endswith('Z'):
                            timestamp_str = timestamp_str[:-1] + '+00:00'
                        created_time = datetime.fromisoformat(timestamp_str)
                    
                    if created_time:
                        # Ensure timezone awareness
                        if created_time.tzinfo is None:
                            created_time = created_time.replace(tzinfo=timezone.utc)
                        
                        now_utc = datetime.now(timezone.utc)
                        age_minutes = (now_utc - created_time).total_seconds() / 60
                        
                        # Apply age filter
                        if not (self.config["pair_age_min"] <= age_minutes <= self.config["pair_age_max"]):
                            logger.debug(f"Token filtered out by age filter: {age_minutes:.2f} minutes (range: {self.config['pair_age_min']}-{self.config['pair_age_max']})")
                            return False
                    else:
                        logger.info("Could not parse timestamp from Dexscreener token")
                        
                except Exception as e:
                    logger.warning(f"Error parsing token creation time: {e}")
            
            # Market cap filter
            market_cap = token.get("marketCap")
            if market_cap is not None:
                try:
                    market_cap = float(market_cap)
                    if not (self.config["market_cap_min"] <= market_cap <= self.config["market_cap_max"]):
                        return False
                except (ValueError, TypeError):
                    pass
            
            # Liquidity filter
            liquidity = token.get("liquidity")
            if liquidity is not None:
                try:
                    liquidity = float(liquidity)
                    if not (self.config["liquidity_min"] <= liquidity <= self.config["liquidity_max"]):
                        return False
                except (ValueError, TypeError):
                    pass
            
            # Social links filter - Dexscreener doesn't provide socials
            # Skip social filters for Dexscreener data
            
            return True
            
        except Exception as e:
            logger.error(f"Error applying general filters to Dexscreener token: {e}")
            return False

    def format_photon_alert(self, token: Dict) -> str:
        """
        ========================================
        DEXSCREENER TOKEN ALERT FORMATTER
        ========================================
        Format Dexscreener token information for alert message
        
        Args:
            token: Token data from Dexscreener
            
        Returns:
            Formatted message string
        """
        mint = token.get("address", "Unknown")
        name = token.get("name", "Unknown")
        symbol = token.get("symbol", "Unknown")
        
        message = f"🚀 **New Token from Dexscreener!**\n\n"
        message += f"**Name:** {name}\n"
        message += f"**Symbol:** {symbol}\n"
        message += f"**Mint:** ` {mint}`\n"
        message += f"**Network:** Solana\n\n"
        
        # Add market data if available
        if token.get("marketCap"):
            message += f"**Market Cap:** ${token['marketCap']:,.2f}\n"
        
        if token.get("liquidity"):
            message += f"**Liquidity:** ${token['liquidity']:,.2f}\n"
        
        if token.get("price"):
            message += f"**Price:** ${token['price']}\n"
        
        # Add platform info
        platform = token.get("platform", "Raydium")
        message += f"**Platform:** {platform}\n"
        
        # Add pair address
        if token.get("pairAddress"):
            message += f"**Pair Address:** `{token['pairAddress']}`\n"
        
        # Add links
        message += f"\n**Links:**\n"
        message += f"- [Dexscreener](https://dexscreener.com/solana/{mint})\n"
        message += f"- [Solscan](https://solscan.io/token/{mint})\n"
        
        message += f"\n**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        
        return message
