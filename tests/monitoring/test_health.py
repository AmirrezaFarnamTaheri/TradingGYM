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
    assert "Boom" in results["check1"].message


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
