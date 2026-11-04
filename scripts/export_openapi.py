import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

if len(sys.argv) != 2:
    raise SystemExit("usage: python scripts/export_openapi.py <output.json>")

Path(sys.argv[1]).write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
