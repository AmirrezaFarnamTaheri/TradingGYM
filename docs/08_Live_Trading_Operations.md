> Current follow-up: [four-view product and guarded funded execution](DEFERRED_COMPLETION.md) supersedes earlier three-view and unconnected-submission descriptions below. Funded execution remains disabled by default and no real orders were placed.

# Live observation and execution boundary

The cockpit reads Abantether quotes, balances and order history. Funded spot order submission is implemented but disabled by default. Live start requires matching saved live mode, an explicit enable switch, credentials and bounded order/buy budgets. Durable intents quarantine ambiguous submissions and are resolved through broker reads, without automatic POST retries. See [the complete operating and qualification boundary](DEFERRED_COMPLETION.md). No credential or successful quote read is itself funded-trading qualification. Paper flatten never closes an exchange position; stopping the feed does not cancel accepted orders or provide an exchange-side stop.

See [first run](FIRST_RUN.md), [settings and decision evidence](SETTINGS_AND_DECISIONS.md), and [current verification](REBUILD.md).
