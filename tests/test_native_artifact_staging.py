import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'tools/native'))

# Keep the live native staging tests in the standard engineering test discovery.
from test_stage_artifacts import ArtifactStagingTest  # noqa: F401,E402
