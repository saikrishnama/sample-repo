# **Model Serving Endpoints Stop/Start Monitoring and Auto‑Deletion Policy**

## **1. Overview**

The Model Serving Endpoints Stop/Start Monitoring capability provides an automated and governed approach to managing Databricks Model Serving Endpoint runtime behavior. Its purpose is to ensure compute resources—including CPU and GPU capacity—are used efficiently while still supporting workloads that require continuous, low‑latency inference availability.

This framework enforces a default shutdown policy and introduces structured exception mechanisms for both new and legacy Model Serving Endpoints. Endpoints that do not require continuous serving are automatically stopped during each monitoring cycle to support cost optimization and operational hygiene.

As part of the broader cloud cost‑optimization strategy, this capability has been enhanced to include **automatic deletion of inactive Model Serving Endpoints**. Any endpoint that remains stopped and unused for more than **30 consecutive days** will be automatically deleted unless it has an approved exception.

This enhancement helps:

- Reduce model serving‑related cloud and GPU costs
- Improve compute and accelerator utilization
- Minimize operational overhead
- Maintain a clean and accurate Model Serving Endpoint inventory
- Remove stale, abandoned, or experimental endpoints

---

## **2. Default Shutdown Policy**

All Model Serving Endpoints are subject to a default stop action during each monitoring cycle.

This ensures:

- Endpoints not requiring continuous serving do not remain running unnecessarily
- Cost optimization and operational discipline are consistently applied
- Inactive endpoints—including those left running after model experimentation or testing—are automatically transitioned to a stopped state

---

## **3. Exception Mechanism for DAB‑Provisioned Model Serving Endpoints**

Model Serving Endpoints deployed using **Databricks Asset Bundles (DAB)** can be exempted from the default shutdown policy.

To qualify:

- A designated **exception tag** must be included during provisioning
- When present, the monitoring system recognizes the endpoint as requiring **24/7/365 availability**
- These endpoints are excluded from the automated stop workflow

This tag‑based approach provides a standardized, auditable, and transparent method for requesting continuous runtime for production inference workloads while maintaining governance controls.

---

## **4. Exception Handling for Pre‑Existing Model Serving Endpoints**

For Model Serving Endpoints created **before** the introduction of this feature, exceptions are managed through a centralized **exception JSON file**.

- If an endpoint name appears in this file, it is excluded from the stop process
- This ensures backward compatibility and prevents disruption to legacy inference workloads
- Platform teams gain improved visibility and control over older endpoints that still require continuous availability

---

## **5. Auto‑Deletion of Stopped Model Serving Endpoints After 30 Days**

As an extension of the monitoring framework, Model Serving Endpoints that remain in a **stopped state for more than 30 consecutive days** will be automatically scheduled for deletion.

### **Deletion applies only when all conditions are met:**

- The endpoint has been stopped by the monitoring process
- It remains inactive for more than 30 days
- It does **not** have an approved exception for retention

### **Benefits of the deletion policy:**

- Reduced model serving‑related cloud and GPU costs
- Stronger resource governance over inference infrastructure
- Cleaner Model Serving Endpoint inventory
- Lower operational complexity
- Removal of stale, abandoned, or one‑off experimental endpoints

---

## **6. Exception Handling for Deletion**

Model Serving Endpoints requiring retention beyond the 30‑day inactivity threshold must have a valid justification, such as:

- Business requirements (e.g., seasonal or on‑demand inference workloads)
- Compliance, audit, or model reproducibility needs
- Operational or downstream application dependencies
- Regulatory model retention obligations

### **Exception requests must include:**

- Business justification
- Endpoint ownership details (team, model owner, served model version)
- Expected retention period
- Approval from designated governance owners

This ensures flexibility for critical inference workloads while maintaining strong governance and lifecycle management.

---

## **7. Conclusion**

The enhanced Model Serving Endpoints Stop/Start Monitoring and Auto‑Deletion framework strengthens cloud resource management by combining:

- Automated stopping of inactive Model Serving Endpoints
- Exception‑based support for continuous, production‑grade inference availability
- Lifecycle‑driven cleanup of unused endpoints

By introducing automatic deletion after 30 days of inactivity, the platform improves cost efficiency, operational discipline, and alignment with enterprise cloud governance standards—while still allowing controlled exceptions for business‑critical model serving workloads.
