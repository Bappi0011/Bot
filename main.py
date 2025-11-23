except aiohttp.ClientError as e:
    if "Name or service not known" in str(e):
        logger.warning(f"DNS resolution failed for PhotonScan API: {e}")
    else:
        logger.error(f"Network error fetching from PhotonScan: {e}")
    return []