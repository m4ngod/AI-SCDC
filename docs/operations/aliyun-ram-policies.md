# Aliyun RAM Policy Examples

## Scope

These examples use concrete development values:

- Account id: `1234567890123456`
- Region: `cn-hangzhou`
- MNS queue: `ai-scdc-cloud-runs-dev`
- OSS bucket: `ai-scdc-dev-artifacts`
- OSS prefix: `ai-scdc/dev/`
- ECI container group prefix: `ai-scdc-run-`

Adjust values in the Aliyun RAM console for each deployment. Use the Aliyun RAM
policy simulator before attaching a policy to a production role.

## API Control Plane Role

The API process can enqueue queue-only MNS work, acknowledge MNS receipts,
write and read OSS run artifacts, create ECI containers, sync ECI logs, and
delete known ECI containers by persisted id.

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "mns:SendMessage",
        "mns:ReceiveMessage",
        "mns:DeleteMessage"
      ],
      "Resource": [
        "acs:mns:cn-hangzhou:1234567890123456:/queues/ai-scdc-cloud-runs-dev/messages"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:PutObject",
        "oss:GetObject"
      ],
      "Resource": [
        "acs:oss:*:1234567890123456:ai-scdc-dev-artifacts/ai-scdc/dev/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "eci:CreateContainerGroup",
        "eci:DescribeContainerLog",
        "eci:DeleteContainerGroup"
      ],
      "Resource": [
        "acs:eci:cn-hangzhou:1234567890123456:containergroup/ai-scdc-run-*"
      ]
    }
  ]
}
```

If an ECI action rejects resource-level scoping in the policy simulator, scope
that action to the smallest Aliyun-supported resource form and enforce the
`ai-scdc-run-` prefix through API-side naming, console review, and deployment
runbooks.

The API role must not be attached to a worker container.

## Pull Worker Role

The pull worker receives MNS messages and calls the AI-SCDC API over HTTPS with
the callback token embedded in the message. The Phase 13A default keeps receipt
acknowledgement API-owned, so worker-side `mns:DeleteMessage` is not required.

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "mns:ReceiveMessage"
      ],
      "Resource": [
        "acs:mns:cn-hangzhou:1234567890123456:/queues/ai-scdc-cloud-runs-dev/messages"
      ]
    }
  ]
}
```

The pull worker role must not include ECI create/delete, OSS read/write,
GitHub credentials, model credentials, or the API process's Aliyun access key
secret.

If a deployment chooses worker-side receipt deletion in a future authenticated
worker design, add only `mns:DeleteMessage` for the same queue resource and keep
the API callback-token completion boundary.

## Assigned ECI Worker

The current assigned-run ECI worker receives:

- `AI_SCDC_API_BASE_URL`
- `AI_SCDC_CLOUD_RUN_ID`
- `AI_SCDC_WORKER_ID`
- `AI_SCDC_CALLBACK_TOKEN`
- `AI_SCDC_QUEUE_PROVIDER`
- `AI_SCDC_STORAGE_PROVIDER`

It does not need Aliyun MNS credentials because it does not poll MNS. It does
not need OSS credentials because artifact upload goes through the callback-token
protected API endpoint. It must not receive `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET`.

## OSS Retention

Use OSS bucket lifecycle rules for development cleanup under
`ai-scdc/dev/cloud-runs/`. Keep artifacts and logs long enough for review,
provider log sync, and audit handoff before ECI cleanup.

Do not add API-side OSS delete-prefix behavior until authenticated
organization-scoped operator controls exist.

## Production KMS Boundary

`DevSecretVault` is development-only. Production must provide a KMS-backed
implementation of the existing `SecretVault` protocol before commercial beta.
The RAM policies here do not grant KMS permissions because Phase 13A does not
integrate a real KMS SDK.
