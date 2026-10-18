from pathlib import Path
from typing import Any, Protocol

from hakimi_analysis.models import Segment


class ResponsesWire(Protocol):
    """Explicit provider request adaptation; business contracts stay in the caller."""

    @property
    def provider_label(self) -> str: ...

    def adapt_request(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def validate_configuration(
        self,
        *,
        base_url: str,
        model_id: str,
        visual_model_id: str,
        retry_delays: tuple[float, ...],
    ) -> None: ...

    async def video_content(self, path: Path, window: Segment) -> list[dict[str, Any]]: ...

    def validate_response(self, payload: dict[str, Any]) -> None: ...
