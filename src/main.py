import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from src.metrics.metrics_calculator import MetricsCalculator
print("MetricsCalculator imported successfully")

# --- Environment validation ---
def validate_environment():
    """Validate environment variables and handle invalid values."""
    # Validate GitHub token if provided
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token and not github_token.strip():
        print("Error: Invalid GitHub token", file=sys.stderr)
        sys.exit(1)

    # Validate log file path if provided
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        try:
            # Test if we can write to the log file path
            with open(log_file, 'a'):
                pass
        except (OSError, IOError) as e:
            print(f"Error: Invalid log file path: {e}", file=sys.stderr)
            sys.exit(1)

    # Handle log level 0 - create blank log file
    log_level = os.environ.get("LOG_LEVEL", "0")
    if log_level == "0" and log_file:
        # Create a blank log file for level 0
        try:
            with open(log_file, 'w'):
                pass
        except (OSError, IOError) as e:
            print(
                f"Error: Failed to create blank log file: {e}",
                file=sys.stderr)
            sys.exit(1)


# Validate environment before setting up logging
validate_environment()


# --- Logging setup ---
# Adheres to the LOG_FILE and LOG_LEVEL environment variable
# requirements
LOG_LEVEL_STR = os.environ.get("LOG_LEVEL", "0")
LOG_FILE = os.environ.get("LOG_FILE")

log_level_map = {
    "1": logging.INFO, "2": logging.DEBUG
}
log_level = log_level_map.get(LOG_LEVEL_STR)

# Configure logging to file or stdout based on environment
if LOG_LEVEL_STR != "0" and log_level:
    if LOG_FILE:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            filename=LOG_FILE,
        )
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            stream=sys.stdout,
        )
else:
    logging.disable(logging.CRITICAL)
# --- End of logging setup ---


def parse_url_file(file_path: str) -> List[
    Tuple[Optional[str], Optional[str], str]
]:
    """
    Reads a file and returns a list of tuples containing
    (code_link, dataset_link, model_link).
    Format: code_link, dataset_link, model_link per line.
    Code and dataset links can be empty.
    """
    logging.info(f"Reading URLs from: {file_path}")
    try:
        with open(file_path, "r", encoding='utf-8') as f:
            entries = []
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                parts = [part.strip() for part in line.split(',')]
                if len(parts) != 3:
                    logging.warning(
                        "Line %d has %d parts, expected 3. Skipping.",
                        line_num, len(parts)
                    )
                    continue

                code_link = parts[0] if parts[0] else None
                dataset_link = parts[1] if parts[1] else None
                model_link = parts[2] if parts[2] else None

                if not model_link:
                    logging.warning(
                        "Line %d has no model link. Skipping.", line_num
                    )
                    continue

                entries.append((code_link, dataset_link, model_link))

        logging.info(f"Found {len(entries)} valid entries.")
        return entries
    except FileNotFoundError:
        # Prints a user-friendly error message and exits as
        # required
        error_msg = f"Error: URL file not found at '{file_path}'."
        logging.error(error_msg)
        print(error_msg + " Please check the path.", file=sys.stderr)
        sys.exit(1)


def calculate_net_score(metrics: Dict[str, Any]) -> float:
    """
    Calculate the net score using the weighted formula from the project plan.
    """
    # Weights are taken directly from the project plan
    # to match Sarah's priorities
    weights = {
        'license': 0.30,
        'ramp_up_time': 0.20,
        'dataset_and_code_score': 0.15,
        'performance_claims': 0.10,
        'bus_factor': 0.15,  # Adjusted based on re-reading priorities
        'code_quality': 0.05,
        'dataset_quality': 0.05,
    }

    net_score = sum(
        metrics.get(metric, 0.0) * weight
        for metric, weight in weights.items()
    )
    # The score must be in the range [0, 1]
    return min(1.0, max(0.0, net_score))


