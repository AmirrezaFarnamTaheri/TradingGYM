from trading_gym.core.operations import (
    ComponentVersion,
    MigrationPlanRequest,
    MigrationStep,
    VersionSkewRequest,
    evaluate_version_skew,
    plan_migration,
)


def step(step_id: str, source: int, target: int, reversible: bool = True):
    return MigrationStep(
        step_id=step_id,
        store="orders",
        from_schema=source,
        to_schema=target,
        implementation_sha256=(str(target) * 64)[:64],
        reversible=reversible,
        description=f"orders {source} to {target}",
    )


def test_migration_plan_selects_shortest_contiguous_path_without_mutation():
    plan = plan_migration(
        MigrationPlanRequest(
            store="orders",
            current_schema=1,
            target_schema=3,
            available_steps=[
                step("migration:orders-1-2", 1, 2),
                step("migration:orders-2-3", 2, 3),
                step("migration:orders-1-4", 1, 4),
            ],
        )
    )
    assert [item.to_schema for item in plan.steps] == [2, 3]
    assert plan.complete and plan.dry_run and not plan.mutation_performed
    assert len(plan.plan_sha256) == 64


def test_migration_plan_reports_missing_path():
    plan = plan_migration(
        MigrationPlanRequest(
            store="orders",
            current_schema=1,
            target_schema=3,
            available_steps=[step("migration:orders-1-2", 1, 2)],
        )
    )
    assert plan.complete is False and plan.steps == []


def component(name: str, major: int, minor: int, pmin: int, pmax: int):
    return ComponentVersion(
        component=name,
        release_major=major,
        release_minor=minor,
        schema_version=1,
        supported_schema_min=1,
        supported_schema_max=2,
        protocol_min=pmin,
        protocol_max=pmax,
    )


def test_version_skew_requires_common_protocol_and_bounded_release_gap():
    healthy = evaluate_version_skew(
        VersionSkewRequest(
            components=[component("api", 8, 0, 2, 4), component("worker", 8, 1, 3, 5)]
        )
    )
    assert (
        healthy.compatible
        and healthy.common_protocol_min == 3
        and healthy.common_protocol_max == 4
    )
    broken = evaluate_version_skew(
        VersionSkewRequest(
            components=[component("api", 8, 0, 1, 2), component("worker", 9, 0, 3, 4)]
        )
    )
    assert broken.compatible is False
    assert "protocol_no_common_version" in broken.violations
    assert "release_major_skew" in broken.violations
