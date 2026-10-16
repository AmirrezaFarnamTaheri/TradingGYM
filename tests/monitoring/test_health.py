from trading_gym.monitoring.health import HealthCheck, HealthStatus


def test_health_check_registration():
    health = HealthCheck()
    health.register("test_check", lambda: True)
    assert "test_check" in health._checks


def test_run_checks_success():
    health = HealthCheck()
    health.register("check1", lambda: True)
    health.register("check2", lambda: True)

    results = health.run_checks()
    assert results["check1"].status == HealthStatus.HEALTHY
    assert results["check2"].status == HealthStatus.HEALTHY


def test_run_checks_failure():
    health = HealthCheck()
    health.register("check1", lambda: False)

    results = health.run_checks()
    assert results["check1"].status == HealthStatus.UNHEALTHY


def test_run_checks_exception():
    health = HealthCheck()

    def failing_check():
        raise RuntimeError("Boom")

    health.register("check1", failing_check)

    results = health.run_checks()
    assert results["check1"].status == HealthStatus.UNHEALTHY
    assert results["check1"].message == "Check error: RuntimeError"


def test_is_ready():
    health = HealthCheck()
    health.register("critical", lambda: True, required_for_readiness=True)
    health.register("optional", lambda: False, required_for_readiness=False)

    assert health.is_ready()

    health.register("critical2", lambda: False, required_for_readiness=True)
    assert not health.is_ready()


def test_is_alive():
    health = HealthCheck()
    health.register("check1", lambda: False)
    assert not health.is_alive()

    health.register("check2", lambda: True)
    assert health.is_alive()


def test_get_status_overall():
    health = HealthCheck()
    health.register("c1", lambda: True)

    status = health.get_status()
    assert status["status"] == HealthStatus.HEALTHY

    health.register("c2", lambda: False)
    status = health.get_status()
    assert status["status"] == HealthStatus.UNHEALTHY
    # Actually logic is: if ANY unhealthy -> Unhealthy.
    # Wait, code says:
    # if all_healthy: HEALTHY
    # elif any_unhealthy: UNHEALTHY
    # else: DEGRADED (e.g. all skipped? or mixed with no unhealthy but not all healthy?)

    # If one fails, it returns UNHEALTHY.


def test_optional_failure_is_degraded_but_critical_capability_remains_ready():
    health = HealthCheck()
    health.register("execution_authority", lambda: True, required_for_readiness=True)
    health.register("metrics_export", lambda: False, required_for_readiness=False)
    status = health.get_status()
    assert status["status"] == HealthStatus.DEGRADED.value
    assert status["ready"] is True
    assert status["failed_required"] == []
    assert status["degraded_optional"] == ["metrics_export"]


def test_critical_failure_blocks_readiness_without_hiding_optional_capability():
    health = HealthCheck()
    health.register("execution_authority", lambda: False, required_for_readiness=True)
    health.register("research", lambda: True, required_for_readiness=False)
    status = health.get_status()
    assert status["status"] == HealthStatus.UNHEALTHY.value
    assert status["ready"] is False
    assert status["failed_required"] == ["execution_authority"]
    assert status["checks"]["research"]["status"] == "healthy"


def test_empty_registry_never_reports_healthy_or_ready():
    health = HealthCheck()
    assert not health.is_ready()
    assert health.get_status()["status"] == HealthStatus.UNHEALTHY.value


def test_probe_error_does_not_expose_exception_message():
    health = HealthCheck()
    def raises_secret():
        raise RuntimeError("private-credential-123456")
    health.register("provider", raises_secret, required_for_readiness=False)
    result = health.get_status()
    assert "private-credential-123456" not in repr(result)
    assert result["status"] == "degraded"


def test_model_probe_requires_regular_nonsymlink_file(tmp_path):
    from trading_gym.monitoring.health import check_model_loaded
    model = tmp_path / "model.zip"
    model.write_bytes(b"model")
    assert check_model_loaded(str(model))
    assert not check_model_loaded(str(tmp_path))
    alias = tmp_path / "alias.zip"
    alias.symlink_to(model)
    assert not check_model_loaded(str(alias))
