"""Research one community and save the Street View photos, to check the method by eye.

Usage: python trial_community.py 上賀 頭份市
"""

import json
import sys
from pathlib import Path

from rentbot import orientation

community, town = sys.argv[1], sys.argv[2]
out = Path("debug") / f"{town}_{community}"
result = orientation.research(community, town, photo_dir=out)
(out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
print("大門：", orientation.describe(result["gate"]))
print("車道口：", orientation.describe(result["driveway"]))
