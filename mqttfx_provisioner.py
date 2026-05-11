"""
mqttfx_provisioner.py
CloudFormation Custom Resource — provisions an IoT X.509 certificate for MQTT.fx.

Lifecycle:
  CREATE  → creates cert + key pair, attaches Policy & Thing, stores in SSM
  UPDATE  → no-op (returns existing cert)
  DELETE  → detaches, deactivates, deletes cert + cleans up SSM params

SSM paths (us-east-2):
  /mahnoor/mqttfx/certificate-pem   (String)
  /mahnoor/mqttfx/private-key       (SecureString — encrypted)
  /mahnoor/mqttfx/certificate-id    (String)
  /mahnoor/mqttfx/certificate-arn   (String)
"""

import json
import urllib.request

import boto3

iot = boto3.client("iot")
ssm = boto3.client("ssm")

SSM_CERT_PEM = "/mahnoor/mqttfx/certificate-pem"
SSM_PRIV_KEY = "/mahnoor/mqttfx/private-key"
SSM_CERT_ID  = "/mahnoor/mqttfx/certificate-id"
SSM_CERT_ARN = "/mahnoor/mqttfx/certificate-arn"


def cfn_respond(event, context, status, data=None, reason=""):
    """Send a response back to CloudFormation via the pre-signed S3 URL."""
    data = data or {}
    body = json.dumps({
        "Status":             status,
        "Reason":             reason or f"CloudWatch log stream: {context.log_stream_name}",
        "PhysicalResourceId": data.get("CertificateId", event.get("PhysicalResourceId", "none")),
        "StackId":            event["StackId"],
        "RequestId":          event["RequestId"],
        "LogicalResourceId":  event["LogicalResourceId"],
        "Data":               data,
    }).encode()

    req = urllib.request.Request(
        url=event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"Content-Type": "", "Content-Length": len(body)},
    )
    urllib.request.urlopen(req)


def handler(event, context):
    props       = event["ResourceProperties"]
    thing_name  = props["ThingName"]
    policy_name = props["PolicyName"]
    request     = event["RequestType"]

    try:
        # ── CREATE ────────────────────────────────────────────────────────────
        if request == "Create":
            # 1. Generate X.509 certificate + RSA key pair
            resp     = iot.create_keys_and_certificate(setAsActive=True)
            cert_id  = resp["certificateId"]
            cert_arn = resp["certificateArn"]
            cert_pem = resp["certificatePem"]
            priv_key = resp["keyPair"]["PrivateKey"]

            # 2. Attach IoT Policy → Certificate
            iot.attach_policy(policyName=policy_name, target=cert_arn)

            # 3. Attach Certificate → Thing
            iot.attach_thing_principal(thingName=thing_name, principal=cert_arn)

            # 4. Store in SSM Parameter Store (private key encrypted as SecureString)
            ssm.put_parameter(Name=SSM_CERT_PEM, Value=cert_pem, Type="String",       Overwrite=True)
            ssm.put_parameter(Name=SSM_PRIV_KEY, Value=priv_key, Type="SecureString",  Overwrite=True)
            ssm.put_parameter(Name=SSM_CERT_ID,  Value=cert_id,  Type="String",       Overwrite=True)
            ssm.put_parameter(Name=SSM_CERT_ARN, Value=cert_arn, Type="String",       Overwrite=True)

            print(f"[CREATE] Certificate created: {cert_id}")
            cfn_respond(event, context, "SUCCESS", {
                "CertificateArn": cert_arn,
                "CertificateId":  cert_id,
            })

        # ── DELETE ────────────────────────────────────────────────────────────
        elif request == "Delete":
            try:
                cert_id  = ssm.get_parameter(Name=SSM_CERT_ID)["Parameter"]["Value"]
                cert_arn = ssm.get_parameter(Name=SSM_CERT_ARN)["Parameter"]["Value"]

                # Detach → deactivate → delete (order matters)
                try: iot.detach_policy(policyName=policy_name, target=cert_arn)
                except Exception as e: print(f"detach_policy: {e}")

                try: iot.detach_thing_principal(thingName=thing_name, principal=cert_arn)
                except Exception as e: print(f"detach_thing_principal: {e}")

                try: iot.update_certificate(certificateId=cert_id, newStatus="INACTIVE")
                except Exception as e: print(f"update_certificate: {e}")

                try: iot.delete_certificate(certificateId=cert_id, forceDelete=True)
                except Exception as e: print(f"delete_certificate: {e}")

            except Exception as e:
                print(f"[DELETE] SSM lookup failed (cert may not exist): {e}")

            # Clean up SSM params (best-effort)
            for param in [SSM_CERT_PEM, SSM_PRIV_KEY, SSM_CERT_ID, SSM_CERT_ARN]:
                try: ssm.delete_parameter(Name=param)
                except Exception: pass

            print("[DELETE] Cleanup complete")
            cfn_respond(event, context, "SUCCESS", {})

        # ── UPDATE ────────────────────────────────────────────────────────────
        else:
            print("[UPDATE] No-op — certificate unchanged")
            cfn_respond(event, context, "SUCCESS", {
                "CertificateArn": event.get("PhysicalResourceId", ""),
            })

    except Exception as exc:
        print(f"[ERROR] {exc}")
        cfn_respond(event, context, "FAILED", reason=str(exc))
