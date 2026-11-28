"""Pair statuses, their labels and the stats periods."""

PAIR_STATUSES = {
    "low-liquidity": ("Low Liquidity", "never"),
    "low-ratio": ("Low Ratio", "always"),
    "no-data": ("No Data", "always"),
    "high-tax": ("High Tax", "limited"),
    "unverified": ("Unverified", "limited"),
    "honey-pot": ("Honeypot", "limited"),
    "owner-controls": ("Owner Controls", "limited"),
    "sell-unchecked": ("Sell Check Failed", "always"),
    "metrics-rejected": ("Metrics", "limited"),
    "invalid": ("Invalid", "limited"),
    "lp-unlocked": ("LP Unlocked", "limited"),
    "whale": ("Big Holder", "limited"),
    "unknown-hook": ("Unknown Hook", "never"),
    "hook-blocked": ("Hook Blocked Quote", "limited"),
    "spam": ("Spam Name", "never"),
}

PAIR_STATUS = list(PAIR_STATUSES)
PAIR_LABELS = {status: label for status, (label, _) in PAIR_STATUSES.items()}
PAIR_RETRY_ALWAYS = [s for s, (_, retry) in PAIR_STATUSES.items() if retry == "always"]
PAIR_RETRY_LIMITED = [
    s for s, (_, retry) in PAIR_STATUSES.items() if retry == "limited"
]
STATS_PERIODS = {1: "24h", 7: "7 Days", 30: "30 Days", 0: "All Time"}
