"""
mahnoor-temp-process-hvac
Lambda triggered by DynamoDB Streams on the HVAC table.
Runs alerting logic whenever a new record is inserted or updated.
"""

import json
import os
import boto3
import logging
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Alert thresholds ──────────────────────────────────────────────
TEMP_HIGH  = Decimal("30")   # °C — too hot
TEMP_LOW   = Decimal("15")   # °C — too cold
HUMIDITY_HIGH = Decimal("80")  # % — too humid


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def handler(event, context):
    """DynamoDB Streams trigger handler."""
    for record in event.get("Records", []):
        event_name = record.get("eventName")  # INSERT | MODIFY | REMOVE

        if event_name not in ("INSERT", "MODIFY"):
            continue

        new_image = record["dynamodb"].get("NewImage", {})
        process_record(new_image, event_name)

    return {"status": "ok"}


def process_record(image: dict, event_name: str):
    """Analyse a single HVAC reading and raise alerts if needed."""
    device_id   = image.get("deviceId",    {}).get("S", "unknown")
    timestamp   = image.get("timestamp",   {}).get("S", "unknown")
    temperature = Decimal(image.get("temperature", {}).get("N", "0"))
    humidity    = Decimal(image.get("humidity",    {}).get("N", "0"))
    status      = image.get("status",      {}).get("S", "unknown")

    logger.info(
        f"[{event_name}] device={device_id} ts={timestamp} "
        f"temp={temperature}°C humidity={humidity}%"
    )

    alerts = []

    if temperature > TEMP_HIGH:
        alerts.append(f"HIGH TEMPERATURE: {temperature}°C on device {device_id}")
    if temperature < TEMP_LOW:
        alerts.append(f"LOW TEMPERATURE: {temperature}°C on device {device_id}")
    if humidity > HUMIDITY_HIGH:
        alerts.append(f"HIGH HUMIDITY: {humidity}% on device {device_id}")
    if status == "fault":
        alerts.append(f"DEVICE FAULT reported by {device_id}")

    for alert in alerts:
        logger.warning(f"ALERT — {alert}")
        # TODO: wire up SNS/SES notifications here
        # sns.publish(TopicArn=..., Message=alert)

    if not alerts:
        logger.info(f"device={device_id} — all readings normal")