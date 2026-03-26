from loguru import logger
import sys
import os
import warnings

def setup_logger(settings):
    # Create a logs directory if it doesn't exist
    LOG_DIR = "logs"
    os.makedirs(LOG_DIR, exist_ok=True)

    warnings.filterwarnings(
        "ignore",
        message=r"datetime\.datetime\.utcnow\(\) is deprecated.*",
        category=DeprecationWarning,
        module=r"jose\.jwt",
    )

    # Configure loguru logging
    logger.remove()  # Remove default handler

    logger.add(
        sys.stdout,  # Log to console
        format="{level} | {file} | {line} | {message}",
        level=settings.LOG_LEVEL
    )

    logger.add(
        f"{LOG_DIR}/app.log",
        format="{level} | {file} | {line} | {message}",
        rotation="00:00",  # Rotates every midnight
        retention="30 days",  # Keep logs for 30 days
        compression=None,  # Do not compress
        level=settings.LOG_LEVEL,
        enqueue=True,  # Async logging
        backtrace=True,
        diagnose=True,
    )
    return logger
