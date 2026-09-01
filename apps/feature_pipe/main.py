import argparse
import logging
import sys
from trading_gym.common.config import load_app_config
from trading_gym.pipelines.feature_pipeline import run_feature_pipeline

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate features from raw data.")
    parser.add_argument(
        "--config", default="config/app.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check configuration, do not generate features.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Setup logging
    logging.basicConfig(level=logging.INFO)

    try:
        cfg = load_app_config(args.config)

        if args.check_only:
            logger.info("✅ Configuration valid. Check-only mode enabled.")
            return

        run_feature_pipeline(cfg)

    except Exception as exc:
        logger.error("Feature pipeline failed error_type=%s", type(exc).__name__)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
