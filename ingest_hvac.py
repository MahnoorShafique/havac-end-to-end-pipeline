import json
import os
import boto3
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])


def handler(event, context):
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            logger.info(f"Processing message {message_id}: {body}")
            save_to_dynamodb(body)
            logger.info(f"Successfully saved message {message_id} to DynamoDB")
        except Exception as e:
            logger.error(f"Failed to process message {message_id}: {e}")
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def save_to_dynamodb(payload: dict):
    device_id   = payload.get("deviceId", "unknown-device")
    timestamp   = payload.get("timestamp", datetime.now(timezone.utc).isoformat())
    temperature = Decimal(str(payload.get("temperature", 0)))
    humidity    = Decimal(str(payload.get("humidity", 0)))
    pressure    = Decimal(str(payload.get("pressure", 0)))
    status      = payload.get("status", "unknown")

    ttl = int(datetime.now(timezone.utc).timestamp()) + (30 * 24 * 60 * 60)

    item = {
        "deviceId":    device_id,
        "timestamp":   timestamp,
        "temperature": temperature,
        "humidity":    humidity,
        "pressure":    pressure,
        "status":      status,
        "ttl":         ttl,
        "createdAt":   datetime.now(timezone.utc).isoformat(),
        "rawPayload":  json.dumps(payload),
    }

    table.put_item(Item=item)
    logger.info(f"Saved item for device={device_id} at {timestamp}")
