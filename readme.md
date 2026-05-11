# HVAC IoT Pipeline — Learning Notes
**Date:** May 5, 2026  
**Project:** `mahnoor-temp-hvac` | Region: `us-east-2`

---

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Serverless Framework](#serverless-framework)
3. [AWS IoT Core](#aws-iot-core)
4. [SQS (Simple Queue Service)](#sqs-simple-queue-service)
5. [AWS Lambda](#aws-lambda)
6. [DynamoDB](#dynamodb)
7. [API Gateway](#api-gateway)
8. [Full Data Flow](#full-data-flow)
9. [Deployment & Commands](#deployment--commands)
10. [Errors & Fixes](#errors--fixes)
11. [Real Device vs Simulated Device](#real-device-vs-simulated-device)
12. [MQTT.fx Monitoring](#mqttfx-monitoring)

---

## Architecture Overview

```
hvac_publisher.py  (Python script — simulates HVAC sensors)
        │
        │  HTTPS + IAM credentials (boto3)
        ▼
AWS IoT Core  ──  topic: mahnoor-temp/hvac/data
        │
        │  IoT Rule: mahnoor_temp_hvac_rule
        │  SQL: SELECT * FROM 'mahnoor-temp/hvac/data'
        ▼
SQS Queue  (mahnoor-temp-hvac-queue)
        │
        │  SQS trigger (batch size: 10)
        ▼
Lambda: mahnoor-temp-ingest-hvac
        │
        │  PutItem
        ▼
DynamoDB  (mahnoor-temp-hvac-table)
        │
        │  DynamoDB Streams (INSERT / MODIFY events)
        ▼
Lambda: mahnoor-temp-process-hvac  ← alert/anomaly detection

API Gateway  ──►  Lambda: mahnoor-temp-dashboard-api  ──►  DynamoDB
```

---

## Serverless Framework

### What is it?
Serverless Framework is an open-source tool that lets you define ALL your AWS resources (Lambda, SQS, DynamoDB, API Gateway, IoT, IAM) in a single `serverless.yml` file and deploy them with one command.

### Key sections of serverless.yml

```yaml
service: mahnoor-temp-hvac        # project name

provider:
  name: aws
  runtime: python3.11             # Lambda runtime
  region: us-east-2               # AWS region
  iam:                            # IAM permissions for all Lambdas
    role:
      statements: [...]

functions:                        # Lambda function definitions
  myFunction:
    handler: ingest_hvac.handler  # filename.function_name
    events:                       # what triggers this Lambda
      - sqs:
          arn: !GetAtt MyQueue.Arn

resources:                        # raw CloudFormation resources
  Resources:
    MyQueue:
      Type: AWS::SQS::Queue
```

### Important commands
```bash
serverless deploy           # deploy everything
serverless remove           # delete everything
serverless info             # show deployed endpoints/resources
serverless logs -f <name>   # view Lambda logs
```

### Plugin: serverless-python-requirements
Packages your Python dependencies for Lambda. Uses Docker to build in a Lambda-compatible Linux environment.

```yaml
custom:
  pythonRequirements:
    dockerizePip: true   # use Docker (recommended)
    slim: true           # strip unnecessary files
```

---

## AWS IoT Core

### What is IoT Core?
A managed AWS service that acts as a message broker for IoT devices. Devices publish messages to **topics**, and IoT Core routes them to other AWS services via **Rules**.

### Key Concepts

#### Topics
MQTT topics are message channels — like a pub/sub address string:
```
mahnoor-temp/hvac/data          # our topic
mahnoor-temp/hvac/alerts        # could use for alerts
mahnoor-temp/+/data             # + wildcard = any single level
mahnoor-temp/#                  # # wildcard = any level below
```

#### IoT Rules
Rules listen to a topic and forward messages to AWS services. Uses SQL syntax:
```sql
SELECT * FROM 'mahnoor-temp/hvac/data'
```
Actions supported: SQS, Lambda, DynamoDB, S3, SNS, Kinesis, and more.

#### In our serverless.yml:
```yaml
mahnoorTempIotRule:
  Type: AWS::IoT::TopicRule
  Properties:
    RuleName: mahnoor_temp_hvac_rule
    TopicRulePayload:
      Sql: "SELECT * FROM 'mahnoor-temp/hvac/data'"
      Actions:
        - Sqs:
            QueueUrl: !Ref mahnoorTempHvacQueue
            RoleArn: !GetAtt mahnoorTempIotRole.Arn
```

#### Why no Device/Certificate in our setup?
We used **boto3 with IAM credentials** instead of a real physical device. This is fine for simulation/testing.

| | Our Setup | Real Production |
|---|---|---|
| Auth method | IAM credentials | X.509 certificates |
| Protocol | HTTPS (boto3) | MQTT over TLS (port 8883) |
| Thing registered | No | Yes |
| Certificate needed | No | Yes |
| Best for | Testing/simulation | Physical hardware |

#### Real device setup would require:
```bash
# 1. Create a Thing
aws iot create-thing --thing-name mahnoor-temp-unit-01

# 2. Create certificate (download 3 files to device)
aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile "device-cert.pem" \
  --private-key-outfile "private-key.pem"

# 3. Attach IoT policy to certificate
# 4. Attach certificate to Thing
```
And the device would use `paho-mqtt` library with the certificates instead of boto3.

---

## SQS (Simple Queue Service)

### What is SQS?
A fully managed message queue. Acts as a buffer between IoT Core and Lambda — if Lambda is slow or fails, messages wait in the queue instead of being lost.

### Key concepts

**Visibility Timeout:** When Lambda picks up a message, it becomes "invisible" to other consumers for this duration. If Lambda fails, the message reappears after timeout.

**Dead Letter Queue (DLQ):** If a message fails processing 3 times (maxReceiveCount), it moves to the DLQ for inspection.

**ApproximateNumberOfMessagesNotVisible:** Messages currently being processed by Lambda. This is how we confirmed IoT → SQS was working even when DynamoDB was empty.

### Our SQS setup:
```yaml
mahnoorTempHvacQueue:
  Type: AWS::SQS::Queue
  Properties:
    QueueName: mahnoor-temp-hvac-queue
    VisibilityTimeout: 60
    RedrivePolicy:
      deadLetterTargetArn: !GetAtt mahnoorTempHvacDLQ.Arn
      maxReceiveCount: 3        # retry 3 times before DLQ
```

### Useful debug command:
```bash
aws sqs get-queue-attributes \
  --queue-url <url> \
  --attribute-names All \
  --region us-east-2
```

---

## AWS Lambda

### What is Lambda?
Serverless compute — runs your code only when triggered, no servers to manage. You pay per invocation.

### Our three Lambdas

| Lambda | Trigger | Job |
|---|---|---|
| `mahnoor-temp-ingest-hvac` | SQS (batch 10) | Parse message, save to DynamoDB |
| `mahnoor-temp-process-hvac` | DynamoDB Streams | Alert on anomalies |
| `mahnoor-temp-dashboard-api` | API Gateway (HTTP) | Serve dashboard data |

### Handler format
```
filename.function_name
ingest_hvac.handler     ← file: ingest_hvac.py, function: handler()
```

**Important:** The `.py` file must be at the ROOT of the deployment package, not inside a subfolder, unless you configure package paths carefully.

### Batch Item Failures (partial failure handling)
```python
def handler(event, context):
    batch_item_failures = []
    for record in event["Records"]:
        try:
            process(record)
        except Exception:
            batch_item_failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": batch_item_failures}
```
Only failed messages go back to the queue — successful ones are not reprocessed.

### Package patterns (exclude venv!)
```yaml
package:
  patterns:
    - '!**'               # exclude everything first
    - 'ingest_hvac.py'    # then include only what's needed
    - 'process_hvac.py'
    - 'dashboard_api.py'
    - '!venv/**'          # explicitly exclude venv
    - '!node_modules/**'
```

---

## DynamoDB

### What is DynamoDB?
AWS fully managed NoSQL database. Schema-less, scales automatically. We use it to store all HVAC readings.

### Our table design
```
Table: mahnoor-temp-hvac-table

Primary Key:
  - deviceId  (Partition Key / HASH)   e.g. "mahnoor-hvac-unit-01"
  - timestamp (Sort Key / RANGE)        e.g. "2026-05-05T12:07:55Z"

Global Secondary Index:
  - mahnoor-temp-timestamp-index (query by time across all devices)

TTL: 30 days (records auto-deleted after 30 days)
```

### DynamoDB Streams
Streams capture every change (INSERT, MODIFY, DELETE) to the table and trigger our second Lambda automatically. No polling needed.

```yaml
StreamSpecification:
  StreamViewType: NEW_AND_OLD_IMAGES   # sends both old and new item
```

### Useful debug command:
```bash
aws dynamodb scan \
  --table-name mahnoor-temp-hvac-table \
  --region us-east-2
```

---

## API Gateway

### What is API Gateway?
Managed service that creates REST APIs and routes HTTP requests to Lambda.

### Our endpoints
| Method | Path | Description |
|---|---|---|
| GET | /hvac | All readings (last 100) |
| GET | /hvac/{deviceId} | Readings for one device |
| GET | /hvac/stats | Aggregate min/max/avg |
| GET | /hvac/latest | Latest reading per device |

### Defined in serverless.yml as Lambda events:
```yaml
events:
  - http:
      path: /hvac
      method: get
      cors: true
  - http:
      path: /hvac/{deviceId}
      method: get
      cors: true
```

### Test with curl:
```bash
BASE=https://xxxx.execute-api.us-east-2.amazonaws.com/dev

curl $BASE/hvac | python3 -m json.tool
curl $BASE/hvac/stats | python3 -m json.tool
curl $BASE/hvac/latest | python3 -m json.tool
```

---

## Full Data Flow

Step by step what happens when `hvac_publisher.py` runs:

```
1. hvac_publisher.py generates a reading:
   { deviceId, timestamp, temperature, humidity, pressure, status }

2. boto3 publishes to IoT Core topic: mahnoor-temp/hvac/data

3. IoT Rule (mahnoor_temp_hvac_rule) matches the topic
   SQL: SELECT * FROM 'mahnoor-temp/hvac/data'

4. IoT Rule action forwards message to SQS queue

5. SQS holds the message (buffer)

6. Lambda (mahnoor-temp-ingest-hvac) is triggered by SQS
   - Parses the JSON body
   - Calls DynamoDB PutItem
   - Returns batch item failures for any failed messages

7. DynamoDB saves the item
   - DynamoDB Streams captures the INSERT event

8. Lambda (mahnoor-temp-process-hvac) is triggered by Streams
   - Checks temperature, humidity thresholds
   - Logs alerts if readings are abnormal

9. Dashboard API Lambda (mahnoor-temp-dashboard-api)
   - Triggered by HTTP request to API Gateway
   - Queries DynamoDB and returns JSON response
```

---

## Deployment & Commands

### First time setup
```bash
npm install -g serverless
npm install                    # install serverless-python-requirements
```

### Deploy
```bash
serverless deploy --region us-east-2
```

### Run the publisher
```bash
python3 hvac_publisher.py --count 10 --interval 3
```

### View logs
```bash
serverless logs -f mahnoorTempIngestHvac --region us-east-2 --tail
serverless logs -f mahnoorTempProcessHvac --region us-east-2 --tail
serverless logs -f mahnoorTempDashboardApi --region us-east-2 --tail
```

### Tear down everything
```bash
serverless remove --region us-east-2
```
All `mahnoor-temp-*` resources deleted in one shot.

---

## Errors & Fixes

### Error: `spawn python3.11 ENOENT`
**Cause:** Serverless plugin tried to use python3.11 locally but it wasn't installed.  
**Fix:** Set `dockerizePip: true` in serverless.yml to use Docker for packaging instead.

### Error: `Runtime.ImportModuleError: No module named 'functions.ingest_hvac'`
**Cause:** Lambda handler was pointing to `functions/ingest_hvac.handler` but Lambda couldn't resolve the module path.  
**Fix:** Move all `.py` files to the root directory and use `handler: ingest_hvac.handler`.

### Error: SQS messages not reaching DynamoDB
**Diagnosis steps:**
```bash
# Check SQS — are messages arriving?
aws sqs get-queue-attributes --queue-url <url> --attribute-names All

# ApproximateNumberOfMessagesNotVisible > 0 = Lambda is processing
# ApproximateNumberOfMessages in DLQ > 0 = Lambda is failing

# Check Lambda logs
serverless logs -f mahnoorTempIngestHvac --region us-east-2
```

### Error: venv files packaged into Lambda zip
**Cause:** No package patterns defined, so everything including `venv/` got zipped.  
**Fix:** Add explicit package patterns to serverless.yml:
```yaml
package:
  patterns:
    - '!**'
    - 'ingest_hvac.py'
    - 'process_hvac.py'
    - 'dashboard_api.py'
```

---

## Real Device vs Simulated Device

### What we built (simulation)
```python
# hvac_publisher.py uses boto3 + IAM credentials
import boto3
client = boto3.client("iot-data", region_name="us-east-2")
client.publish(topic="mahnoor-temp/hvac/data", payload=json.dumps(data))
```

### What a real physical device would use
```python
# paho-mqtt + X.509 certificates
import paho.mqtt.client as mqtt
client = mqtt.Client()
client.tls_set(
    ca_certs="Amazon-root-CA.pem",
    certfile="device-cert.pem",
    keyfile="private-key.pem"
)
client.connect("your-endpoint.iot.us-east-2.amazonaws.com", port=8883)
client.publish("mahnoor-temp/hvac/data", json.dumps(data))
```

### IoT Core authentication options
| Method | Used by | How |
|---|---|---|
| IAM credentials | AWS services, SDKs | `~/.aws/credentials` |
| X.509 certificates | Physical IoT devices | cert + key files on device |
| Custom authorizer | Web/mobile apps | Lambda-based auth |
| Cognito | Mobile apps | User pool tokens |

---

## MQTT.fx Monitoring

**Date added:** May 11, 2026

MQTT.fx (or MQTTX) is a desktop GUI client that subscribes to your IoT topic and shows live sensor messages as they arrive — without touching the pipeline.

### Why a separate Thing + Certificate?

Your `hvac_publisher.py` uses **boto3 + IAM credentials** (HTTPS). MQTT.fx speaks the **MQTT protocol over port 8883** and requires X.509 certificate authentication. AWS IoT Core demands three linked resources for any MQTT client:

```
IoT Thing  ←──  X.509 Certificate  ──→  IoT Policy
(identity)       (proves identity)        (permissions)
```

### What was added to `serverless.yml`

| Resource | CloudFormation Type | Purpose |
|---|---|---|
| `mahnoorMqttFxThing` | `AWS::IoT::Thing` | Registers MQTT.fx as a named device |
| `mahnoorMqttFxPolicy` | `AWS::IoT::Policy` | Allows connect/subscribe/receive/publish on `mahnoor-temp/*` |
| `mahnoorMqttFxCertificate` | `Custom::IoTCertificate` | Runs provisioner Lambda to create cert + attach everything |
| `mahnoorMqttFxProvisioner` | Lambda function | Custom Resource handler — creates cert, stores keys in SSM |

New IAM permissions were also added to the Lambda role:
- `iot:CreateKeysAndCertificate`, `iot:AttachPolicy`, `iot:AttachThingPrincipal`, etc.
- `ssm:PutParameter / GetParameter / DeleteParameter` scoped to `/mahnoor/mqttfx/*`

### How the Custom Resource works

```
serverless deploy
      │
      ▼ CloudFormation sees Custom::IoTCertificate
      │
      ▼ Invokes mqttfx_provisioner.handler (RequestType=Create)
      │
      ├── iot.create_keys_and_certificate()   → cert PEM + private key
      ├── iot.attach_policy()                 → links cert → policy
      ├── iot.attach_thing_principal()        → links cert → thing
      └── ssm.put_parameter() x4             → stores keys in SSM

serverless remove
      │
      ▼ Invokes mqttfx_provisioner.handler (RequestType=Delete)
      │
      ├── detach policy + thing from cert
      ├── deactivate + delete certificate
      └── delete SSM parameters
```

### Deploy

```bash
serverless deploy --region us-east-2
```

After deploy you'll see two new outputs:
```
MqttFxThingName      → mahnoor-mqttfx-monitor
MqttFxCertificateArn → arn:aws:iot:us-east-2:...:cert/abc123...
```

### Fetch certificates from SSM

```bash
# Certificate PEM
aws ssm get-parameter \
  --name /mahnoor/mqttfx/certificate-pem \
  --region us-east-2 \
  --query Parameter.Value \
  --output text > mqttfx-cert.pem

# Private key (encrypted SecureString — needs --with-decryption)
aws ssm get-parameter \
  --name /mahnoor/mqttfx/private-key \
  --region us-east-2 \
  --with-decryption \
  --query Parameter.Value \
  --output text > mqttfx-private.key

# Amazon Root CA (one-time download)
curl -o amazon-root-ca.pem https://www.amazontrust.com/repository/AmazonRootCA1.pem

# Get your IoT endpoint
aws iot describe-endpoint --endpoint-type iot:Data-ATS --region us-east-2
```

### Configure MQTT.fx / MQTTX

| Field | Value |
|---|---|
| **Broker Address** | `xxxxxx-ats.iot.us-east-2.amazonaws.com` (your endpoint) |
| **Port** | `8883` |
| **Client ID** | `mahnoor-mqttfx-monitor` |
| **CA File** | `amazon-root-ca.pem` |
| **Client Certificate** | `mqttfx-cert.pem` |
| **Client Key** | `mqttfx-private.key` |
| **TLS Version** | `TLSv1.2` |

After connecting, subscribe to:
```
mahnoor-temp/hvac/data       # live sensor readings
mahnoor-temp/#               # all topics under mahnoor-temp
```

Then run the publisher and watch messages appear in real time:
```bash
python3 hvac_publisher.py --interval 5
```

### Files added/changed

| File | Change |
|---|---|
| `serverless.yml` | Added Thing, Policy, Custom Resource, provisioner function, IAM permissions, outputs, package pattern |
| `mqttfx_provisioner.py` | New — Custom Resource Lambda for cert lifecycle |
| `.gitignore` | Added `*.pem`, `*.key`, `*.csr` to prevent committing secrets |

---

## Resources

- [Serverless Framework Docs](https://www.serverless.com/framework/docs)
- [AWS IoT Core Docs](https://docs.aws.amazon.com/iot/latest/developerguide/)
- [AWS Lambda Docs](https://docs.aws.amazon.com/lambda/)
- [DynamoDB Developer Guide](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/)
- [SQS Developer Guide](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/)