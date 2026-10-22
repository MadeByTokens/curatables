"""Shared constants — single source of truth for UI choices."""

# Avatar options: key → emoji
AVATAR_EMOJIS = {
    "default": "\U0001f464",   # 👤
    "bear": "\U0001f43b",      # 🐻
    "cat": "\U0001f431",       # 🐱
    "dog": "\U0001f436",       # 🐶
    "fox": "\U0001f98a",       # 🦊
    "rabbit": "\U0001f430",    # 🐰
    "panda": "\U0001f43c",     # 🐼
    "unicorn": "\U0001f984",   # 🦄
    "star": "\u2b50",          # ⭐
    "rocket": "\U0001f680",    # 🚀
    "rainbow": "\U0001f308",   # 🌈
    "sun": "\u2600\ufe0f",     # ☀️
}

# Pictures a kid can give a channel on the Channels page.
CHANNEL_EMOJIS = [
    "\U0001f682", "\U0001f697", "\U0001f69a", "\u2708\ufe0f", "\U0001f680", "\U0001f996",
    "\U0001f436", "\U0001f431", "\U0001f981", "\U0001f438", "\U0001f42c", "\U0001f984",
    "\U0001f3b5", "\U0001f3a8", "\u26bd", "\U0001f3ae", "\U0001f52c", "\U0001f4da",
    "\U0001f308", "\u2b50", "\U0001f30d", "\U0001f355", "\u2764\ufe0f", "\U0001f600",
]

# For parent form dropdowns: list of (key, emoji) tuples
AVATAR_CHOICES = list(AVATAR_EMOJIS.items())

THEME_CHOICES = [
    ("base", "Default"),
    ("playful", "Playful"),
    ("calm", "Calm"),
    ("clear", "Big & clear (large text, high contrast)"),
]
PIN_STYLE_CHOICES = [("digits", "Numbers (4–6 digits)"), ("pictures", "Pictures (pick 3)")]

# Picture PIN: each picture stands for a digit, so a picture PIN is
# stored and checked like a 3-digit PIN.
PIN_PICTURES = [
    ("1", "\U0001f436", "dog"),
    ("2", "\U0001f431", "cat"),
    ("3", "\U0001f430", "rabbit"),
    ("4", "\U0001f981", "lion"),
    ("5", "\U0001f438", "frog"),
    ("6", "\U0001f435", "monkey"),
    ("7", "\U0001f43c", "panda"),
    ("8", "\U0001f427", "penguin"),
    ("9", "\U0001f422", "turtle"),
]
PICTURE_PIN_LENGTH = 3

SEARCH_CHOICES = [("disabled", "Off"), ("curated", "On: they can search the videos they can see")]
