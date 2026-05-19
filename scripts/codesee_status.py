"""快速查看 codesee 实施进度"""

import json

with open(".codesee/features.json", encoding="utf-8") as f:
    d = json.load(f)

features = d["features"]
implemented = [x for x in features if "planned" not in (x.get("tags") or [])]
planned = [x for x in features if "planned" in (x.get("tags") or [])]

print(f"Implemented: {len(implemented)}/{len(features)}")
for x in implemented:
    print(f"  ✓ {x['id']:30} [conf={x['confidence']}]  tags={x.get('tags', [])}")
print(f"\nPlanned still: {len(planned)}")
for x in planned:
    print(f"  ◯ {x['id']:30} [conf={x['confidence']}]  tags={x.get('tags', [])}")
