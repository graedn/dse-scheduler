import discord
import logging

log = logging.getLogger(__name__)


class RescheduleView(discord.ui.View):
    """Posted to the log channel when a confirmed broadcast is rescheduled.
    Full implementation in Task 5."""

    def __init__(self, match_id: int, old_ts: int, new_ts: int):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.old_ts = old_ts
        self.new_ts = new_ts
