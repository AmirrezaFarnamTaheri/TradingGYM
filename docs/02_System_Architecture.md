# System architecture

One trading_gym package contains core, data, market, execution, rl, agent and api. The React cockpit talks to the loopback API. The connector stores observed bars and imports broker fills; the agent journals immutable decisions in SQLite. Legacy research APIs remain internal and have a separate schema. The optional native crate lives in kernel/.

See [first run](FIRST_RUN.md), [settings and decision evidence](SETTINGS_AND_DECISIONS.md), and [current verification](REBUILD.md).
