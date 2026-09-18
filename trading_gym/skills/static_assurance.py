def assess_skill_static(skill):
    blockers = []
    if not skill.get("sha256") or len(str(skill["sha256"])) != 64:
        blockers.append("missing_hash")
    forbidden = {"execute", "shell", "wallet_sign", "trade", "payment"} & set(
        skill.get("capabilities", ())
    )
    if forbidden:
        blockers.append("forbidden_capabilities")
    return {
        "blockers": blockers,
        "ready_for_human_review": not blockers,
        "executed": False,
    }


def assess_workflow_static(workflow):
    steps = {x["id"]: x for x in workflow.get("steps", ())}
    blockers = []
    for s in steps.values():
        if int(s.get("retries", 0)) > 10:
            blockers.append("unbounded_retries")
        if (
            float(s.get("timeout_seconds", 0)) <= 0
            or float(s.get("timeout_seconds", 0)) > 86400
        ):
            blockers.append("timeout_out_of_bounds")
        unknown = set(s.get("depends_on", ())) - steps.keys()
        if unknown:
            blockers.append("unknown_dependency")
    # DAG check. Skip steps whose dependencies are unknown — they were already
    # reported above, and visiting them would raise KeyError on the lookup.
    visiting = set()
    done = set()

    def visit(n):
        if n in visiting:
            return False
        if n in done:
            return True
        visiting.add(n)
        for d in steps[n].get("depends_on", ()):
            if d not in steps:
                return False
            if not visit(d):
                return False
        visiting.remove(n)
        done.add(n)
        return True

    if any(not visit(n) for n in steps):
        blockers.append("cycle")
    return {
        "blockers": sorted(set(blockers)),
        "ready_for_human_review": not blockers,
        "executed": False,
    }
