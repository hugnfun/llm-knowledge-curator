"""Data models and enums — shared vocabulary across the pipeline."""

from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional
import json


class ItemSource(str, Enum):
    CLIPPINGS = "clippings"
    X_BOOKMARKS = "x-bookmarks"
    TELEGRAM = "telegram"


class ItemVerdict(str, Enum):
    SEED = "seed"
    ASSET = "asset"
    ARCHIVE = "archive"
    PENDING = "pending"


class ItemStatus(str, Enum):
    PENDING = "pending"
    POOLED = "pooled"        # written to vault pool
    USED = "used"             # consumed by daily thinking / draft
    DISMISSED = "dismissed"   # user manually dismissed


class ItemPriority(str, Enum):
    HIGH = "high"
    NORMAL = "normal"


class PipelineStage(str, Enum):
    COLLECT = "collect"           # scan inbox
    CLASSIFY = "classify"         # LLM parser
    POOL = "pool"                 # write back to vault
    DAILY_THINKING = "daily_thinking"
    USER_THINKING = "user_thinking"
    DRAFT_GENERATE = "draft_generate"
    DRAFT_SELECT = "draft_select"
    DRAFT_POLISH = "draft_polish"
    ASSET_PRODUCE = "asset_produce"  # image/podcast/video
    PUBLISH = "publish"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class EventType(str, Enum):
    RAW_ITEM_CREATED = "RawItem.Created"
    ITEM_CLASSIFIED = "Item.Classified"
    ITEM_POOLED = "Item.Pooled"
    DAILY_THINKING_REQUESTED = "DailyThinking.Requested"
    USER_THINKING_SUBMITTED = "UserThinking.Submitted"
    DRAFT_GENERATED = "Draft.Generated"
    DRAFT_SELECTED = "Draft.Selected"
    DRAFT_POLISHED = "Draft.Polished"
    ASSET_READY = "Asset.Ready"
    POST_PUBLISHED = "Post.Published"


# Stage progression order (for pipeline kanban display)
STAGE_ORDER = [
    PipelineStage.COLLECT,
    PipelineStage.CLASSIFY,
    PipelineStage.POOL,
    PipelineStage.DAILY_THINKING,
    PipelineStage.USER_THINKING,
    PipelineStage.DRAFT_GENERATE,
    PipelineStage.DRAFT_SELECT,
    PipelineStage.DRAFT_POLISH,
    PipelineStage.ASSET_PRODUCE,
    PipelineStage.PUBLISH,
]


class ThinkingStatus(str, Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class DraftStatus(str, Enum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    POLISHED = "polished"
    PUBLISHED = "published"
    DISMISSED = "dismissed"


@dataclass
class Verdict:
    verdict: str = "archive"
    category: str = ""
    trigger: str = ""
    reason: str = ""
    confidence: str = "medium"
    priority: str = "normal"


@dataclass
class InboxUnit:
    unit_id: str
    source: str
    source_path: str
    abs_path: str
    title: str
    preview: str = ""
    char_len: int = 0
    tg_message_idx: Optional[int] = None
    tg_message_time: Optional[str] = None


@dataclass
class Draft:
    id: str = ""
    date: str = ""
    angle_id: str = ""
    angle_name: str = ""
    headline: str = ""
    body: str = ""
    hook: str = ""
    image_count: int = 0
    linked_seeds: list = field(default_factory=list)
    status: str = "candidate"


def to_json(obj) -> str:
    """Serialize a dataclass to JSON string."""
    return json.dumps(asdict(obj), ensure_ascii=False)


def from_json(cls, data: str):
    """Deserialize from JSON string into a dataclass."""
    return cls(**json.loads(data))
