# APISIX gateway config

`generate_apisix_config_from_spec.py` writes two outputs from `openapi/openapi_v1.public.yaml`.
Do not edit either by hand.

- `adc.yaml`, for [ADC](https://github.com/api7/adc) (`adc lint|validate|diff|sync`).
- `k8s/templates/`, for the `oan-a2c-gateway` Helm chart (`k8s/`). This is used with the
  APISIX Ingress Controller.

```bash
python3 apisix/generate_apisix_config_from_spec.py
adc lint -f apisix/adc.yaml
adc sync -f apisix/adc.yaml --label-selector app=oan-a2c
```

Shared logic lives in `oan_auth_service/gateway_apisix.py`, so the generator runs in a bench
that has `oan_auth_service` installed. The throttling tiers and the `(method, path) -> tier`
table are `TIERS` and `TIER_OVERRIDES` in the generator. The generator refuses to run while an
operation has no tier.

Manage a gateway with either the Ingress Controller or `adc sync`, never both. Each one
overwrites the other's routes.

## What the gateway does

| Spec security       | Gateway                                                                                                                                                                                                                                                                          |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BearerAuth`        | Rate limit by client address. Frappe's JWT middleware verifies the token                                                                                                                                                                                                         |
| none                | Rate limit by client address                                                                                                                                                                                                                                                     |
| `PartnerApiKeyAuth` | `key-auth` (header `apikey`). `consumer-restriction` to the one partner consumer of each endpoint (`PARTNER_ENDPOINTS`), so a partner cannot call another partner's endpoint. `ip-restriction`. Rate limit per consumer. `Authorization` is set to the Frappe service credential |
| `DashboardKeyAuth`  | `key-auth` and `consumer-restriction` to the dashboards consumer. Rate limit per consumer                                                                                                                                                                                        |

There is also `/files/*` (GET and HEAD, `static-assets` tier) with a long `Cache-Control`.
Request IDs and Prometheus metrics are global rules on the shared gateway, not per app.

## Secrets

Nothing secret is in the generated files. APISIX resolves `$env://NAME` from its own
environment:

| Variable                           | Holds                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------- |
| `A2C_FRAPPE_SERVICE_AUTHORIZATION` | `token <api_key>:<api_secret>` of the login-disabled Frappe service user for partner routes |
| `A2C_OPENG2P_CONSENT_WEBHOOK_KEY`  | Partner API key                                                                             |
| `OAN_IVR_VENDOR_KEY`               | Telco IVR vendor API key, shared with grievance                                             |
| `A2C_OAN_DASHBOARDS_KEY`           | OAN dashboards API key                                                                      |

In Kubernetes, the consumer keys come from Secrets instead. There is one Secret per
consumer, named after it (key `key`), in the release namespace. Only
`A2C_FRAPPE_SERVICE_AUTHORIZATION` is needed in the APISIX pods' environment.

The telco IVR vendor also calls grievance with the same key, so its consumer,
`oan-ivr-vendor`, is shared (`gateway_apisix.SHARED_CONSUMERS`). This chart does not create
it: the APISIX release does (oan-helm `apisix.yaml`, `extraDeploy`), with its Secret in that
release's namespace. The partner routes whitelist `<sharedConsumerNamespace>_oan-ivr-vendor`,
so set `sharedConsumerNamespace` to that namespace.

## Kubernetes

The chart has no default values. Hosts, the Frappe Service name and port, the site name,
the Redis host, the partner IP allowlist and the CORS origins come from the
per-environment values in [oan-helm](https://github.com/Centre-for-Open-Societal-Systems/oan-helm).
A missing required value fails the render.

```bash
helm upgrade --install oan-a2c-gateway apisix/k8s -n <namespace> \
  -f <oan-helm>/environments/dev/values/oan-a2c-gateway.yaml
```

Environment values read when `adc.yaml` is generated: `OAN_A2C_UPSTREAM_PORT` (8080),
`CORS_ORIGINS_REGEX`, `APISIX_REDIS_HOST` and `APISIX_REDIS_PORT`. These are read by ADC at
sync time: `OAN_A2C_UPSTREAM_HOST` and `FRAPPE_SITE_NAME`.
