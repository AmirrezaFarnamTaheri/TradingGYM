> Current follow-up: [four-view product and guarded funded execution](DEFERRED_COMPLETION.md) supersedes earlier three-view and unconnected-submission descriptions below. Funded execution remains disabled by default and no real orders were placed.

# First run

Follow the install and build commands in [README](../README.md), then open http://127.0.0.1:8000/.

1. Choose English or فارسی in the header. Persian uses right-to-left layout. The language preference persists in this browser.
2. Open Settings. Enter the Abantether key and save. Existing values are never returned; blank fields retain the saved value. Refresh Abantether and inspect its actual connection status.
3. Choose markets and risk limits. Observe is the default. Paper mode is a separately labeled virtual account. Funded spot execution requires live mode, the explicit enable switch, credentials and order/budget limits. Read [funded operation and qualification limits](DEFERRED_COMPLETION.md) before enabling it.
4. Wait for observed complete five-minute bars. A new installation has no invented history and needs 14 complete bars for initial signals. Connection failures and missing credentials remain visible.
5. Start the robot after the source is connected. Live monitor shows recorded head activity, tools and the decision route. Configured heads are distinguished from heads that actually contributed.
6. Experience lists broker-confirmed executions separately from paper executions. Open a recorded decision to see its market inputs, signals, weights, planner rationale, risk limits and outcome. An imported order may have no matching robot decision.
7. Optional model gateways are configured in Settings with endpoint, model, enable switch and a key roster. The LLM planner and Decision-1 each have their own roster. Saving a nonempty replacement roster replaces the old list; removing all saved rows clears that roster. No key belongs in a screenshot or issue report.
8. Strategy lab accepts your actual OHLCV CSV for causal replay. It never activates a policy or submits orders. Experience offers CSV exports of recorded executions.

## شروع فارسی

پس از نصب و ساخت، صفحهٔ محلی را باز کنید و زبان «فارسی» را انتخاب کنید. در تنظیمات، کلید آبان‌تتر را ذخیره و اتصال را تازه‌سازی کنید. حالت پیش‌فرض فقط مشاهده است؛ حالت کاغذی حساب مجازی جداگانه دارد. ارسال سفارش واقعی به انتخاب حالت زنده، فعال‌سازی صریح و تعیین سقف سفارش و بودجه نیاز دارد و پیش‌فرض خاموش است. در این بررسی هیچ معاملهٔ واقعی انجام نشده است. سوابق ساختگی نمایش داده نمی‌شود. برای مشاهدهٔ دلیل تصمیم، در بخش سوابق و تجربه، تصمیم ثبت‌شده را باز کنید.

## Storage and recovery

The default account directory is `~/.trading-gym/cockpit-v2`. `tgym serve --home PATH` uses `PATH/cockpit-v2`. Only one server or CLI agent may own this directory. Stop the server before making a consistent backup of the whole directory. SQLite decisions, execution records, observed bars, paper ledger and risk references belong together. Keep encrypted secrets and their protected master file together; Windows DPAPI ties decryption to its local user context. Cross-machine migration requires re-entering credentials. Do not hand-edit ledger or halt files.

Pause blocks entries; Stop ends processing. Emergency flatten affects the paper account, not your exchange holdings. A halted account must be stopped and flat before resetting its risk reference. Review the recorded reason before restarting.
