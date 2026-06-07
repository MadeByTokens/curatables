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

# For parent form dropdowns: list of (key, emoji) tuples
AVATAR_CHOICES = list(AVATAR_EMOJIS.items())

THEME_CHOICES = [("base", "Default"), ("playful", "Playful"), ("calm", "Calm")]
SEARCH_CHOICES = [("disabled", "Disabled"), ("curated", "Curated (approved content only)")]
