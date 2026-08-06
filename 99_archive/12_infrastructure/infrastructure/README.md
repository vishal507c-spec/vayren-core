# infrastructure/ — Infrastructure

## What Is Infrastructure?

**Infrastructure** is everything needed to run, deploy, and monitor the system. It includes:

- **Docker:** Containerization for consistent environments
- **CI/CD:** Automated testing and deployment pipelines
- **Monitoring:** Watching system health and performance
- **Scripts:** Automation for setup and maintenance

---

## Purpose

`infrastructure/` contains all deployment, operations, and monitoring configuration. It defines how the system runs in production.

---

## Why This Folder Exists

Infrastructure configuration (Dockerfiles, deployment scripts, CI pipelines) is fundamentally different from application code:

- It is declarative (YAML, configuration) rather than imperative (Python)
- It depends on specific environments (development, staging, production)
- It changes at a different cadence than application code

By keeping infrastructure separate from application code, neither one pollutes the other.

---

## Rule

**No Python source files should be in `infrastructure/`.** Only YAML, shell scripts, and configuration files belong here.

---

## What Is Inside

| Component | Purpose |
|---|---|
| `docker/` | Dockerfile and docker-compose for containerized deployment |
| `kubernetes/` | Kubernetes manifests for orchestrated deployment |
| `terraform/` | Infrastructure as Code for cloud resources |
| `monitoring/` | Prometheus and Grafana configuration |
| `ci/` | CI/CD pipeline definitions |
| `scripts/` | Deployment and setup shell scripts |

---

## Quick Example

```yaml
# infrastructure/docker/docker-compose.yml
version: "3.9"
services:
  api:
    build:
      context: ../..
      dockerfile: infrastructure/docker/Dockerfile
    ports:
      - "8000:8000"
```

---

## Continue to the Next Lesson

→ `knowledge/` — Company memory
