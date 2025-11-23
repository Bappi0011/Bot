# ========================================
# SOLSCAN API CONFIGURATION
# ========================================
# Solscan Pro API endpoint for fetching latest tokens (new pairs)
PHOTON_API_URL = os.getenv("PHOTON_API_URL", "https://pro-api.solscan.io/v2.0/token/latest")

# Solscan polling interval in seconds
PHOTON_POLL_INTERVAL = int(os.getenv("PHOTON_POLL_INTERVAL", "60"))

# Solscan API key (required for Pro API)
PHOTON_API_KEY = os.getenv("PHOTON_API_KEY", "")

# Configuration constants
MAX_PAIRS_FETCH = 100  # Maximum number of pairs to fetch per API call

    async def fetch_photon_tokens(self) -> List[Dict]:
        """
        ========================================
        SOLSCAN TOKEN FETCHER (formerly PhotonScan)
        ========================================
        Fetch latest tokens from Solscan Pro API with filters for new pairs
        
        Returns:
            List of token dictionaries matching filters
        """
        await self.start_session()
        
        try:
            # Build query parameters for Solscan API
            params = {
                "platform_id": "raydium",  # Focus on Raydium new pairs
                "page": 1,
                "page_size": MAX_PAIRS_FETCH,
            }
            
            # API key required for Solscan Pro
            headers = {}
            if PHOTON_API_KEY:
                headers["token"] = PHOTON_API_KEY
            else:
                logger.warning("Solscan Pro API key not set - API calls may fail")
            
            logger.info(f"Fetching latest tokens from Solscan API: {PHOTON_API_URL}")
            
            # Make request to Solscan API
            async with self.session.get(PHOTON_API_URL, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Extract tokens from response
                    tokens = data.get("data", [])
                    
                    if isinstance(tokens, list):
                        logger.info(f"Fetched {len(tokens)} latest tokens from Solscan")
                        return tokens
                    else:
                        logger.warning("Unexpected response format from Solscan")
                        return []
                else:
                    error_text = await resp.text()
                    logger.error(f"Solscan API error: HTTP {resp.status} - {error_text}")
                    return []
                    
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching from Solscan: {e}")
            return []
        except Exception as e:
            error_msg = f"Error fetching tokens from Solscan: {e}"
            logger.error(error_msg, exc_info=True)
            return []

    def apply_general_filters_to_photon_token(self, token: Dict) -> bool:
        """
        ========================================
        GENERAL FILTER APPLIER FOR SOLSCAN TOKENS
        ========================================
        Apply general user-defined filters to a Solscan token
        
        Args:
            token: Token data dictionary from Solscan
            
        Returns:
            True if token passes all general filters, False otherwise
        """
        try:
            # Network filter - Solscan tokens are Solana-only
            if self.config["network"] != "all" and self.config["network"] != "solana":
                logger.debug(f"Token filtered out by network filter: {self.config['network']}")
                return False
            
            # Pair age filter - check when the token was created
            created_at = token.get("createdAt") or token.get("created_at") or token.get("timestamp")
            
            if created_at:
                try:
                    created_time = None
                    
                    # Handle timestamp formats from Solscan (usually milliseconds)
                    if isinstance(created_at, (int, float)):
                        # Unix timestamp (milliseconds)
                        created_time = datetime.fromtimestamp(created_at / 1000, timezone.utc)
                    elif isinstance(created_at, str):
                        # ISO format string
                        if created_at.endswith('Z'):
                            created_at = created_at[:-1] + '+00:00'
                        created_time = datetime.fromisoformat(created_at)
                    
                    if created_time:
                        now_utc = datetime.now(timezone.utc)
                        age_minutes = (now_utc - created_time).total_seconds() / 60
                        
                        # Apply age filter
                        if not (self.config["pair_age_min"] <= age_minutes <= self.config["pair_age_max"]):
                            logger.debug(f"Token filtered out by age filter: {age_minutes:.2f} minutes (range: {self.config['pair_age_min']}-{self.config['pair_age_max']})")
                            return False
                    else:
                        logger.info("Could not parse timestamp from Solscan token")
                        
                except Exception as e:
                    logger.warning(f"Error parsing token creation time: {e}")
            
            # Market cap filter (if available)
            market_cap = token.get("marketCap") or token.get("mcap")
            if market_cap is not None:
                try:
                    market_cap = float(market_cap)
                    if not (self.config["market_cap_min"] <= market_cap <= self.config["market_cap_max"]):
                        return False
                except (ValueError, TypeError):
                    pass
            
            # Liquidity filter (if available)
            liquidity = token.get("liquidity") or token.get("liquidityUsd")
            if liquidity is not None:
                try:
                    liquidity = float(liquidity)
                    if not (self.config["liquidity_min"] <= liquidity <= self.config["liquidity_max"]):
                        return False
                except (ValueError, TypeError):
                    pass
            
            # Social links filter (if available)
            socials = token.get("socials", {})
            
            # Check Telegram requirement
            if self.config["social_links"]["telegram"]:
                if not socials.get("telegram"):
                    return False
            
            # Check Twitter requirement
            if self.config["social_links"]["twitter"]:
                if not socials.get("twitter"):
                    return False
            
            # Check Website requirement
            if self.config["social_links"]["website"]:
                if not socials.get("website"):
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error applying general filters to Solscan token: {e}")
            return False

    def format_photon_alert(self, token: Dict) -> str:
        """
        ========================================
        SOLSCAN TOKEN ALERT FORMATTER
        ========================================
        Format Solscan token information for alert message
        
        Args:
            token: Token data from Solscan
            
        Returns:
            Formatted message string
        """
        mint = token.get("address") or token.get("mint", "Unknown")
        name = token.get("name", "Unknown")
        symbol = token.get("symbol", "Unknown")
        
        message = f"🚀 **New Token from Solscan!**\n\n"
        message += f"**Name:** {name}\n"
        message += f"**Symbol:** {symbol}\n"
        message += f"**Mint:** `{{mint}}`\n"
        message += f"**Network:** Solana\n\n"
        
        # Add market data if available
        if "marketCap" in token:
            message += f"**Market Cap:** ${{token['marketCap']:,.2f}}\n"
        
        if "liquidity" in token:
            message += f"**Liquidity:** ${{token['liquidity']:,.2f}}\n"
        
        if "price" in token:
            message += f"**Price:** ${{token['price']}}\n"
        
        # Add platform info
        platform = token.get("platform", "Raydium")
        message += f"**Platform:** {{platform}}\n"
        
        # Add security info if available
        if token.get("freezeAuthority"):
            message += f"**Freeze Authority:** {{'✅ Revoked' if not token['freezeAuthority'] else '❌ Not Revoked'}}\n"
        
        # Add links
        message += f"\n**Links:**\n"
        message += f"- [Solscan](https://solscan.io/token/{{mint}})\n"
        message += f"- [Dexscreener](https://dexscreener.com/solana/{{mint}})\n"
        
        message += f"\n**Time:** {{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}}\n"
        
        return message