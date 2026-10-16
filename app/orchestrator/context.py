from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.deliverables.explanations import Explainer
from app.extraction.extractor import Extractor
from app.fallback.loader import CuratedDataset
from app.orchestrator.job_store import JobStore
from app.providers.factory import Providers
from app.scoring.scorer import Scorer


@dataclass
class AppContext:
    settings: Settings
    providers: Providers
    store: JobStore
    curated: CuratedDataset
    scorer: Scorer

    @property
    def live(self) -> bool:
        return self.providers.effective_mode == "live" and self.providers.llm is not None \
            and self.providers.search is not None

    @property
    def replay_available(self) -> bool:
        from app.orchestrator.replay import load_replay
        return load_replay(self.settings.demo_replay_path) is not None

    def extractor(self) -> Extractor:
        return Extractor(self.providers.llm, self.settings.llm_effort_extraction, self.settings.max_extraction_chars)

    def explainer(self) -> Explainer:
        return Explainer(self.providers.llm if self.live else None, self.settings.llm_effort_writing)
