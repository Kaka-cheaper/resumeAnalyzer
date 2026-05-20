"""审计 features.json：列出每个 feature 的 step.refs 实际是否存在 / 是否实施"""

import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
FEATURES = REPO / ".codesee" / "features.json"


def check_ref(file_ref: str) -> str:
    """检查 ref 文件是否存在；返回标记。"""
    p = REPO / file_ref
    if p.exists():
        return "OK"
    return "MISSING"


with open(FEATURES, encoding="utf-8") as f:
    d = json.load(f)

print(f"manifest.lang     : {d['manifest'].get('lang')}")
print(f"manifest.generator: {d['manifest'].get('generator')}")
print(f"epics             : {len(d['epics'])}")
print(f"features          : {len(d['features'])}")
print()

# Epic 概览
print("=" * 70)
print("EPICS")
print("=" * 70)
for e in d["epics"]:
    print(f"  [{e.get('order')}] {e['id']:15} {e['name']}  ({e.get('importance', '')})")

# Feature 详细审计
print()
print("=" * 70)
print("FEATURES（按 implemented / planned 分组）")
print("=" * 70)
for f in d["features"]:
    tags = f.get("tags", [])
    is_planned = "planned" in tags
    is_deferred = "deferred" in tags
    status_chip = "[IMPL]" if not is_planned else ("[DEFR]" if is_deferred else "[PLAN]")
    print(
        f"\n  {status_chip} {f['id']:25} [conf={f['confidence']}] "
        f"tags={tags}\n    -> {f.get('summary', '<None>')[:80]}"
    )
    # 检查每个 step 的 refs
    for s in f["steps"]:
        refs = s.get("refs", [])
        if not refs:
            print(f"    - {s['id']:18} [{s.get('role','?')}]  WARN: no refs")
        else:
            for r in refs:
                fn = r.get("file", "")
                mark = check_ref(fn)
                lines_info = ""
                if "lines" in r:
                    lines_info = f"  lines={r['lines']}"
                if mark == "OK":
                    print(f"    - {s['id']:18} [{s.get('role','?'):14}] -> {fn}{lines_info}")
                else:
                    print(
                        f"    - {s['id']:18} [{s.get('role','?'):14}] -> {fn} {mark}{lines_info}"
                    )
    # error 分支统计
    flow_kinds = [fl["kind"] for fl in f.get("flow", [])]
    has_error_branch = "error" in flow_kinds or any(s.get("role") == "error" for s in f["steps"])
    if not has_error_branch:
        print("    WARN: no error branch or error step")

# cross_feature 概览
print()
print("=" * 70)
print(f"CROSS_FEATURE（{len(d.get('cross_feature', []))}）")
print("=" * 70)
for c in d.get("cross_feature", []):
    note = c.get("note", "")
    mode = c.get("mode", "")
    mode_str = f" ({mode})" if mode else ""
    print(f"  {c['from']:25} --{c['kind']}{mode_str}--> {c['to']:25}  {note}")

# epic_flow 概览
print()
print("=" * 70)
print(f"EPIC_FLOW（{len(d.get('epic_flow', []))}）")
print("=" * 70)
for ef in d.get("epic_flow", []):
    print(f"  {ef['from']:15} --{ef['kind']}--> {ef['to']:15}  {ef.get('note', '')}")
