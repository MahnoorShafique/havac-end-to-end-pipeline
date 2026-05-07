"""
hvac_publisher.py
Simulates HVAC sensors and publishes readings to AWS IoT Core.
IoT Core rule → SQS → Lambda (ingest) → DynamoDB → Lambda (process)

Usage:
  python hvac_publisher.py                   # run forever
  python hvac_publisher.py --count 10        # send 10 readings then stop
  python hvac_publisher.py --interval 5      # publish every 5 seconds
"""

import argparse
import json
import random
import time
from datetime import datetime, timezone

import boto3

# ── Config ────────────────────────────────────────────────────────
REGION     = "us-east-2"
IOT_TOPIC  = "mahnoor-temp/hvac/data"
DEVICES    = ["mahnoor-hvac-unit-01", "mahnoor-hvac-unit-02", "mahnoor-hvac-unit-03"]


def generate_reading(device_id: str) -> dict:
    """Produce a realistic HVAC sensor payload."""
    # Occasionally inject a fault for testing the alert Lambda
    status = "fault" if random.random() < 0.05 else "ok"

    return {
        "deviceId":    device_id,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "temperature": round(random.uniform(18.0, 35.0), 2),   # °C
        "humidity":    round(random.uniform(30.0, 90.0), 2),   # %
        "pressure":    round(random.uniform(990.0, 1020.0), 2), # hPa
        "status":      status,
        "metadata": {
            "firmwareVersion": "1.2.3",
            "location":        "Building-A",
        },
    }


def publish(client, topic: str, payload: dict):
    client.publish(
        topic   = topic,
        qos     = 1,
        payload = json.dumps(payload),
    )
    print(f"[{payload['timestamp']}] Published → {topic}")
    print(f"  device={payload['deviceId']}  "
          f"temp={payload['temperature']}°C  "
          f"humidity={payload['humidity']}%  "
          f"status={payload['status']}")


def main():
    parser = argparse.ArgumentParser(description="HVAC IoT publisher")
    parser.add_argument("--count",    type=int,   default=0,   help="0 = run forever")
    parser.add_argument("--interval", type=float, default=10,  help="seconds between publishes")
    args = parser.parse_args()

    # boto3 IoT data client — endpoint auto-resolved from your account
    iot_client = boto3.client("iot-data", region_name=REGION)
    # Fetch the custom endpoint
    iot_mgmt   = boto3.client("iot", region_name=REGION)
    endpoint   = iot_mgmt.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]

    iot_client = boto3.client(
        "iot-data",
        region_name=REGION,
        endpoint_url=f"https://{endpoint}",
    )

    print(f"IoT endpoint : {endpoint}")
    print(f"Topic        : {IOT_TOPIC}")
    print(f"Devices      : {DEVICES}")
    print(f"Interval     : {args.interval}s")
    print(f"Count        : {'∞' if args.count == 0 else args.count}")
    print("─" * 60)

    sent = 0
    try:
        while True:
            device  = random.choice(DEVICES)
            reading = generate_reading(device)
            publish(iot_client, IOT_TOPIC, reading)
            sent += 1

            if args.count and sent >= args.count:
                print(f"\nDone — sent {sent} readings.")
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\nStopped after {sent} readings.")


if __name__ == "__main__":
    main()