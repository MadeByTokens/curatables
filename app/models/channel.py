from dataclasses import dataclass


@dataclass
class Channel:
    name: str
    description: str = ""
    position: int = 0
    created_at: str = ""
    id: int | None = None
    owner_profile_id: int | None = None
    banner_filename: str = ""
    icon_filename: str = ""
    color: str = "#2a9d8f"
    # One kid's own look for the channel (see ChannelRepository.with_looks);
    # never stored on the channel itself.
    kid_emoji: str = ""
    kid_picture: str = ""
    original_name: str = ""
