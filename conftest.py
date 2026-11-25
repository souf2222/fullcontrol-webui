import pathlib
import sys

# Make the API package (services/api/app) importable when running pytest from
# the repository root, e.g.:  pytest tests/
sys.path.insert(0, str(pathlib.Path(__file__).parent / "services" / "api"))
