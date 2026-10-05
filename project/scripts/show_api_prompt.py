from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm_common import BASE_SYSTEM
print(BASE_SYSTEM)
