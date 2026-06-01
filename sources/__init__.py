from .wechat import WeFlowSource
from .github_webhook import GitHubWebhookSource

try:
    from .telegram import TelegramSource
except ModuleNotFoundError:
    TelegramSource = None  # type: ignore[assignment]

try:
    from .slack import SlackSource
except ModuleNotFoundError:
    SlackSource = None  # type: ignore[assignment]

__all__ = [
    "WeFlowSource",
    "GitHubWebhookSource",
    "TelegramSource",
    "SlackSource",
]
