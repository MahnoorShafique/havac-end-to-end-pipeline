"""
mahnoor-temp-dashboard-api
REST API Lambda for the HVAC dashboard.

Routes (all served via API Gateway):
  GET /hvac              — list all readings (last 100)
  GET /hvac/{deviceId}   — readings for a specific device
  GET /hvac/stats        — aggregate stats across all devices
  GET /hvac/latest       — most recent reading per device
"""

import json
import os
import boto3
import logging
from datetime import datetime, timezone
from decimal import Decimal
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])


# ── helpers ───────────────────────────────────────────────────────

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
        "body": json.dumps(body, cls=DecimalEncoder),
    }


# ── router ────────────────────────────────────────────────────────

def handler(event, context):
    logger.info(f"Event: {json.dumps(event)}")

    http_method = event.get("httpMethod", "GET")
    path        = event.get("path", "/")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    if http_method == "OPTIONS":
        return response(200, {})

    try:
        # GET /hvac/stats
        if path.endswith("/stats"):
            return get_stats()

        # GET /hvac/latest
        if path.endswith("/latest"):
            return get_latest()

        # GET /hvac/{deviceId}
        device_id = path_params.get("deviceId")
        if device_id:
            limit = int(query_params.get("limit", 50))
            return get_device_readings(device_id, limit)

        # GET /hvac
        limit = int(query_params.get("limit", 100))
        return get_all_readings(limit)

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return response(500, {"error": str(e)})


# ── handlers ──────────────────────────────────────────────────────

def get_all_readings(limit: int = 100):
    result = table.scan(Limit=limit)
    items  = result.get("Items", [])
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return response(200, {"count": len(items), "items": items})


def get_device_readings(device_id: str, limit: int = 50):
    result = table.query(
        KeyConditionExpression=Key("deviceId").eq(device_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = result.get("Items", [])
    return response(200, {"deviceId": device_id, "count": len(items), "items": items})


def get_stats():
    result = table.scan()
    items  = result.get("Items", [])

    if not items:
        return response(200, {"message": "No data yet"})

    temps      = [float(i["temperature"]) for i in items if "temperature" in i]
    humidities = [float(i["humidity"])    for i in items if "humidity"    in i]
    devices    = list({i["deviceId"] for i in items})

    stats = {
        "totalReadings": len(items),
        "deviceCount":   len(devices),
        "devices":       devices,
        "temperature": {
            "min": min(temps),
            "max": max(temps),
            "avg": round(sum(temps) / len(temps), 2),
        } if temps else {},
        "humidity": {
            "min": min(humidities),
            "max": max(humidities),
            "avg": round(sum(humidities) / len(humidities), 2),
        } if humidities else {},
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
    return response(200, stats)


def get_latest():
    result  = table.scan()
    items   = result.get("Items", [])

    latest_per_device: dict[str, dict] = {}
    for item in items:
        device_id = item["deviceId"]
        if (
            device_id not in latest_per_device
            or item.get("timestamp", "") > latest_per_device[device_id].get("timestamp", "")
        ):
            latest_per_device[device_id] = item

    return response(200, {
        "deviceCount": len(latest_per_device),
        "latest":      list(latest_per_device.values()),
    })