async def analyze_entry(
    entry: Tuple[Optional[str], Optional[str], str],
    process_pool: ProcessPoolExecutor,
    encountered_datasets: set
) -> Dict[str, Any]:
    """
    Analyzes a single entry containing code, dataset, and model links,
    orchestrates metric calculation, and returns the final scorecard.
    """
    code_link, dataset_link, model_link = entry
    start_time = time.time()

    github_token = os.environ.get("GITHUB_TOKEN")
    calculator = MetricsCalculator(process_pool, github_token)
    local_metrics = await calculator.analyze_entry(
        code_link, dataset_link, model_link, encountered_datasets
    )

    net_score = calculate_net_score(local_metrics)
    total_latency_ms = int((time.time() - start_time) * 1000)

    # The output format strictly follows Table 1 in
    # the project specification
    scorecard: Dict[str, Any] = {
        "name": model_link.split("/")[-1],
        "category": "MODEL",
        "net_score": round(net_score, 2),
        "net_score_latency": total_latency_ms,
        "ramp_up_time": local_metrics.get('ramp_up_time', 0.0),
        "ramp_up_time_latency": local_metrics.get(
            'ramp_up_time_latency', 0
        ),
        "bus_factor": local_metrics.get('bus_factor', 0.0),
        "bus_factor_latency": local_metrics.get('bus_factor_latency', 0),
        "performance_claims": local_metrics.get('performance_claims', 0.0),
        "performance_claims_latency": local_metrics.get(
            'performance_claims_latency', 0
        ),
        "license": local_metrics.get('license', 0.0),
        "license_latency": local_metrics.get('license_latency', 0),
        "size_score": local_metrics.get('size_score', {}),
        "size_score_latency": local_metrics.get('size_score_latency', 0),
        "dataset_and_code_score": local_metrics.get(
            'dataset_and_code_score', 0.0
        ),
        "dataset_and_code_score_latency": local_metrics.get(
            'dataset_and_code_score_latency', 0
        ),
        "dataset_quality": local_metrics.get('dataset_quality', 0.0),
        "dataset_quality_latency": local_metrics.get(
            'dataset_quality_latency', 0
        ),
        "code_quality": local_metrics.get('code_quality', 0.0),
        "code_quality_latency": local_metrics.get('code_quality_latency', 0),
    }
    return scorecard


async def process_entries(
    entries: List[Tuple[Optional[str], Optional[str], str]]
) -> None:
    """
    Processes each entry concurrently using an advanced hybrid model.
    """
    logging.info(
        "Processing %d entries with advanced concurrency.", len(entries)
    )
    # Manages workers based on available CPU cores,
    # as requested by Sarah
    max_workers = os.cpu_count() or 4
    logging.info("Using %d worker processes.", max_workers)

    # Track encountered datasets to handle shared datasets
    encountered_datasets: set[str] = set()

    with ProcessPoolExecutor(max_workers=max_workers) as process_pool:
        tasks = [analyze_entry(entry, process_pool, encountered_datasets)
                 for entry in entries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logging.error("An analysis task failed: %s", result)
                # failed entries
                entry = entries[i]
                code_link, dataset_link, model_link = entry
                default_scorecard = {
                    "name": model_link.split("/")[-1]
                    if model_link else "unknown",
                    "category": "MODEL",
                    "net_score": 0.0,
                    "net_score_latency": 0,
                    "ramp_up_time": 0.0,
                    "ramp_up_time_latency": 0,
                    "bus_factor": 0.0,
                    "bus_factor_latency": 0,
                    "performance_claims": 0.0,
                    "performance_claims_latency": 0,
                    "license": 0.0,
                    "license_latency": 0,
                    "size_score": {},
                    "size_score_latency": 0,
                    "dataset_and_code_score": 0.0,
                    "dataset_and_code_score_latency": 0,
                    "dataset_quality": 0.0,
                    "dataset_quality_latency": 0,
                    "code_quality": 0.0,
                    "code_quality_latency": 0,
                }
                print(json.dumps(default_scorecard, separators=(',', ':')))
            else:
                # Prints output to stdout in NDJSON format
                print(json.dumps(result, separators=(',', ':')))


def main():
    """Main entry point of the application."""
    # Handles the `./run URL_FILE` invocation
    if len(sys.argv) != 2:
        print("Usage: python -m src.main <URL_FILE>", file=sys.stderr)
        sys.exit(1)

    url_file = sys.argv[1]
    entries = parse_url_file(url_file)
    if entries:
        asyncio.run(process_entries(entries))


if __name__ == "__main__":
    main()
