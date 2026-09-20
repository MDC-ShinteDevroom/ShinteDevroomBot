import json
import logging
from pathlib import Path

logger = logging.getLogger("bot.json_store")


def load_id_map(path: Path) -> dict[int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(key): int(value) for key, value in data.items()}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.exception("%s の読み込みに失敗しました", path.name)
        return {}


def save_id_map(path: Path, data: dict[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
